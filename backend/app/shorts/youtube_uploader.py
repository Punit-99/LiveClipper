"""YouTube Shorts video uploader and daily quota manager.

Implements the official Google YouTube Data API v3 Resumable Upload protocol
with chunk streaming, transient failure retries, status query resumption,
and daily quota protection.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from app.logging_setup import get_logger
from app.shorts.metadata import format_youtube_snippet
from app.shorts.youtube_auth import (
    ReauthRequired,
    TransientAuthError,
    YouTubeAuthError,
    YouTubeTokenManager,
)

log = get_logger(__name__)

# Official YouTube resumable upload endpoint
RESUMABLE_UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
)

# Standard YouTube Data API v3 quota costs
VIDEO_INSERT_QUOTA_COST = 1600
DEFAULT_DAILY_QUOTA_UNITS = 10000

VALID_PRIVACY_STATUSES = ("private", "unlisted", "public")


class YouTubeUploadError(Exception):
    """Base error for YouTube upload failures."""


class QuotaExceededError(YouTubeUploadError):
    """Daily upload quota or upload limit reached."""


class TransientUploadError(YouTubeUploadError):
    """Transient upload error (network drop, 5xx). Can be retried."""


class UploadSessionExpiredError(YouTubeUploadError):
    """Upload session URL expired (404). Must start from the beginning."""


class DailyQuotaTracker:
    """Thread-safe tracker for YouTube API upload quota with atomic persistence."""

    def __init__(
        self,
        quota_path: Path | str,
        daily_limit: int = 5,
        max_units: int = DEFAULT_DAILY_QUOTA_UNITS,
        date_fn: Callable[[], str] | None = None,
    ) -> None:
        self.quota_path = Path(quota_path)
        self.daily_limit = max(1, int(daily_limit))
        self.max_units = max(1, int(max_units))
        self._date_fn = date_fn or self._default_date_fn
        self._lock = threading.Lock()

    @staticmethod
    def _default_date_fn() -> str:
        """Default date provider: current UTC date string YYYY-MM-DD."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def get_today_date(self) -> str:
        return self._date_fn()

    def get_status(self) -> dict[str, Any]:
        """Return current quota snapshot, automatically handling midnight resets."""
        with self._lock:
            return self._get_status_locked()

    def _get_status_locked(self) -> dict[str, Any]:
        today = self.get_today_date()
        data = self._read_quota_file_locked()
        if not data or data.get("date") != today:
            # New day: reset counters
            return {
                "date": today,
                "uploads_today": 0,
                "units_used": 0,
                "daily_limit": self.daily_limit,
                "max_units": self.max_units,
            }
        return {
            "date": today,
            "uploads_today": int(data.get("uploads_today", 0)),
            "units_used": int(data.get("units_used", 0)),
            "daily_limit": self.daily_limit,
            "max_units": self.max_units,
        }

    def can_upload(self) -> tuple[bool, str]:
        """Check if another upload is permitted under both daily limits and quota."""
        with self._lock:
            status = self._get_status_locked()
            uploads = status["uploads_today"]
            units = status["units_used"]

            if uploads >= self.daily_limit:
                return (
                    False,
                    f"Daily upload limit reached ({uploads}/{self.daily_limit} uploads today).",
                )
            if units + VIDEO_INSERT_QUOTA_COST > self.max_units:
                return (
                    False,
                    f"Daily YouTube API quota limit reached ({units}/{self.max_units} units used).",
                )
            return True, "OK"

    def record_upload(self) -> None:
        """Record a completed video upload (increments count and quota units)."""
        with self._lock:
            status = self._get_status_locked()
            status["uploads_today"] += 1
            status["units_used"] += VIDEO_INSERT_QUOTA_COST
            status["updated_at"] = time.time()
            self._write_quota_file_locked(status)
            log.info(
                "YouTube upload recorded (uploads_today=%d/%d, units_used=%d/%d)",
                status["uploads_today"],
                self.daily_limit,
                status["units_used"],
                self.max_units,
            )

    def reset_today(self) -> None:
        """Reset quota for today (primarily for testing)."""
        with self._lock:
            today = self.get_today_date()
            payload = {
                "date": today,
                "uploads_today": 0,
                "units_used": 0,
                "daily_limit": self.daily_limit,
                "max_units": self.max_units,
                "updated_at": time.time(),
            }
            self._write_quota_file_locked(payload)

    def _read_quota_file_locked(self) -> dict[str, Any] | None:
        if not self.quota_path.exists():
            return None
        try:
            with open(self.quota_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read YouTube quota file at %s: %s", self.quota_path, exc)
            return None

    def _write_quota_file_locked(self, data: dict[str, Any]) -> None:
        self.quota_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".youtube_quota.",
            suffix=".tmp",
            dir=str(self.quota_path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.replace(tmp_path, self.quota_path)
            except OSError as err:
                if err.errno in (16, 18):  # EBUSY or EXDEV
                    with open(self.quota_path, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, indent=2)
                        fh.flush()
                        os.fsync(fh.fileno())
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                else:
                    raise
        except BaseException:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise


class YouTubeUploader:
    """Manages resumable video uploads to YouTube Shorts with retries and resumption."""

    def __init__(
        self,
        token_manager: YouTubeTokenManager,
        quota_tracker: DailyQuotaTracker | None = None,
        session: requests.Session | None = None,
        chunk_size: int = 4 * 1024 * 1024,  # 4 MiB chunks
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self.token_manager = token_manager
        self.quota_tracker = quota_tracker
        self._session = session or requests.Session()
        self.chunk_size = max(256 * 1024, int(chunk_size))  # Must be >= 256 KiB
        self.max_retries = max(1, int(max_retries))
        self.retry_delay = max(0.1, float(retry_delay))

    def upload_video(
        self,
        video_path: Path | str,
        snippet: dict[str, Any] | None = None,
        sidecar_path: Path | str | None = None,
        sidecar_data: dict[str, Any] | None = None,
        privacy_status: str = "public",
    ) -> dict[str, Any]:
        """Upload a video file to YouTube Shorts using the resumable upload protocol."""
        video_file = Path(video_path)
        if not video_file.is_file():
            raise FileNotFoundError(f"Video file not found at: {video_file}")

        file_size = video_file.stat().st_size
        if file_size <= 0:
            raise YouTubeUploadError(f"Cannot upload empty video file: {video_file}")

        # Validate privacy status
        clean_privacy = str(privacy_status or "public").strip().lower()
        if clean_privacy not in VALID_PRIVACY_STATUSES:
            raise ValueError(
                f"Invalid privacy_status '{privacy_status}'. Must be one of {VALID_PRIVACY_STATUSES}."
            )

        # Quota check
        if self.quota_tracker:
            allowed, reason = self.quota_tracker.can_upload()
            if not allowed:
                raise QuotaExceededError(reason)

        # Resolve snippet metadata
        final_snippet = self._resolve_snippet(
            snippet=snippet,
            sidecar_path=sidecar_path,
            sidecar_data=sidecar_data,
        )

        status_payload = {
            "privacyStatus": clean_privacy,
            "selfDeclaredMadeForKids": False,
        }

        # Step 1: Initiate resumable upload session
        upload_url = self._initiate_upload_session(
            file_size=file_size,
            snippet=final_snippet,
            status=status_payload,
        )

        # Step 2: Stream chunks with resumption and retries
        upload_result = self._stream_chunks(upload_url, video_file, file_size)

        # Step 3: Record successful upload against quota
        if self.quota_tracker:
            self.quota_tracker.record_upload()

        video_id = upload_result.get("id") or "unknown"
        shorts_url = f"https://www.youtube.com/shorts/{video_id}"

        log.info(
            "Successfully uploaded YouTube Short: title='%s', id=%s, privacy=%s",
            final_snippet.get("title"),
            video_id,
            clean_privacy,
        )

        return {
            "video_id": video_id,
            "url": shorts_url,
            "title": final_snippet.get("title"),
            "privacy_status": clean_privacy,
            "uploaded_at_iso": datetime.now(timezone.utc).isoformat(),
            "raw_response": upload_result,
        }

    def _resolve_snippet(
        self,
        snippet: dict[str, Any] | None,
        sidecar_path: Path | str | None,
        sidecar_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Construct a full YouTube snippet from explicit snippet or sidecar metadata."""
        if snippet:
            return dict(snippet)

        data = sidecar_data
        if not data and sidecar_path:
            p = Path(sidecar_path)
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except Exception as exc:
                    log.warning("Could not read sidecar file %s: %s", p, exc)

        if data:
            return format_youtube_snippet(data)

        # Fallback snippet
        return format_youtube_snippet({
            "analysis": {"title": "Live Stream Highlight"},
            "streamer_name": "Streamer",
        })

    def _initiate_upload_session(
        self,
        file_size: int,
        snippet: dict[str, Any],
        status: dict[str, Any],
    ) -> str:
        """Send the initial POST request to obtain the resumable upload URL."""
        body = {
            "snippet": snippet,
            "status": status,
        }

        for attempt in range(self.max_retries):
            token = self.token_manager.get_valid_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(file_size),
                "X-Upload-Content-Type": "video/mp4",
            }

            try:
                resp = self._session.post(
                    RESUMABLE_UPLOAD_URL,
                    headers=headers,
                    json=body,
                    timeout=30.0,
                )
            except requests.RequestException as exc:
                if attempt < self.max_retries - 1:
                    sleep_s = self._backoff_delay(attempt)
                    log.warning(
                        "Network error initiating upload session (attempt %d/%d): %s. Retrying in %.1fs...",
                        attempt + 1,
                        self.max_retries,
                        exc,
                        sleep_s,
                    )
                    time.sleep(sleep_s)
                    continue
                raise TransientUploadError(f"Network error initiating upload: {exc}") from exc

            # Handle 401 Unauthorized (token may have been revoked or invalidated)
            if resp.status_code == 401 and attempt < self.max_retries - 1:
                log.info("Received HTTP 401 during upload initiation; forcing token refresh...")
                try:
                    self.token_manager.refresh_access_token()
                except Exception as e:
                    raise ReauthRequired(f"Token refresh failed after 401: {e}") from e
                continue

            # Handle Quota Exceeded (403 quotaExceeded)
            if resp.status_code == 403:
                try:
                    err_info = resp.json().get("error", {})
                    errors = err_info.get("errors", [])
                    reasons = [e.get("reason") for e in errors]
                    if "quotaExceeded" in reasons or "rateLimitExceeded" in reasons:
                        raise QuotaExceededError(
                            f"YouTube API quota exceeded: {err_info.get('message')}"
                        )
                except QuotaExceededError:
                    raise
                except Exception:
                    pass
                raise YouTubeUploadError(f"HTTP 403 Forbidden initiating upload: {resp.text}")

            # Handle transient 5xx errors
            if resp.status_code in (500, 502, 503, 504) and attempt < self.max_retries - 1:
                sleep_s = self._backoff_delay(attempt)
                log.warning(
                    "HTTP %d initiating upload session (attempt %d/%d). Retrying in %.1fs...",
                    resp.status_code,
                    attempt + 1,
                    self.max_retries,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue

            if resp.status_code != 200:
                raise YouTubeUploadError(
                    f"Unexpected HTTP {resp.status_code} initiating upload: {resp.text}"
                )

            upload_url = resp.headers.get("Location")
            if not upload_url:
                raise YouTubeUploadError("Upload session response missing 'Location' header.")

            log.info("Acquired resumable upload session URL from YouTube")
            return upload_url

        raise TransientUploadError("Failed to initiate upload session after retries.")

    def _stream_chunks(
        self,
        upload_url: str,
        video_file: Path,
        file_size: int,
    ) -> dict[str, Any]:
        """Stream video bytes in chunks to the resumable upload URL with resumption support."""
        start_byte = 0

        with open(video_file, "rb") as fh:
            while start_byte < file_size:
                end_byte = min(start_byte + self.chunk_size, file_size) - 1
                chunk_length = end_byte - start_byte + 1

                fh.seek(start_byte)
                chunk_data = fh.read(chunk_length)

                headers = {
                    "Content-Length": str(len(chunk_data)),
                    "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                    "Content-Type": "video/mp4",
                }

                chunk_success = False
                for attempt in range(self.max_retries):
                    try:
                        resp = self._session.put(
                            upload_url,
                            headers=headers,
                            data=chunk_data,
                            timeout=60.0,
                        )
                    except requests.RequestException as exc:
                        log.warning(
                            "Network exception uploading chunk [%d-%d/%d] (attempt %d/%d): %s",
                            start_byte,
                            end_byte,
                            file_size,
                            attempt + 1,
                            self.max_retries,
                            exc,
                        )
                        if attempt < self.max_retries - 1:
                            time.sleep(self._backoff_delay(attempt))
                            # Query upload status to determine what YouTube actually received
                            resumed_offset, completed_body = self._query_upload_status(
                                upload_url, file_size
                            )
                            if completed_body:
                                return completed_body
                            start_byte = resumed_offset
                            chunk_success = True
                            break
                        raise TransientUploadError(
                            f"Network error uploading chunk: {exc}"
                        ) from exc

                    # Upload Complete: 200 OK or 201 Created
                    if resp.status_code in (200, 201):
                        try:
                            return resp.json()
                        except ValueError as exc:
                            raise YouTubeUploadError(
                                "Invalid JSON response on upload completion"
                            ) from exc

                    # Chunk received, resume next chunk: HTTP 308 Resume Incomplete
                    if resp.status_code == 308:
                        range_hdr = resp.headers.get("Range")
                        if range_hdr and range_hdr.startswith("bytes="):
                            last_byte = int(range_hdr.split("=")[1].split("-")[1])
                            start_byte = last_byte + 1
                        else:
                            start_byte = end_byte + 1
                        chunk_success = True
                        break

                    # Transient server error on chunk upload
                    if resp.status_code in (500, 502, 503, 504):
                        log.warning(
                            "HTTP %d on chunk [%d-%d/%d] (attempt %d/%d)",
                            resp.status_code,
                            start_byte,
                            end_byte,
                            file_size,
                            attempt + 1,
                            self.max_retries,
                        )
                        if attempt < self.max_retries - 1:
                            time.sleep(self._backoff_delay(attempt))
                            # Resume from current server state
                            resumed_offset, completed_body = self._query_upload_status(
                                upload_url, file_size
                            )
                            if completed_body:
                                return completed_body
                            start_byte = resumed_offset
                            chunk_success = True
                            break
                        raise TransientUploadError(
                            f"HTTP {resp.status_code} during chunk upload: {resp.text}"
                        )

                    # Non-recoverable error
                    raise YouTubeUploadError(
                        f"Chunk upload failed with HTTP {resp.status_code}: {resp.text}"
                    )

                if not chunk_success:
                    raise TransientUploadError(
                        f"Failed to upload chunk starting at byte {start_byte} after retries."
                    )

        raise YouTubeUploadError("Reached end of file without receiving 200/201 from YouTube.")

    def _query_upload_status(
        self, upload_url: str, file_size: int
    ) -> tuple[int, dict[str, Any] | None]:
        """Query YouTube upload status after interruption to discover last received byte offset."""
        headers = {
            "Content-Length": "0",
            "Content-Range": f"bytes */{file_size}",
        }
        try:
            resp = self._session.put(upload_url, headers=headers, timeout=20.0)
        except requests.RequestException as exc:
            log.warning("Status query failed due to network: %s; keeping current offset", exc)
            return 0, None

        if resp.status_code in (200, 201):
            try:
                return file_size, resp.json()
            except ValueError:
                return file_size, {"id": "unknown"}

        if resp.status_code == 308:
            range_hdr = resp.headers.get("Range")
            if range_hdr and range_hdr.startswith("bytes="):
                last_byte = int(range_hdr.split("=")[1].split("-")[1])
                return last_byte + 1, None
            return 0, None

        if resp.status_code == 404:
            raise UploadSessionExpiredError("Resumable upload session expired (HTTP 404)")

        log.warning("Upload status query returned unexpected HTTP %d: %s", resp.status_code, resp.text)
        return 0, None

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter."""
        base = self.retry_delay * (2 ** attempt)
        jitter = random.uniform(0.0, 0.5)
        return round(base + jitter, 2)

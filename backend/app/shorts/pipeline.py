"""Shorts pipeline orchestrator and state management.

Coordinates the end-to-end flow:
1. Scan clips directory for LiveClipper highlights.
2. Filter highlights by score and state.
3. Process via OpenShortsBridge (duration adaptation + 9:16 vertical reframing).
4. Maintain persistent processing state in shorts/manifest.jsonl.
5. Review and upload approved/qualifying shorts to YouTube Shorts.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import Settings, load_settings
from app.logging_setup import get_logger
from app.shorts.openshorts_bridge import OpenShortsBridge
from app.shorts.viral_metadata import (
    ViralMetadata,
    ViralMetadataGenerator,
    sanitize_viral_title,
)
from app.shorts.youtube_auth import YouTubeTokenManager
from app.shorts.youtube_uploader import DailyQuotaTracker, QuotaExceededError, YouTubeUploader

log = get_logger(__name__)

# Valid clip manifest states
STATE_DISCOVERED = "DISCOVERED"
STATE_PROCESSED = "PROCESSED"
STATE_APPROVED = "APPROVED"
STATE_REJECTED = "REJECTED"
STATE_UPLOADED = "UPLOADED"
STATE_FAILED = "FAILED"

VALID_STATES = (
    STATE_DISCOVERED,
    STATE_PROCESSED,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_UPLOADED,
    STATE_FAILED,
)


class ShortsManifest:
    """Thread-safe persistent manifest tracking clip processing and upload lifecycle."""

    def __init__(self, manifest_path: Path | str) -> None:
        self.manifest_path = Path(manifest_path)
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> dict[str, dict[str, Any]]:
        """Load manifest from jsonl. Latest line per clip_id wins."""
        with self._lock:
            self._records.clear()
            if not self.manifest_path.is_file():
                return {}
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            clip_id = record.get("clip_id")
                            if clip_id:
                                self._records[str(clip_id)] = record
                        except json.JSONDecodeError:
                            continue
            except OSError as exc:
                log.warning("Could not read manifest at %s: %s", self.manifest_path, exc)
            return dict(self._records)

    def get(self, clip_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._records.get(str(clip_id))
            return dict(rec) if rec else None

    def all_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._records.values()]

    def update_record(self, clip_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update or insert a clip record and persist atomically."""
        with self._lock:
            cid = str(clip_id)
            current = self._records.get(cid, {"clip_id": cid})
            current.update(kwargs)
            current["updated_at_iso"] = datetime.now(timezone.utc).isoformat()
            self._records[cid] = current
            self._save_locked()
            return dict(current)

    def remove_record(self, clip_id: str) -> bool:
        """Remove a clip record from the manifest and persist atomically."""
        with self._lock:
            cid = str(clip_id)
            if cid in self._records:
                del self._records[cid]
                try:
                    self._save_locked()
                except OSError:
                    with open(self.manifest_path, "w", encoding="utf-8") as fh:
                        for record in self._records.values():
                            fh.write(json.dumps(record) + "\n")
                return True
            return False

    def clear_records(self) -> int:
        """Clear all records from the manifest and persist atomically."""
        with self._lock:
            count = len(self._records)
            self._records.clear()
            try:
                self._save_locked()
            except OSError:
                with open(self.manifest_path, "w", encoding="utf-8") as fh:
                    pass
            return count

    def _save_locked(self) -> None:
        """Atomic write of all records to manifest.jsonl."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".manifest.",
            suffix=".tmp",
            dir=str(self.manifest_path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for record in self._records.values():
                    fh.write(json.dumps(record) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.replace(tmp_path, self.manifest_path)
            except OSError as err:
                if err.errno in (16, 18):  # EBUSY or EXDEV
                    with open(self.manifest_path, "w", encoding="utf-8") as fh:
                        for record in self._records.values():
                            fh.write(json.dumps(record) + "\n")
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


class ShortsPipeline:
    """Coordinates batch scanning, reframing, and optional YouTube Shorts uploading."""

    def __init__(
        self,
        settings: Settings | None = None,
        bridge: OpenShortsBridge | None = None,
        uploader: YouTubeUploader | None = None,
        manifest: ShortsManifest | None = None,
        clips_dir: Path | str | None = None,
        shorts_dir: Path | str | None = None,
        viral_generator: ViralMetadataGenerator | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.clips_dir = Path(clips_dir) if clips_dir else self.settings.clips_output_dir
        self.shorts_dir = Path(shorts_dir) if shorts_dir else self.settings.shorts_output_dir
        self.imports_dir = Path(getattr(self.settings, "imports_dir", self.clips_dir.parent / "imports"))

        self.manifest = manifest or ShortsManifest(self.shorts_dir / "manifest.jsonl")

        self.bridge = bridge or OpenShortsBridge(
            output_dir=self.shorts_dir,
            engine=self.settings.shorts_engine,
            max_duration=self.settings.shorts_max_duration,
            docker_image=self.settings.openshorts_docker_image,
            docker_timeout=self.settings.openshorts_docker_timeout,
            subtitle_style=self.settings.openshorts_subtitle_style,
            reframe_mode=self.settings.openshorts_reframe_mode,
            output_bitrate=self.settings.shorts_output_bitrate,
            output_crf=self.settings.shorts_output_crf,
            hook_overlay_enabled=getattr(self.settings, "hook_overlay_enabled", False),
        )

        if viral_generator is not None:
            self.viral_generator: ViralMetadataGenerator | None = viral_generator
        elif getattr(self.settings, "viral_metadata_enabled", True):
            self.viral_generator = ViralMetadataGenerator.from_settings(self.settings)
        else:
            self.viral_generator = None

        if uploader is not None:
            self.uploader: YouTubeUploader | None = uploader
        elif self.settings.youtube_client_id and self.settings.youtube_client_secret:
            token_mgr = YouTubeTokenManager(
                tokens_path=self.settings.youtube_tokens_path,
                client_id=self.settings.youtube_client_id,
                client_secret=self.settings.youtube_client_secret,
            )
            quota_trk = DailyQuotaTracker(
                quota_path=self.shorts_dir / ".youtube_quota.json",
                daily_limit=self.settings.youtube_daily_limit,
            )
            self.uploader = YouTubeUploader(
                token_manager=token_mgr,
                quota_tracker=quota_trk,
            )
        else:
            self.uploader = None

    def scan_clips(self, channel: str | None = None) -> list[dict[str, Any]]:
        """Scan clips_dir for MP4 clips and register discovered clips into manifest."""
        if not self.clips_dir.is_dir():
            return []

        clean_channel = channel.lstrip("#").strip().lower() if channel else None
        discovered: list[dict[str, Any]] = []

        # Scan clips/<channel>/*.mp4, clips/<platform>/<channel>/*.mp4, and clips/*.mp4
        clip_files: list[Path] = []
        if clean_channel:
            clip_files.extend(self.clips_dir.glob(f"{clean_channel}/*.mp4"))
            clip_files.extend(self.clips_dir.glob(f"*/{clean_channel}/*.mp4"))
        else:
            clip_files.extend(self.clips_dir.glob("*/*/*.mp4"))
            clip_files.extend(self.clips_dir.glob("*/*.mp4"))
            clip_files.extend(self.clips_dir.glob("*.mp4"))

        # Also scan imported clips directory (e.g. imports/<video_id>/clips/*.mp4)
        if self.imports_dir.is_dir():
            clip_files.extend(self.imports_dir.glob("*/*/*.mp4"))
            clip_files.extend(self.imports_dir.glob("*/*.mp4"))

        seen_paths: set[Path] = set()
        deduped_files: list[Path] = []
        for cf in clip_files:
            res = cf.resolve()
            if res not in seen_paths:
                seen_paths.add(res)
                deduped_files.append(cf)
        clip_files = deduped_files

        for clip_file in clip_files:
            if clip_file.name.startswith((".", "_")):
                continue

            # Load sidecar if present
            sidecar_path = clip_file.with_suffix(".json")
            sidecar_data = {}
            if sidecar_path.is_file():
                try:
                    with open(sidecar_path, "r", encoding="utf-8") as fh:
                        sidecar_data = json.load(fh)
                except Exception:
                    pass

            clip_id = sidecar_data.get("clip_id") or self._extract_clip_id(clip_file.stem)
            ch = sidecar_data.get("streamer_name") or clip_file.parent.name
            if ch == self.clips_dir.name:
                ch = "streamer"
            ch = ch.lstrip("#").strip().lower()

            analysis = sidecar_data.get("analysis") or {}
            score = int(analysis.get("highlight_score", 0))
            title = analysis.get("title") or "Stream Highlight"
            category = analysis.get("category") or "highlight"

            is_imported = bool(sidecar_data.get("is_imported") or sidecar_data.get("source_type") == "imported" or clip_file.stem.startswith("imp_"))
            source_type = "imported" if is_imported else "live"
            if is_imported:
                platform = "youtube"
                score = int(sidecar_data.get("score") or analysis.get("highlight_score", 0))
                title = sidecar_data.get("suggested_title") or sidecar_data.get("title") or analysis.get("title") or "Imported Highlight"
            else:
                platform = sidecar_data.get("platform")
                if not platform:
                    if clip_file.parent.parent.name in ("twitch", "youtube"):
                        platform = clip_file.parent.parent.name
                    else:
                        platform = "youtube"

            existing = self.manifest.get(clip_id)
            if existing is None:
                record = self.manifest.update_record(
                    clip_id=clip_id,
                    channel=ch,
                    platform=platform,
                    source_clip_path=str(clip_file.resolve()),
                    source_sidecar_path=str(sidecar_path.resolve()) if sidecar_path.is_file() else None,
                    highlight_score=score,
                    title=title,
                    category=category,
                    state=STATE_DISCOVERED,
                    is_imported=is_imported,
                    source_type=source_type,
                )
            else:
                record = existing
                # If file paths changed, update them
                if record.get("source_clip_path") != str(clip_file.resolve()) or not record.get("platform") or record.get("is_imported") != is_imported:
                    record = self.manifest.update_record(
                        clip_id=clip_id,
                        platform=platform,
                        source_clip_path=str(clip_file.resolve()),
                        source_sidecar_path=str(sidecar_path.resolve()) if sidecar_path.is_file() else None,
                        is_imported=is_imported,
                        source_type=source_type,
                    )

            discovered.append(record)

        return discovered

    def process_pending_shorts(
        self,
        channel: str | None = None,
        clip_id_or_file: str | None = None,
        dry_run: bool = False,
        force: bool = False,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        """Process pending DISCOVERED clips into vertical 9:16 shorts.

        Idempotent: skips clips already in PROCESSED, APPROVED, or UPLOADED state unless force=True.
        Isolated: failure processing one clip does not halt processing of others.
        """
        all_clips = self.scan_clips(channel=channel)

        candidates: list[dict[str, Any]] = []
        for c in all_clips:
            cid = c["clip_id"]
            state = c.get("state", STATE_DISCOVERED)

            # Filter by clip_id_or_file if specified
            if clip_id_or_file:
                c_stem = Path(c["source_clip_path"]).stem
                c_name = Path(c["source_clip_path"]).name
                if clip_id_or_file not in (cid, c_stem, c_name):
                    continue

            if force or state in (STATE_DISCOVERED, STATE_FAILED):
                candidates.append(c)

        if dry_run:
            log.info("[DRY RUN] Found %d clip(s) to process.", len(candidates))
            return {
                "dry_run": True,
                "found": len(all_clips),
                "candidates": len(candidates),
                "processed": 0,
                "skipped": len(all_clips) - len(candidates),
                "failed": 0,
                "items": candidates,
            }

        processed_count = 0
        failed_count = 0
        skipped_count = len(all_clips) - len(candidates)
        results: list[dict[str, Any]] = []

        for item in candidates:
            cid = item["clip_id"]
            src_path = Path(item["source_clip_path"])
            sidecar_path = item.get("source_sidecar_path")
            ch = item.get("channel", "streamer")

            bridge_progress = None
            if progress_callback:
                bridge_progress = lambda stage, pct, _cid=cid: progress_callback(_cid, stage, pct)

            log.info("Processing short candidate %s (%s)...", cid, src_path.name)
            try:
                short_vid, short_sidecar, meta = self.bridge.process_clip(
                    clip_path=src_path,
                    sidecar_path_or_data=sidecar_path,
                    channel=ch,
                    progress_callback=bridge_progress,
                )

                viral_titles = None
                if self.viral_generator and short_sidecar and Path(short_sidecar).is_file():
                    try:
                        sidecar_dict = {}
                        try:
                            with open(short_sidecar, "r", encoding="utf-8") as sfh:
                                content = sfh.read().strip()
                                if content:
                                    sidecar_dict = json.loads(content)
                        except Exception:
                            sidecar_dict = {}

                        if not sidecar_dict.get("viral_metadata"):
                            vmeta = self.viral_generator.generate_metadata(sidecar_dict)
                            sidecar_dict["viral_metadata"] = vmeta.to_dict()
                            with open(short_sidecar, "w", encoding="utf-8") as sfh:
                                json.dump(sidecar_dict, sfh, indent=2)
                            viral_titles = [t["title"] for t in vmeta.titles]
                        else:
                            vm = sidecar_dict["viral_metadata"]
                            if isinstance(vm, dict) and "titles" in vm:
                                viral_titles = [
                                    t["title"] if isinstance(t, dict) else str(t)
                                    for t in vm["titles"]
                                ]
                    except Exception as v_exc:
                        log.warning(
                            "Could not enrich short sidecar %s with viral metadata: %s",
                            short_sidecar,
                            v_exc,
                        )

                rec_kwargs: dict[str, Any] = {
                    "clip_id": cid,
                    "state": STATE_PROCESSED,
                    "short_video_path": str(short_vid.resolve()),
                    "short_sidecar_path": str(short_sidecar.resolve()),
                    "processing_engine": meta.get("engine"),
                    "final_duration": meta.get("final_duration_seconds"),
                    "error": None,
                }
                if viral_titles:
                    rec_kwargs["viral_titles"] = viral_titles
                updated = self.manifest.update_record(**rec_kwargs)
                processed_count += 1
                results.append(updated)
            except Exception as exc:
                log.error("Failed to process clip %s: %s", cid, exc, exc_info=True)
                updated = self.manifest.update_record(
                    clip_id=cid,
                    state=STATE_FAILED,
                    error=str(exc),
                )
                failed_count += 1
                results.append(updated)

        return {
            "dry_run": False,
            "found": len(all_clips),
            "candidates": len(candidates),
            "processed": processed_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "items": results,
        }

    def list_upload_candidates(
        self,
        channel: str | None = None,
        min_score: int | None = None,
    ) -> list[dict[str, Any]]:
        """List processed shorts eligible for YouTube upload, sorted by score descending."""
        self.scan_clips(channel=channel)
        records = self.manifest.all_records()
        clean_channel = channel.lstrip("#").strip().lower() if channel else None
        threshold = self.settings.youtube_min_score if min_score is None else int(min_score)

        eligible: list[dict[str, Any]] = []
        for r in records:
            if clean_channel and r.get("channel") != clean_channel:
                continue

            state = r.get("state")
            # Only PROCESSED or APPROVED shorts are eligible
            if state not in (STATE_PROCESSED, STATE_APPROVED):
                continue

            short_vid = r.get("short_video_path")
            if not short_vid or not Path(short_vid).is_file():
                continue

            score = int(r.get("highlight_score", 0))
            if score >= threshold:
                eligible.append(r)

        # Sort by highlight_score descending
        eligible.sort(key=lambda x: int(x.get("highlight_score", 0)), reverse=True)
        return eligible

    def upload_shorts(
        self,
        channel: str | None = None,
        min_score: int | None = None,
        interactive: bool = False,
        auto_upload: bool = False,
        privacy_status: str | None = None,
        clip_id: str | None = None,
        prompt_fn: Callable[[dict[str, Any]], str] | None = None,
        title_prompt_fn: Callable[[dict[str, Any], list[dict[str, str]]], str] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Upload eligible shorts to YouTube Shorts respecting quota limits."""
        if not self.uploader:
            raise RuntimeError(
                "YouTubeUploader not configured. Provide YOUTUBE_CLIENT_ID and "
                "YOUTUBE_CLIENT_SECRET or run 'python backend/authorize_youtube.py'."
            )

        privacy = privacy_status or self.settings.youtube_privacy_status

        if clip_id:
            record = self.manifest.get(clip_id)
            if record:
                # Check video path and resolve if relative or moved
                video_str = record.get("short_video_path")
                v_path = Path(video_str) if video_str else None
                if not v_path or not v_path.is_file():
                    for cand in self.settings.shorts_output_dir.rglob(f"{clip_id}*_short.mp4"):
                        if cand.is_file():
                            v_path = cand
                            record["short_video_path"] = str(cand)
                            break
                if v_path and v_path.is_file():
                    candidates = [record]
                else:
                    candidates = []
            else:
                candidates = []
        else:
            candidates = self.list_upload_candidates(channel=channel, min_score=min_score)

        uploaded_count = 0
        skipped_count = 0
        rejected_count = 0
        failed_count = 0
        results: list[dict[str, Any]] = []

        for candidate in candidates:
            cid = candidate["clip_id"]
            score = candidate.get("highlight_score", 0)
            orig_title = candidate.get("title", "")
            video_path = Path(candidate["short_video_path"])
            if not video_path.is_file():
                for cand in self.settings.shorts_output_dir.rglob(f"{cid}*_short.mp4"):
                    if cand.is_file():
                        video_path = cand
                        break
            sidecar_path = candidate.get("short_sidecar_path") or candidate.get("source_sidecar_path")
            target_sidecar_path = Path(sidecar_path) if sidecar_path else None
            if not target_sidecar_path or not target_sidecar_path.is_file():
                for cand_sc in self.settings.shorts_output_dir.rglob(f"{cid}*_short.json"):
                    if cand_sc.is_file():
                        target_sidecar_path = cand_sc
                        break

            # 1. Check daily quota before attempting upload
            if self.uploader.quota_tracker:
                allowed, reason = self.uploader.quota_tracker.can_upload()
                if not allowed:
                    log.warning("Halting uploads: %s", reason)
                    break

            # 2. Resolve sidecar data
            sidecar_data: dict[str, Any] = {}
            if target_sidecar_path and target_sidecar_path.is_file():
                try:
                    with open(target_sidecar_path, "r", encoding="utf-8") as sfh:
                        content = sfh.read().strip()
                        if content:
                            sidecar_data = json.loads(content)
                except Exception as exc:
                    log.warning("Could not read sidecar file at %s: %s", target_sidecar_path, exc)

            if not sidecar_data:
                sidecar_data = {
                    "clip_id": cid,
                    "streamer_name": candidate.get("channel", "streamer"),
                    "analysis": {
                        "title": orig_title or "Stream Highlight",
                        "highlight_score": score,
                        "category": candidate.get("category", "highlight"),
                    },
                }

            # 3. Generate or retrieve viral metadata
            viral_titles: list[dict[str, str]] = []
            viral_meta_dict = sidecar_data.get("viral_metadata")

            if self.viral_generator and getattr(self.settings, "viral_metadata_enabled", True):
                if not viral_meta_dict:
                    try:
                        v_meta = self.viral_generator.generate_metadata(sidecar_data)
                        viral_meta_dict = v_meta.to_dict()
                        sidecar_data["viral_metadata"] = viral_meta_dict
                    except Exception as v_err:
                        log.warning("Failed to generate viral metadata for %s: %s", cid, v_err)
                        v_meta = self.viral_generator.generate_fallback_metadata(sidecar_data)
                        viral_meta_dict = v_meta.to_dict()
                        sidecar_data["viral_metadata"] = viral_meta_dict
                if viral_meta_dict and "titles" in viral_meta_dict:
                    raw_titles = viral_meta_dict["titles"]
                    viral_titles = [
                        t if isinstance(t, dict) else {"title": str(t), "style": "viral_hook"}
                        for t in raw_titles
                    ]

            # 4. Title selection: user choice or auto-selection
            chosen_title: str | None = sidecar_data.get("selected_viral_title")

            if not chosen_title:
                if auto_upload:
                    if viral_titles:
                        chosen_title = viral_titles[0]["title"]
                    else:
                        chosen_title = orig_title or "Stream Highlight"
                elif interactive:
                    chosen_title = self._select_title(
                        candidate=candidate,
                        viral_titles=viral_titles,
                        title_prompt_fn=title_prompt_fn,
                        approval_prompt_fn_provided=(prompt_fn is not None),
                    )
                else:
                    if viral_titles:
                        chosen_title = viral_titles[0]["title"]
                    else:
                        chosen_title = orig_title or "Stream Highlight"

            # Sanitize chosen title ensuring #Shorts and <= 100 chars
            chosen_title = sanitize_viral_title(
                chosen_title,
                allow_emoji=getattr(self.settings, "viral_title_emoji", True),
            )
            sidecar_data["selected_viral_title"] = chosen_title
            if viral_meta_dict:
                viral_meta_dict["selected_title"] = chosen_title
                sidecar_data["viral_metadata"] = viral_meta_dict

            # Persist updated sidecar to disk
            if target_sidecar_path:
                try:
                    target_sidecar_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(target_sidecar_path, "w", encoding="utf-8") as sfh:
                        json.dump(sidecar_data, sfh, indent=2)
                except Exception as exc:
                    log.warning("Could not persist updated sidecar to %s: %s", target_sidecar_path, exc)

            # Update manifest record with chosen viral title while preserving analytical title
            self.manifest.update_record(
                clip_id=cid,
                title=chosen_title,
                analytical_title=orig_title,
                selected_viral_title=chosen_title,
                viral_titles=[t["title"] for t in viral_titles] if viral_titles else None,
            )

            # 5. Interactive review if requested and not auto_upload
            if interactive and not auto_upload:
                decision = self._ask_user(candidate, chosen_title=chosen_title, prompt_fn=prompt_fn)
                if decision == "r":
                    log.info("Clip %s rejected by user.", cid)
                    self.manifest.update_record(clip_id=cid, state=STATE_REJECTED)
                    rejected_count += 1
                    continue
                if decision == "s":
                    log.info("Clip %s skipped.", cid)
                    skipped_count += 1
                    continue
                # Approved -> proceed to upload
                self.manifest.update_record(clip_id=cid, state=STATE_APPROVED)

            # 6. Perform upload
            log.info("Uploading Short '%s' (score=%d, id=%s)...", chosen_title, score, cid)
            try:
                upload_res = self.uploader.upload_video(
                    video_path=video_path,
                    sidecar_path=target_sidecar_path,
                    sidecar_data=sidecar_data,
                    privacy_status=privacy,
                    progress_callback=progress_callback,
                )
                yt_id = upload_res.get("video_id")
                yt_url = upload_res.get("url")

                updated = self.manifest.update_record(
                    clip_id=cid,
                    state=STATE_UPLOADED,
                    youtube_video_id=yt_id,
                    youtube_id=yt_id,
                    youtube_url=yt_url,
                    title=chosen_title,
                    selected_viral_title=chosen_title,
                    privacy_status=privacy,
                    error=None,
                )
                uploaded_count += 1
                results.append(updated)
            except QuotaExceededError as q_exc:
                log.warning("Upload quota limit reached during upload: %s", q_exc)
                break
            except Exception as exc:
                log.error("Failed to upload short %s: %s", cid, exc, exc_info=True)
                updated = self.manifest.update_record(
                    clip_id=cid,
                    state=STATE_FAILED,
                    error=str(exc),
                )
                failed_count += 1
                results.append(updated)

        quota_status = (
            self.uploader.quota_tracker.get_status()
            if self.uploader.quota_tracker
            else {}
        )

        return {
            "candidates": len(candidates),
            "uploaded": uploaded_count,
            "skipped": skipped_count,
            "rejected": rejected_count,
            "failed": failed_count,
            "quota_status": quota_status,
            "items": results,
        }

    def _select_title(
        self,
        candidate: dict[str, Any],
        viral_titles: list[dict[str, str]],
        title_prompt_fn: Callable[[dict[str, Any], list[dict[str, str]]], str] | None = None,
        approval_prompt_fn_provided: bool = False,
    ) -> str:
        """Prompt user to select from viral title options or enter a custom title."""
        if title_prompt_fn:
            choice = title_prompt_fn(candidate, viral_titles).strip()
            if choice == "1" and len(viral_titles) >= 1:
                return viral_titles[0]["title"]
            if choice == "2" and len(viral_titles) >= 2:
                return viral_titles[1]["title"]
            if choice == "3" and len(viral_titles) >= 3:
                return viral_titles[2]["title"]
            if choice:
                return choice
            return viral_titles[0]["title"] if viral_titles else candidate.get("title", "Stream Highlight")

        if not viral_titles:
            return candidate.get("title", "Stream Highlight")

        # If a programmatic approval prompt function was provided without a title prompt function,
        # or if stdin is non-interactive, default to the top-ranked title.
        import sys
        if approval_prompt_fn_provided or not sys.stdin.isatty():
            return viral_titles[0]["title"]

        print("\n" + "=" * 65)
        print(f"  Channel  : {candidate.get('channel')}")
        print(f"  Clip ID  : {candidate.get('clip_id')}")
        print(f"  Score    : {candidate.get('highlight_score')}/100")
        print(f"  Original : {candidate.get('title')}")
        print("-" * 65)
        print("  Viral Title Options (Ranked):")
        for idx, opt in enumerate(viral_titles[:3], 1):
            t_str = opt.get("title", "")
            style = opt.get("style", "viral_hook").replace("_", " ").title()
            impact = opt.get("predicted_impact", "")
            impact_str = f" ({impact})" if impact else ""
            print(f"    [{idx}] {t_str}")
            print(f"        Style: {style}{impact_str}")
        print("-" * 65)

        try:
            choice = input("  Select title [1-3] or type a custom title (default: 1): ").strip()
            if not choice or choice == "1":
                return viral_titles[0]["title"]
            if choice == "2" and len(viral_titles) >= 2:
                return viral_titles[1]["title"]
            if choice == "3" and len(viral_titles) >= 3:
                return viral_titles[2]["title"]
            return choice
        except (EOFError, KeyboardInterrupt):
            return viral_titles[0]["title"]

    def _ask_user(
        self,
        candidate: dict[str, Any],
        chosen_title: str | None = None,
        prompt_fn: Callable[[dict[str, Any]], str] | None = None,
    ) -> str:
        """Query user decision: 'a' (approve), 'r' (reject), 's' (skip)."""
        if prompt_fn:
            return prompt_fn(candidate).strip().lower()

        title_to_show = chosen_title or candidate.get("selected_viral_title") or candidate.get("title")
        print("\n" + "-" * 60)
        print(f"  Channel : {candidate.get('channel')}")
        print(f"  Clip ID : {candidate.get('clip_id')}")
        print(f"  Score   : {candidate.get('highlight_score')}/100")
        print(f"  Title   : {title_to_show}")
        print(f"  File    : {candidate.get('short_video_path')}")
        print("-" * 60)
        try:
            choice = input("  Upload this Short? [A]pprove / [R]eject / [S]kip (default: S): ").strip().lower()
            return choice if choice in ("a", "r", "s") else "s"
        except (EOFError, KeyboardInterrupt):
            return "s"

    @staticmethod
    def _extract_clip_id(stem: str) -> str:
        parts = stem.split("_")
        if len(parts) >= 3 and len(parts[2]) in (8, 32):
            return parts[2]
        return stem

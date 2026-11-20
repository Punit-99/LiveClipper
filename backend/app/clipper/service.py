"""ClipperService managing background jobs, persistence, and Shorts dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Callable

from app.api.events import ws_manager
from app.clipper.analyzer import detect_and_rank_moments
from app.clipper.downloader import (
    download_video,
    extract_youtube_id,
    fetch_video_info,
    parse_timestamp_seconds,
)
from app.clipper.extractor import extract_all_clips
from app.clipper.models import DownloadedVideo, ImportClip, ImportJob, VideoMetadata
from app.config import get_settings

log = logging.getLogger(__name__)


def probe_video_duration(video_path: Path) -> float:
    """Probe video file duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            return max(0.0, float(res.stdout.strip()))
    except Exception as exc:
        log.warning("Could not probe duration for %s: %s", video_path.name, exc)
    return 0.0


def generate_video_thumbnail(video_path: Path, thumb_path: Path, offset_sec: float = 1.0) -> bool:
    """Generate a JPEG thumbnail snapshot from a video file using ffmpeg."""
    try:
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{offset_sec:.2f}",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_path),
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=15)
        return res.returncode == 0 and thumb_path.is_file()
    except Exception as exc:
        log.warning("Could not generate thumbnail for %s: %s", video_path.name, exc)
        return False


class ClipperService:
    """Manages YouTube URL analysis, library storage, background jobs, and clip persistence."""

    def __init__(self, imports_dir: Path | str | None = None) -> None:
        settings = get_settings()
        raw_dir = imports_dir or getattr(settings, "imports_dir", Path(__file__).resolve().parents[2] / "imports")
        self.imports_dir = Path(raw_dir)
        self.imports_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.imports_dir / "manifest.json"
        self._jobs: dict[str, ImportJob] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._analysis_semaphore = threading.Semaphore(2)
        self._lock = threading.Lock()
        self._load_manifest()

    def _load_manifest(self) -> None:
        """Load past import jobs from disk."""
        if not self.manifest_path.is_file():
            return
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for j_data in data.get("jobs", []):
                clips = [ImportClip(**c) for c in j_data.get("clips", [])]
                meta_dict = j_data.get("metadata")
                meta = VideoMetadata(**meta_dict) if meta_dict else None
                job = ImportJob(
                    job_id=j_data["job_id"],
                    url=j_data["url"],
                    video_id=j_data.get("video_id", ""),
                    status=j_data.get("status", "completed"),
                    progress=j_data.get("progress", 100),
                    current_step=j_data.get("current_step", "Completed"),
                    error=j_data.get("error"),
                    metadata=meta,
                    clips=clips,
                    options=j_data.get("options", {}),
                    created_at=j_data.get("created_at", time.time()),
                    updated_at=j_data.get("updated_at", time.time()),
                )
                self._jobs[job.job_id] = job
            log.info("Loaded %d import jobs from %s", len(self._jobs), self.manifest_path)
        except Exception as exc:
            log.warning("Could not read import manifest: %s", exc)

    def _save_manifest(self) -> None:
        """Persist current jobs state to disk atomically."""
        with self._lock:
            try:
                jobs_data = [j.to_dict() for j in self._jobs.values()]
                tmp_path = self.manifest_path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump({"jobs": jobs_data, "updated_at": time.time()}, f, indent=2)
                tmp_path.replace(self.manifest_path)
            except Exception as exc:
                log.warning("Failed saving import manifest: %s", exc)

    def get_cancel_event(self, job_id: str) -> threading.Event:
        """Get or create cancellation event for a job."""
        with self._lock:
            if job_id not in self._cancel_events:
                self._cancel_events[job_id] = threading.Event()
            return self._cancel_events[job_id]

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Cancel a running download or analysis job."""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job '{job_id}' not found.")

        cancel_ev = self.get_cancel_event(job_id)
        cancel_ev.set()

        job.status = "cancelled"
        job.current_step = "Analysis stopped by user."
        job.updated_at = time.time()
        self._save_manifest()

        ws_manager.broadcast_sync("import_cancelled", {
            "job_id": job.job_id,
            "video_id": job.video_id,
            "status": "cancelled",
            "clips_count": len(job.clips),
            "message": "Job cancelled by user.",
        })
        log.info("Import job %s was cancelled by user.", job_id)
        return {
            "job_id": job_id,
            "status": "cancelled",
            "message": "Job cancelled successfully.",
            "clips_extracted": len(job.clips),
        }

    def cleanup_all_imports(self) -> dict[str, Any]:
        """Delete all imported clips, library source videos, and clear manifests."""
        with self._lock:
            total_freed_bytes = 0
            clips_deleted = len(self.list_clips())
            videos_deleted = 0

            for entry in list(self.imports_dir.iterdir()):
                if entry.name == "manifest.json":
                    continue
                try:
                    if entry.is_dir():
                        dir_size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                        total_freed_bytes += dir_size
                        shutil.rmtree(entry, ignore_errors=True)
                        videos_deleted += 1
                    elif entry.is_file():
                        total_freed_bytes += entry.stat().st_size
                        entry.unlink(missing_ok=True)
                except Exception as exc:
                    log.warning("Failed deleting %s during cleanup: %s", entry, exc)

            self._jobs.clear()
            self._cancel_events.clear()
            try:
                with open(self.manifest_path, "w", encoding="utf-8") as f:
                    json.dump({"jobs": [], "updated_at": time.time()}, f, indent=2)
            except Exception as exc:
                log.warning("Failed resetting manifest: %s", exc)

            freed_mb = round(total_freed_bytes / (1024 * 1024), 2)
            ws_manager.broadcast_sync("imports_cleaned", {
                "freed_mb": freed_mb,
                "clips_deleted": clips_deleted,
                "videos_deleted": videos_deleted,
            })
            log.info("Cleanup completed: freed %.2f MB, deleted %d clips, %d videos", freed_mb, clips_deleted, videos_deleted)
            return {
                "freed_mb": freed_mb,
                "clips_deleted": clips_deleted,
                "videos_deleted": videos_deleted,
                "message": f"Cleaned up {freed_mb} MB of imports.",
            }

    # ==========================================
    # Library Management (Two-Step Workflow)
    # ==========================================

    def list_library(self) -> list[DownloadedVideo]:
        """List all downloaded or uploaded videos available in the library."""
        items: list[DownloadedVideo] = []
        if not self.imports_dir.is_dir():
            return items

        for vdir in self.imports_dir.iterdir():
            if not vdir.is_dir():
                continue
            video_id = vdir.name
            src_files = list(vdir.glob("source.*"))
            video_file = None
            for sf in src_files:
                if sf.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi") and sf.stat().st_size > 0:
                    video_file = sf
                    break

            if not video_file:
                continue

            # Count extracted clips
            clips_dir = vdir / "clips"
            clips_count = len(list(clips_dir.glob("*.mp4"))) if clips_dir.is_dir() else 0

            # Read metadata.json if available
            meta_path = vdir / "metadata.json"
            meta: dict[str, Any] = {}
            if meta_path.is_file():
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                except Exception:
                    pass

            size_mb = round(video_file.stat().st_size / (1024 * 1024), 2)
            duration = float(meta.get("duration") or 0.0)
            if duration <= 0:
                duration = probe_video_duration(video_file)

            title = meta.get("title") or (video_id if not video_id.startswith("up_") else f"Uploaded Video {video_file.name}")
            channel = meta.get("channel") or ("YouTube" if not video_id.startswith("up_") else "Uploaded File")
            source_type = meta.get("source_type") or ("uploaded" if video_id.startswith("up_") else "youtube")
            thumb_url = meta.get("thumbnail_url") or f"/api/import/library/{video_id}/thumbnail"

            # Check status from active jobs
            status = "downloaded"
            for j in self._jobs.values():
                if j.video_id == video_id:
                    if j.status in ("pending", "analyzing", "extracting", "downloading"):
                        status = "analyzing"
                    elif j.status == "completed":
                        status = "completed"
                    elif j.status == "failed":
                        status = "failed"

            items.append(
                DownloadedVideo(
                    video_id=video_id,
                    title=title,
                    channel=channel,
                    duration=duration,
                    file_size_mb=size_mb,
                    source_type=source_type,
                    thumbnail_url=thumb_url,
                    source_video_path=str(video_file),
                    created_at=meta.get("created_at", video_file.stat().st_mtime),
                    status=status,
                    clips_count=clips_count,
                )
            )

        items.sort(key=lambda x: x.created_at, reverse=True)
        return items

    def get_library_item(self, video_id: str) -> DownloadedVideo | None:
        """Find a library video by video_id."""
        for item in self.list_library():
            if item.video_id == video_id:
                return item
        return None

    def delete_library_item(self, video_id: str) -> bool:
        """Delete a downloaded/uploaded video and all associated clips from the library."""
        vdir = self.imports_dir / video_id
        if not vdir.is_dir():
            return False

        try:
            shutil.rmtree(vdir, ignore_errors=True)
        except Exception as exc:
            log.warning("Could not delete library directory for %s: %s", video_id, exc)

        with self._lock:
            self._jobs = {jid: j for jid, j in self._jobs.items() if j.video_id != video_id}
            if video_id in self._cancel_events:
                del self._cancel_events[video_id]
        self._save_manifest()

        ws_manager.broadcast_sync("library_item_deleted", {"video_id": video_id})
        log.info("Deleted library video %s and its directory.", video_id)
        return True

    def download_video_to_library(
        self,
        url: str,
        quality: int = 1080,
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> DownloadedVideo:
        """Download YouTube video and save to the library without starting analysis."""
        vid = extract_youtube_id(url)
        if not vid:
            raise ValueError(f"Invalid YouTube URL: {url}")

        vdir = self.imports_dir / vid
        vdir.mkdir(parents=True, exist_ok=True)

        metadata = fetch_video_info(url)
        src_mp4, sub_path = download_video(
            url=url,
            output_dir=vdir,
            max_quality=quality,
            progress_cb=progress_cb,
        )

        # Generate thumbnail
        thumb_file = vdir / "thumbnail.jpg"
        if not thumb_file.is_file():
            generate_video_thumbnail(src_mp4, thumb_file, offset_sec=2.0)

        meta_dict = metadata.to_dict()
        meta_dict["created_at"] = time.time()
        meta_dict["source_type"] = "youtube"
        meta_dict["thumbnail_url"] = f"/api/import/library/{vid}/thumbnail"
        with open(vdir / "metadata.json", "w", encoding="utf-8") as mf:
            json.dump(meta_dict, mf, indent=2)

        size_mb = round(src_mp4.stat().st_size / (1024 * 1024), 2)
        lib_item = DownloadedVideo(
            video_id=vid,
            title=metadata.title,
            channel=metadata.channel,
            duration=metadata.duration,
            file_size_mb=size_mb,
            source_type="youtube",
            thumbnail_url=meta_dict["thumbnail_url"],
            source_video_path=str(src_mp4),
            created_at=time.time(),
            status="downloaded",
            clips_count=0,
        )
        ws_manager.broadcast_sync("library_updated", lib_item.to_dict())
        return lib_item

    def upload_video_file_stream(
        self,
        stream: BinaryIO,
        filename: str,
        progress_cb: Callable[[int], None] | None = None,
    ) -> DownloadedVideo:
        """Stream an uploaded video file chunk by chunk to library without buffering in memory."""
        clean_ext = Path(filename).suffix.lower() or ".mp4"
        vid = f"up_{uuid.uuid4().hex[:8]}"
        vdir = self.imports_dir / vid
        vdir.mkdir(parents=True, exist_ok=True)

        target_file = vdir / f"source{clean_ext}"
        chunk_size = 1024 * 1024  # 1MB chunks
        total_written = 0

        with open(target_file, "wb") as out_f:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                out_f.write(chunk)
                total_written += len(chunk)
                if progress_cb:
                    progress_cb(total_written)

        duration = probe_video_duration(target_file)
        thumb_file = vdir / "thumbnail.jpg"
        generate_video_thumbnail(target_file, thumb_file, offset_sec=min(2.0, max(0.5, duration / 5.0)))

        size_mb = round(total_written / (1024 * 1024), 2)
        base_title = Path(filename).stem.replace("_", " ").title()
        meta_dict = {
            "url": "",
            "video_id": vid,
            "title": base_title,
            "channel": "Uploaded Video",
            "duration": duration,
            "source_type": "uploaded",
            "created_at": time.time(),
            "thumbnail_url": f"/api/import/library/{vid}/thumbnail",
            "chapters": [],
            "heatmap": [],
        }
        with open(vdir / "metadata.json", "w", encoding="utf-8") as mf:
            json.dump(meta_dict, mf, indent=2)

        lib_item = DownloadedVideo(
            video_id=vid,
            title=base_title,
            channel="Uploaded Video",
            duration=duration,
            file_size_mb=size_mb,
            source_type="uploaded",
            thumbnail_url=meta_dict["thumbnail_url"],
            source_video_path=str(target_file),
            created_at=time.time(),
            status="downloaded",
            clips_count=0,
        )
        ws_manager.broadcast_sync("library_updated", lib_item.to_dict())
        log.info("Saved uploaded video %s (%.2f MB) to library.", vid, size_mb)
        return lib_item

    # ==========================================
    # Job Submission & Analysis Pipeline
    # ==========================================

    def submit_job(self, url: str, options: dict[str, Any] | None = None) -> ImportJob:
        """Create and launch a full download & analysis job in background (one-shot)."""
        vid = extract_youtube_id(url)
        if not vid:
            raise ValueError(f"Invalid YouTube URL: {url}")

        opts = options or {}
        job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        job = ImportJob(
            job_id=job_id,
            url=url,
            video_id=vid,
            status="pending",
            progress=0,
            current_step="Queued for analysis",
            options=opts,
        )

        with self._lock:
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
        self._save_manifest()

        ws_manager.broadcast_sync("import_started", {
            "job_id": job.job_id,
            "url": job.url,
            "video_id": job.video_id,
        })

        thread = threading.Thread(
            target=self._run_job_worker,
            args=(job,),
            daemon=True,
            name=f"import-worker-{job_id}",
        )
        thread.start()
        return job

    def analyze_library_video(self, video_id: str, options: dict[str, Any] | None = None) -> ImportJob:
        """Launch analysis on a video already stored in the library."""
        vdir = self.imports_dir / video_id
        if not vdir.is_dir():
            raise FileNotFoundError(f"Video '{video_id}' not found in library.")

        src_files = list(vdir.glob("source.*"))
        video_file = None
        for sf in src_files:
            if sf.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi") and sf.stat().st_size > 0:
                video_file = sf
                break

        if not video_file:
            raise FileNotFoundError(f"Source video file missing for '{video_id}'.")

        opts = options or {}
        job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        job = ImportJob(
            job_id=job_id,
            url="",
            video_id=video_id,
            status="pending",
            progress=0,
            current_step="Queued for analysis",
            options=opts,
        )

        with self._lock:
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
        self._save_manifest()

        ws_manager.broadcast_sync("import_started", {
            "job_id": job.job_id,
            "url": "",
            "video_id": job.video_id,
        })

        thread = threading.Thread(
            target=self._run_library_analysis_worker,
            args=(job, video_file),
            daemon=True,
            name=f"import-analyze-{job_id}",
        )
        thread.start()
        return job

    def _run_library_analysis_worker(self, job: ImportJob, video_file: Path) -> None:
        """Worker thread executing analysis on an existing library video (concurrency up to 2)."""
        cancel_ev = self.get_cancel_event(job.job_id)
        with self._analysis_semaphore:
            if cancel_ev.is_set():
                job.status = "cancelled"
                job.current_step = "Cancelled before start."
                self._save_manifest()
                return
            self._execute_analysis_pipeline(job=job, source_mp4=video_file, is_download_needed=False)

    def _run_job_worker(self, job: ImportJob) -> None:
        """Worker thread executing download + analysis (concurrency up to 2)."""
        cancel_ev = self.get_cancel_event(job.job_id)
        with self._analysis_semaphore:
            if cancel_ev.is_set():
                job.status = "cancelled"
                job.current_step = "Cancelled before start."
                self._save_manifest()
                return
            self._execute_analysis_pipeline(job=job, source_mp4=None, is_download_needed=True)

    def _execute_analysis_pipeline(
        self,
        job: ImportJob,
        source_mp4: Path | None,
        is_download_needed: bool,
    ) -> None:
        """Unified analysis pipeline across 7 stages with cancellation and heartbeat."""
        settings = get_settings()
        video_work_dir = self.imports_dir / job.video_id
        clips_output_dir = video_work_dir / "clips"
        video_work_dir.mkdir(parents=True, exist_ok=True)
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        cancel_ev = self.get_cancel_event(job.job_id)
        heartbeat_stop = threading.Event()

        def _update(status: str, progress: int, step: str, stage_num: int = 1) -> None:
            job.status = status
            job.progress = progress
            job.current_step = step
            job.updated_at = time.time()
            self._save_manifest()
            ws_manager.broadcast_sync("import_progress", {
                "job_id": job.job_id,
                "video_id": job.video_id,
                "status": status,
                "progress": progress,
                "current_step": step,
                "stage": stage_num,
                "total_stages": 7,
            })

        def _heartbeat_loop() -> None:
            start_t = time.time()
            while not heartbeat_stop.is_set():
                heartbeat_stop.wait(10.0)
                if heartbeat_stop.is_set():
                    break
                elapsed = int(time.time() - start_t)
                ws_manager.broadcast_sync("import_heartbeat", {
                    "job_id": job.job_id,
                    "video_id": job.video_id,
                    "status": job.status,
                    "current_step": job.current_step,
                    "progress": job.progress,
                    "elapsed_seconds": elapsed,
                })

        hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True, name=f"hb-{job.job_id}")
        hb_thread.start()

        try:
            # Metadata resolution
            metadata = job.metadata
            meta_json = video_work_dir / "metadata.json"
            if not metadata and meta_json.is_file():
                try:
                    with open(meta_json, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                        metadata = VideoMetadata(**{k: v for k, v in m_data.items() if k in VideoMetadata.__dataclass_fields__})
                        job.metadata = metadata
                except Exception:
                    pass

            # Stage 1: Download / Verify media file (0% - 15%)
            if is_download_needed and not source_mp4:
                _update("downloading", 5, "Stage 1/7: Fetching video metadata...", stage_num=1)
                if not metadata:
                    metadata = fetch_video_info(job.url)
                    job.metadata = metadata

                _update("downloading", 12, "Stage 1/7: Downloading source video...", stage_num=1)

                def _dl_cb(pct: float, msg: str) -> None:
                    mapped = int(12 + (pct * 0.18))
                    _update("downloading", mapped, f"Stage 1/7: {msg}", stage_num=1)

                quality = int(job.options.get("quality", getattr(settings, "import_download_quality", 1080)))
                source_mp4, sub_path = download_video(
                    url=job.url,
                    output_dir=video_work_dir,
                    max_quality=quality,
                    progress_cb=_dl_cb,
                )
            else:
                _update("analyzing", 15, "Stage 1/7: Video media verified.", stage_num=1)
                sub_candidates = list(video_work_dir.glob("source*.vtt")) + list(video_work_dir.glob("source*.srt"))
                sub_path = sub_candidates[0] if sub_candidates else None
                if not metadata:
                    dur = probe_video_duration(source_mp4)
                    metadata = VideoMetadata(
                        url="",
                        video_id=job.video_id,
                        title=job.video_id,
                        channel="Library Video",
                        duration=dur,
                    )
                    job.metadata = metadata

            if cancel_ev.is_set():
                _update("cancelled", job.progress, "Cancelled by user.", stage_num=1)
                return

            # Stage 2: Extract Transcript & Speech (15% - 30%)
            _update("analyzing", 22, "Stage 2/7: Extracting speech transcript & captions...", stage_num=2)

            # Stage 3: Gemini Highlight Intelligence (30% - 50%)
            _update("analyzing", 35, "Stage 3/7: Analyzing transcript context with Gemini AI...", stage_num=3)

            # Stage 4: Audio Waveform & Energy Spikes (50% - 65%)
            _update("analyzing", 52, "Stage 4/7: Analyzing audio waveform and volume spikes...", stage_num=4)

            # Stage 5: Visual Scene & Cut Detection (65% - 75%)
            _update("analyzing", 68, "Stage 5/7: Detecting visual scene transitions & cuts...", stage_num=5)

            # Stage 6: Multi-Signal Ranking & Scoring (75% - 85%)
            _update("analyzing", 78, "Stage 6/7: Scoring and ranking candidate highlight moments...", stage_num=6)

            max_clips = int(job.options.get("max_clips", getattr(settings, "import_max_clips", 5)))
            target_dur = float(job.options.get("clip_duration", getattr(settings, "import_clip_duration", 55.0)))
            skip_intro = float(job.options.get("skip_intro", getattr(settings, "import_skip_intro", 0.0)))
            skip_outro = float(job.options.get("skip_outro", getattr(settings, "import_skip_outro", 0.0)))

            start_t = parse_timestamp_seconds(job.options.get("start_time"))
            end_t = parse_timestamp_seconds(job.options.get("end_time"))

            moments = detect_and_rank_moments(
                media_path=source_mp4,
                metadata=metadata,
                subtitle_path=sub_path,
                max_clips=max_clips,
                target_clip_duration=target_dur,
                skip_intro_sec=skip_intro,
                skip_outro_sec=skip_outro,
                start_time=start_t,
                end_time=end_t,
                progress_cb=lambda msg: _update("analyzing", 80, f"Stage 6/7: {msg}", stage_num=6),
            )

            # Broadcast individual moments found
            for m in moments:
                ws_manager.broadcast_sync("import_moment_found", {
                    "job_id": job.job_id,
                    "video_id": job.video_id,
                    "moment": m.to_dict(),
                })

            if cancel_ev.is_set():
                _update("cancelled", job.progress, "Cancelled by user.", stage_num=6)
                return

            # Stage 7: Extract Clips & Thumbnails (85% - 100%)
            _update("extracting", 85, f"Stage 7/7: Cutting {len(moments)} highlight clips with FFmpeg...", stage_num=7)

            def _extract_cb(idx: int, total: int, clip_obj: ImportClip | None = None) -> None:
                mapped = int(85 + ((idx / total) * 14))
                _update("extracting", mapped, f"Stage 7/7: Extracted clip {idx}/{total}...", stage_num=7)
                if clip_obj:
                    ws_manager.broadcast_sync("import_clip_extracted", {
                        "job_id": job.job_id,
                        "video_id": job.video_id,
                        "clip": clip_obj.to_dict(),
                        "index": idx,
                        "total": total,
                    })

            clips = extract_all_clips(
                source_video_path=source_mp4,
                moments=moments,
                metadata=metadata,
                output_dir=clips_output_dir,
                progress_cb=_extract_cb,
                cancel_check=lambda: cancel_ev.is_set(),
            )

            job.clips = clips
            if cancel_ev.is_set():
                _update("cancelled", job.progress, f"Analysis stopped. Preserved {len(clips)} extracted clips.", stage_num=7)
            else:
                _update("completed", 100, f"Complete! Extracted {len(clips)} highlight clips.", stage_num=7)

            ws_manager.broadcast_sync("import_complete", {
                "job_id": job.job_id,
                "video_id": job.video_id,
                "clips_count": len(clips),
                "clips": [c.to_dict() for c in clips],
            })
            log.info("Import job %s finished: %d clips extracted.", job.job_id, len(clips))

        except Exception as exc:
            log.exception("Import job %s error: %s", job.job_id, exc)
            job.status = "failed"
            job.error = str(exc)
            job.current_step = f"Failed: {exc}"
            job.updated_at = time.time()
            self._save_manifest()
            ws_manager.broadcast_sync("import_failed", {
                "job_id": job.job_id,
                "video_id": job.video_id,
                "error": str(exc),
            })
        finally:
            heartbeat_stop.set()

    def get_job(self, job_id: str) -> ImportJob | None:
        """Retrieve job by id."""
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[ImportJob]:
        """List all import jobs sorted by creation date descending."""
        jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def list_clips(self) -> list[ImportClip]:
        """List all extracted imported clips across all jobs."""
        all_clips: list[ImportClip] = []
        for job in self.list_jobs():
            all_clips.extend(job.clips)
        return all_clips

    def get_clip(self, clip_id: str) -> ImportClip | None:
        """Find a specific clip by its clip_id."""
        for job in self._jobs.values():
            for clip in job.clips:
                if clip.clip_id == clip_id:
                    return clip
        return None

    def delete_clip(self, clip_id: str) -> bool:
        """Delete an imported clip and its media files."""
        target_clip = self.get_clip(clip_id)
        if not target_clip:
            return False

        for p in (Path(target_clip.file_path), Path(target_clip.thumbnail_path)):
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        sidecar = Path(target_clip.file_path).with_suffix(".json")
        if sidecar.is_file():
            try:
                sidecar.unlink()
            except OSError:
                pass

        for job in self._jobs.values():
            job.clips = [c for c in job.clips if c.clip_id != clip_id]
        self._save_manifest()

        ws_manager.broadcast_sync("imported_clip_deleted", {"clip_id": clip_id})
        return True


_clipper_service_instance: ClipperService | None = None
_service_lock = threading.Lock()


def get_clipper_service() -> ClipperService:
    """Return the global ClipperService singleton."""
    global _clipper_service_instance
    with _service_lock:
        if _clipper_service_instance is None:
            _clipper_service_instance = ClipperService()
        return _clipper_service_instance

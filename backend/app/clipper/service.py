"""ClipperService managing background jobs, persistence, and Shorts dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.api.events import ws_manager
from app.clipper.analyzer import detect_and_rank_moments
from app.clipper.downloader import download_video, extract_youtube_id, fetch_video_info
from app.clipper.extractor import extract_all_clips
from app.clipper.models import ImportClip, ImportJob, VideoMetadata
from app.config import get_settings

log = logging.getLogger(__name__)


class ClipperService:
    """Manages YouTube URL analysis, background job lifecycle, and clip persistence."""

    def __init__(self, imports_dir: Path | None = None) -> None:
        settings = get_settings()
        self.imports_dir = imports_dir or getattr(settings, "imports_dir", Path(__file__).resolve().parents[2] / "imports")
        self.imports_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.imports_dir / "manifest.json"
        self._jobs: dict[str, ImportJob] = {}
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

    def submit_job(self, url: str, options: dict[str, Any] | None = None) -> ImportJob:
        """Create and launch an import analysis job in a background thread."""
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
        self._save_manifest()

        # Broadcast job submission
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

    def _run_job_worker(self, job: ImportJob) -> None:
        """Worker thread executing the full import pipeline."""
        settings = get_settings()
        video_work_dir = self.imports_dir / job.video_id
        clips_output_dir = video_work_dir / "clips"
        video_work_dir.mkdir(parents=True, exist_ok=True)
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        def _update(status: str, progress: int, step: str) -> None:
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
            })

        try:
            # Step 1: Metadata fetch
            _update("downloading", 5, "Fetching video metadata...")
            metadata = fetch_video_info(job.url)
            job.metadata = metadata

            # Step 2: Download video & captions
            _update("downloading", 15, "Downloading video and subtitles...")

            def _dl_cb(pct: float, msg: str) -> None:
                mapped = int(15 + (pct * 0.35))  # map 0-100% to 15-50%
                _update("downloading", mapped, msg)

            quality = int(job.options.get("quality", getattr(settings, "import_download_quality", 1080)))
            source_mp4, sub_path = download_video(
                url=job.url,
                output_dir=video_work_dir,
                max_quality=quality,
                progress_cb=_dl_cb,
            )

            # Step 3: Multi-signal moment detection
            _update("analyzing", 55, "Detecting highlight moments across 4 signals...")

            def _analysis_cb(msg: str) -> None:
                _update("analyzing", 65, msg)

            max_clips = int(job.options.get("max_clips", getattr(settings, "import_max_clips", 5)))
            target_dur = float(job.options.get("clip_duration", getattr(settings, "import_clip_duration", 55.0)))
            skip_intro = float(job.options.get("skip_intro", getattr(settings, "import_skip_intro", 0.0)))
            skip_outro = float(job.options.get("skip_outro", getattr(settings, "import_skip_outro", 0.0)))

            moments = detect_and_rank_moments(
                media_path=source_mp4,
                metadata=metadata,
                subtitle_path=sub_path,
                max_clips=max_clips,
                target_clip_duration=target_dur,
                skip_intro_sec=skip_intro,
                skip_outro_sec=skip_outro,
                progress_cb=_analysis_cb,
            )

            # Step 4: Clip extraction
            _update("extracting", 80, f"Extracting {len(moments)} highlight clips...")

            def _extract_cb(idx: int, total: int) -> None:
                mapped = int(80 + ((idx / total) * 18))
                _update("extracting", mapped, f"Extracting clip {idx}/{total}...")

            clips = extract_all_clips(
                source_video_path=source_mp4,
                moments=moments,
                metadata=metadata,
                output_dir=clips_output_dir,
                progress_cb=_extract_cb,
            )

            # Step 5: Completion
            job.clips = clips
            _update("completed", 100, f"Done! {len(clips)} clips extracted successfully.")

            ws_manager.broadcast_sync("import_complete", {
                "job_id": job.job_id,
                "video_id": job.video_id,
                "clips_count": len(clips),
                "clips": [c.to_dict() for c in clips],
            })
            log.info("Import job %s completed successfully: %d clips extracted", job.job_id, len(clips))

        except Exception as exc:
            log.exception("Import job %s failed: %s", job.job_id, exc)
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

        # Remove video and thumbnail files
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

        # Update in-memory job
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

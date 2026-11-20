"""Queue management for concurrent Shorts processing and YouTube uploads."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.api.events import ws_manager
from app.shorts.youtube_uploader import DailyQuotaTracker, QuotaExceededError

log = logging.getLogger("app.shorts.queue_manager")


@dataclass
class QueueItem:
    """Represents a queued or active task in a processing or upload queue."""

    clip_id: str
    title: str
    status: str  # "queued", "processing", "uploading", "completed", "failed", "cancelled"
    added_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    position: int | None = None
    stage: str = ""
    percentage: int = 0
    bytes_sent: int = 0
    total_bytes: int = 0
    error: str | None = None
    video_id: str | None = None
    url: str | None = None
    short_path: str | None = None
    is_reupload: bool = False
    worker_fn: Callable[[], Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "title": self.title,
            "status": self.status,
            "added_at": self.added_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "position": self.position,
            "stage": self.stage,
            "percentage": self.percentage,
            "bytes_sent": self.bytes_sent,
            "total_bytes": self.total_bytes,
            "error": self.error,
            "video_id": self.video_id,
            "url": self.url,
            "short_path": self.short_path,
            "is_reupload": self.is_reupload,
        }


class ProcessingQueueManager:
    """Thread-safe queue manager for concurrent clip processing to vertical shorts."""

    def __init__(self, max_concurrent: int = 2) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self._lock = threading.Lock()
        self._active: dict[str, QueueItem] = {}
        self._waiting: list[QueueItem] = []
        self._history: dict[str, QueueItem] = {}

    def get_status(self) -> dict[str, Any]:
        """Return the current queue status including active and waiting tasks."""
        with self._lock:
            return {
                "max_concurrent": self.max_concurrent,
                "active_count": len(self._active),
                "waiting_count": len(self._waiting),
                "active": [it.to_dict() for it in self._active.values()],
                "waiting": [it.to_dict() for it in self._waiting],
            }

    def get_item(self, clip_id: str) -> QueueItem | None:
        """Find a queue item by clip_id across active, waiting, or history."""
        with self._lock:
            if clip_id in self._active:
                return self._active[clip_id]
            for it in self._waiting:
                if it.clip_id == clip_id:
                    return it
            return self._history.get(clip_id)

    def is_busy(self, clip_id: str) -> tuple[bool, str, int | None]:
        """Check if a clip is currently processing or queued.

        Returns:
            (is_busy, status, position)
        """
        with self._lock:
            if clip_id in self._active:
                return True, "processing", None
            for it in self._waiting:
                if it.clip_id == clip_id:
                    return True, "queued", it.position
            return False, "", None

    def enqueue(
        self,
        clip_id: str,
        title: str,
        worker_fn: Callable[[], Any],
    ) -> tuple[QueueItem, bool]:
        """Enqueue a clip for processing.

        Returns:
            (item, is_active): item object and boolean indicating if task is active or queued.
        """
        with self._lock:
            if clip_id in self._active:
                return self._active[clip_id], True
            for it in self._waiting:
                if it.clip_id == clip_id:
                    return it, False

            if len(self._active) < self.max_concurrent:
                item = QueueItem(
                    clip_id=clip_id,
                    title=title,
                    status="processing",
                    started_at=time.time(),
                    worker_fn=worker_fn,
                )
                self._active[clip_id] = item
                is_active = True
            else:
                pos = len(self._waiting) + 1
                item = QueueItem(
                    clip_id=clip_id,
                    title=title,
                    status="queued",
                    position=pos,
                    worker_fn=worker_fn,
                )
                self._waiting.append(item)
                is_active = False

        if not is_active:
            ws_manager.broadcast_sync("processing_queued", {
                "clip_id": clip_id,
                "title": title,
                "position": item.position,
            })
        self._broadcast_queue_updated()
        return item, is_active

    def cancel(self, clip_id: str) -> tuple[bool, str]:
        """Cancel a queued processing task if not yet started."""
        with self._lock:
            if clip_id in self._active:
                return False, "Cannot cancel task that is already processing"

            found_idx = -1
            for idx, it in enumerate(self._waiting):
                if it.clip_id == clip_id:
                    found_idx = idx
                    break

            if found_idx == -1:
                return False, f"Task '{clip_id}' not found in waiting queue"

            item = self._waiting.pop(found_idx)
            item.status = "cancelled"
            item.finished_at = time.time()
            self._history[clip_id] = item

            # Re-index remaining waiting items
            for i, remaining in enumerate(self._waiting):
                remaining.position = i + 1

        ws_manager.broadcast_sync("processing_cancelled", {"clip_id": clip_id})
        self._broadcast_queue_updated()
        return True, "Cancelled"

    def report_progress(self, clip_id: str, stage: str, percentage: int) -> None:
        """Report processing progress for an active task."""
        with self._lock:
            item = self._active.get(clip_id)
            if item:
                item.stage = stage
                item.percentage = percentage

        ws_manager.broadcast_sync("processing_progress", {
            "clip_id": clip_id,
            "stage": stage,
            "percentage": percentage,
        })

    def run_item(self, clip_id: str) -> Any:
        """Execute the worker function for an active processing item."""
        with self._lock:
            item = self._active.get(clip_id)

        if not item or not item.worker_fn:
            return None

        ws_manager.broadcast_sync("processing_started", {
            "clip_id": clip_id,
            "title": item.title,
        })
        ws_manager.broadcast_sync("short_processing_started", {
            "clip_id": clip_id,
            "title": item.title,
        })

        result = None
        error: Exception | None = None
        try:
            result = item.worker_fn()
        except Exception as exc:
            error = exc
            log.error("Error executing processing worker for %s: %s", clip_id, exc, exc_info=True)

        with self._lock:
            if clip_id in self._active:
                finished_item = self._active.pop(clip_id)
                finished_item.finished_at = time.time()
                if error is None:
                    finished_item.status = "completed"
                    finished_item.percentage = 100
                    if isinstance(result, dict):
                        items = result.get("items", [])
                        if items and isinstance(items[0], dict):
                            finished_item.short_path = items[0].get("short_video_path")
                else:
                    finished_item.status = "failed"
                    finished_item.error = str(error)
                self._history[clip_id] = finished_item

        if error is None:
            ws_manager.broadcast_sync("processing_complete", {
                "clip_id": clip_id,
                "short_path": getattr(finished_item, "short_path", None),
                "title": finished_item.title,
            })
            ws_manager.broadcast_sync("short_processing_complete", {
                "clip_id": clip_id,
                "summary": result if isinstance(result, dict) else {},
            })
        else:
            ws_manager.broadcast_sync("processing_failed", {
                "clip_id": clip_id,
                "error": str(error),
                "title": finished_item.title,
            })

        self._advance_next()
        self._broadcast_queue_updated()
        return result

    def _advance_next(self) -> None:
        """Promote next waiting item to active and launch worker thread."""
        next_to_run: list[QueueItem] = []
        with self._lock:
            while len(self._active) < self.max_concurrent and self._waiting:
                next_item = self._waiting.pop(0)
                next_item.status = "processing"
                next_item.position = None
                next_item.started_at = time.time()
                self._active[next_item.clip_id] = next_item
                next_to_run.append(next_item)

            for i, remaining in enumerate(self._waiting):
                remaining.position = i + 1

        for item in next_to_run:
            threading.Thread(
                target=self.run_item,
                args=(item.clip_id,),
                daemon=True,
            ).start()

    def _broadcast_queue_updated(self) -> None:
        ws_manager.broadcast_sync("processing_queue_updated", self.get_status())


class UploadQueueManager:
    """Thread-safe queue manager for concurrent YouTube Shorts uploads with global quota protection."""

    def __init__(
        self,
        max_concurrent: int = 3,
        quota_tracker: DailyQuotaTracker | None = None,
    ) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.quota_tracker = quota_tracker
        self._lock = threading.Lock()
        self._active: dict[str, QueueItem] = {}
        self._waiting: list[QueueItem] = []
        self._history: dict[str, QueueItem] = {}

    def get_status(self) -> dict[str, Any]:
        """Return the current upload queue status."""
        with self._lock:
            return {
                "max_concurrent": self.max_concurrent,
                "active_count": len(self._active),
                "waiting_count": len(self._waiting),
                "active": [it.to_dict() for it in self._active.values()],
                "waiting": [it.to_dict() for it in self._waiting],
            }

    def get_item(self, clip_id: str) -> QueueItem | None:
        """Find an upload item by clip_id."""
        with self._lock:
            if clip_id in self._active:
                return self._active[clip_id]
            for it in self._waiting:
                if it.clip_id == clip_id:
                    return it
            return self._history.get(clip_id)

    def is_busy(self, clip_id: str) -> tuple[bool, str, int | None]:
        """Check if a short is currently uploading or queued for upload.

        Returns:
            (is_busy, status, position)
        """
        with self._lock:
            if clip_id in self._active:
                return True, "uploading", None
            for it in self._waiting:
                if it.clip_id == clip_id:
                    return True, "queued", it.position
            return False, "", None

    def enqueue(
        self,
        clip_id: str,
        title: str,
        worker_fn: Callable[[], Any],
        is_reupload: bool = False,
    ) -> tuple[QueueItem, bool]:
        """Enqueue a Short for YouTube upload after quota validation."""
        if self.quota_tracker:
            allowed, reason = self.quota_tracker.can_upload()
            if not allowed:
                raise QuotaExceededError(reason)

        with self._lock:
            if clip_id in self._active:
                return self._active[clip_id], True
            for it in self._waiting:
                if it.clip_id == clip_id:
                    return it, False

            if len(self._active) < self.max_concurrent:
                item = QueueItem(
                    clip_id=clip_id,
                    title=title,
                    status="uploading",
                    started_at=time.time(),
                    is_reupload=is_reupload,
                    worker_fn=worker_fn,
                )
                self._active[clip_id] = item
                is_active = True
            else:
                pos = len(self._waiting) + 1
                item = QueueItem(
                    clip_id=clip_id,
                    title=title,
                    status="queued",
                    position=pos,
                    is_reupload=is_reupload,
                    worker_fn=worker_fn,
                )
                self._waiting.append(item)
                is_active = False

        if not is_active:
            ws_manager.broadcast_sync("upload_queued", {
                "clip_id": clip_id,
                "title": title,
                "position": item.position,
            })
        self._broadcast_queue_updated()
        return item, is_active

    def cancel(self, clip_id: str) -> tuple[bool, str]:
        """Cancel a queued upload task if not yet uploading."""
        with self._lock:
            if clip_id in self._active:
                return False, "Cannot cancel upload that is already in progress"

            found_idx = -1
            for idx, it in enumerate(self._waiting):
                if it.clip_id == clip_id:
                    found_idx = idx
                    break

            if found_idx == -1:
                return False, f"Upload '{clip_id}' not found in waiting queue"

            item = self._waiting.pop(found_idx)
            item.status = "cancelled"
            item.finished_at = time.time()
            self._history[clip_id] = item

            for i, remaining in enumerate(self._waiting):
                remaining.position = i + 1

        ws_manager.broadcast_sync("upload_cancelled", {"clip_id": clip_id})
        self._broadcast_queue_updated()
        return True, "Cancelled"

    def report_progress(self, clip_id: str, bytes_sent: int, total_bytes: int) -> None:
        """Report upload progress in percentage and bytes."""
        pct = int((bytes_sent / total_bytes) * 100) if total_bytes > 0 else 0
        with self._lock:
            item = self._active.get(clip_id)
            if item:
                item.percentage = pct
                item.bytes_sent = bytes_sent
                item.total_bytes = total_bytes

        ws_manager.broadcast_sync("upload_progress", {
            "clip_id": clip_id,
            "percentage": pct,
            "bytes_sent": bytes_sent,
            "total_bytes": total_bytes,
        })

    def run_item(self, clip_id: str) -> Any:
        """Execute the upload worker for an active upload item."""
        with self._lock:
            item = self._active.get(clip_id)

        if not item or not item.worker_fn:
            return None

        # Re-check quota before active network transmission
        if self.quota_tracker:
            allowed, reason = self.quota_tracker.can_upload()
            if not allowed:
                log.warning("Cancelling upload for %s: quota limit reached (%s)", clip_id, reason)
                with self._lock:
                    if clip_id in self._active:
                        item = self._active.pop(clip_id)
                        item.status = "failed"
                        item.error = reason
                        item.finished_at = time.time()
                        self._history[clip_id] = item
                ws_manager.broadcast_sync("upload_failed", {
                    "clip_id": clip_id,
                    "error": reason,
                    "title": item.title,
                })
                self._broadcast_queue_updated()
                return None

        ws_manager.broadcast_sync("upload_started", {
            "clip_id": clip_id,
            "title": item.title,
            "is_reupload": item.is_reupload,
        })

        result = None
        error: Exception | None = None
        try:
            result = item.worker_fn()
        except Exception as exc:
            error = exc
            log.error("Error executing upload worker for %s: %s", clip_id, exc, exc_info=True)

        with self._lock:
            if clip_id in self._active:
                finished_item = self._active.pop(clip_id)
                finished_item.finished_at = time.time()
                if error is None:
                    finished_item.status = "completed"
                    finished_item.percentage = 100
                    if isinstance(result, dict):
                        items = result.get("items", [])
                        if items and isinstance(items[0], dict):
                            finished_item.video_id = items[0].get("youtube_id")
                            finished_item.url = items[0].get("youtube_url")
                        elif "youtube_id" in result:
                            finished_item.video_id = result.get("youtube_id")
                            finished_item.url = result.get("youtube_url")
                else:
                    finished_item.status = "failed"
                    finished_item.error = str(error)
                self._history[clip_id] = finished_item

        if error is None:
            vid = getattr(finished_item, "video_id", None)
            vurl = getattr(finished_item, "url", None)
            ws_manager.broadcast_sync("upload_complete", {
                "clip_id": clip_id,
                "video_id": vid,
                "youtube_id": vid,
                "url": vurl,
                "youtube_url": vurl,
                "title": finished_item.title,
                "is_reupload": finished_item.is_reupload,
            })
        else:
            ws_manager.broadcast_sync("upload_failed", {
                "clip_id": clip_id,
                "error": str(error),
                "title": finished_item.title,
            })

        self._advance_next()
        self._broadcast_queue_updated()
        return result

    def _advance_next(self) -> None:
        """Promote next waiting upload to active and launch worker thread."""
        next_to_run: list[QueueItem] = []
        with self._lock:
            while len(self._active) < self.max_concurrent and self._waiting:
                next_item = self._waiting.pop(0)
                next_item.status = "uploading"
                next_item.position = None
                next_item.started_at = time.time()
                self._active[next_item.clip_id] = next_item
                next_to_run.append(next_item)

            for i, remaining in enumerate(self._waiting):
                remaining.position = i + 1

        for item in next_to_run:
            threading.Thread(
                target=self.run_item,
                args=(item.clip_id,),
                daemon=True,
            ).start()

    def _broadcast_queue_updated(self) -> None:
        ws_manager.broadcast_sync("upload_queue_updated", self.get_status())


_processing_queue_instance: ProcessingQueueManager | None = None
_upload_queue_instance: UploadQueueManager | None = None
_instance_lock = threading.Lock()


def get_processing_queue(max_concurrent: int | None = None) -> ProcessingQueueManager:
    """Return the global ProcessingQueueManager singleton."""
    global _processing_queue_instance
    with _instance_lock:
        if _processing_queue_instance is None:
            from app.config import load_settings
            cfg = load_settings()
            conc = max_concurrent or cfg.max_concurrent_processing
            _processing_queue_instance = ProcessingQueueManager(max_concurrent=conc)
        elif max_concurrent is not None:
            _processing_queue_instance.max_concurrent = max_concurrent
        return _processing_queue_instance


def get_upload_queue(
    max_concurrent: int | None = None,
    quota_tracker: DailyQuotaTracker | None = None,
) -> UploadQueueManager:
    """Return the global UploadQueueManager singleton."""
    global _upload_queue_instance
    with _instance_lock:
        if _upload_queue_instance is None:
            from app.config import load_settings
            from app.api.state import get_quota_tracker
            cfg = load_settings()
            conc = max_concurrent or cfg.max_concurrent_uploads
            tracker = quota_tracker or get_quota_tracker(settings=cfg)
            _upload_queue_instance = UploadQueueManager(max_concurrent=conc, quota_tracker=tracker)
        else:
            if max_concurrent is not None:
                _upload_queue_instance.max_concurrent = max_concurrent
            if quota_tracker is not None:
                _upload_queue_instance.quota_tracker = quota_tracker
        return _upload_queue_instance

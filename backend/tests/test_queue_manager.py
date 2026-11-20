"""Unit tests for concurrent processing queue and upload queue managers."""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.api.events import ws_manager
from app.shorts.queue_manager import (
    ProcessingQueueManager,
    QueueItem,
    UploadQueueManager,
    get_processing_queue,
    get_upload_queue,
)
from app.shorts.youtube_uploader import DailyQuotaTracker, QuotaExceededError
from fastapi import FastAPI
from starlette.testclient import TestClient


class TestQueueManagers(TestCase):
    """Test suite for ProcessingQueueManager and UploadQueueManager."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.quota_path = Path(self.tmp_dir.name) / "test_quota.json"
        self.quota_tracker = DailyQuotaTracker(
            quota_path=self.quota_path,
            daily_limit=3,
            max_units=10000,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_processing_queue_concurrency_and_fifo(self) -> None:
        """ProcessingQueueManager enforces max_concurrent and queues remaining items FIFO."""
        pq = ProcessingQueueManager(max_concurrent=2)

        executed: list[str] = []

        def make_worker(cid: str):
            return lambda: executed.append(cid)

        # Item 1 -> Active
        item1, is_act1 = pq.enqueue("clip1", "Title 1", make_worker("clip1"))
        self.assertTrue(is_act1)
        self.assertEqual(item1.status, "processing")
        self.assertIsNone(item1.position)

        # Item 2 -> Active
        item2, is_act2 = pq.enqueue("clip2", "Title 2", make_worker("clip2"))
        self.assertTrue(is_act2)
        self.assertEqual(item2.status, "processing")
        self.assertIsNone(item2.position)

        # Item 3 -> Queued at position 1 (sleeps briefly so we can check it in active)
        item3, is_act3 = pq.enqueue("clip3", "Title 3", lambda: (time.sleep(0.1), executed.append("clip3")))
        self.assertFalse(is_act3)
        self.assertEqual(item3.status, "queued")
        self.assertEqual(item3.position, 1)

        # Item 4 -> Queued at position 2
        item4, is_act4 = pq.enqueue("clip4", "Title 4", make_worker("clip4"))
        self.assertFalse(is_act4)
        self.assertEqual(item4.status, "queued")
        self.assertEqual(item4.position, 2)

        status = pq.get_status()
        self.assertEqual(status["active_count"], 2)
        self.assertEqual(status["waiting_count"], 2)
        self.assertEqual(status["max_concurrent"], 2)

        # Run item1 -> finishes and promotes item3
        pq.run_item("clip1")
        self.assertIn("clip1", executed)

        # Wait briefly for thread to start item3
        time.sleep(0.02)
        status_after = pq.get_status()
        active_ids = [it["clip_id"] for it in status_after["active"]]
        self.assertIn("clip3", active_ids)
        self.assertEqual(status_after["waiting_count"], 1)
        self.assertEqual(status_after["waiting"][0]["clip_id"], "clip4")
        self.assertEqual(status_after["waiting"][0]["position"], 1)

    def test_processing_queue_cancellation(self) -> None:
        """Waiting items can be cancelled; active items cannot."""
        pq = ProcessingQueueManager(max_concurrent=1)
        pq.enqueue("clipA", "Title A", lambda: None)
        pq.enqueue("clipB", "Title B", lambda: None)
        pq.enqueue("clipC", "Title C", lambda: None)

        # Cannot cancel active clipA
        ok, msg = pq.cancel("clipA")
        self.assertFalse(ok)
        self.assertIn("already processing", msg)

        # Can cancel waiting clipB
        ok, msg = pq.cancel("clipB")
        self.assertTrue(ok)

        status = pq.get_status()
        self.assertEqual(status["waiting_count"], 1)
        self.assertEqual(status["waiting"][0]["clip_id"], "clipC")
        self.assertEqual(status["waiting"][0]["position"], 1)

    def test_upload_queue_concurrency_and_quota_enforcement(self) -> None:
        """UploadQueueManager enforces max_concurrent and global quota."""
        uq = UploadQueueManager(max_concurrent=3, quota_tracker=self.quota_tracker)

        executed: list[str] = []

        def worker(cid: str):
            return lambda: (executed.append(cid), self.quota_tracker.record_upload())

        # Enqueue 3 uploads -> all 3 become active
        u1, act1 = uq.enqueue("up1", "Upload 1", worker("up1"))
        u2, act2 = uq.enqueue("up2", "Upload 2", worker("up2"))
        u3, act3 = uq.enqueue("up3", "Upload 3", worker("up3"))
        self.assertTrue(act1 and act2 and act3)

        # 4th upload -> queued
        u4, act4 = uq.enqueue("up4", "Upload 4", worker("up4"))
        self.assertFalse(act4)
        self.assertEqual(u4.position, 1)

        # Exhaust quota (daily_limit=3)
        self.quota_tracker.record_upload()
        self.quota_tracker.record_upload()
        self.quota_tracker.record_upload()

        # Attempting to enqueue when quota is exhausted raises QuotaExceededError
        with self.assertRaises(QuotaExceededError):
            uq.enqueue("up5", "Upload 5", worker("up5"))

    def test_upload_queue_cancellation(self) -> None:
        """Waiting upload items can be cancelled."""
        uq = UploadQueueManager(max_concurrent=1, quota_tracker=self.quota_tracker)
        uq.enqueue("upA", "Upload A", lambda: None)
        uq.enqueue("upB", "Upload B", lambda: None)

        ok, _ = uq.cancel("upA")
        self.assertFalse(ok)

        ok, msg = uq.cancel("upB")
        self.assertTrue(ok)
        self.assertEqual(uq.get_status()["waiting_count"], 0)

    def test_progress_reporting_and_websocket_events(self) -> None:
        """Progress reporting broadcasts proper websocket payloads."""
        events: list[tuple[str, dict]] = []

        def fake_broadcast(event_type: str, data: dict = None):
            events.append((event_type, data or {}))

        with patch.object(ws_manager, "broadcast_sync", side_effect=fake_broadcast):
            pq = ProcessingQueueManager(max_concurrent=2)
            pq.enqueue("c_test", "Test Clip", lambda: None)
            pq.report_progress("c_test", "reframing", 45)

            uq = UploadQueueManager(max_concurrent=2)
            uq.enqueue("u_test", "Test Upload", lambda: None)
            uq.report_progress("u_test", 5000000, 10000000)

        p_progress = [d for ev, d in events if ev == "processing_progress"]
        self.assertEqual(len(p_progress), 1)
        self.assertEqual(p_progress[0]["stage"], "reframing")
        self.assertEqual(p_progress[0]["percentage"], 45)

        u_progress = [d for ev, d in events if ev == "upload_progress"]
        self.assertEqual(len(u_progress), 1)
        self.assertEqual(u_progress[0]["percentage"], 50)
        self.assertEqual(u_progress[0]["bytes_sent"], 5000000)
        self.assertEqual(u_progress[0]["total_bytes"], 10000000)


class TestQueueAPIEndpoints(TestCase):
    """Integration tests for Queue API endpoints."""

    def setUp(self) -> None:
        from app.api.routes.shorts import router as shorts_router
        from app.api.routes.upload import router as upload_router

        self.tmp_dir = tempfile.TemporaryDirectory()
        quota_path = Path(self.tmp_dir.name) / "test_quota.json"
        self.mock_tracker = DailyQuotaTracker(quota_path=quota_path, daily_limit=10, max_units=100000)

        app = FastAPI()
        app.include_router(shorts_router, prefix="/api")
        app.include_router(upload_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_shorts_queue_endpoints(self) -> None:
        """GET /api/shorts/queue and POST /api/shorts/{clip_id}/cancel operate properly."""
        pq = get_processing_queue(max_concurrent=1)
        pq._active.clear()
        pq._waiting.clear()

        # Enqueue item 1 (active) and item 2 (waiting)
        pq.enqueue("clip_act", "Active Clip", lambda: None)
        pq.enqueue("clip_wait", "Waiting Clip", lambda: None)

        resp = self.client.get("/api/shorts/queue")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["active_count"], 1)
        self.assertEqual(data["waiting_count"], 1)

        # Cancel waiting clip
        resp_cancel = self.client.post("/api/shorts/clip_wait/cancel")
        self.assertEqual(resp_cancel.status_code, 200)
        self.assertEqual(resp_cancel.json()["status"], "cancelled")

        # Cancel active clip returns 400
        resp_cancel_act = self.client.post("/api/shorts/clip_act/cancel")
        self.assertEqual(resp_cancel_act.status_code, 400)

    def test_upload_queue_endpoints(self) -> None:
        """GET /api/upload/queue and POST /api/upload/{clip_id}/cancel operate properly."""
        uq = get_upload_queue(max_concurrent=1, quota_tracker=self.mock_tracker)
        uq.quota_tracker = self.mock_tracker
        uq._active.clear()
        uq._waiting.clear()

        uq.enqueue("up_act", "Active Upload", lambda: None)
        uq.enqueue("up_wait", "Waiting Upload", lambda: None)

        resp = self.client.get("/api/upload/queue")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["active_count"], 1)
        self.assertEqual(data["waiting_count"], 1)

        resp_cancel = self.client.post("/api/upload/up_wait/cancel")
        self.assertEqual(resp_cancel.status_code, 200)
        self.assertEqual(resp_cancel.json()["status"], "cancelled")

        resp_cancel_act = self.client.post("/api/upload/up_act/cancel")
        self.assertEqual(resp_cancel_act.status_code, 400)

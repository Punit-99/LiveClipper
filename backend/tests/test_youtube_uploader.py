"""Unit tests for YouTube OAuth, resumable uploader, quota tracker, and metadata.

All tests are 100% offline using mocked HTTP calls and isolated temporary directories.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from app.shorts.metadata import (
    GAMING_CATEGORY_ID,
    format_description,
    format_tags,
    format_youtube_snippet,
    sanitize_title,
)
from app.shorts.youtube_auth import (
    ReauthRequired,
    TransientAuthError,
    YouTubeAuthError,
    YouTubeTokenManager,
)
from app.shorts.youtube_uploader import (
    DailyQuotaTracker,
    QuotaExceededError,
    TransientUploadError,
    UploadSessionExpiredError,
    YouTubeUploader,
    YouTubeUploadError,
)


class TestYouTubeAuth(unittest.TestCase):
    """Tests for YouTubeTokenManager and OAuth token handling."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tokens_path = Path(self.tmp_dir.name) / ".youtube_tokens.json"
        self.client_id = "test-client-id"
        self.client_secret = "test-client-secret"
        self.session = MagicMock(spec=requests.Session)
        self.manager = YouTubeTokenManager(
            tokens_path=self.tokens_path,
            client_id=self.client_id,
            client_secret=self.client_secret,
            expiry_buffer_seconds=60.0,
            session=self.session,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_and_load_tokens_atomic_permissions(self):
        self.manager.save_tokens(
            access_token="test-access-123",
            refresh_token="test-refresh-456",
            expires_in=3600,
            scope="https://www.googleapis.com/auth/youtube.upload",
        )
        self.assertTrue(self.tokens_path.exists())
        # Check permissions: owner-only (0600)
        mode = self.tokens_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

        data = self.manager.load_tokens()
        self.assertEqual(data["access_token"], "test-access-123")
        self.assertEqual(data["refresh_token"], "test-refresh-456")
        self.assertTrue(self.manager.has_tokens())

    def test_get_valid_access_token_unexpired(self):
        self.manager.save_tokens(
            access_token="valid-token",
            refresh_token="refresh-tok",
            expires_in=1800,  # 30 mins remaining
        )
        # Should return without making an HTTP refresh call
        token = self.manager.get_valid_access_token()
        self.assertEqual(token, "valid-token")
        self.session.post.assert_not_called()

    def test_get_valid_access_token_proactive_refresh(self):
        # Save token with expires_in = 30s (within 60s buffer)
        self.manager.save_tokens(
            access_token="expiring-token",
            refresh_token="refresh-tok",
            expires_in=30.0,
        )

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "fresh-token-999",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        self.session.post.return_value = mock_resp

        token = self.manager.get_valid_access_token()
        self.assertEqual(token, "fresh-token-999")
        self.session.post.assert_called_once()

        # Check token file updated with new access token and preserved refresh token
        updated = self.manager.load_tokens()
        self.assertEqual(updated["access_token"], "fresh-token-999")
        self.assertEqual(updated["refresh_token"], "refresh-tok")

    def test_refresh_rotates_refresh_token_when_provided(self):
        self.manager.save_tokens(
            access_token="old-token",
            refresh_token="old-refresh",
            expires_in=10.0,
        )

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        }
        self.session.post.return_value = mock_resp

        token = self.manager.get_valid_access_token()
        self.assertEqual(token, "new-access")

        updated = self.manager.load_tokens()
        self.assertEqual(updated["refresh_token"], "rotated-refresh")

    def test_refresh_transient_error_5xx(self):
        self.manager.save_tokens(
            access_token="old-token",
            refresh_token="old-refresh",
            expires_in=10.0,
        )
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503
        self.session.post.return_value = mock_resp

        with self.assertRaises(TransientAuthError):
            self.manager.get_valid_access_token()

        # Tokens file must still exist
        self.assertTrue(self.tokens_path.exists())

    def test_refresh_transient_network_exception(self):
        self.manager.save_tokens(
            access_token="old-token",
            refresh_token="old-refresh",
            expires_in=10.0,
        )
        self.session.post.side_effect = requests.ConnectionError("Connection reset")

        with self.assertRaises(TransientAuthError):
            self.manager.get_valid_access_token()

        self.assertTrue(self.tokens_path.exists())

    def test_refresh_permanent_error_invalid_grant(self):
        self.manager.save_tokens(
            access_token="old-token",
            refresh_token="revoked-refresh",
            expires_in=10.0,
        )
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked.",
        }
        self.session.post.return_value = mock_resp

        with self.assertRaises(ReauthRequired):
            self.manager.get_valid_access_token()

    def test_missing_tokens_raises_reauth_required(self):
        with self.assertRaises(ReauthRequired):
            self.manager.get_valid_access_token()


class TestDailyQuotaTracker(unittest.TestCase):
    """Tests for YouTube upload daily limit and quota tracking."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.quota_path = Path(self.tmp_dir.name) / ".youtube_quota.json"
        self.current_date = "2026-11-19"
        self.tracker = DailyQuotaTracker(
            quota_path=self.quota_path,
            daily_limit=3,
            max_units=10000,
            date_fn=lambda: self.current_date,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_initial_state_can_upload(self):
        allowed, reason = self.tracker.can_upload()
        self.assertTrue(allowed)
        self.assertEqual(reason, "OK")

    def test_record_upload_increments_count_and_units(self):
        self.tracker.record_upload()
        status = self.tracker.get_status()
        self.assertEqual(status["uploads_today"], 1)
        self.assertEqual(status["units_used"], 1600)

        self.tracker.record_upload()
        status = self.tracker.get_status()
        self.assertEqual(status["uploads_today"], 2)
        self.assertEqual(status["units_used"], 3200)

    def test_blocks_when_daily_limit_reached(self):
        for _ in range(3):
            self.tracker.record_upload()

        allowed, reason = self.tracker.can_upload()
        self.assertFalse(allowed)
        self.assertIn("Daily upload limit reached", reason)

    def test_blocks_when_max_units_exceeded(self):
        # Set max units to 2000 (enough for 1 upload of 1600, not 2)
        tight_tracker = DailyQuotaTracker(
            quota_path=self.quota_path,
            daily_limit=5,
            max_units=2000,
            date_fn=lambda: self.current_date,
        )
        tight_tracker.record_upload()
        allowed, reason = tight_tracker.can_upload()
        self.assertFalse(allowed)
        self.assertIn("Daily YouTube API quota limit reached", reason)

    def test_resets_at_midnight_date_rollover(self):
        self.tracker.record_upload()
        self.tracker.record_upload()
        self.assertEqual(self.tracker.get_status()["uploads_today"], 2)

        # Midnight rollover: date changes
        self.current_date = "2026-11-20"
        status = self.tracker.get_status()
        self.assertEqual(status["date"], "2026-11-20")
        self.assertEqual(status["uploads_today"], 0)
        self.assertEqual(status["units_used"], 0)

        allowed, _ = self.tracker.can_upload()
        self.assertTrue(allowed)


class TestMetadataFormatter(unittest.TestCase):
    """Tests for YouTube title sanitization and snippet formatting."""

    def test_sanitize_title_under_limit_appends_shorts(self):
        raw = "Funny Car Collision in GTA"
        out = sanitize_title(raw)
        self.assertEqual(out, "Funny Car Collision in GTA #Shorts")
        self.assertLessEqual(len(out), 100)

    def test_sanitize_title_truncates_on_word_boundary(self):
        raw = (
            "This is an extraordinarily long title describing a massive and intense "
            "clutch moment in Grand Theft Auto 6 that totally blew the streamer's mind"
        )
        out = sanitize_title(raw, max_length=100)
        self.assertTrue(out.endswith("#Shorts"))
        self.assertLessEqual(len(out), 100)
        # Verify word boundary: should not cut in the middle of a word
        base = out.replace(" #Shorts", "")
        self.assertFalse(base.endswith(" "))
        self.assertFalse(base.endswith("mindth"))

    def test_sanitize_title_avoids_duplicate_shorts_tag(self):
        raw = "Epic Stunt Jump #Shorts"
        out = sanitize_title(raw)
        self.assertEqual(out, "Epic Stunt Jump #Shorts")
        # Should not append a second #Shorts
        self.assertEqual(out.count("#Shorts"), 1)

    def test_sanitize_title_empty_fallback(self):
        out = sanitize_title("")
        self.assertEqual(out, "Live Stream Highlight #Shorts")

    def test_format_description_all_fields(self):
        sidecar = {
            "streamer_name": "xqc",
            "analysis": {
                "title": "Crazy Police Chase",
                "reaction_summary": "Chat exploded with laughter as 5 cruisers crashed.",
                "category": "funny_moment",
                "key_quotes": ["OMEGALUL WHAT A CRASH", "BEST DRIFT EVER"],
            },
        }
        desc = format_description(sidecar)
        self.assertIn("Chat exploded with laughter", desc)
        self.assertIn("Streamer: xqc (https://twitch.tv/xqc)", desc)
        self.assertIn("Highlight Type: Funny Moment", desc)
        self.assertIn('• "OMEGALUL WHAT A CRASH"', desc)
        self.assertIn("#Shorts", desc)
        self.assertIn("#xqc", desc)
        self.assertIn("#GTA6", desc)
        self.assertLessEqual(len(desc), 5000)

    def test_format_description_missing_optional_fields(self):
        sidecar = {
            "streamer_name": "kaicenat",
            "analysis": {},
        }
        desc = format_description(sidecar)
        self.assertIn("Streamer: kaicenat", desc)
        self.assertNotIn("Top Chat Reactions:", desc)
        self.assertIn("#Shorts", desc)
        self.assertIn("#kaicenat", desc)

    def test_format_tags(self):
        sidecar = {
            "streamer_name": "kaicenat",
            "analysis": {"category": "unexpected_moment"},
        }
        tags = format_tags(sidecar)
        self.assertIn("Shorts", tags)
        self.assertIn("kaicenat", tags)
        self.assertIn("GTA6", tags)
        self.assertIn("unexpected moment", tags)
        # Verify deduplication
        self.assertEqual(len(tags), len(set(tags)))

    def test_format_youtube_snippet(self):
        sidecar = {
            "streamer_name": "xqc",
            "analysis": {
                "title": "Unbelievable Mission Finale",
                "reaction_summary": "Incredible heist finish.",
            },
        }
        snippet = format_youtube_snippet(sidecar)
        self.assertEqual(snippet["categoryId"], GAMING_CATEGORY_ID)
        self.assertTrue(snippet["title"].endswith("#Shorts"))
        self.assertIn("xqc", snippet["description"])
        self.assertIsInstance(snippet["tags"], list)


class TestYouTubeUploader(unittest.TestCase):
    """Tests for resumable chunked upload, retries, and status resumption."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tokens_path = Path(self.tmp_dir.name) / ".youtube_tokens.json"
        self.quota_path = Path(self.tmp_dir.name) / ".youtube_quota.json"

        # Create dummy video file (1 MiB of test bytes)
        self.video_path = Path(self.tmp_dir.name) / "test_clip.mp4"
        self.video_bytes = b"X" * (1024 * 1024)  # 1 MiB
        with open(self.video_path, "wb") as fh:
            fh.write(self.video_bytes)

        self.token_manager = MagicMock(spec=YouTubeTokenManager)
        self.token_manager.get_valid_access_token.return_value = "mock_access_token_abc"

        self.quota_tracker = DailyQuotaTracker(
            quota_path=self.quota_path,
            daily_limit=5,
            max_units=10000,
            date_fn=lambda: "2026-11-19",
        )

        self.session = MagicMock(spec=requests.Session)
        self.uploader = YouTubeUploader(
            token_manager=self.token_manager,
            quota_tracker=self.quota_tracker,
            session=self.session,
            chunk_size=512 * 1024,  # 512 KiB chunks -> 2 chunks for 1 MiB
            max_retries=3,
            retry_delay=0.01,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_privacy_status_validation(self):
        with self.assertRaises(ValueError):
            self.uploader.upload_video(
                self.video_path,
                privacy_status="super_secret",
            )

    def test_nonexistent_or_empty_video_raises(self):
        nonexistent = Path(self.tmp_dir.name) / "does_not_exist.mp4"
        with self.assertRaises(FileNotFoundError):
            self.uploader.upload_video(nonexistent)

        empty_file = Path(self.tmp_dir.name) / "empty.mp4"
        empty_file.touch()
        with self.assertRaises(YouTubeUploadError):
            self.uploader.upload_video(empty_file)

    def test_quota_blocked_before_initiation(self):
        # Exceed daily limit
        for _ in range(5):
            self.quota_tracker.record_upload()

        with self.assertRaises(QuotaExceededError):
            self.uploader.upload_video(self.video_path)

        # No HTTP calls should have been made
        self.session.post.assert_not_called()

    def test_resumable_upload_initiation_and_single_chunk_success(self):
        # Setup Initiation POST
        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.headers = {"Location": "https://upload.youtube.com/upload_session_123"}
        self.session.post.return_value = init_resp

        # Setup 2 chunks: Chunk 1 -> 308, Chunk 2 -> 200
        chunk1_resp = MagicMock()
        chunk1_resp.status_code = 308
        chunk1_resp.headers = {"Range": "bytes=0-524287"}

        chunk2_resp = MagicMock()
        chunk2_resp.status_code = 200
        chunk2_resp.json.return_value = {
            "id": "vid_xyz_789",
            "snippet": {"title": "Test Title #Shorts"},
        }

        self.session.put.side_effect = [chunk1_resp, chunk2_resp]

        result = self.uploader.upload_video(
            self.video_path,
            snippet={"title": "Test Title #Shorts"},
            privacy_status="unlisted",
        )

        self.assertEqual(result["video_id"], "vid_xyz_789")
        self.assertEqual(result["url"], "https://www.youtube.com/shorts/vid_xyz_789")
        self.assertEqual(result["privacy_status"], "unlisted")

        # Quota should have recorded 1 upload
        status = self.quota_tracker.get_status()
        self.assertEqual(status["uploads_today"], 1)

    def test_resumable_upload_retry_on_5xx_during_initiation(self):
        fail_resp = MagicMock()
        fail_resp.status_code = 503

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {"Location": "https://upload.youtube.com/upload_session_456"}

        self.session.post.side_effect = [fail_resp, ok_resp]

        # Put chunks finish immediately
        put_resp = MagicMock()
        put_resp.status_code = 200
        put_resp.json.return_value = {"id": "vid_recovered"}
        self.session.put.return_value = put_resp

        # Make chunk_size larger than file so it's 1 chunk
        self.uploader.chunk_size = 2 * 1024 * 1024
        result = self.uploader.upload_video(self.video_path)

        self.assertEqual(result["video_id"], "vid_recovered")
        self.assertEqual(self.session.post.call_count, 2)

    def test_chunk_upload_transient_500_and_resumes_from_query_offset(self):
        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.headers = {"Location": "https://upload.youtube.com/upload_session_resume"}
        self.session.post.return_value = init_resp

        # 1. Chunk 1 PUT succeeds with 308 (received bytes 0-524287)
        chunk1_resp = MagicMock()
        chunk1_resp.status_code = 308
        chunk1_resp.headers = {"Range": "bytes=0-524287"}

        # 2. Chunk 2 PUT fails with 500
        chunk2_fail = MagicMock()
        chunk2_fail.status_code = 500
        chunk2_fail.text = "Internal Server Error"

        # 3. Status Query PUT returns 308 indicating server received up to 524287
        query_resp = MagicMock()
        query_resp.status_code = 308
        query_resp.headers = {"Range": "bytes=0-524287"}

        # 4. Chunk 2 PUT retry succeeds with 200
        chunk2_success = MagicMock()
        chunk2_success.status_code = 200
        chunk2_success.json.return_value = {"id": "vid_resumed_ok"}

        self.session.put.side_effect = [
            chunk1_resp,
            chunk2_fail,
            query_resp,
            chunk2_success,
        ]

        result = self.uploader.upload_video(self.video_path)
        self.assertEqual(result["video_id"], "vid_resumed_ok")
        self.assertEqual(result["url"], "https://www.youtube.com/shorts/vid_resumed_ok")

    def test_resumable_upload_session_expired_404(self):
        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.headers = {"Location": "https://upload.youtube.com/upload_session_exp"}
        self.session.post.return_value = init_resp

        # Chunk fails with network error
        self.session.put.side_effect = [
            requests.ConnectionError("Network dropped"),
            # Status query returns 404 expired session
            MagicMock(status_code=404),
        ]

        with self.assertRaises(UploadSessionExpiredError):
            self.uploader.upload_video(self.video_path)

    def test_quota_exceeded_error_from_api_403(self):
        err_resp = MagicMock()
        err_resp.status_code = 403
        err_resp.json.return_value = {
            "error": {
                "errors": [{"reason": "quotaExceeded"}],
                "message": "The request cannot be completed because you have exceeded your quota.",
            }
        }
        self.session.post.return_value = err_resp

        with self.assertRaises(QuotaExceededError):
            self.uploader.upload_video(self.video_path)

    def test_auth_error_401_triggers_token_refresh(self):
        unauth_resp = MagicMock()
        unauth_resp.status_code = 401

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {"Location": "https://upload.youtube.com/upload_session_refresh"}

        self.session.post.side_effect = [unauth_resp, ok_resp]

        chunk_resp = MagicMock()
        chunk_resp.status_code = 200
        chunk_resp.json.return_value = {"id": "vid_refreshed_auth"}
        self.session.put.return_value = chunk_resp

        self.uploader.chunk_size = 2 * 1024 * 1024
        result = self.uploader.upload_video(self.video_path)

        self.assertEqual(result["video_id"], "vid_refreshed_auth")
        self.token_manager.refresh_access_token.assert_called_once()


if __name__ == "__main__":
    unittest.main()

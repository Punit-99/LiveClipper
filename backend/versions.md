# LiveClipper — Version History & Changelog

---

## v1.0.0 — Stages 1–5: Single-Stream Twitch Clipper (Foundation)
*Completed: Nov 18, 2026*

### 1.1.1 — Stream Discovery
- Discovers live Twitch streams in the GTA 6 category via Twitch Helix API.

### 1.1.2 — Twitch Chat Monitoring
- Connects to Twitch IRC chat via WebSocket.
- Parses IRC messages in real time.

### 1.1.3 — Chat Spike Detection
- StreamHighlightDetector monitors chat activity.
- Detects unusual spikes in message rate, emote bursts, and unique chatter count.
- Rolling baseline comparison for spike identification.

### 1.1.4 — Gemini AI Highlight Analysis
- Sends detected highlight candidates to Gemini (gemini-3.1-flash-lite).
- Gemini evaluates chat context and determines if the moment is worth clipping (should_clip=true/false).
- Returns: score (0-100), confidence, category, title, reasoning.

### 1.1.5 — Video Recording Pipeline
- Records Twitch video using Streamlink + FFmpeg.
- Maintains a rolling video buffer of 4-second .ts segments.
- Pins relevant segments when a highlight is detected.

### 1.1.6 — Clip Extraction
- Extracts approved clips into MP4 files (720p, 60fps, H.264/AAC).
- Generates JSON sidecars containing clip metadata and Gemini analysis results.
- Run-isolated buffer cleanup after extraction.

### 1.1.7 — Verified Results
- 100/100 offline tests passed.
- Successfully monitored #kaicenat GTA 6 stream.
- Recorded 96 video segments.
- Gemini approved 3 real highlights.
- Extracted 3 playable MP4 clips with audio.
- JSON sidecars validated.

---

## v2.0.0 — Stage 6: Multi-Stream Architecture
*Completed: Nov 18, 2026*

### 2.1.1 — Multi-Stream Prerequisites & Hardening (Stage 6.1)
- Added asyncio.Semaphore for concurrent FFmpeg extraction.
- Per-stream fairness caps (max_queued_per_stream) on Gemini worker.
- Process-group termination for child Streamlink/FFmpeg trees.
- Thread-safe lock guards across shared resources.
- 12 tests added.

### 2.1.2 — Hardening Patch (Stage 6.1.1)
- Atomic token file writes using tempfile + os.replace with 0o600 permissions.
- OAuth refresh token rotation preserved on validation failure.
- Gemini worker shutdown: in-flight cancelled events invoke drop callback exactly once.
- WebSocket handshake shutdown race fixed.
- Subprocess cancellation between Streamlink and FFmpeg creation fixed.
- 10 tests added. Total: 122/122.

### 2.2.1 — StreamSession & StreamRoutingAnalysisSink (Stage 6.2)
- StreamSession encapsulates one streamer's full lifecycle.
- Each session independently startable, stoppable, and restartable.
- StreamRoutingAnalysisSink routes Gemini verdicts back to correct session.
- Restart-safe unregistration prevents race conditions.
- Two-phase shutdown support (stop_chat + stop).
- 13 tests added. Total: 135/135.

### 2.3.1 — MultiStreamManager, GlobalDiskBudget, Liveness (Stage 6.3)
- MultiStreamManager orchestrates multiple StreamSession instances.
- GlobalDiskBudget monitors aggregate disk usage, trims oldest unpinned segments.
- Emergency stop when disk budget exceeded (never deletes pinned segments).
- ChannelLivenessChecker batches Helix queries (up to 100 channels per request).
- Two-phase graceful shutdown with SIGINT/SIGTERM handling.
- 13 tests added. Total: 148/148.

### 2.4.1 — Multi-Stream CLI (Stage 6.4)
- Created monitor_streams.py as the multi-channel entry point.
- Flexible channel resolution: positional args, flags, .env, auto-discovery.
- Single-stream equivalence when only 1 channel monitored.
- Periodic status reporting every 30 seconds.
- 28 tests added. Total: 176/176.

### 2.5.1 — Live Multi-Stream Integration Test (Stage 6.5)
- Monitored kaicenat (99k viewers) and xqc (60k viewers) for 7 minutes 33 seconds.
- 226 total segments recorded (113 per stream).
- 9 highlight events detected and analyzed.
- 9 clips extracted (3 kaicenat, 6 xqc).
- All clips verified at 720p, 60fps, H.264/AAC.
- Zero orphaned processes after shutdown.

---

## v3.0.0 — Stage 7: YouTube Shorts Upload Pipeline
*Completed: Nov 18, 2026*

### 3.1.1 — YouTube OAuth & Resumable Uploader (Stage 7.1)
- YouTubeTokenManager with atomic file writes and proactive token refresh.
- authorize_youtube.py: interactive CLI for first-time Google OAuth login.
- YouTubeUploader: resumable chunked upload protocol (POST initiate, PUT stream).
- DailyQuotaTracker: tracks daily uploads, blocks at 10,000 unit limit.
- Metadata formatter: title truncation (100 chars), description, tags, Gaming category.
- 30 tests added. Total: 206/206.

### 3.2.1 — Duration Adapter & Vertical Reframing (Stage 7.2)
- Duration adapter: clips over 60s trimmed to 55s centered on peak chat reaction.
- OpenShorts Bridge: dual-engine approach (Docker/subprocess + native FFmpeg fallback).
- FFmpeg fallback: 9:16 blur-pad + hook title overlay without ML dependencies.
- Output: 1080x1920, H.264/AAC, YouTube Shorts compliant.
- 13 tests added. Total: 219/219.

### 3.3.1 — Shorts Pipeline & CLI (Stage 7.3)
- ShortsPipeline: scan clips → filter by score → process → upload.
- ShortsManifest: persistent JSONL tracking (DISCOVERED → PROCESSED → APPROVED → UPLOADED).
- process_shorts.py CLI: batch/single clip processing, dry-run, engine selection.
- upload_shorts.py CLI: interactive review, auto-upload, quota enforcement.
- 15 tests added. Total: 234/234.

### 3.4.1 — Live End-to-End Upload Test (Stage 7.4)
- Processed all 12 clips into vertical Shorts (1080x1920, 60fps).
- First YouTube Short uploaded: https://www.youtube.com/shorts/cQQpOv_6wIU
- Resumable upload confirmed working with real YouTube API.
- Quota tracking validated (1/5 uploads used).

---

## v4.0.0 — Stage 8: Full OpenShorts Docker Integration
*Completed: Nov 19, 2026*

### 4.1.1 — Docker Image Build
- Built liveclipper/openshorts:latest on ARM64 (Apple M5).
- PyTorch CPU, MediaPipe, faster-whisper, FFmpeg with libass.
- Persistent Whisper model cache via Docker volume.

### 4.1.2 — OpenShorts Processing Engine
- Whisper AI speech-to-text with word-level timestamps.
- MediaPipe face/speaker tracking for intelligent reframing.
- Animated ASS karaoke subtitles with style presets.
- Hook title overlay with face avoidance positioning.
- 236 tests passing.

---

## v5.0.0 — Stage 9: Viral Metadata Engine
*Completed: Nov 19, 2026*

### 5.1.1 — Viral Title Generator
- Gemini generates 3 ranked viral title options per clip.
- Viral patterns: curiosity gap, reaction bait, emotional hooks, pattern interrupts.
- Emoji support (configurable on/off).
- Category-aware tonal adaptation (funny, fail, hype, unexpected).

### 5.1.2 — Description & Tag Optimizer
- YouTube-optimized descriptions under 500 characters.
- Front-loaded keywords, streamer credit, game tag, CTA, hashtags.
- Deduplicated tags under 500-character YouTube limit.

### 5.1.3 — Fallback System
- Deterministic offline fallback when Gemini is unavailable.
- Rule-based title/description/tag generation per category.
- 253 tests passing.

---

## v6.0.0 — Stage 10: Multi-Platform & Quality Upgrades
*Completed: Nov 19, 2026*

### 6.1.1 — Video Quality Fix
- Recording quality upgraded to 1080p60 (was defaulting to 720p).
- Lanczos interpolation for vertical scaling.
- CRF 18, 6 Mbps target, 8 Mbps maxrate, 192 kbps audio.
- Verified output: 1080x1920 at 4.43 Mbps.

### 6.1.2 — YouTube Live Stream Support
- YouTubeStreamFinder: priority channel checking + category discovery.
- YouTube Live Chat monitoring via Data API v3 polling.
- Quota-aware rate limiter for chat polling.
- Pre-configured Indian gaming creators: CarryMinati, Mortal, Techno Gamerz, etc.

### 6.1.3 — Language Filtering
- Multi-tier detection: API metadata, Devanagari script, Hinglish lexicon.
- Configurable target languages (default: en, hi).
- Safety default: ambiguous streams accepted.

### 6.1.4 — Unified Multi-Platform CLI
- --platform youtube/twitch/all flags.
- YouTube channel ID and Twitch channel name support.
- 275 tests passing.

---

## v7.0.0 — Multi-Stream Controls & CLI Runner
*Completed: Nov 19, 2026*

### 7.1.1 — MULTI_STREAM_ENABLED Toggle
- Boolean .env variable (default: false).
- --multi and --single CLI flags for per-run override.
- Startup logging: "Multi-stream: ENABLED (max 3)" or "DISABLED (single stream only)".
- 280 tests passing.

### 7.1.2 — Universal CLI Runner (run.py)
- Cross-platform (Windows, Mac, Linux).
- Commands: monitor, process, list, upload, status, test, auth, help.
- Auto-detects venv, sets PYTHONPATH, colored output, --dry-run support.
- run.sh (Mac/Linux) and run.bat (Windows) wrapper scripts.
- 286 tests passing.

---

## v8.0.0 — Phase 1: OpenShorts Merge into Single Repo
*Completed: Nov 20, 2026*

### 8.1.1 — Code Migration
- Copied needed modules into backend/app/video/:
  - reframe.py, subtitles.py, transcribe.py, hooks.py, ffmpeg_utils.py, scene_detection.py.
- Bundled fonts: Anton, Montserrat, ArchivoBlack, NotoSerif.
- Removed dependency on external OpenShorts Docker container.
- Internal Python pipeline replaces Docker subprocess calls.

### 8.1.2 — Cleanup
- Deleted openshorts/ folder entirely.
- Removed unused modules: app.py, dashboard/, editor.py, yt_account.py, social_platforms.py, etc.
- 289 tests passing.

---

## v9.0.0 — Phase 2: React Frontend Dashboard
*Completed: Nov 20, 2026*

### 9.1.1 — FastAPI Backend Wrapper
- REST API wrapping existing pipeline classes.
- Endpoints: /api/status, /api/clips, /api/shorts, /api/settings, /api/highlights.
- POST /api/monitor/start and /api/monitor/stop for lifecycle control.
- WebSocket /ws/live for real-time event streaming.
- HTTP 206 Partial Content range streaming for video preview.

### 9.1.2 — React Frontend
- Dashboard: stream engine status, KPI cards, disk usage, Gemini status.
- Clips page: video cards grid, score badges, process to short action.
- Shorts page: vertical video cards, viral titles, upload to YouTube.
- Live Feed: real-time WebSocket event timeline.
- Settings: editable .env configuration form.
- 299 tests passing.

---

## v10.0.0 — Phase 3: Docker Compose
*Completed: Nov 20, 2026*

### 10.1.1 — Docker Compose Setup
- docker-compose.yml with backend + frontend services.
- Backend: Python 3.11-slim, FFmpeg, libass, PyTorch CPU, all ML dependencies.
- Frontend: multi-stage build (Node 20 → Nginx Alpine).
- Nginx reverse proxy: /api/* and /ws/* to backend, SPA catch-all.
- Health checks, volume persistence, env_file loading.
- One command: `docker compose up`.

### 10.1.2 — Docker Image Optimization
- Multi-stage build: build-essential discarded after compilation.
- Python healthcheck replaces curl (smaller image).
- Backend: 3.92 GB → reduced from 4.33 GB.
- Frontend: 92.6 MB.
- 300 tests passing.

---

## v11.0.0 — UI/UX Upgrades & Docker Fixes
*Completed: Nov 20, 2026*

### 11.1.1 — Settings Page Redesign
- Lock/unlock toggle per card (locked by default, fields read-only).
- Dynamic channel fields with + Add button (replaces comma-separated input).
- Channel name auto-resolution from YouTube API.
- Include/exclude checkboxes for channels.
- Language filter as checkboxes instead of text input.

### 11.1.2 — Real-Time Activity Notifications
- Toast notifications for every pipeline event.
- WebSocket events: monitoring_started, chat_spike_detected, gemini_verdict, clip_extracted, etc.
- Dashboard live stats panel: segments, chat messages, highlights, clips in real time.

### 11.1.3 — Channel Selection & Fallback Approval
- Start Monitoring opens channel selection modal.
- Check Live Status button shows live/offline status of all configured channels.
- Fallback streams require user approval before recording.
- Max concurrent stream enforcement in selection modal.

### 11.1.4 — Clip Processing Progress
- 6-stage WebSocket progress (trimming, reframing, transcribing, subtitles, hooks, encoding).
- Progress bar with percentage on each clip card.
- Toast notifications per stage completion.

### 11.1.5 — Docker Bug Fixes
- FFmpeg rc=183 fixed (bad YouTube URL resolution in Streamlink).
- WebSocket socket.send() spam eliminated (disconnect handling).
- EBUSY bind mount fix for .env and token writes inside containers.
- 307 tests passing.

---

## v12.0.0 — Video Processing Fixes
*Completed: Nov 20, 2026*

### 12.1.1 — Hook Title Overlay Removed
- HOOK_OVERLAY_ENABLED=false by default.
- Yellow banner box eliminated from video output.
- Viral title preserved in YouTube metadata only.

### 12.1.2 — Whisper Subtitles Restored
- OPENSHORTS_SUBTITLE_STYLE=bold (was set to "none").
- faster-whisper transcription pipeline verified end-to-end.
- ASS karaoke word-pop subtitles burning via FFmpeg libass.

### 12.1.3 — Split-Screen Layout
- OPENSHORTS_REFRAME_MODE=split as default.
- Facecam detection with multi-frame spatial clustering.
- Top ~40% streamer facecam, bottom ~60% gameplay.
- Graceful fallback to wide mode if no face detected.

---

## v13.0.0 — Concurrent Operations & Queue System
*Completed: Nov 20, 2026*

### 13.1.1 — Concurrent Clip Processing
- ProcessingQueueManager with configurable concurrency (MAX_CONCURRENT_PROCESSING=2).
- FIFO queue with position tracking.
- Cancel queued items before execution.
- Independent progress tracking per clip.

### 13.1.2 — Concurrent YouTube Uploads
- UploadQueueManager with configurable concurrency (MAX_CONCURRENT_UPLOADS=3).
- Quota enforcement across all concurrent uploads.
- Upload progress via WebSocket (bytes sent, total bytes, percentage).

### 13.1.3 — Queue Management UI
- Queue status indicator on Clips and Shorts pages.
- Popover dropdown showing active/waiting items.
- Cancel buttons for queued items.
- 330 tests passing.

---

## v14.0.0 — Upload Verification & Re-upload
*Completed: Nov 20, 2026*

### 14.1.1 — YouTube Upload Verification
- GET /api/upload/<id>/verify: checks if YouTube video still exists.
- Batch verification: POST /api/upload/verify-all.
- Status: Verified ✓, Not found on YouTube, Upload failed.

### 14.1.2 — Re-upload Functionality
- POST /api/upload/<id>/reupload: resets status and uploads fresh.
- Warning logged if previous video still exists on YouTube.
- 323 tests passing.

---

## v15.0.0 — shadcn/ui Frontend Rebuild & Twitch Removal
*Completed: Nov 20, 2026*

### 15.1.1 — shadcn/ui Component Library
- Replaced raw Tailwind CSS with shadcn/ui design system.
- Dark theme (zinc/slate palette).
- Collapsible sidebar navigation with icons.
- Command palette (Cmd+K) for quick navigation.
- Consistent Card, Badge, Button, Dialog, Select, Tabs, Progress components.
- Skeleton loaders, empty state illustrations, responsive layout.

### 15.1.2 — Twitch Removal
- Removed all Twitch IRC chat monitoring code.
- Removed Twitch Helix API integration.
- Removed Twitch OAuth token management.
- Removed TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET from config.
- Removed --platform twitch/all CLI options.
- YouTube-only platform.

### 15.1.3 — YouTube Video Clipper (Import Page)
- Download any YouTube video via URL and analyze for highlights.
- Multi-signal analysis: transcript (Gemini), audio energy, visual changes, scene detection.
- Combined scoring: transcript 50%, audio 20%, visual 15%, engagement 15%.
- Top N moments extracted as clips.
- Import page with URL input, analysis settings, progress tracking.
- 337 tests passing.

---

## v16.0.0 — Frontend Bug Fixes & Quality Improvements
*Completed: Nov 21, 2026*

### 16.1.1 — Critical Frontend Fixes
- Fixed clip/short ID "undefined" in all API calls (backend returns both id and clip_id).
- Fixed Settings page crash (TypeError: .map() on non-array).
- Fixed Shorts score showing 0/100 (field name mismatch).
- Fixed Start Monitor button (wired to channel selection modal).
- Dynamic thumbnail generation with disk caching.
- 338 tests passing.

### 16.1.2 — Import Page Improvements
- Subtitle download rate limit (429) gracefully falls back to Whisper.
- Controls locked during analysis (sliders, toggles disabled).

### 16.1.3 — Toast Notifications Everywhere
- Every API call shows success/error toast.
- WebSocket events trigger colored toasts.
- Never fails silently.

---

## v17.0.0 — Video Playback, Import Library & Upload Quality
*Completed: Nov 21, 2026*

### 17.1.1 — Video Playback Fix
- Fixed await on sync function crashing import previews.
- Added HEAD method support for browser video preloading.
- Cleaned corrupted short files with broken moov atoms.
- React video player: key-based remounting, preload=auto, error fallback.

### 17.1.2 — Two-Step Import (Download + Analyze)
- Separated download and analyze into independent operations.
- Downloaded Videos Library: grid/list view with status tracking.
- Multiple concurrent downloads (up to 2).
- Analyze with configurable settings per video.
- File upload support: drag & drop local video files up to 5GB.

### 17.1.3 — YouTube Upload Quality Fix
- Minimum 10-12 Mbps bitrate for uploads (was 6 Mbps — caused poor quality after YouTube re-encode).
- High Profile H.264 (-profile:v high -level 4.2).
- BT.709 color space tags.
- AAC audio at 256 kbps.
- CRF 15 for upload versions.

### 17.1.4 — Fallback Stream Banner
- Shows channel names prominently (was showing only viewer count).
- Dismiss X button to hide banner.

### 17.1.5 — Quota Reset Countdown
- Midnight Pacific Time countdown in header.
- Warning badge at 80%+ usage.
- Manual quota counter reset in Settings.

### 17.1.6 — Shorts Video Player Fix
- Vertical 9:16 modal layout (was using 16:9 with black bars).
- Max height 80% viewport, width auto-calculated from aspect ratio.

### 17.1.7 — Duplicate Processing Prevention
- Same clip cannot be queued twice.
- Button disabled while clip is queued/processing.
- Separate "Re-convert" option for already-processed clips.
- Same protection for upload queue.

### 17.1.8 — Detailed Analysis Progress
- 7-stage progress with sub-steps and time estimates.
- Heartbeat indicator (pulsing dot every 10 seconds).
- Toast notifications when moments found and clips extracted.
- "Still processing..." message if no update for 30+ seconds.
- 344 tests passing.

---

## Configuration Reference

### .env Settings Summary
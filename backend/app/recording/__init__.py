"""Stage 5 (Steps 5.1–5.4): Recording models, SegmentIndex, ClipWindowPlanner,
RollingBufferManager, StreamRecorder, ClipExtractor, and ClipExtractionWorker.

No Stage 1–4 `monitor_chat.py` integration yet (Step 5.5).
"""

from app.recording.buffer_manager import (
    BufferCapacityError,
    BufferCapacityState,
    CleanupSummary,
    RollingBufferManager,
)
from app.recording.clip_extractor import (
    ClipExtractionError,
    ClipExtractionResult,
    ClipExtractionTimeoutError,
    ClipExtractor,
    build_clip_output_paths,
    build_ffmpeg_extract_command,
)
from app.recording.clip_planner import ClipWindowPlanner, find_peak_chat_timestamp
from app.recording.clip_worker import ClipExtractionWorker, Stage5PinAndSubmitSink
from app.recording.models import (
    BufferPin,
    ClipWindowPlan,
    DiscontinuityRecord,
    ExtractedClipMetadata,
    PinCoverageReport,
    PinState,
    RecordingConfig,
    SegmentSelection,
    VideoSegment,
    build_recording_config,
)
from app.recording.segment_index import (
    PinCapacityError,
    SegmentIndex,
    SegmentPinnedError,
)
from app.recording.stream_recorder import (
    PipelineProcesses,
    RecorderDependencyError,
    StreamRecorder,
    build_ffmpeg_segment_command,
    build_streamlink_command,
    find_ffmpeg_binary,
    find_streamlink_binary,
)

__all__ = [
    "BufferCapacityError",
    "BufferCapacityState",
    "BufferPin",
    "CleanupSummary",
    "ClipExtractionError",
    "ClipExtractionResult",
    "ClipExtractionTimeoutError",
    "ClipExtractionWorker",
    "ClipExtractor",
    "ClipWindowPlan",
    "ClipWindowPlanner",
    "DiscontinuityRecord",
    "ExtractedClipMetadata",
    "PinCapacityError",
    "PinCoverageReport",
    "PinState",
    "PipelineProcesses",
    "RecorderDependencyError",
    "RecordingConfig",
    "RollingBufferManager",
    "SegmentIndex",
    "SegmentPinnedError",
    "SegmentSelection",
    "Stage5PinAndSubmitSink",
    "StreamRecorder",
    "VideoSegment",
    "build_clip_output_paths",
    "build_ffmpeg_extract_command",
    "build_ffmpeg_segment_command",
    "build_recording_config",
    "build_streamlink_command",
    "find_ffmpeg_binary",
    "find_peak_chat_timestamp",
    "find_streamlink_binary",
]

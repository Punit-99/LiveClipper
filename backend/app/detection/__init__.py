"""Chat activity & highlight detection.

Pure, network-free logic that spots candidate highlight moments from chat and
packages the surrounding chat context for later (Stage 4 / Gemini) analysis.
Kept independent from Twitch networking so it is deterministically testable and
reusable for other platforms (e.g. YouTube) later.
"""

from app.detection.activity_detector import (
    ActivityDetector,
    ActivityDetectorConfig,
    SpikeEvent,
)
from app.detection.highlight_event import (
    ChatMessage,
    DetectionSignals,
    HighlightEvent,
)
from app.detection.highlight_detector import (
    CollectingSink,
    DetectionManager,
    HighlightConfig,
    HighlightSink,
    StreamHighlightDetector,
    build_highlight_config,
    logging_sink,
)
from app.detection.signals import (
    SignalAnalyzer,
    SignalSnapshot,
    emote_count_from_tags,
    normalize_text,
)

__all__ = [
    "ActivityDetector",
    "ActivityDetectorConfig",
    "SpikeEvent",
    "ChatMessage",
    "DetectionSignals",
    "HighlightEvent",
    "SignalAnalyzer",
    "SignalSnapshot",
    "emote_count_from_tags",
    "normalize_text",
    "StreamHighlightDetector",
    "DetectionManager",
    "HighlightConfig",
    "HighlightSink",
    "CollectingSink",
    "build_highlight_config",
    "logging_sink",
]

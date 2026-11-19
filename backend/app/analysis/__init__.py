"""Stage 4: Gemini AI Highlight Intelligence.

Consumes finalized `HighlightEvent` candidates via a non-blocking asyncio queue,
pre-filters obvious spam/bot noise locally, asks Gemini to explain WHY viewers
are reacting based on chat context, and emits structured `AnalyzedHighlight`
records to logging and JSONL persistence sinks.
"""

from app.analysis.gemini_client import (
    GeminiAnalysisError,
    GeminiAuthError,
    GeminiHighlightAnalyzer,
    GeminiRateLimitError,
    HighlightAnalyzer,
    apply_clip_policy,
)
from app.analysis.models import (
    AnalysisConfig,
    AnalysisSink,
    AnalysisStatus,
    AnalyzedHighlight,
    CompositeAnalysisSink,
    CompositeHighlightSink,
    GeminiHighlightVerdict,
    HighlightCategory,
    JsonlAnalysisSink,
    TokenUsage,
    build_analysis_config,
    logging_analysis_sink,
)
from app.analysis.prefilter import PrefilterResult, evaluate_prefilter
from app.analysis.prompt_builder import SYSTEM_INSTRUCTION, build_user_prompt
from app.analysis.worker import DroppedEventRecord, HighlightAnalysisWorker

__all__ = [
    "AnalysisConfig",
    "AnalysisSink",
    "AnalysisStatus",
    "AnalyzedHighlight",
    "CompositeAnalysisSink",
    "CompositeHighlightSink",
    "DroppedEventRecord",
    "GeminiAnalysisError",
    "GeminiAuthError",
    "GeminiHighlightAnalyzer",
    "GeminiHighlightVerdict",
    "GeminiRateLimitError",
    "HighlightAnalysisWorker",
    "HighlightAnalyzer",
    "HighlightCategory",
    "JsonlAnalysisSink",
    "PrefilterResult",
    "SYSTEM_INSTRUCTION",
    "TokenUsage",
    "apply_clip_policy",
    "build_analysis_config",
    "build_user_prompt",
    "evaluate_prefilter",
    "logging_analysis_sink",
]

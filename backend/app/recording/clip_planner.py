"""Pure clip-window planning from finalized `HighlightEvent` objects.

Why this module exists:
- Translates a Stage 3.6 `HighlightEvent` (with verified Stage 1–4 semantics:
  `start_timestamp = trigger - pre`, `end_timestamp = last_spike + post`
  clamped at `start + max_event_seconds`) into a requested video extraction
  window `[requested_clip_start, requested_clip_end]`.
- Treats `reaction_offset_seconds` strictly as a configurable **heuristic**
  accounting for viewer reaction + typing + spike ramp-up + stream latency
  differences, NOT exact broadcast synchronization.
- When a long merged event exceeds `clip_max_duration_seconds`, locates the
  densest viewer reaction cluster (`peak_chat_timestamp`) from `event.messages`
  and preserves the most relevant moment instead of blindly taking the first N
  seconds.

Pure logic: no networking, no clock calls, no FFmpeg calls.
"""

from __future__ import annotations

from app.detection.highlight_event import ChatMessage, HighlightEvent
from app.recording.models import ClipWindowPlan, RecordingConfig


def find_peak_chat_timestamp(
    messages: list[ChatMessage], *, window_seconds: float = 10.0
) -> float | None:
    """Return the center timestamp of the densest chat reaction window.

    Scores each sliding window by `(unique_chatters + 0.5 * total_emotes + 0.25 * msg_count)`
    so genuine multi-viewer hype beats single-user spam. Returns None if
    `messages` is empty.
    """
    if not messages:
        return None
    ordered = sorted(messages, key=lambda m: m.timestamp)
    if len(ordered) == 1:
        return ordered[0].timestamp

    best_score = -1.0
    best_count = -1
    best_ts = ordered[0].timestamp

    right = 0
    n = len(ordered)
    for left in range(n):
        start_ts = ordered[left].timestamp
        end_bound = start_ts + window_seconds
        while right < n and ordered[right].timestamp <= end_bound:
            right += 1

        window_msgs = ordered[left:right]
        if not window_msgs:
            continue
        unique_chatters = len({m.username for m in window_msgs if m.username})
        emotes = sum(max(0, m.emote_count) for m in window_msgs)
        count = len(window_msgs)
        score = unique_chatters + (0.5 * emotes) + (0.25 * count)

        if score > best_score or (score == best_score and count > best_count):
            best_score = score
            best_count = count
            # Use the median message timestamp of the peak window as the anchor.
            best_ts = window_msgs[count // 2].timestamp

    return best_ts


def _estimate_active_chat_end(
    event: HighlightEvent,
    *,
    peak_chat_ts: float | None,
    event_post_seconds: float,
    max_event_seconds: float,
) -> tuple[float, bool]:
    """Estimate when the active reaction phase ended for a merged event.

    Verified Stage 3.6 semantics:
    - If `event.duration_seconds < max_event_seconds - 0.5`, the event was NOT
      clamped, so the last merged spike occurred at `end_timestamp - event_post_seconds`.
    - If the event WAS clamped at `max_event_seconds`, we cannot assume a full
      20s quiet tail exists; use `max(trigger, peak_chat_ts, latest_dense_msg)`
      instead of blindly subtracting `event_post_seconds`.
    """
    was_clamped = event.duration_seconds >= (max_event_seconds - 0.5)
    if not was_clamped:
        inferred = max(
            event.trigger_timestamp,
            event.end_timestamp - event_post_seconds,
        )
        if peak_chat_ts is not None:
            inferred = max(inferred, peak_chat_ts)
        return inferred, False

    # Clamped merged event: anchor to peak (or trigger) and latest message time.
    candidates = [event.trigger_timestamp]
    if peak_chat_ts is not None:
        candidates.append(peak_chat_ts)
    if event.messages:
        # Use 90th-percentile message timestamp to avoid trailing stragglers.
        ts_sorted = sorted(m.timestamp for m in event.messages)
        p90 = ts_sorted[int(0.9 * (len(ts_sorted) - 1))]
        candidates.append(p90)
    return max(candidates), True


class ClipWindowPlanner:
    """Computes approximate video clip bounds from a finalized `HighlightEvent`."""

    def __init__(
        self,
        config: RecordingConfig,
        *,
        event_post_seconds: float = 20.0,
        max_event_seconds: float = 120.0,
    ) -> None:
        self._config = config
        self._event_post_seconds = max(0.0, event_post_seconds)
        self._max_event_seconds = max(1.0, max_event_seconds)

    def plan(self, event: HighlightEvent) -> ClipWindowPlan:
        """Compute a `ClipWindowPlan` for `event`."""
        cfg = self._config
        notes: list[str] = ["reaction_offset_is_heuristic"]

        # Only consider messages on/after trigger when locating peak reaction,
        # falling back to all messages if none are >= trigger.
        post_trigger_msgs = [
            m for m in event.messages if m.timestamp >= event.trigger_timestamp - 2.0
        ]
        peak_chat_ts = find_peak_chat_timestamp(
            post_trigger_msgs if post_trigger_msgs else event.messages
        )

        # Heuristic moment start derived from first trigger.
        trigger_moment = event.trigger_timestamp - cfg.reaction_offset_seconds
        peak_moment = (
            peak_chat_ts - cfg.reaction_offset_seconds
            if peak_chat_ts is not None
            else trigger_moment
        )

        if event.merged_count <= 0:
            moment_start = trigger_moment
            moment_end = trigger_moment
            clip_start = moment_start - cfg.clip_pre_seconds
            clip_end = moment_end + cfg.clip_post_seconds
            truncated = False
        else:
            active_end_chat, was_clamped = _estimate_active_chat_end(
                event,
                peak_chat_ts=peak_chat_ts,
                event_post_seconds=self._event_post_seconds,
                max_event_seconds=self._max_event_seconds,
            )
            if was_clamped:
                notes.append("merged_event_hit_max_event_seconds")

            moment_start = trigger_moment
            moment_end = max(trigger_moment, active_end_chat - cfg.reaction_offset_seconds)
            full_start = moment_start - cfg.clip_pre_seconds
            full_end = moment_end + cfg.clip_post_seconds

            if (full_end - full_start) <= cfg.clip_max_duration_seconds:
                clip_start = full_start
                clip_end = full_end
                truncated = False
                notes.append("merged_event_fit_within_max_duration")
            else:
                clip_start, clip_end, cap_note = self._select_peak_window(
                    full_start=full_start,
                    full_end=full_end,
                    trigger_moment=trigger_moment,
                    peak_moment=peak_moment,
                )
                truncated = True
                notes.append(cap_note)

        # Final safety cap (e.g., if pre + post alone exceeded max_duration).
        if (clip_end - clip_start) > cfg.clip_max_duration_seconds:
            anchor = peak_moment if event.merged_count > 0 else trigger_moment
            half_pre = cfg.clip_max_duration_seconds * 0.4
            half_post = cfg.clip_max_duration_seconds - half_pre
            clip_start = anchor - half_pre
            clip_end = anchor + half_post
            truncated = True
            notes.append("clamped_pre_post_to_max_duration")

        return ClipWindowPlan(
            event_id=event.event_id,
            stream_id=event.stream_id,
            streamer_name=event.streamer_name,
            event_start_timestamp=event.start_timestamp,
            event_trigger_timestamp=event.trigger_timestamp,
            event_end_timestamp=event.end_timestamp,
            event_merged_count=event.merged_count,
            peak_chat_timestamp=peak_chat_ts,
            reaction_offset_seconds=cfg.reaction_offset_seconds,
            estimated_moment_start=moment_start,
            estimated_moment_end=moment_end,
            requested_clip_start=clip_start,
            requested_clip_end=clip_end,
            truncated_to_peak=truncated,
            planning_notes=notes,
        )

    def _select_peak_window(
        self,
        *,
        full_start: float,
        full_end: float,
        trigger_moment: float,
        peak_moment: float,
    ) -> tuple[float, float, str]:
        """Choose the highest-value bounded window when a merged event exceeds max duration.

        Strategy:
        1. If both the initiating moment (`trigger_moment - pre`) and the peak
           reaction (`peak_moment + post`) fit within `clip_max_duration_seconds`,
           keep both by starting at `trigger_moment - pre`.
        2. Otherwise, center the capped window around `peak_moment` (40% buildup
           before peak, 60% aftermath/reaction after peak), clamped inside
           `[full_start, full_end]`.
        """
        cfg = self._config
        max_dur = cfg.clip_max_duration_seconds

        trigger_start = trigger_moment - cfg.clip_pre_seconds
        peak_end = peak_moment + cfg.clip_post_seconds
        if peak_moment >= trigger_moment and (peak_end - trigger_start) <= max_dur:
            clip_start = trigger_start
            clip_end = min(full_end, clip_start + max_dur)
            return clip_start, clip_end, "preserved_trigger_and_peak_within_cap"

        # Center around peak_moment (40% buildup, 60% aftermath).
        pre_share = max_dur * 0.40
        post_share = max_dur - pre_share
        clip_start = peak_moment - pre_share
        clip_end = peak_moment + post_share

        # Clamp within [full_start, full_end] while preserving max_dur width.
        if clip_end > full_end:
            clip_end = full_end
            clip_start = max(full_start, clip_end - max_dur)
        if clip_start < full_start:
            clip_start = full_start
            clip_end = min(full_end, clip_start + max_dur)

        return clip_start, clip_end, "centered_on_peak_chat_activity"

"""System status and highlights routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from app.api.state import supervisor
from app.config import load_settings
from app.discovery.youtube_finder import resolve_channel_name

router = APIRouter(tags=["Status"])


@router.get("/status")
def get_system_status() -> dict[str, Any]:
    """Return the current monitoring status, active channels, disk usage, and Gemini stats."""
    return supervisor.get_status()


@router.get("/highlights")
def get_recent_highlights(
    limit: int = Query(default=50, ge=1, le=200),
    min_score: int | None = Query(default=None, ge=0, le=100),
) -> list[dict[str, Any]]:
    """Return the log of recently analyzed highlight events from highlights.jsonl."""
    settings = load_settings()
    highlights_file = settings.highlights_output_path

    if not highlights_file or not Path(highlights_file).is_file():
        return []

    results: list[dict[str, Any]] = []
    try:
        with open(highlights_file, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        # Iterate in reverse to get most recent first
        for line in reversed(lines):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                record = json.loads(line_str)
            except Exception:
                continue

            if min_score is not None:
                score = record.get("highlight_score", 0)
                if score < min_score:
                    continue

            if "streamer_name" in record:
                record["streamer_name"] = resolve_channel_name(record["streamer_name"])
            if "stream_id" in record and record["stream_id"]:
                record["stream_id"] = resolve_channel_name(record["stream_id"])

            results.append(record)
            if len(results) >= limit:
                break
    except Exception:
        return []

    return results

"""Configuration and .env management routes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, status

from app.config import _BACKEND_DIR, load_settings

router = APIRouter(tags=["Settings"])

ENV_PATH = _BACKEND_DIR / ".env"


def _read_env_file() -> dict[str, str]:
    """Read .env file directly into a dictionary."""
    if not ENV_PATH.is_file():
        return {}
    try:
        return {k: v or "" for k, v in dotenv_values(ENV_PATH).items()}
    except OSError:
        return {}


def _write_env_file(updates: dict[str, Any]) -> None:
    """Update existing keys or append new keys to the .env file cleanly."""
    lines: list[str] = []
    if ENV_PATH.is_file():
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

    updated_keys = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            val = str(updates[key])
            new_lines.append(f"{key}={val}\n")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append any remaining keys that were not present previously
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    # Write updates directly to .env to preserve inode across Docker bind mounts
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    # Update in-memory os.environ so load_settings picks up new values
    for k, v in updates.items():
        os.environ[k] = str(v)


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """Return the current system configuration settings."""
    cfg = load_settings()
    env_raw = _read_env_file()

    return {
        "platform": env_raw.get("MONITOR_PLATFORM") or cfg.monitor_platform,
        "target_channels": env_raw.get("TARGET_CHANNELS") or ",".join(cfg.target_channels),
        "excluded_target_channels": env_raw.get("EXCLUDED_TARGET_CHANNELS") or ",".join(cfg.excluded_target_channels),
        "target_youtube_channels": env_raw.get("TARGET_YOUTUBE_CHANNELS") or ",".join(cfg.target_youtube_channels),
        "excluded_youtube_channels": env_raw.get("EXCLUDED_YOUTUBE_CHANNELS") or ",".join(cfg.excluded_youtube_channels),
        "target_languages": env_raw.get("TARGET_LANGUAGES") or ",".join(cfg.target_languages),
        "multi_stream_enabled": env_raw.get("MULTI_STREAM_ENABLED", "false").lower() == "true",
        "max_streams": int(env_raw.get("MAX_STREAMS") or cfg.max_streams),
        "recording_enabled": env_raw.get("RECORDING_ENABLED", "true").lower() == "true",
        "recording_quality": env_raw.get("RECORDING_QUALITY") or cfg.recording_quality,
        "shorts_engine": env_raw.get("SHORTS_ENGINE") or cfg.shorts_engine,
        "subtitle_style": env_raw.get("OPENSHORTS_SUBTITLE_STYLE") or cfg.openshorts_subtitle_style,
        "reframe_mode": env_raw.get("OPENSHORTS_REFRAME_MODE") or cfg.openshorts_reframe_mode,
        "hook_overlay_enabled": env_raw.get("HOOK_OVERLAY_ENABLED", "false").lower() == "true",
        "youtube_privacy_status": env_raw.get("YOUTUBE_PRIVACY_STATUS") or cfg.youtube_privacy_status,
        "youtube_min_score": int(env_raw.get("YOUTUBE_MIN_SCORE") or cfg.youtube_min_score),
        "youtube_daily_limit": int(env_raw.get("YOUTUBE_DAILY_LIMIT") or cfg.youtube_daily_limit),
        "youtube_auto_upload": env_raw.get("YOUTUBE_AUTO_UPLOAD", "false").lower() == "true",
        "viral_metadata_enabled": env_raw.get("VIRAL_METADATA_ENABLED", "true").lower() == "true",
        "gemini_model": env_raw.get("GEMINI_MODEL") or cfg.gemini_model,
        "has_gemini_key": bool(cfg.gemini_api_key),
        "has_youtube_credentials": bool(cfg.youtube_client_id and cfg.youtube_client_secret),
        "has_twitch_credentials": bool(cfg.client_id and cfg.client_secret),
    }


@router.post("/settings")
def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Update settings and persist them to the .env configuration file."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must be a JSON object.")

    env_map = {
        "platform": "MONITOR_PLATFORM",
        "target_channels": "TARGET_CHANNELS",
        "excluded_target_channels": "EXCLUDED_TARGET_CHANNELS",
        "target_youtube_channels": "TARGET_YOUTUBE_CHANNELS",
        "excluded_youtube_channels": "EXCLUDED_YOUTUBE_CHANNELS",
        "target_languages": "TARGET_LANGUAGES",
        "multi_stream_enabled": "MULTI_STREAM_ENABLED",
        "max_streams": "MAX_STREAMS",
        "recording_enabled": "RECORDING_ENABLED",
        "recording_quality": "RECORDING_QUALITY",
        "shorts_engine": "SHORTS_ENGINE",
        "subtitle_style": "OPENSHORTS_SUBTITLE_STYLE",
        "reframe_mode": "OPENSHORTS_REFRAME_MODE",
        "hook_overlay_enabled": "HOOK_OVERLAY_ENABLED",
        "youtube_privacy_status": "YOUTUBE_PRIVACY_STATUS",
        "youtube_min_score": "YOUTUBE_MIN_SCORE",
        "youtube_daily_limit": "YOUTUBE_DAILY_LIMIT",
        "youtube_auto_upload": "YOUTUBE_AUTO_UPLOAD",
        "viral_metadata_enabled": "VIRAL_METADATA_ENABLED",
        "gemini_model": "GEMINI_MODEL",
        "gemini_api_key": "GEMINI_API_KEY",
    }

    env_updates: dict[str, str] = {}
    for key, val in payload.items():
        if key in env_map:
            env_key = env_map[key]
            if isinstance(val, bool):
                env_updates[env_key] = "true" if val else "false"
            else:
                env_updates[env_key] = str(val)

    if env_updates:
        try:
            _write_env_file(env_updates)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save settings: {exc}",
            ) from exc

    return get_settings()

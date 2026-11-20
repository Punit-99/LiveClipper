"""OpenShorts bridge and vertical video reframing engine.

Provides dual-engine vertical 9:16 processing for LiveClipper highlights:
1. OpenShorts Engine (Docker / Subprocess): Advanced scene-aware reframing & subtitle burning.
2. Native FFmpeg Engine (Built-in Fallback): Pure FFmpeg blur-padded 9:16 reframing
   with top-third hook title overlay. Zero extra Python or ML dependencies.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger
from app.shorts.duration_adapter import adapt_clip_duration, probe_video_duration
from app.shorts.text_overlay import create_hook_banner_bmp

log = get_logger(__name__)

SUPPORTED_ENGINES = ("auto", "openshorts", "ffmpeg_fallback")


class OpenShortsBridgeError(Exception):
    """Base error for OpenShorts bridge operations."""


class EngineUnavailableError(OpenShortsBridgeError):
    """Requested engine is not available in the environment."""


class OpenShortsBridge:
    """Orchestrates duration adaptation, vertical reframing, and hook overlay."""

    def __init__(
        self,
        output_dir: Path | str,
        engine: str = "auto",
        max_duration: float = 59.0,
        target_duration: float = 55.0,
        openshorts_dir: Path | str | None = None,
        docker_image: str = "liveclipper/openshorts:latest",
        docker_timeout: float = 600.0,
        subtitle_style: str = "bold",
        reframe_mode: str = "split",
        output_bitrate: str = "6M",
        output_crf: int = 18,
        hook_overlay_enabled: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.engine = str(engine or "auto").strip().lower()
        if self.engine not in SUPPORTED_ENGINES:
            raise ValueError(f"Invalid engine '{engine}'. Must be one of {SUPPORTED_ENGINES}.")

        self.max_duration = float(max_duration)
        self.target_duration = float(target_duration)
        self.openshorts_dir = Path(openshorts_dir) if openshorts_dir else Path("openshorts")
        self.docker_image = docker_image
        self.docker_timeout = float(docker_timeout)
        self.subtitle_style = str(subtitle_style or "bold").strip().lower()
        self.reframe_mode = str(reframe_mode or "auto").strip().lower()
        self.output_bitrate = str(output_bitrate or "6M").strip()
        self.output_crf = int(output_crf)
        self.hook_overlay_enabled = bool(hook_overlay_enabled)

    def is_docker_available(self) -> bool:
        """Check if Docker CLI is installed and the target image exists locally."""
        docker_path = shutil.which("docker")
        if not docker_path:
            return False
        try:
            res = subprocess.run(
                ["docker", "image", "inspect", self.docker_image],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            return res.returncode == 0
        except Exception:
            return False

    def is_openshorts_native_available(self) -> bool:
        """Check if internal video processing package is available."""
        try:
            import app.video
            return True
        except ImportError:
            return False

    def can_use_openshorts(self) -> bool:
        """True if either Docker or internal video processing package is functional."""
        return self.is_openshorts_native_available() or self.is_docker_available()

    def process_clip(
        self,
        clip_path: Path | str,
        sidecar_path_or_data: Path | str | dict[str, Any] | None = None,
        channel: str | None = None,
        progress_callback: Any = None,
    ) -> tuple[Path, Path, dict[str, Any]]:
        """Process a horizontal LiveClipper MP4 clip into a 9:16 vertical Short.

        Returns:
            (short_video_path, short_sidecar_path, metadata)
        """
        src_path = Path(clip_path)
        if not src_path.is_file():
            raise FileNotFoundError(f"Source clip not found: {src_path}")

        sidecar = self._load_sidecar(sidecar_path_or_data, src_path)

        # 1. Resolve channel name and platform
        channel_name = channel or sidecar.get("streamer_name")
        platform = sidecar.get("platform")
        if not platform:
            if src_path.parent.parent.name in ("twitch", "youtube"):
                platform = src_path.parent.parent.name
            else:
                platform = "twitch"

        if not channel_name:
            # Infer from directory structure: clips/[platform/]<channel>/<clip_id>.mp4
            channel_name = src_path.parent.name if src_path.parent.name not in ("clips", "twitch", "youtube") else "streamer"
        channel_name = channel_name.lstrip("#").strip().lower()

        # 2. Setup destination paths: shorts/<channel>/<clip_id>_short.mp4
        clip_id = sidecar.get("clip_id") or self._extract_clip_id(src_path.stem)
        channel_out_dir = self.output_dir / channel_name
        channel_out_dir.mkdir(parents=True, exist_ok=True)

        short_video_path = channel_out_dir / f"{clip_id}_short.mp4"
        short_sidecar_path = channel_out_dir / f"{clip_id}_short.json"

        # 3. Duration adaptation (trim if > 60s)
        temp_dir = channel_out_dir / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            try:
                progress_callback("trimming", 15)
            except Exception:
                pass

        adapted_path, was_trimmed, duration_meta = adapt_clip_duration(
            clip_path=src_path,
            sidecar_path_or_data=sidecar,
            max_duration=self.max_duration,
            target_duration=self.target_duration,
            output_dir=temp_dir,
        )

        # 4. Resolve Hook Title Text from sidecar analysis
        analysis = sidecar.get("analysis") or {}
        hook_title = analysis.get("title") or "Stream Highlight"

        # 5. Engine selection and rendering
        engine_used = self._determine_engine()
        log.info(
            "Rendering vertical Short using engine '%s' for clip %s (duration=%.1fs, channel=%s)",
            engine_used,
            src_path.name,
            duration_meta.get("final_duration", 0.0),
            channel_name,
        )

        try:
            if engine_used == "openshorts":
                if progress_callback is not None:
                    try:
                        self._render_with_openshorts(adapted_path, short_video_path, hook_title, progress_callback=progress_callback)
                    except TypeError:
                        self._render_with_openshorts(adapted_path, short_video_path, hook_title)
                else:
                    self._render_with_openshorts(adapted_path, short_video_path, hook_title)
            else:
                if progress_callback is not None:
                    try:
                        self._render_with_ffmpeg(adapted_path, short_video_path, hook_title, temp_dir, progress_callback=progress_callback)
                    except TypeError:
                        self._render_with_ffmpeg(adapted_path, short_video_path, hook_title, temp_dir)
                else:
                    self._render_with_ffmpeg(adapted_path, short_video_path, hook_title, temp_dir)
        except Exception as exc:
            # If auto mode attempted OpenShorts and failed, fallback to FFmpeg
            if self.engine == "auto" and engine_used == "openshorts":
                log.warning(
                    "OpenShorts render failed (%s); falling back to native FFmpeg engine...",
                    exc,
                )
                engine_used = "ffmpeg_fallback"
                if progress_callback is not None:
                    try:
                        self._render_with_ffmpeg(adapted_path, short_video_path, hook_title, temp_dir, progress_callback=progress_callback)
                    except TypeError:
                        self._render_with_ffmpeg(adapted_path, short_video_path, hook_title, temp_dir)
                else:
                    self._render_with_ffmpeg(adapted_path, short_video_path, hook_title, temp_dir)
            else:
                raise

        # 6. Verify output file exists and is valid
        if not short_video_path.is_file() or short_video_path.stat().st_size <= 0:
            raise OpenShortsBridgeError(f"Rendering failed: output missing at {short_video_path}")

        final_duration = probe_video_duration(short_video_path)

        # 7. Construct and write updated sidecar JSON
        updated_sidecar = dict(sidecar)
        updated_sidecar["clip_id"] = clip_id
        updated_sidecar["streamer_name"] = channel_name
        updated_sidecar["platform"] = platform
        updated_sidecar["short_video_path"] = str(short_video_path.resolve())
        updated_sidecar["processing"] = {
            "engine": engine_used,
            "processed_at_iso": datetime.now(timezone.utc).isoformat(),
            "original_clip_path": str(src_path.resolve()),
            "was_trimmed": was_trimmed,
            "duration_metadata": duration_meta,
            "final_duration_seconds": final_duration,
            "output_format": "9:16 vertical (1080x1920)",
            "hook_title": hook_title,
            "subtitle_style": self.subtitle_style if engine_used == "openshorts" else None,
            "reframe_mode": self.reframe_mode if engine_used == "openshorts" else None,
            "docker_image": self.docker_image if engine_used == "openshorts" else None,
        }

        with open(short_sidecar_path, "w", encoding="utf-8") as fh:
            json.dump(updated_sidecar, fh, indent=2)

        # 8. Clean up intermediate trimmed file if generated
        if was_trimmed and adapted_path.is_file() and adapted_path != src_path:
            try:
                adapted_path.unlink()
            except OSError:
                pass

        try:
            if temp_dir.exists() and not any(temp_dir.iterdir()):
                temp_dir.rmdir()
        except OSError:
            pass

        log.info(
            "Short processing complete: %s (duration=%.1fs, engine=%s)",
            short_video_path.name,
            final_duration,
            engine_used,
        )

        return short_video_path, short_sidecar_path, updated_sidecar["processing"]

    def _determine_engine(self) -> str:
        """Resolve which engine to use based on configuration and host availability."""
        if self.engine == "ffmpeg_fallback":
            return "ffmpeg_fallback"
        if self.engine == "openshorts":
            if not self.can_use_openshorts():
                raise EngineUnavailableError(
                    "SHORTS_ENGINE is 'openshorts' but neither Docker nor an OpenShorts "
                    "environment is functional."
                )
            return "openshorts"
        # engine == "auto"
        if self.can_use_openshorts():
            return "openshorts"
        return "ffmpeg_fallback"

    def _render_with_ffmpeg(
        self,
        input_video: Path,
        output_video: Path,
        hook_title: str,
        temp_dir: Path,
        progress_callback: Any = None,
    ) -> None:
        """Render 9:16 vertical video using native FFmpeg filtergraphs.

        Applies:
        - Scaled & blurred 9:16 background filler from the 16:9 source.
        - Sharp 16:9 foreground video centered vertically.
        - Hook banner title overlay for the first 4.0 seconds (if hook_overlay_enabled).
        """
        if progress_callback:
            try:
                progress_callback("reframing", 40)
            except Exception:
                pass

        # Create hook banner image if enabled
        wants_hook = bool(self.hook_overlay_enabled and hook_title and hook_title.strip())
        banner_path = temp_dir / f"banner_{input_video.stem}.bmp"
        if wants_hook:
            create_hook_banner_bmp(
                text=hook_title,
                output_path=banner_path,
                width=960,
                height=200,
                scale=4,
            )
            filter_complex = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,boxblur=20:5[bg];"
                "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
                "[base][1:v]overlay=(W-w)/2:220:enable='between(t,0,4)'[v_out]"
            )
            extra_inputs = ["-i", str(banner_path)]
        else:
            filter_complex = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,boxblur=20:5[bg];"
                "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[v_out]"
            )
            extra_inputs = []

        if progress_callback:
            try:
                progress_callback("hooks", 70)
                progress_callback("encoding", 85)
            except Exception:
                pass

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            *extra_inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[v_out]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            str(self.output_crf),
            "-b:v",
            self.output_bitrate,
            "-maxrate",
            "8M",
            "-bufsize",
            "12M",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_video),
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120.0)
            if progress_callback:
                try:
                    progress_callback("encoding", 100)
                except Exception:
                    pass
        except subprocess.SubprocessError as exc:
            log.error("FFmpeg vertical reframing failed: %s", exc)
            raise OpenShortsBridgeError(f"FFmpeg vertical render failed: {exc}") from exc
        finally:
            if banner_path.is_file():
                try:
                    banner_path.unlink()
                except OSError:
                    pass

    def _render_with_openshorts(
        self,
        input_video: Path,
        output_video: Path,
        hook_title: str,
        progress_callback: Any = None,
    ) -> None:
        """Invoke OpenShorts via Docker container or native subprocess."""
        if self.is_docker_available():
            # Docker execution
            input_dir = input_video.parent.resolve()
            output_dir = output_video.parent.resolve()
            cmd = [
                "docker",
                "run",
                "--rm",
                "-e",
                f"AUTO_HOOK={1 if self.hook_overlay_enabled else 0}",
                "-e",
                f"VIRAL_HOOK_TEXT={hook_title}",
                "-e",
                "AUTO_HOOK_STYLE=pill",
                "-e",
                "AUTO_CAPTIONS=1",
                "-e",
                "DELIVERY_MIN_WIDTH=1080",
                "-e",
                f"OUTPUT_CRF={self.output_crf}",
                "-e",
                f"OUTPUT_BITRATE={self.output_bitrate}",
                "-v",
                f"{input_dir}:/input:ro",
                "-v",
                f"{output_dir}:/output",
                "-v",
                "liveclipper_hf_cache:/app/.cache/huggingface",
                self.docker_image,
                "python",
                "main.py",
                "-i",
                f"/input/{input_video.name}",
                "-o",
                f"/output/{output_video.name}",
                "--skip-analysis",
                "--hook-text",
                hook_title,
                "--subtitle-style",
                self.subtitle_style,
                "--reframe-mode",
                self.reframe_mode,
            ]

            log.info(
                "Executing OpenShorts (docker) for %s (style=%s, reframe=%s)",
                input_video.name,
                self.subtitle_style,
                self.reframe_mode,
            )

            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.docker_timeout,
                )
                if res.returncode != 0:
                    err_detail = (res.stderr or res.stdout or "").strip()
                    log.error("OpenShorts execution failed (exit %d): %s", res.returncode, err_detail[-500:])
                    raise OpenShortsBridgeError(
                        f"OpenShorts execution exited with code {res.returncode}: {err_detail[-300:]}"
                    )
                log.info("OpenShorts execution succeeded for %s", output_video.name)
            except subprocess.TimeoutExpired as exc:
                log.error("OpenShorts execution timed out after %.1fs", self.docker_timeout)
                raise OpenShortsBridgeError(f"OpenShorts timed out after {self.docker_timeout}s") from exc
            except subprocess.SubprocessError as exc:
                log.error("OpenShorts execution failed: %s", exc)
                raise OpenShortsBridgeError(f"OpenShorts execution failed: {exc}") from exc
        else:
            # Internal video module execution (no Docker required)
            log.info(
                "Executing internal video pipeline for %s (style=%s, reframe=%s)",
                input_video.name,
                self.subtitle_style,
                self.reframe_mode,
            )
            self._render_with_internal_video(input_video, output_video, hook_title, progress_callback=progress_callback)

    def _render_with_internal_video(
        self,
        input_video: Path,
        output_video: Path,
        hook_title: str,
        progress_callback: Any = None,
    ) -> None:
        """Render vertical Short using internal app.video package."""
        import tempfile
        from app.video import (
            AUTO_CAPTION_STYLE,
            CAPTION_PRESETS,
            add_hook_to_video,
            burn_subtitles,
            generate_ass,
            reframe_video,
            subtitles_filter,
            transcribe_media,
        )

        with tempfile.TemporaryDirectory(prefix="shorts_render_") as tmpdir:
            temp_dir = Path(tmpdir)
            framed_path = temp_dir / f"framed_{input_video.name}"

            # 1. Vertical 9:16 reframing
            if progress_callback:
                try:
                    progress_callback("reframing", 35)
                except Exception:
                    pass
            reframe_video(
                input_video=input_video,
                output_video=framed_path,
                mode=self.reframe_mode,
                output_crf=self.output_crf,
                output_bitrate=self.output_bitrate,
            )

            # 2. Transcribe & Subtitles
            wants_captions = self.subtitle_style != "none"
            transcript = None
            if wants_captions:
                if progress_callback:
                    try:
                        progress_callback("transcribing", 55)
                    except Exception:
                        pass
                try:
                    transcript = transcribe_media(input_video)
                except Exception as exc:
                    log.warning("Transcription unavailable (%s); proceeding without captions", exc)

            ass_path = None
            if transcript and transcript.get("segments"):
                if progress_callback:
                    try:
                        progress_callback("subtitles", 75)
                    except Exception:
                        pass
                ass_path = temp_dir / "captions.ass"
                preset = CAPTION_PRESETS.get(self.subtitle_style, AUTO_CAPTION_STYLE)
                dur = probe_video_duration(framed_path)
                sub_alignment = "middle" if self.reframe_mode == "split" else "bottom"
                generate_ass(
                    transcript=transcript,
                    clip_start=0.0,
                    clip_end=dur,
                    output_path=ass_path,
                    alignment=sub_alignment,
                    fontsize=preset.get("font_size", 40),
                    font_name=preset.get("font_name", "Anton"),
                    font_color=preset.get("font_color", "#FFFFFF"),
                    border_color=preset.get("border_color", "#000000"),
                    border_width=preset.get("border_width", 2),
                    highlight_color=preset.get("highlight_color", "#FFE500"),
                    effect=preset.get("effect", "pop"),
                    base_opacity=preset.get("base_opacity", 1.0),
                    uppercase=preset.get("uppercase", True),
                    shadow=preset.get("shadow", 0),
                    reveal=preset.get("reveal", False),
                )

            # 3. Hook Overlay & Subtitle Burning
            wants_hook = bool(self.hook_overlay_enabled and hook_title and hook_title.strip())
            curr_video = framed_path

            if progress_callback:
                try:
                    progress_callback("hooks", 90)
                except Exception:
                    pass

            if wants_hook and ass_path:
                try:
                    vf = subtitles_filter(ass_path)
                    add_hook_to_video(
                        clip_path=curr_video,
                        text=hook_title,
                        out_path=output_video,
                        style="pill",
                        also=(vf, output_video),
                        output_crf=self.output_crf,
                        output_bitrate=self.output_bitrate,
                    )
                except Exception as s_exc:
                    log.warning("Subtitle burn failed (%s); proceeding with hook banner only", s_exc)
                    add_hook_to_video(
                        clip_path=curr_video,
                        text=hook_title,
                        out_path=output_video,
                        style="pill",
                        output_crf=self.output_crf,
                        output_bitrate=self.output_bitrate,
                    )
            elif wants_hook:
                add_hook_to_video(
                    clip_path=curr_video,
                    text=hook_title,
                    out_path=output_video,
                    style="pill",
                    output_crf=self.output_crf,
                    output_bitrate=self.output_bitrate,
                )
            elif ass_path:
                try:
                    burn_subtitles(
                        clip_path=curr_video,
                        ass_path=ass_path,
                        out_path=output_video,
                        output_crf=self.output_crf,
                        output_bitrate=self.output_bitrate,
                    )
                except Exception as s_exc:
                    log.warning("Subtitle burn failed (%s); copying reframed video", s_exc)
                    shutil.copyfile(framed_path, output_video)
            else:
                shutil.copyfile(framed_path, output_video)

            if progress_callback:
                try:
                    progress_callback("encoding", 100)
                except Exception:
                    pass

    def _load_sidecar(
        self,
        sidecar_path_or_data: Path | str | dict[str, Any] | None,
        clip_path: Path,
    ) -> dict[str, Any]:
        """Load sidecar data from dict, explicit path, or adjacent JSON."""
        if isinstance(sidecar_path_or_data, dict):
            return dict(sidecar_path_or_data)

        if sidecar_path_or_data:
            p = Path(sidecar_path_or_data)
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        return json.load(fh)
                except Exception as exc:
                    log.warning("Could not load sidecar %s: %s", p, exc)

        # Check adjacent file: <clip_stem>.json
        adjacent = clip_path.with_suffix(".json")
        if adjacent.is_file():
            try:
                with open(adjacent, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:
                log.warning("Could not load adjacent sidecar %s: %s", adjacent, exc)

        # Fallback minimal sidecar
        return {
            "clip_id": self._extract_clip_id(clip_path.stem),
            "streamer_name": "streamer",
            "analysis": {"title": "Live Stream Highlight"},
        }

    @staticmethod
    def _extract_clip_id(stem: str) -> str:
        """Extract or fallback clip ID from filename (e.g. 20261118_182427_ce5e4f32_funny)."""
        parts = stem.split("_")
        if len(parts) >= 3 and len(parts[2]) in (8, 32):
            return parts[2]
        return stem

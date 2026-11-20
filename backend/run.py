#!/usr/bin/env python3
"""LiveClipper Universal CLI Runner.

Single cross-platform entry point for Windows, macOS, and Linux.
Usage:
    python run.py <command> [options]
    ./run.sh <command> [options]      (macOS / Linux)
    run <command> [options]           (Windows)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent

# Ensure backend directory is in PYTHONPATH and sys.path
os.environ["PYTHONPATH"] = str(BACKEND_DIR)
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _supports_color() -> bool:
    """Check if the terminal environment supports ANSI escape color codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            mode = ctypes.c_ulong()
            h_out = kernel32.GetStdHandle(-11)
            if kernel32.GetConsoleMode(h_out, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h_out, mode.value | 0x0004)
                return True
        except Exception:
            return False
    return True


USE_COLOR = _supports_color()


class C:
    """Terminal colors and styling."""

    GREEN = "\033[92m" if USE_COLOR else ""
    RED = "\033[91m" if USE_COLOR else ""
    YELLOW = "\033[93m" if USE_COLOR else ""
    CYAN = "\033[96m" if USE_COLOR else ""
    BLUE = "\033[94m" if USE_COLOR else ""
    MAGENTA = "\033[95m" if USE_COLOR else ""
    BOLD = "\033[1m" if USE_COLOR else ""
    DIM = "\033[2m" if USE_COLOR else ""
    RESET = "\033[0m" if USE_COLOR else ""


def find_python_interpreter(backend_dir: Path | None = None) -> Path | None:
    """Locate the project virtualenv Python executable across Windows, Mac, and Linux.

    Priority:
    1. Already activated virtual environment (sys.prefix != sys.base_prefix).
    2. Local venv/ or .venv/ directory in the project root.
    """
    # 1. Detect if the user is ALREADY inside an activated virtual environment
    if sys.prefix != sys.base_prefix:
        return Path(sys.executable).absolute()

    root = Path(backend_dir) if backend_dir else BACKEND_DIR
    candidates: list[Path] = []

    # 2. Check for both venv/ and .venv/ directory structures
    for folder in ("venv", ".venv"):
        venv_dir = root / folder
        if sys.platform == "win32":
            candidates.extend([
                venv_dir / "Scripts" / "python.exe",
                venv_dir / "python.exe",
            ])
        else:
            candidates.extend([
                venv_dir / "bin" / "python3",
                venv_dir / "bin" / "python",
            ])

    for cand in candidates:
        if cand.is_file():
            return cand.absolute()

    return None


@dataclass
class CommandPlan:
    """Details of the command to be executed."""

    command: str
    cmd_args: list[str] = field(default_factory=list)
    description: str = ""
    is_internal: bool = False
    dry_run: bool = False


def build_command(
    raw_args: list[str],
    backend_dir: Path | None = None,
    python_bin: Path | None = None,
) -> CommandPlan:
    """Parse user arguments and map them to the appropriate underlying CLI script.

    Pure function for testability.
    """
    root = Path(backend_dir) if backend_dir else BACKEND_DIR
    py = str(python_bin) if python_bin else sys.executable

    # Check for --dry-run anywhere in the arguments
    dry_run = "--dry-run" in raw_args
    filtered_args = [a for a in raw_args if a != "--dry-run"]

    if not filtered_args or filtered_args[0] in ("help", "--help", "-h"):
        return CommandPlan(
            command="help",
            is_internal=True,
            description="Display help and usage instructions",
            dry_run=dry_run,
        )

    cmd_name = filtered_args[0].lower()
    sub_args = filtered_args[1:]

    if cmd_name == "status":
        return CommandPlan(
            command="status",
            is_internal=True,
            description="Show .env configuration status",
            dry_run=dry_run,
        )

    if cmd_name == "monitor":
        # Monitor live YouTube streams
        cmd = [py, str(root / "monitor_streams.py")]
        extra: list[str] = ["--platform", "youtube"]

        for a in sub_args:
            if a in ("--yt", "--youtube"):
                continue  # Default and only platform
            extra.append(a)

        return CommandPlan(
            command="monitor",
            cmd_args=cmd + extra,
            description="Monitor live YouTube streams with LiveClipper",
            dry_run=dry_run,
        )

    if cmd_name == "process":
        cmd = [py, str(root / "process_shorts.py")]
        extra = []
        has_engine = False

        for a in sub_args:
            if a == "--fast":
                extra.extend(["--engine", "ffmpeg_fallback"])
                has_engine = True
            else:
                if a == "--engine":
                    has_engine = True
                extra.append(a)

        # Default engine: openshorts
        if not has_engine:
            cmd.extend(["--engine", "openshorts"])

        return CommandPlan(
            command="process",
            cmd_args=cmd + extra,
            description="Process clips into vertical YouTube Shorts",
            dry_run=dry_run,
        )

    if cmd_name == "list":
        cmd = [py, str(root / "upload_shorts.py"), "--list"] + sub_args
        return CommandPlan(
            command="list",
            cmd_args=cmd,
            description="List processed shorts eligible for YouTube upload",
            dry_run=dry_run,
        )

    if cmd_name == "upload":
        cmd = [py, str(root / "upload_shorts.py")]
        extra = []
        privacy = "public"

        for a in sub_args:
            if a == "--unlisted":
                privacy = "unlisted"
            elif a == "--public":
                privacy = "public"
            else:
                extra.append(a)

        if "--privacy" not in extra:
            cmd.extend(["--privacy", privacy])

        return CommandPlan(
            command="upload",
            cmd_args=cmd + extra,
            description="Upload processed shorts to YouTube",
            dry_run=dry_run,
        )

    if cmd_name == "test":
        # If specific test target(s) are provided, run unittest directly without 'discover -s tests'
        has_target = any(not a.startswith("-") for a in sub_args)
        if has_target:
            cleaned_args = [
                a[len("backend/") :] if a.startswith("backend/") else a
                for a in sub_args
            ]
            cmd = [py, "-m", "unittest"] + cleaned_args
        else:
            cmd = [py, "-m", "unittest", "discover", "-s", "tests"] + sub_args
        return CommandPlan(
            command="test",
            cmd_args=cmd,
            description="Run test suite",
            dry_run=dry_run,
        )

    if cmd_name == "auth":
        cmd = [py, str(root / "authorize_youtube.py")] + sub_args
        return CommandPlan(
            command="auth",
            cmd_args=cmd,
            description="Run YouTube OAuth authorization flow",
            dry_run=dry_run,
        )

    if cmd_name == "server":
        cmd = [py, str(root / "server.py")] + sub_args
        return CommandPlan(
            command="server",
            cmd_args=cmd,
            description="Start the LiveClipper Dashboard backend API server",
            dry_run=dry_run,
        )

    if cmd_name == "docker":
        cmd = ["docker", "compose", "up", "--build"] + sub_args
        return CommandPlan(
            command="docker",
            cmd_args=cmd,
            description="Build and run the entire LiveClipper stack in Docker Compose",
            dry_run=dry_run,
        )

    if cmd_name == "docker-down":
        cmd = ["docker", "compose", "down"] + sub_args
        return CommandPlan(
            command="docker-down",
            cmd_args=cmd,
            description="Stop all LiveClipper Docker Compose containers",
            dry_run=dry_run,
        )

    # Unknown command
    return CommandPlan(
        command="unknown",
        cmd_args=[cmd_name],
        description=f"Unknown command: '{cmd_name}'",
        is_internal=True,
        dry_run=dry_run,
    )


def print_help() -> None:
    """Print command guide and usage."""
    b = C.BOLD
    g = C.GREEN
    c = C.CYAN
    y = C.YELLOW
    r = C.RESET
    dim = C.DIM

    print(f"""
{b}{c}======================================================================{r}
{b}{c}  LiveClipper: Universal CLI Runner{r}
{b}{c}======================================================================{r}

{b}Usage:{r}
  python run.py <command> [options]
  ./run.sh <command> [options]       {dim}(macOS / Linux){r}
  run <command> [options]            {dim}(Windows){r}

{b}Available Commands:{r}
  {g}monitor{r}               Monitor YouTube live streams {dim}(default platform from .env){r}
  {g}monitor --tw{r}          Monitor Twitch streams {dim}(--platform twitch){r}
  {g}monitor --yt{r}          Monitor YouTube streams {dim}(--platform youtube){r}
  {g}monitor --all{r}         Monitor both Twitch and YouTube concurrently

  {g}process{r}               Process extracted clips with OpenShorts Docker {dim}(captions + 9:16){r}
  {g}process --fast{r}        Fast vertical processing using pure FFmpeg fallback {dim}(no Docker){r}

  {g}list{r}                  List all processed Shorts pending YouTube upload
  {g}upload{r}                Interactively review and upload eligible Shorts {dim}(public default){r}
  {g}upload --auto{r}         Auto-upload all eligible Shorts without interactive prompts
  {g}upload --unlisted{r}     Upload Shorts as unlisted rather than public

  {g}status{r}                Display current .env configuration summary and quotas
  {g}server{r}                Start the FastAPI Dashboard server {dim}(http://localhost:8000){r}
  {g}docker{r}                Start entire stack with Docker Compose {dim}(docker compose up --build){r}
  {g}docker-down{r}           Stop Docker Compose stack {dim}(docker compose down){r}
  {g}test{r}                  Run complete automated test suite
  {g}auth{r}                  Launch YouTube OAuth token authorization wizard
  {g}help{r}                  Show this help guide

{b}Global Options:{r}
  {y}--dry-run{r}             Preview the exact command line without executing it

{b}Examples:{r}
  python run.py monitor --channels kaicenat,xqc
  python run.py monitor --yt --yt-category "GTA 6 live"
  python run.py process --force
  python run.py upload --clip 500cda9f
  python run.py test -v
{c}======================================================================{r}
""")


def print_status(backend_dir: Path) -> None:
    """Print configuration and authentication status summary."""
    b = C.BOLD
    g = C.GREEN
    y = C.YELLOW
    red = C.RED
    c = C.CYAN
    r = C.RESET
    dim = C.DIM

    from app.config import load_settings

    print(f"\n{b}{c}======================================================================{r}")
    print(f"{b}{c}  LiveClipper Configuration Status (.env){r}")
    print(f"{b}{c}======================================================================{r}")

    try:
        settings = load_settings()
    except Exception as exc:
        print(f"  {red}✗ Could not load configuration: {exc}{r}\n")
        return

    # Platform & Multi-stream
    plat = settings.monitor_platform
    multi = f"{g}ENABLED (max {settings.max_concurrent_streams}){r}" if settings.multi_stream_enabled else f"{y}DISABLED (single stream only){r}"
    print(f"  {b}Monitor Platform{r}     : {c}{plat.upper()}{r}")
    print(f"  {b}Multi-Stream Mode{r}    : {multi}")

    # Channels
    tw_ch = ", ".join(settings.target_channels) if settings.target_channels else f"{dim}(none configured; uses --auto){r}"
    yt_ch = ", ".join(settings.target_youtube_channels[:4])
    if len(settings.target_youtube_channels) > 4:
        yt_ch += f" (+{len(settings.target_youtube_channels) - 4} more)"
    print(f"  {b}Twitch Channels{r}      : {tw_ch}")
    print(f"  {b}YouTube Channels{r}     : {yt_ch}")

    # Languages
    lang_str = ", ".join(settings.target_languages)
    lang_status = f"{g}active{r}" if settings.language_filter_enabled else f"{dim}disabled{r}"
    print(f"  {b}Language Filter{r}      : {lang_str} ({lang_status})")

    # Recording & Video Quality
    rec = f"{g}ENABLED{r}" if settings.recording_enabled else f"{dim}disabled{r}"
    print(f"  {b}Stream Recording{r}     : {rec} (profile: {settings.recording_quality})")
    print(f"  {b}Shorts Output{r}        : 1080x1920 @ {settings.shorts_output_bitrate} (CRF {settings.shorts_output_crf})")
    print(f"  {b}Default Engine{r}       : {settings.shorts_engine}")

    # AI & Metadata
    ai_status = f"{g}ENABLED{r} ({settings.gemini_model})" if settings.gemini_enabled and settings.gemini_api_key else f"{y}Detection-only (no key){r}"
    print(f"  {b}Gemini AI Highlights{r} : {ai_status}")
    print(f"  {b}Viral Metadata Model{r} : {settings.viral_metadata_model}")

    # Auth Status
    yt_tok = backend_dir / ".youtube_tokens.json"
    yt_auth = f"{g}Authorized{r} (.youtube_tokens.json)" if yt_tok.is_file() else f"{y}Not logged in{r} (run 'python run.py auth')"
    print(f"  {b}YouTube Auth{r}         : {yt_auth}")

    # Quota Status
    quota_file = backend_dir / "shorts" / ".youtube_quota.json"
    if quota_file.is_file():
        try:
            q_data = json.loads(quota_file.read_text())
            print(f"  {b}YouTube Daily Quota{r}  : {q_data.get('uploads_today', 0)}/{q_data.get('daily_limit', 5)} uploads ({q_data.get('units_used', 0)}/10000 units)")
        except Exception:
            pass

    print(f"{b}{c}======================================================================{r}\n")


def print_troubleshooting(cmd_name: str, exit_code: int) -> None:
    """Print helpful contextual recovery hints on command failure."""
    red = C.RED
    y = C.YELLOW
    b = C.BOLD
    r = C.RESET

    print(f"\n{red}{b}Command '{cmd_name}' exited with error code {exit_code}.{r}")
    print(f"{y}Troubleshooting Tips:{r}")

    if cmd_name == "monitor":
        print("  • If Twitch authentication failed: run 'python authorize.py' to login.")
        print("  • If recording failed: verify ffmpeg and streamlink are installed in backend/venv.")
        print("  • If YouTube channels are offline: check 'python run.py status' or pass '--auto'.")
    elif cmd_name == "process":
        print("  • If Docker failed: ensure Docker Desktop is running ('docker ps').")
        print("  • Or try the fast native fallback: 'python run.py process --fast'")
    elif cmd_name == "upload":
        print("  • If unauthorized: run 'python run.py auth' to refresh YouTube credentials.")
        print("  • If quota exceeded: YouTube allows 5 uploads per day on the default quota.")
    elif cmd_name == "test":
        print("  • Review the traceback output above to locate failing test assertions.")
    print()


def main(argv: list[str] | None = None) -> int:
    """CLI runner main entrypoint."""
    args = argv if argv is not None else sys.argv[1:]

    # Locate virtualenv Python
    python_bin = find_python_interpreter(BACKEND_DIR)
    if python_bin is None:
        print(f"\n{C.RED}{C.BOLD}✗ Virtual Environment Not Found!{C.RESET}")
        print(f"  Expected location: {BACKEND_DIR / 'venv'} or {BACKEND_DIR / '.venv'}\n")
        print("  To set up your virtual environment, run:")
        if sys.platform == "win32":
            print(f"    {C.CYAN}cd backend{C.RESET}")
            print(f"    {C.CYAN}python -m venv venv{C.RESET}")
            print(f"    {C.CYAN}venv\\Scripts\\activate{C.RESET}")
            print(f"    {C.CYAN}pip install -r requirements.txt{C.RESET}\n")
        else:
            print(f"    {C.CYAN}cd backend{C.RESET}")
            print(f"    {C.CYAN}python3 -m venv venv{C.RESET}")
            print(f"    {C.CYAN}source venv/bin/activate{C.RESET}")
            print(f"    {C.CYAN}pip install -r requirements.txt{C.RESET}\n")
        return 1

    plan = build_command(args, backend_dir=BACKEND_DIR, python_bin=python_bin)

    if plan.command == "help":
        print_help()
        return 0

    if plan.command == "status":
        print_status(BACKEND_DIR)
        return 0

    if plan.command == "unknown":
        print(f"\n{C.RED}{C.BOLD}✗ Unknown command: '{plan.cmd_args[0]}'{C.RESET}")
        print(f"  Run {C.CYAN}python run.py help{C.RESET} to see all available commands.\n")
        return 1

    if plan.dry_run:
        print(f"\n{C.YELLOW}{C.BOLD}[DRY-RUN] Previewing command execution:{C.RESET}")
        print(f"  {C.BOLD}Command   :{C.RESET} {' '.join(plan.cmd_args)}")
        print(f"  {C.BOLD}Work Dir  :{C.RESET} {BACKEND_DIR}")
        print(f"  {C.BOLD}PYTHONPATH:{C.RESET} {BACKEND_DIR}\n")
        return 0

    # Execute underlying command
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_DIR)

    try:
        proc = subprocess.run(plan.cmd_args, cwd=str(BACKEND_DIR), env=env)
        if proc.returncode != 0:
            print_troubleshooting(plan.command, proc.returncode)
        return proc.returncode
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Interrupted by user. Bye!{C.RESET}")
        return 130
    except Exception as exc:
        print(f"\n{C.RED}{C.BOLD}Execution error:{C.RESET} {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Unit tests for LiveClipper Universal CLI Runner (backend/run.py)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from run import CommandPlan, build_command, find_python_interpreter


class TestRunCLI(unittest.TestCase):
    """Test suite for CLI command parsing and routing in run.py."""

    def setUp(self) -> None:
        self.backend_dir = Path(__file__).resolve().parent.parent
        self.mock_python = Path("/fake/bin/python3")

    def test_monitor_routing_and_flags(self) -> None:
        """Verify monitor routing for default and YouTube platform."""
        # Default
        plan = build_command(["monitor"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan.command, "monitor")
        self.assertFalse(plan.dry_run)
        self.assertEqual(
            plan.cmd_args,
            [str(self.mock_python), str(self.backend_dir / "monitor_streams.py"), "--platform", "youtube"],
        )

        # YouTube flag
        plan_yt = build_command(["monitor", "--yt"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertIn("--platform", plan_yt.cmd_args)
        self.assertIn("youtube", plan_yt.cmd_args)

        # Extra args pass-through
        plan_extra = build_command(
            ["monitor", "--channels", "UCWsDFcIhY2DBi3GB5uykGXA"],
            backend_dir=self.backend_dir,
            python_bin=self.mock_python,
        )
        self.assertIn("--channels", plan_extra.cmd_args)
        self.assertIn("UCWsDFcIhY2DBi3GB5uykGXA", plan_extra.cmd_args)

    def test_process_routing_and_flags(self) -> None:
        """Verify process routing defaults to openshorts and supports --fast (ffmpeg_fallback)."""
        # Default openshorts
        plan = build_command(["process"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan.command, "process")
        self.assertIn("--engine", plan.cmd_args)
        self.assertIn("openshorts", plan.cmd_args)

        # --fast switch
        plan_fast = build_command(["process", "--fast"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertIn("--engine", plan_fast.cmd_args)
        self.assertIn("ffmpeg_fallback", plan_fast.cmd_args)
        self.assertNotIn("--fast", plan_fast.cmd_args)

        # Extra passthrough arguments
        plan_extra = build_command(
            ["process", "--fast", "--clip-id", "test1234"],
            backend_dir=self.backend_dir,
            python_bin=self.mock_python,
        )
        self.assertIn("--clip-id", plan_extra.cmd_args)
        self.assertIn("test1234", plan_extra.cmd_args)

    def test_upload_and_list_routing(self) -> None:
        """Verify list and upload commands, including privacy defaults and overrides."""
        # List command
        plan_list = build_command(["list"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_list.command, "list")
        self.assertEqual(
            plan_list.cmd_args,
            [str(self.mock_python), str(self.backend_dir / "upload_shorts.py"), "--list"],
        )

        # Upload default (public)
        plan_up = build_command(["upload"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_up.command, "upload")
        self.assertIn("--privacy", plan_up.cmd_args)
        self.assertIn("public", plan_up.cmd_args)

        # Upload unlisted override
        plan_unlisted = build_command(["upload", "--unlisted"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertIn("--privacy", plan_unlisted.cmd_args)
        self.assertIn("unlisted", plan_unlisted.cmd_args)

        # Upload auto
        plan_auto = build_command(["upload", "--auto"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertIn("--auto", plan_auto.cmd_args)
        self.assertIn("--privacy", plan_auto.cmd_args)
        self.assertIn("public", plan_auto.cmd_args)

    def test_test_auth_and_status_routing(self) -> None:
        """Verify test suite runner, auth wizard, and status routing."""
        # Test command
        plan_test = build_command(["test"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_test.command, "test")
        self.assertEqual(
            plan_test.cmd_args,
            [str(self.mock_python), "-m", "unittest", "discover", "-s", "tests"],
        )

        # Auth command
        plan_auth = build_command(["auth"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_auth.command, "auth")
        self.assertEqual(
            plan_auth.cmd_args,
            [str(self.mock_python), str(self.backend_dir / "authorize_youtube.py")],
        )

        # Status command
        plan_status = build_command(["status"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_status.command, "status")
        self.assertTrue(plan_status.is_internal)

    def test_docker_and_server_commands(self) -> None:
        """Verify docker, docker-down, and server commands in run.py."""
        # Docker up command
        plan_docker = build_command(["docker"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_docker.command, "docker")
        self.assertEqual(plan_docker.cmd_args, ["docker", "compose", "up", "--build"])

        # Docker down command
        plan_down = build_command(["docker-down"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_down.command, "docker-down")
        self.assertEqual(plan_down.cmd_args, ["docker", "compose", "down"])

        # Server command
        plan_server = build_command(["server"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_server.command, "server")
        self.assertEqual(
            plan_server.cmd_args,
            [str(self.mock_python), str(self.backend_dir / "server.py")],
        )

    def test_dry_run_and_help_handling(self) -> None:
        """Verify --dry-run flag is detected, stripped from cmd_args, and help commands resolve properly."""
        # Dry-run flag
        plan_dry = build_command(
            ["monitor", "--dry-run", "--yt"],
            backend_dir=self.backend_dir,
            python_bin=self.mock_python,
        )
        self.assertTrue(plan_dry.dry_run)
        self.assertNotIn("--dry-run", plan_dry.cmd_args)
        self.assertIn("--platform", plan_dry.cmd_args)
        self.assertIn("youtube", plan_dry.cmd_args)

        # Help commands
        for h_arg in [[], ["help"], ["--help"], ["-h"]]:
            plan_help = build_command(h_arg, backend_dir=self.backend_dir, python_bin=self.mock_python)
            self.assertEqual(plan_help.command, "help")
            self.assertTrue(plan_help.is_internal)

        # Unknown command
        plan_unk = build_command(["nonexistent"], backend_dir=self.backend_dir, python_bin=self.mock_python)
        self.assertEqual(plan_unk.command, "unknown")
        self.assertEqual(plan_unk.cmd_args, ["nonexistent"])

    def test_find_python_interpreter(self) -> None:
        """Verify find_python_interpreter finds venv binaries or active virtualenv."""
        # Test finding real venv or active virtualenv
        cand = find_python_interpreter(self.backend_dir)
        if cand is None and not (self.backend_dir / ".venv").is_dir() and not (self.backend_dir / "venv").is_dir():
            self.skipTest("No venv folder exists in container; virtualenv detection tested in unit tests below.")
        self.assertIsNotNone(cand)
        self.assertTrue(cand.is_file())

    def test_find_python_interpreter_active_venv(self) -> None:
        """When user is already in an activated virtualenv, return sys.executable directly."""
        with patch.object(sys, "prefix", "/active/virtualenv"), \
             patch.object(sys, "base_prefix", "/usr/system"), \
             patch.object(sys, "executable", "/active/virtualenv/bin/python3"):
            result = find_python_interpreter(self.backend_dir)
            self.assertEqual(result, Path("/active/virtualenv/bin/python3").absolute())

    def test_find_python_interpreter_dot_venv_and_standard_venv(self) -> None:
        """When not in an active virtualenv, search both .venv and venv folders."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()

            with patch.object(sys, "prefix", "/usr/system"), \
                 patch.object(sys, "base_prefix", "/usr/system"):

                # Neither folder exists
                self.assertIsNone(find_python_interpreter(tmp_path))

                # Create standard venv
                if sys.platform == "win32":
                    venv_bin = tmp_path / "venv" / "Scripts" / "python.exe"
                else:
                    venv_bin = tmp_path / "venv" / "bin" / "python3"
                venv_bin.parent.mkdir(parents=True, exist_ok=True)
                venv_bin.touch()
                self.assertEqual(find_python_interpreter(tmp_path), venv_bin.absolute())

                # Create .venv in a separate empty directory
                with tempfile.TemporaryDirectory() as tmp_dir2:
                    tmp_path2 = Path(tmp_dir2).resolve()
                    if sys.platform == "win32":
                        dot_venv_bin = tmp_path2 / ".venv" / "Scripts" / "python.exe"
                    else:
                        dot_venv_bin = tmp_path2 / ".venv" / "bin" / "python3"
                    dot_venv_bin.parent.mkdir(parents=True, exist_ok=True)
                    dot_venv_bin.touch()
                    self.assertEqual(find_python_interpreter(tmp_path2), dot_venv_bin.absolute())


if __name__ == "__main__":
    unittest.main()

"""Stage 6.4: Offline unit & integration tests for monitor_streams.py CLI."""

from __future__ import annotations

import asyncio
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import monitor_streams
from app.auth.user_token import ReauthRequired
from app.config import Settings
from app.discovery.stream_finder import DiscoveryError, LiveStream
from app.multistream.disk_budget import BudgetSnapshot, BudgetState
from app.multistream.liveness import LivenessStatusSnapshot
from app.multistream.manager import (
    GeminiWorkerSnapshot,
    MultiStreamManager,
    MultiStreamStatus,
)
from app.multistream.session import SessionSnapshot, SessionState
from app.recording import RecorderDependencyError


def _dummy_settings(
    root: Path,
    *,
    recording_enabled: bool = False,
    target_channels: tuple[str, ...] = (),
    max_concurrent_streams: int = 3,
    multi_stream_enabled: bool = True,
) -> Settings:
    return Settings(
        client_id="cid",
        client_secret="csec",
        redirect_uri="http://localhost:3000",
        category_name="Grand Theft Auto VI",
        max_streams=20,
        stream_language=None,
        spike_window_seconds=10.0,
        spike_multiplier=3.0,
        spike_min_messages=10,
        spike_cooldown_seconds=15.0,
        spike_warmup_seconds=30.0,
        event_pre_seconds=10.0,
        event_post_seconds=20.0,
        emote_weight=0.5,
        max_event_seconds=120.0,
        gemini_api_key="fake-key",
        gemini_enabled=True,
        highlights_output_path=root / "highlights.jsonl",
        recording_enabled=recording_enabled,
        recording_buffer_dir=root / ".buffer",
        clips_output_dir=root / "clips",
        max_concurrent_streams=max_concurrent_streams,
        max_total_buffer_mb=1500.0,
        max_concurrent_extractions=2,
        liveness_poll_seconds=60.0,
        target_channels=target_channels,
        multi_stream_enabled=multi_stream_enabled,
    )


class _MockManager:
    """Mock MultiStreamManager for offline CLI lifecycle testing."""

    def __init__(self, *args, **kwargs) -> None:
        self.init_args = args
        self.init_kwargs = kwargs
        self.is_running = False
        self.started = False
        self.stopped = False
        self.signal_handlers_installed = False
        self.signal_handlers_removed = False
        self._shutdown_event = asyncio.Event()

    def install_signal_handlers(self, loop=None) -> None:
        self.signal_handlers_installed = True

    def remove_signal_handlers(self, loop=None) -> None:
        self.signal_handlers_removed = True

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()

    async def start(self) -> dict[str, bool]:
        self.started = True
        self.is_running = True
        self._shutdown_event.set()
        return {}

    async def stop(self, *args, **kwargs) -> None:
        self.stopped = True
        self.is_running = False

    def status(self) -> MultiStreamStatus:
        return MultiStreamStatus(
            running=self.is_running,
            max_concurrent_streams=3,
            target_channels=["alpha"],
            active_channels=["alpha"],
            sessions={
                "alpha": SessionSnapshot(
                    channel="alpha",
                    state=SessionState.RUNNING,
                    run_id="run1",
                    recording_enabled=True,
                    recording_session_id=1,
                    start_count=1,
                    segments_registered=10,
                    active_pins=1,
                    buffer_bytes=100 * 1024 * 1024,
                    clips_extracted=1,
                    clips_failed=0,
                    clips_dropped=0,
                    error_message=None,
                )
            },
            disk_budget=BudgetSnapshot(
                total_bytes=100 * 1024 * 1024,
                pinned_bytes=0,
                unpinned_bytes=100 * 1024 * 1024,
                max_total_bytes=1500 * 1024 * 1024,
                emergency_bytes=1800 * 1024 * 1024,
                state=BudgetState.OK,
                active_session_count=1,
                bytes_over_budget=0,
            ),
            gemini=GeminiWorkerSnapshot(
                enabled=True,
                pending_count=0,
                submitted_count=2,
                analyzed_count=2,
                prefilter_skipped_count=0,
                failed_count=0,
                dropped_count=0,
            ),
            liveness=LivenessStatusSnapshot(
                active_channels=["alpha"],
                last_check_mono=1.0,
                last_check_wall=1.0,
                consecutive_errors=0,
                online_count=1,
                offline_count=0,
                is_running=True,
            ),
            routed_verdicts=2,
            discarded_verdicts=0,
        )


class TestCLIArgParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = monitor_streams.build_parser()

    def test_positional_channels(self) -> None:
        args = self.parser.parse_args(["kaicenat", "xqc", "shroud"])
        self.assertEqual(args.channels, ["kaicenat", "xqc", "shroud"])
        self.assertEqual(args.flag_channels, "")
        self.assertFalse(args.quiet)
        self.assertFalse(args.verbose_chat)
        self.assertFalse(args.auto_discover)

    def test_flag_channels_comma_and_space_separated(self) -> None:
        args = self.parser.parse_args(["-c", "kaicenat, xqc, shroud"])
        channels = monitor_streams.parse_channel_list(args.flag_channels)
        self.assertEqual(channels, ["kaicenat", "xqc", "shroud"])

    def test_mixed_positional_and_flag_channels(self) -> None:
        args = self.parser.parse_args(["alpha", "-c", "beta, gamma"])
        combined = list(args.channels) + monitor_streams.parse_channel_list(args.flag_channels)
        self.assertEqual(combined, ["alpha", "beta", "gamma"])

    def test_channel_normalization_and_deduplication(self) -> None:
        raw = ["#KaiCenat", "kaicenat", "#xqc", "  XQC  ", ""]
        cleaned = monitor_streams.parse_channel_list(raw)
        self.assertEqual(cleaned, ["kaicenat", "xqc"])

    def test_optional_flags(self) -> None:
        args = self.parser.parse_args([
            "kaicenat",
            "--max-streams", "5",
            "--status-interval", "10.0",
            "--quiet",
            "--auto-restart",
            "--no-liveness",
            "--no-budget",
            "--combine-env",
        ])
        self.assertEqual(args.max_streams, 5)
        self.assertEqual(args.status_interval, 10.0)
        self.assertTrue(args.quiet)
        self.assertTrue(args.auto_restart)
        self.assertTrue(args.no_liveness)
        self.assertTrue(args.no_budget)
        self.assertTrue(args.combine_env)


class TestChannelResolution(unittest.TestCase):
    def test_cli_only(self) -> None:
        resolved = monitor_streams.resolve_target_channels(
            cli_channels=["kaicenat", "xqc"],
            env_channels=None,
        )
        self.assertEqual(resolved, ["kaicenat", "xqc"])

    def test_env_only(self) -> None:
        resolved = monitor_streams.resolve_target_channels(
            cli_channels=[],
            env_channels="kaicenat, xqc",
        )
        self.assertEqual(resolved, ["kaicenat", "xqc"])

    def test_env_tuple_only(self) -> None:
        resolved = monitor_streams.resolve_target_channels(
            cli_channels=[],
            env_channels=("kaicenat", "xqc"),
        )
        self.assertEqual(resolved, ["kaicenat", "xqc"])

    def test_cli_overrides_env_by_default(self) -> None:
        resolved = monitor_streams.resolve_target_channels(
            cli_channels=["shroud"],
            env_channels=("kaicenat", "xqc"),
            combine_env=False,
        )
        self.assertEqual(resolved, ["shroud"])

    def test_cli_and_env_combined_when_requested(self) -> None:
        resolved = monitor_streams.resolve_target_channels(
            cli_channels=["shroud", "kaicenat"],
            env_channels=("kaicenat", "xqc"),
            combine_env=True,
        )
        self.assertEqual(resolved, ["shroud", "kaicenat", "xqc"])

    def test_empty_sources_return_empty(self) -> None:
        resolved = monitor_streams.resolve_target_channels(cli_channels=[], env_channels=())
        self.assertEqual(resolved, [])


class TestSingleVsMultiChannelMode(unittest.TestCase):
    def test_single_channel_defaults_to_chat_printing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                ["kaicenat"],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(created_instances), 1)
            # print_messages MUST default to True for single-stream equivalence!
            self.assertTrue(created_instances[0].init_kwargs["print_messages"])

    def test_multi_channel_defaults_to_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                ["kaicenat", "xqc"],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(created_instances), 1)
            # print_messages MUST default to False for multi-channel to prevent console spam!
            self.assertFalse(created_instances[0].init_kwargs["print_messages"])

    def test_explicit_quiet_flag_overrides_single_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                ["kaicenat", "--quiet"],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertFalse(created_instances[0].init_kwargs["print_messages"])

    def test_explicit_verbose_chat_flag_overrides_multi_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                ["kaicenat", "xqc", "--chat"],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertTrue(created_instances[0].init_kwargs["print_messages"])


class TestStatusFormatting(unittest.TestCase):
    def test_format_status_line(self) -> None:
        mock_mgr = _MockManager()
        status = mock_mgr.status()
        line = monitor_streams.format_status_line(status)

        self.assertIn("Streams (1/3): [#alpha]", line)
        self.assertIn("Disk: 100.0/1500MB (6.7%)", line)
        self.assertIn("Gemini: submitted=2 analyzed=2 pending=0 dropped=0", line)
        self.assertIn("Liveness: 1 live, 0 offline", line)
        self.assertIn("Router: routed=2 discarded=0", line)

    def test_format_status_line_disabled_gemini(self) -> None:
        status = MultiStreamStatus(
            running=True,
            max_concurrent_streams=2,
            target_channels=["ch1"],
            active_channels=["ch1"],
            sessions={},
            disk_budget=BudgetSnapshot(
                total_bytes=0,
                pinned_bytes=0,
                unpinned_bytes=0,
                max_total_bytes=1000 * 1024 * 1024,
                emergency_bytes=1200 * 1024 * 1024,
                state=BudgetState.OK,
                active_session_count=1,
                bytes_over_budget=0,
            ),
            gemini=GeminiWorkerSnapshot(
                enabled=False,
                pending_count=0,
                submitted_count=0,
                analyzed_count=0,
                prefilter_skipped_count=0,
                failed_count=0,
                dropped_count=0,
            ),
            liveness=None,
            routed_verdicts=0,
            discarded_verdicts=0,
        )
        line = monitor_streams.format_status_line(status)
        self.assertIn("Streams (1/2): [#ch1]", line)
        self.assertNotIn("Gemini:", line)
        self.assertNotIn("Liveness:", line)


class TestMainExecutionAndExitCodes(unittest.TestCase):
    def test_missing_channels_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp), target_channels=())
            rc = monitor_streams.main(
                [],
                settings=settings,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 2)

    def test_reauth_required_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))

            def _bad_token(s):
                raise ReauthRequired("need login")

            rc = monitor_streams.main(
                ["kaicenat"],
                settings=settings,
                token_checker=_bad_token,
            )
            self.assertEqual(rc, 1)

    def test_transient_token_error_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))
            from app.auth.user_token import TransientTokenError

            def _network_err(s):
                raise TransientTokenError("network unreachable")

            rc = monitor_streams.main(
                ["kaicenat"],
                settings=settings,
                token_checker=_network_err,
            )
            self.assertEqual(rc, 1)

    def test_recording_enabled_missing_dependencies_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp), recording_enabled=True)
            with mock.patch(
                "monitor_streams.find_streamlink_binary",
                side_effect=RecorderDependencyError("streamlink missing"),
            ):
                rc = monitor_streams.main(
                    ["kaicenat"],
                    settings=settings,
                    token_checker=lambda s: "token",
                )
                self.assertEqual(rc, 2)

    def test_auto_discovery_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp), target_channels=(), max_concurrent_streams=2)

            fake_finder = mock.MagicMock()
            fake_finder.discover.return_value = (
                "Grand Theft Auto VI",
                [
                    LiveStream("ch_one", "Ch One", "title1", 5000, "en"),
                    LiveStream("ch_two", "Ch Two", "title2", 3000, "en"),
                    LiveStream("ch_three", "Ch Three", "title3", 1000, "en"),
                ],
            )
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                ["--auto"],
                settings=settings,
                manager_factory=_factory,
                stream_finder=fake_finder,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(created_instances), 1)
            # Picked top 2 channels up to max_concurrent_streams=2
            self.assertEqual(created_instances[0].init_kwargs["channels"], ["ch_one", "ch_two"])

    def test_auto_discovery_no_streams_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))
            fake_finder = mock.MagicMock()
            fake_finder.discover.return_value = ("Grand Theft Auto VI", [])

            rc = monitor_streams.main(
                ["--auto"],
                settings=settings,
                stream_finder=fake_finder,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 1)

    def test_auto_discovery_failure_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))
            fake_finder = mock.MagicMock()
            fake_finder.discover.side_effect = DiscoveryError("helix timeout")

            rc = monitor_streams.main(
                ["--auto"],
                settings=settings,
                stream_finder=fake_finder,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 1)

    def test_env_channels_used_when_no_cli_args_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp), target_channels=("env_ch1", "env_ch2"))
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                [],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(created_instances[0].init_kwargs["channels"], ["env_ch1", "env_ch2"])


class TestPeriodicStatusLoggerAndShutdown(unittest.TestCase):
    def test_status_logger_stops_cleanly_on_event(self) -> None:
        async def _test():
            mock_mgr = _MockManager()
            mock_mgr.is_running = True
            stop_event = asyncio.Event()

            task = asyncio.create_task(
                monitor_streams._periodic_status_logger(mock_mgr, 0.05, stop_event)
            )
            await asyncio.sleep(0.08)
            stop_event.set()
            await task

        asyncio.run(_test())

    def test_run_multi_stream_session_lifecycle(self) -> None:
        async def _test():
            mock_mgr = _MockManager()

            # Keep running until explicit shutdown
            async def _manual_start():
                mock_mgr.started = True
                mock_mgr.is_running = True
                return {}

            mock_mgr.start = _manual_start

            # Schedule shutdown after a short delay
            async def _delayed_shutdown():
                await asyncio.sleep(0.05)
                mock_mgr.request_shutdown()

            asyncio.create_task(_delayed_shutdown())

            await monitor_streams.run_multi_stream_session(
                mock_mgr,
                status_interval_seconds=0.02,
                gemini_drain_timeout=1.0,
                clip_drain_timeout=1.0,
            )

            self.assertTrue(mock_mgr.started)
            self.assertTrue(mock_mgr.stopped)
            self.assertTrue(mock_mgr.signal_handlers_installed)
            self.assertTrue(mock_mgr.signal_handlers_removed)

        asyncio.run(_test())

    def test_keyboard_interrupt_handled_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(Path(tmp))

            class _InterruptManager(_MockManager):
                async def wait_for_shutdown(self) -> None:
                    raise KeyboardInterrupt()

            rc = monitor_streams.main(
                ["kaicenat"],
                settings=settings,
                manager_factory=lambda *a, **kw: _InterruptManager(*a, **kw),
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)


class TestMultiStreamToggle(unittest.TestCase):
    """Tests for MULTI_STREAM_ENABLED and --multi / --single toggle behavior."""

    def test_multi_stream_disabled_env_channels_limits_to_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(
                Path(tmp),
                target_channels=("env_ch1", "env_ch2", "env_ch3"),
                multi_stream_enabled=False,
            )
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                [],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(created_instances), 1)
            # Only first channel monitored
            self.assertEqual(created_instances[0].init_kwargs["channels"], ["env_ch1"])
            # Effective max concurrent streams forced to 1
            self.assertEqual(created_instances[0].init_kwargs["max_concurrent_streams"], 1)
            # Single-stream equivalence: chat printing defaults to True
            self.assertTrue(created_instances[0].init_kwargs["print_messages"])

    def test_multi_stream_disabled_positional_args_limits_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(
                Path(tmp),
                target_channels=(),
                multi_stream_enabled=False,
            )
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                ["cli1", "cli2", "cli3"],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(created_instances), 1)
            self.assertEqual(created_instances[0].init_kwargs["channels"], ["cli1"])
            self.assertEqual(created_instances[0].init_kwargs["max_concurrent_streams"], 1)

    def test_multi_flag_overrides_disabled_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(
                Path(tmp),
                target_channels=("env_ch1", "env_ch2"),
                max_concurrent_streams=3,
                multi_stream_enabled=False,
            )
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                ["--multi"],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(created_instances), 1)
            # Both channels monitored
            self.assertEqual(created_instances[0].init_kwargs["channels"], ["env_ch1", "env_ch2"])
            self.assertEqual(created_instances[0].init_kwargs["max_concurrent_streams"], 3)

    def test_single_flag_overrides_enabled_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _dummy_settings(
                Path(tmp),
                target_channels=("env_ch1", "env_ch2"),
                max_concurrent_streams=3,
                multi_stream_enabled=True,
            )
            created_instances: list[_MockManager] = []

            def _factory(*args, **kwargs):
                m = _MockManager(*args, **kwargs)
                created_instances.append(m)
                return m

            rc = monitor_streams.main(
                ["--single"],
                settings=settings,
                manager_factory=_factory,
                token_checker=lambda s: "token",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(created_instances), 1)
            # Only first channel monitored
            self.assertEqual(created_instances[0].init_kwargs["channels"], ["env_ch1"])
            self.assertEqual(created_instances[0].init_kwargs["max_concurrent_streams"], 1)

    def test_startup_logging_multi_stream_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_enabled = _dummy_settings(Path(tmp), target_channels=("ch1",), multi_stream_enabled=True)
            settings_disabled = _dummy_settings(Path(tmp), target_channels=("ch1",), multi_stream_enabled=False)

            with self.assertLogs("monitor_streams", level="INFO") as cm1:
                monitor_streams.main(
                    [],
                    settings=settings_enabled,
                    manager_factory=lambda *a, **kw: _MockManager(*a, **kw),
                    token_checker=lambda s: "token",
                )
            self.assertTrue(any("Multi-stream: ENABLED (max 3)" in msg for msg in cm1.output))

            with self.assertLogs("monitor_streams", level="INFO") as cm2:
                monitor_streams.main(
                    [],
                    settings=settings_disabled,
                    manager_factory=lambda *a, **kw: _MockManager(*a, **kw),
                    token_checker=lambda s: "token",
                )
            self.assertTrue(any("Multi-stream: DISABLED (single stream only)" in msg for msg in cm2.output))


if __name__ == "__main__":
    unittest.main()

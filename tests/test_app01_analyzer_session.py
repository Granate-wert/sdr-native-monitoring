import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from sdr_monitor.application.analyzer_session import (
    AnalyzerSessionApplicationService, AnalyzerMode, AnalyzerPhase,
)
from sdr_monitor.domain.live import LiveSnapshot, LiveSessionState
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest


class Live:
    running = False

    def __init__(self, events):
        self.events = events

    def is_running(self):
        return self.running

    def start(self):
        self.events.append("rtbw-start")
        self.running = True
        return LiveSnapshot(generation=1, sequence=1, state=LiveSessionState.RUNNING)

    def stop(self):
        self.events.append("rtbw-stop")
        self.running = False
        return LiveSnapshot(generation=1, sequence=2, state=LiveSessionState.CONNECTED)


class Sweep:
    def __init__(self, events):
        self.events = events

    def start(self, request):
        self.events.append("sweep-start")

    def stop(self):
        self.events.append("sweep-stop")


class AnalyzerSessionTests(unittest.TestCase):
    def test_bounded_sweep_tool_blocks_both_strategies_until_scope_exits(self):
        events = []
        owner = AnalyzerSessionApplicationService(Live(events), Sweep(events))
        with owner.bounded_sweep_operation():
            self.assertIs(owner.state.mode, AnalyzerMode.SWEEP)
            self.assertIs(owner.state.phase, AnalyzerPhase.RUNNING)
            operation_id = owner.state.operation_id
            for command in (owner.start, owner.stop,
                            lambda: owner.select_mode(AnalyzerMode.RTBW)):
                with self.assertRaises(RuntimeError):
                    command()
            self.assertEqual(events, [])
        self.assertIs(owner.state.phase, AnalyzerPhase.IDLE)
        owner.select_mode(AnalyzerMode.RTBW)
        owner.start()
        self.assertGreater(owner.state.operation_id, operation_id)
        owner.stop()
        self.assertEqual(events, ["rtbw-start", "rtbw-stop"])

    def test_shutdown_always_reaches_backend_cleanup_when_controller_stop_raises(self):
        from unittest.mock import Mock
        from sdr_monitor.application.live_session import LiveSessionApplicationService

        for reason in ("analyzer lifecycle operation is pending", "native stop failed"):
            with self.subTest(reason=reason):
                live, controller = Mock(), Mock()
                controller.stop.side_effect = RuntimeError(reason)
                application = LiveSessionApplicationService(live, analyzer=controller)
                with self.assertRaisesRegex(RuntimeError, reason):
                    application.shutdown(2.0)
                live.stop_and_wait.assert_called_once_with(2.0)

    def test_port_exceptions_leave_visible_cleanup_and_allow_explicit_stop_retry(self):
        from unittest.mock import Mock
        from sdr_monitor.application.live_session import LiveSessionApplicationService
        from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state, LiveAction

        live = Mock()
        live.is_running.return_value = False
        live.latest_snapshot.return_value = LiveSnapshot(
            generation=1, sequence=1, state=LiveSessionState.CONNECTED,
        )
        live.start.side_effect = RuntimeError("start exception")
        owner = AnalyzerSessionApplicationService(live, Sweep([]))
        application = LiveSessionApplicationService(live, analyzer=owner)
        failed = application.start()
        self.assertEqual(failed.error, "start exception")
        self.assertTrue(failed.stop_required)
        live.stop.side_effect = RuntimeError("stop exception")
        failed_stop = application.stop()
        self.assertEqual(failed_stop.error, "stop exception")
        self.assertIs(build_live_view_state(failed_stop).primary_action, LiveAction.STOP)
        self.assertEqual(application.current_snapshot().error, "stop exception")
        with self.assertRaises(RuntimeError):
            application.start()
        live.stop.side_effect = None
        live.stop.return_value = live.latest_snapshot.return_value
        self.assertFalse(application.stop().stop_required)
        self.assertIsNone(application.current_snapshot().error)

    def test_live_application_routes_both_strategies_through_shared_owner(self):
        from sdr_monitor.application.live_session import LiveSessionApplicationService
        from sdr_monitor.services.live_session import InMemoryLiveSessionService, fake_pluto_device
        from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
        from sdr_monitor.domain.live import LiveConfiguration

        events = []
        live = InMemoryLiveSessionService((fake_pluto_device(),))
        owner = AnalyzerSessionApplicationService(live, Sweep(events))
        application = LiveSessionApplicationService(
            live, analyzer=owner,
            sweep_preflight=NativeContinuousSweepPlanFactory.preflight_profile,
        )
        application.select_device("fake-pluto-usb")
        from sdr_monitor.domain import BackendKind
        configuration = LiveConfiguration(sample_rate_hz=3e6, backend=BackendKind.CPU)
        self.assertTrue(application.start_with_configuration(configuration).stop_required)
        self.assertIs(owner.state.phase, AnalyzerPhase.RUNNING)
        with self.assertRaises(RuntimeError):
            application.start_sweep(ContinuousSweepPlanRequest(100e6, 102e6, usable_window_hz=2e6, overlap_hz=0))
        self.assertEqual(events, [])
        self.assertFalse(application.stop().stop_required)
        application.start_sweep(ContinuousSweepPlanRequest(100e6, 102e6, usable_window_hz=2e6, overlap_hz=0))
        self.assertIs(owner.state.mode, AnalyzerMode.SWEEP)
        for action in (application.start, lambda: application.apply_configuration(configuration),
                       lambda: application.select_device("fake-pluto-usb")):
            with self.assertRaises(RuntimeError):
                action()
        application.stop()
        self.assertEqual(events, ["sweep-start", "sweep-stop"])
        application.start()
        application.stop()
        application.shutdown()

    def test_rejected_live_keeps_exact_error_and_explicit_stop_action(self):
        from unittest.mock import Mock
        from sdr_monitor.application.live_session import LiveSessionApplicationService
        from sdr_monitor.domain.live import LiveErrorKind
        from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state, LiveAction

        failed = LiveSnapshot(generation=1, sequence=2, state=LiveSessionState.ERROR,
                              error="specific native failure", error_kind=LiveErrorKind.STREAM_START_FAILED)
        stopped = LiveSnapshot(generation=1, sequence=3, state=LiveSessionState.CONNECTED)
        live = Mock()
        live.is_running.return_value = False
        live.start.return_value = failed
        live.latest_snapshot.return_value = failed
        owner = AnalyzerSessionApplicationService(live, Sweep([]))
        application = LiveSessionApplicationService(live, analyzer=owner)
        snapshot = application.start()
        self.assertEqual(snapshot.error, failed.error)
        self.assertIs(snapshot.error_kind, failed.error_kind)
        self.assertTrue(snapshot.stop_required)
        view = build_live_view_state(snapshot)
        self.assertIs(view.primary_action, LiveAction.STOP)
        self.assertTrue(view.primary_action_enabled)
        # A refresh must not erase a rejected command when the raw port keeps
        # its earlier snapshot instead of publishing the returned failure.
        live.latest_snapshot.return_value = stopped
        refreshed = application.current_snapshot()
        self.assertEqual(refreshed.error, failed.error)
        self.assertIs(refreshed.error_kind, failed.error_kind)
        self.assertTrue(refreshed.stop_required)
        with self.assertRaises(RuntimeError):
            application.start()
        live.start.assert_called_once()
        live.stop.return_value = stopped
        live.latest_snapshot.return_value = stopped
        self.assertFalse(application.stop().stop_required)
        self.assertIs(owner.state.phase, AnalyzerPhase.IDLE)

    def test_rejected_external_live_admission_never_stops_external_owner(self):
        events = []
        live = Live(events)
        live.running = True
        owner = AnalyzerSessionApplicationService(live, Sweep(events))
        with self.assertRaisesRegex(RuntimeError, "outside this analyzer"):
            owner.start()
        self.assertIs(owner.state.phase, AnalyzerPhase.ERROR)
        owner.stop()
        self.assertTrue(live.running)
        self.assertEqual(events, [])
        self.assertIs(owner.state.phase, AnalyzerPhase.IDLE)

    def test_dispatched_failed_start_requires_explicit_cleanup(self):
        events = []
        class FailedSweep(Sweep):
            def start(self, request):
                super().start(request)
                raise RuntimeError("native start failed")

        owner = AnalyzerSessionApplicationService(Live(events), FailedSweep(events))
        owner.select_mode(AnalyzerMode.SWEEP)
        with self.assertRaisesRegex(RuntimeError, "native start failed"):
            owner.start(ContinuousSweepPlanRequest(100e6, 136e6))
        with self.assertRaises(RuntimeError):
            owner.select_mode(AnalyzerMode.RTBW)
        self.assertEqual(events, ["sweep-start"])
        owner.stop()
        self.assertEqual(events, ["sweep-start", "sweep-stop"])
        self.assertIs(owner.state.phase, AnalyzerPhase.IDLE)

    def test_explicit_mode_switch_has_no_hidden_start_stop(self):
        events = []
        owner = AnalyzerSessionApplicationService(Live(events), Sweep(events))
        self.assertEqual(events, [])
        owner.start()
        with self.assertRaises(RuntimeError):
            owner.select_mode(AnalyzerMode.SWEEP)
        owner.stop()
        owner.select_mode(AnalyzerMode.SWEEP)
        self.assertEqual(events, ["rtbw-start", "rtbw-stop"])
        owner.start(ContinuousSweepPlanRequest(100e6, 136e6))
        owner.stop()
        owner.select_mode(AnalyzerMode.RTBW)
        owner.start()
        owner.stop()
        self.assertEqual(events, ["rtbw-start", "rtbw-stop", "sweep-start", "sweep-stop",
                                  "rtbw-start", "rtbw-stop"])

    def test_switch_and_restart_barred_until_stop_finishes(self):
        events = []
        entered, release = threading.Event(), threading.Event()

        class SlowSweep(Sweep):
            def stop(self):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test barrier timeout")
                super().stop()

        owner = AnalyzerSessionApplicationService(Live(events), SlowSweep(events))
        owner.select_mode(AnalyzerMode.SWEEP)
        owner.start(ContinuousSweepPlanRequest(100e6, 136e6))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(owner.stop)
            try:
                self.assertTrue(entered.wait(1))
                self.assertIs(owner.state.phase, AnalyzerPhase.STOPPING)
                for command in (owner.start, owner.stop,
                                lambda: owner.select_mode(AnalyzerMode.RTBW)):
                    with self.assertRaises(RuntimeError):
                        command()
                self.assertEqual(events, ["sweep-start"])
            finally:
                release.set()
            pending.result(timeout=2)
        self.assertIs(owner.state.phase, AnalyzerPhase.IDLE)
        owner.select_mode(AnalyzerMode.RTBW)

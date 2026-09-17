"""Real continuous Sweep service layers, deterministic native failures; no SDR."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
from sdr_monitor.services.native_continuous_sweep_factory import (
    NativeContinuousSweepPlanFactory, NativeLiveContinuousSweepDisplayService,
)
from sdr_monitor.services.native_sweep import NativeSweepLease, NativeSweepSource


class _Coordinator:
    def __init__(self):
        self.calls = []
        self.failures = {}

    def _call(self, name):
        self.calls.append(name)
        count = self.failures.get(name, 0)
        if count:
            self.failures[name] = count - 1
            raise RuntimeError(f"injected {name} failure")

    def configure(self, config):
        self._call("configure")

    def start(self):
        self._call("start")

    def stop(self):
        self._call("stop")

    def disconnect(self):
        self._call("disconnect")

    def poll_lines(self):
        self._call("poll")
        return ()

    def metrics(self):
        return SimpleNamespace()


def _config():
    return SimpleNamespace(epoch=1, segments=(
        SimpleNamespace(fixed_band=SimpleNamespace(device=SimpleNamespace(source_id="fake"))),
    ))


class SweepFailureOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = _Coordinator()
        self.native = SimpleNamespace(NativeContinuousSweepCoordinator=lambda *_: self.coordinator)
        self.release_calls = []
        self.release_failures = 0
        self.lease_active = False

        def release():
            self.release_calls.append("release")
            if self.release_failures:
                self.release_failures -= 1
                raise RuntimeError("injected release failure")
            self.lease_active = False

        source = NativeSweepSource("usb:fake", "fake", LiveConfiguration(backend=BackendKind.CPU))
        self.lease = NativeSweepLease(self.native, source, lambda: None, release)

        def acquire():
            if self.lease_active:
                raise RuntimeError("lease still owned")
            self.lease_active = True
            return self.lease

        self.live = SimpleNamespace(acquire_native_sweep_lease=acquire)
        self.service = NativeLiveContinuousSweepDisplayService(self.live)
        self.request = ContinuousSweepPlanRequest(100e6, 110e6)
        self.build_patch = patch.object(NativeContinuousSweepPlanFactory, "build", return_value=_config())
        self.build_patch.start()
        self.addCleanup(self.build_patch.stop)

    def test_repeated_stop_failure_never_disconnects_or_releases(self):
        self.service.start(self.request)
        self.coordinator.failures["stop"] = 2
        for expected in (1, 2):
            with self.assertRaisesRegex(RuntimeError, "stop failure"):
                self.service.stop()
            self.assertEqual(self.coordinator.calls.count("stop"), expected)
            self.assertNotIn("disconnect", self.coordinator.calls)
            self.assertEqual(self.release_calls, [])
            with self.assertRaises(RuntimeError):
                self.service.start(self.request)
            self.assertTrue(self.lease_active)
        self.service.stop()
        self.assertEqual(self.coordinator.calls.count("stop"), 3)
        self.assertEqual(self.coordinator.calls.count("disconnect"), 1)
        self.assertEqual(self.release_calls, ["release"])
        self.assertFalse(self.lease_active)

    def test_disconnect_failure_retries_only_unfinished_phase_and_keeps_lease(self):
        self.service.start(self.request)
        self.coordinator.failures["disconnect"] = 2
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "disconnect failure"):
                self.service.close()
            self.assertTrue(self.lease_active)
            self.assertEqual(self.release_calls, [])
        self.service.close()
        self.assertEqual(self.coordinator.calls.count("stop"), 1)
        self.assertEqual(self.coordinator.calls.count("poll"), 1)
        self.assertEqual(self.coordinator.calls.count("disconnect"), 3)
        self.assertEqual(self.release_calls, ["release"])

    def test_failed_start_cleanup_retains_the_actual_native_handle_for_retry(self):
        self.coordinator.failures.update(start=1, stop=1)
        with self.assertRaisesRegex(RuntimeError, "stop failure"):
            self.service.start(self.request)
        self.assertTrue(self.lease_active)
        self.assertNotIn("disconnect", self.coordinator.calls)
        self.service.close()
        self.assertFalse(self.lease_active)
        self.assertEqual(self.coordinator.calls.count("stop"), 2)
        self.assertEqual(self.coordinator.calls.count("disconnect"), 1)
        self.assertEqual(self.coordinator.calls.count("poll"), 0)

    def test_terminal_poll_error_still_cleans_and_is_not_silently_lost(self):
        self.service.start(self.request)
        self.coordinator.failures["poll"] = 1
        with self.assertRaisesRegex(RuntimeError, "poll failure"):
            self.service.stop()
        self.assertFalse(self.lease_active)
        self.assertEqual(self.release_calls, ["release"])
        self.service.close()
        self.assertEqual(self.release_calls, ["release"])

    def test_failed_release_is_retryable_without_repeating_native_phases(self):
        self.service.start(self.request)
        self.release_failures = 1
        with self.assertRaisesRegex(RuntimeError, "release failure"):
            self.service.stop()
        self.assertTrue(self.lease_active)
        calls = tuple(self.coordinator.calls)
        self.service.close()
        self.assertFalse(self.lease_active)
        self.assertEqual(tuple(self.coordinator.calls), calls)
        self.assertEqual(self.release_calls, ["release", "release"])

    def test_direct_close_does_not_mark_closed_on_native_stop_failure(self):
        display = NativeContinuousSweepDisplayService(self.native, "usb:fake")
        display.start(_config())
        self.coordinator.failures["stop"] = 1
        with self.assertRaisesRegex(RuntimeError, "stop failure"):
            display.close()
        self.assertNotIn("disconnect", self.coordinator.calls)
        with self.assertRaises(RuntimeError):
            display.start(_config())
        display.close()
        self.assertEqual(self.coordinator.calls.count("stop"), 2)
        self.assertEqual(self.coordinator.calls.count("disconnect"), 1)
        display.close()
        self.assertEqual(self.coordinator.calls.count("disconnect"), 1)

    def test_shared_analyzer_cannot_switch_to_rtbw_until_disconnect_is_confirmed(self):
        from sdr_monitor.application.analyzer_session import (
            AnalyzerMode, AnalyzerPhase, AnalyzerSessionApplicationService,
        )
        from tests.test_app01_analyzer_session import Live

        events = []
        owner = AnalyzerSessionApplicationService(Live(events), self.service)
        owner.select_mode(AnalyzerMode.SWEEP)
        owner.start(self.request)
        self.coordinator.failures["disconnect"] = 1
        with self.assertRaisesRegex(RuntimeError, "disconnect failure"):
            owner.stop()
        self.assertIs(owner.state.phase, AnalyzerPhase.ERROR)
        self.assertTrue(owner.stop_required)
        self.assertTrue(self.lease_active)
        with self.assertRaises(RuntimeError):
            owner.select_mode(AnalyzerMode.RTBW)
        with self.assertRaises(RuntimeError):
            owner.start(self.request)
        self.assertEqual(events, [])
        owner.stop()
        self.assertIs(owner.state.phase, AnalyzerPhase.IDLE)
        self.assertFalse(owner.stop_required)
        self.assertFalse(self.lease_active)
        owner.select_mode(AnalyzerMode.RTBW)
        owner.start()
        owner.stop()
        self.assertEqual(events, ["rtbw-start", "rtbw-stop"])

    def test_configure_failure_still_requires_stop_before_disconnect(self):
        self.coordinator.failures["configure"] = 1
        with self.assertRaisesRegex(RuntimeError, "configure failure"):
            self.service.start(self.request)
        self.assertEqual(self.coordinator.calls, ["configure", "stop", "disconnect"])
        self.assertFalse(self.lease_active)

    def test_build_and_release_failure_keeps_factory_until_explicit_cleanup(self):
        self.release_failures = 1
        with patch.object(NativeContinuousSweepPlanFactory, "build", side_effect=ValueError("bad plan")):
            with self.assertRaisesRegex(RuntimeError, "release failure"):
                self.service.start(self.request)
        self.assertTrue(self.lease_active)
        self.assertEqual(self.coordinator.calls, [])
        with self.assertRaises(RuntimeError):
            self.service.start(self.request)
        self.service.close()
        self.assertFalse(self.lease_active)
        self.assertEqual(self.release_calls, ["release", "release"])

    def test_poll_error_survives_a_failed_disconnect_and_is_reported_after_cleanup(self):
        self.service.start(self.request)
        self.coordinator.failures.update(poll=1, disconnect=1)
        with self.assertRaisesRegex(RuntimeError, "disconnect failure"):
            self.service.stop()
        self.assertTrue(self.lease_active)
        with self.assertRaisesRegex(RuntimeError, "poll failure"):
            self.service.close()
        self.assertFalse(self.lease_active)
        self.assertEqual(self.coordinator.calls.count("poll"), 1)
        self.service.close()

    def test_restart_after_successful_cleanup_resets_phase_tracking(self):
        for _ in range(2):
            self.service.start(self.request)
            self.service.stop()
            self.assertFalse(self.lease_active)
        for phase in ("configure", "start", "stop", "poll", "disconnect"):
            self.assertEqual(self.coordinator.calls.count(phase), 2)
        self.assertEqual(self.release_calls, ["release", "release"])

    def test_explicit_native_idle_state_allows_cleanup_after_rejected_configure(self):
        self.coordinator.failures["configure"] = 1
        self.coordinator.state = lambda: SimpleNamespace(name="CREATED")
        with self.assertRaisesRegex(RuntimeError, "configure failure"):
            self.service.start(self.request)
        self.assertEqual(self.coordinator.calls, ["configure", "disconnect"])
        self.assertFalse(self.lease_active)

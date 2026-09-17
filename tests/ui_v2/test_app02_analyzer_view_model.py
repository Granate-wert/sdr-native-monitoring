"""Command isolation of one RTBW/Sweep presentation session; no hardware."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.ui.v2.state.live_view_state import LiveAction, build_live_view_state
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewModel


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self, value):
        for callback in tuple(self.callbacks):
            callback(value)


class Live:
    def __init__(self):
        self.state = replace(build_live_view_state(None), primary_action=LiveAction.START,
                             primary_action_enabled=True, has_applied_configuration=True)
        self.calls = []
        self.callbacks = []

    def subscribe(self, callback):
        self.callbacks.append(callback)
        callback(self.state)
        return lambda: self.callbacks.remove(callback)

    def execute_primary_action(self):
        self.calls.append(self.state.primary_action)
        self.state = replace(self.state, busy=True)
        for callback in tuple(self.callbacks):
            callback(self.state)
        return True

    def discover_devices(self):
        self.calls.append("discover")
        return True

    def select_device(self, value):
        self.calls.append(("select", value))
        return True

    def select_manual_uri(self, value):
        self.calls.append(("uri", value))
        return True

    def apply_configuration(self, value):
        self.calls.append(("apply", value))
        return True


class Sweep:
    def __init__(self):
        self.analyzer_ready = Signal()
        self.snapshot_ready = Signal()
        self.task_failed = Signal()
        self.starting_changed = Signal()
        self.stopping_changed = Signal()
        self.running_changed = Signal()
        self.calls = []
        self.idle = True

    def can_close(self):
        return self.idle

    def start(self, request):
        self.calls.append(("start", request))
        self.idle = False
        self.starting_changed.emit(True)

    def stop(self):
        self.calls.append("stop")
        self.stopping_changed.emit(True)


class AnalyzerViewModelTests(unittest.TestCase):
    def setUp(self):
        self.live, self.sweep = Live(), Sweep()
        self.model = AnalyzerViewModel(self.live, self.sweep)

    def tearDown(self):
        self.model.dispose()

    def test_mode_and_navigation_are_inert_and_start_requires_applied(self):
        self.assertTrue(self.model.select_mode(AnalyzerMode.SWEEP))
        self.assertEqual(self.live.calls + self.sweep.calls, [])
        self.assertFalse(self.model.start())
        self.live.state = replace(self.live.state, has_applied_configuration=False)
        self.assertFalse(self.model.start(ContinuousSweepPlanRequest(100e6, 200e6)))
        self.assertEqual(self.sweep.calls, [])

    def test_pending_start_and_stop_bar_mode_source_apply_and_restart(self):
        request = ContinuousSweepPlanRequest(100e6, 200e6)
        self.model.select_mode(AnalyzerMode.SWEEP)
        self.assertTrue(self.model.start(request))
        self.assertFalse(self.model.stop())
        for action in (
            lambda: self.model.select_mode(AnalyzerMode.RTBW),
            self.model.discover_devices,
            lambda: self.model.select_device("other"),
            lambda: self.model.select_manual_uri("usb:other"),
            lambda: self.model.apply_configuration(object()),
            lambda: self.model.start(request),
        ):
            self.assertFalse(action())
        self.sweep.running_changed.emit(True)
        self.sweep.starting_changed.emit(False)
        self.assertTrue(self.model.stop())
        self.sweep.running_changed.emit(False)
        self.assertFalse(self.model.select_mode(AnalyzerMode.RTBW))
        self.sweep.idle = True
        self.sweep.stopping_changed.emit(False)
        self.assertTrue(self.model.select_mode(AnalyzerMode.RTBW))
        self.assertEqual(self.sweep.calls, [("start", request), "stop"])
        self.assertEqual(self.live.calls, [])

    def test_owned_failure_exposes_stop_not_an_idle_restart(self):
        self.model.select_mode(AnalyzerMode.SWEEP)
        self.model.start(ContinuousSweepPlanRequest(100e6, 200e6))
        self.sweep.starting_changed.emit(False)
        self.sweep.task_failed.emit("receiver cleanup required")
        self.assertTrue(self.model.state.stop_required)
        self.assertEqual(self.model.state.error, "receiver cleanup required")
        self.assertFalse(self.model.select_mode(AnalyzerMode.RTBW))
        self.assertTrue(self.model.stop())

    def test_rtbw_start_uses_live_port_and_disposal_never_stops_owner(self):
        self.assertTrue(self.model.start())
        self.assertFalse(self.model.select_mode(AnalyzerMode.SWEEP))
        self.assertEqual(self.live.calls, [LiveAction.START])
        self.assertEqual(self.sweep.calls, [])
        self.model.dispose()
        self.model.dispose()
        self.assertFalse(self.model.start())
        self.assertEqual(self.live.callbacks, [])
        self.assertEqual(self.sweep.analyzer_ready.callbacks, [])
        self.assertEqual(self.sweep.snapshot_ready.callbacks, [])

    def test_apply_pending_blocks_source_mode_and_start_until_resolved(self):
        self.assertTrue(self.model.apply_configuration(object()))
        self.assertTrue(self.model.state.configuration_pending)
        self.assertFalse(self.model.select_mode(AnalyzerMode.SWEEP))
        self.assertFalse(self.model.select_device("other"))
        self.assertFalse(self.model.discover_devices())
        self.assertFalse(self.model.start())
        self.model.resolve_configuration_request()
        # Historical requested/applied difference means normalized readback,
        # not a new unsent UI draft. Start uses the displayed applied profile.
        self.live.state = replace(self.live.state, configuration_dirty=True)
        self.assertTrue(self.model.start())


if __name__ == "__main__":
    unittest.main()

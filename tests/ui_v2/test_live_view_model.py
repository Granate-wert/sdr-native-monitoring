from __future__ import annotations

from dataclasses import dataclass
import unittest

import numpy as np

from sdr_monitor.domain import LiveSessionState
from sdr_monitor.ui.v2.composition import compose_live_view_model
from sdr_monitor.ui.v2.state.live_view_state import LiveAction


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback: object) -> None:
        self.callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class FakePresenter:
    def __init__(self) -> None:
        self.devices_discovered = FakeSignal()
        self.snapshot_changed = FakeSignal()
        self.task_failed = FakeSignal()
        self.busy_changed = FakeSignal()
        self.render_ready = FakeSignal()
        self.discover_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.shutdown_calls = 0

    def discover_devices(self) -> None:
        self.discover_calls += 1

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    state: LiveSessionState
    spectrum: object | None = None
    unit: str = "dBFS/bin"
    device: object | None = None
    applied: object | None = None
    quality: object | None = None
    performance: object | None = None

    @property
    def reports_dbm(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class FakeSpectrum:
    timestamp_ns: int
    frequencies_hz: np.ndarray
    values: np.ndarray


class LiveViewModelTests(unittest.TestCase):
    def test_public_signals_publish_immutable_state_and_render_frame_identity(self) -> None:
        presenter = FakePresenter()
        model = compose_live_view_model(presenter, now_ns=lambda: 2_000_000_000)
        observed = []
        unsubscribe = model.subscribe(observed.append)
        spectrum = self._spectrum()
        snapshot = FakeSnapshot(state=LiveSessionState.RUNNING, spectrum=spectrum)
        presenter.snapshot_changed.emit(snapshot)
        presenter.render_ready.emit(snapshot)
        self.assertIs(model.state.snapshot, snapshot)
        self.assertIs(model.state.spectrum, spectrum)
        self.assertEqual(model.state.primary_action, LiveAction.STOP)
        self.assertGreaterEqual(len(observed), 3)
        unsubscribe()
        published = len(observed)
        presenter.snapshot_changed.emit(snapshot)
        self.assertEqual(len(observed), published)

    def test_primary_actions_only_delegate_to_public_presenter_commands(self) -> None:
        presenter = FakePresenter()
        model = compose_live_view_model(presenter, now_ns=lambda: 1)
        self.assertTrue(model.execute_primary_action())
        self.assertEqual(presenter.discover_calls, 1)

        presenter.snapshot_changed.emit(FakeSnapshot(state=LiveSessionState.CONNECTED))
        self.assertTrue(model.execute_primary_action())
        self.assertEqual(presenter.start_calls, 1)

        presenter.snapshot_changed.emit(FakeSnapshot(state=LiveSessionState.RUNNING))
        self.assertTrue(model.execute_primary_action())
        self.assertEqual(presenter.stop_calls, 1)

        presenter.snapshot_changed.emit(FakeSnapshot(state=LiveSessionState.ERROR))
        self.assertFalse(model.execute_primary_action())
        self.assertEqual(presenter.shutdown_calls, 0)

    def test_busy_disables_control_and_dispose_never_owns_presenter_shutdown(self) -> None:
        presenter = FakePresenter()
        model = compose_live_view_model(presenter, now_ns=lambda: 1)
        presenter.busy_changed.emit(True)
        self.assertTrue(model.state.busy)
        self.assertFalse(model.state.primary_action_enabled)
        self.assertFalse(model.execute_primary_action())
        model.dispose()
        self.assertEqual(presenter.shutdown_calls, 0)
        self.assertEqual(presenter.snapshot_changed.callbacks, [])
        self.assertEqual(presenter.render_ready.callbacks, [])

    def test_discovery_payload_is_published_only_when_it_is_a_device_sequence(self) -> None:
        presenter = FakePresenter()
        model = compose_live_view_model(presenter, now_ns=lambda: 1)
        observed: list[tuple[object, ...]] = []
        model.subscribe_devices(observed.append)
        presenter.devices_discovered.emit("not-a-device-list")
        self.assertEqual(model.devices, ())
        self.assertEqual(observed[-1], ())
        device = object()
        presenter.devices_discovered.emit((device,))
        self.assertEqual(model.devices, (device,))
        self.assertEqual(observed[-1], (device,))

    @staticmethod
    def _spectrum() -> FakeSpectrum:
        frequencies = np.array([1.0, 2.0], dtype=np.float64)
        values = np.array([-1.0, -2.0], dtype=np.float32)
        frequencies.setflags(write=False)
        values.setflags(write=False)
        return FakeSpectrum(timestamp_ns=1_000_000_000, frequencies_hz=frequencies, values=values)

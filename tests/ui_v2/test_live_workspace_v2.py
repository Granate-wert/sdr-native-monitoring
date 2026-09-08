"""Offscreen UI2-07 core tests with an injected fake public presenter only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from sdr_monitor.domain import AppliedLiveConfiguration, BackendKind, LiveConfiguration, LiveSessionState
from sdr_monitor.ui.v2.composition import compose_live_view_model
from sdr_monitor.ui.v2.i18n import text
from sdr_monitor.ui.v2.spectrum import DensityValueMode, PersistenceDensityFrame
from sdr_monitor.ui.v2.waterfall import WaterfallLineFrame
from sdr_monitor.ui.v2.workspaces import LiveWorkspaceV2, live_workspace_definition


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[Callable[..., object]] = []

    def connect(self, callback: Callable[..., object]) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback: Callable[..., object]) -> None:
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
        self.selected: list[str] = []
        self.manual_uris: list[str] = []
        self.configurations: list[object] = []
        self.start_calls = 0
        self.stop_calls = 0

    def discover_devices(self) -> None:
        self.discover_calls += 1

    def select_device(self, device_id: str) -> None:
        self.selected.append(device_id)

    def select_manual_uri(self, uri: str) -> None:
        self.manual_uris.append(uri)

    def apply_configuration(self, configuration: object) -> None:
        self.configurations.append(configuration)

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


@dataclass(frozen=True, slots=True)
class FakeQuality:
    calibration: str = "uncalibrated"
    backend: BackendKind = BackendKind.CPU
    fallback_reason: str | None = None
    dropped_blocks: int = 0


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    state: LiveSessionState
    device: object | None = None
    applied: object | None = None
    quality: object | None = None
    spectrum: object | None = None
    persistence: object | None = None
    waterfall_line: object | None = None
    unit: str = "dBm"
    error: str | None = None
    error_kind: str | None = None

    @property
    def reports_dbm(self) -> bool:
        return False


class LiveWorkspaceV2Tests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.presenter = FakePresenter()
        self.model = compose_live_view_model(self.presenter, now_ns=lambda: 1_000_000_000)
        self.workspace = LiveWorkspaceV2(self.model)
        self.workspace.resize(1366, 768)
        self.workspace.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.workspace.close()
        self.workspace.deleteLater()
        self.model.dispose()
        self.app.processEvents()

    def test_discovery_selection_and_manual_uri_use_only_view_model_commands(self) -> None:
        self.workspace._primary_action.click()
        self.assertEqual(self.presenter.discover_calls, 1)
        device = type("Device", (), {"device_id": "safe-device", "label": "Test SDR"})()
        self.presenter.devices_discovered.emit((device,))
        self.workspace._device_selector.setCurrentIndex(1)
        self.assertEqual(self.presenter.selected, ["safe-device"])
        self.workspace._manual_uri.set_value("ip:192.168.2.1")
        self.workspace._select_manual_uri()
        self.assertEqual(self.presenter.manual_uris, ["ip:192.168.2.1"])

    def test_space_on_focused_spectrum_delegates_only_available_start_or_stop(self) -> None:
        applied = LiveConfiguration()
        self.presenter.snapshot_changed.emit(
            FakeSnapshot(
                state=LiveSessionState.CONNECTED,
                applied=AppliedLiveConfiguration(requested=applied, applied=applied),
                quality=FakeQuality(),
            )
        )
        scene = self.workspace.visualization.spectrum_scene
        scene.setFocus(Qt.FocusReason.TabFocusReason)
        self.app.processEvents()
        QTest.keyClick(scene, Qt.Key.Key_Space)
        self.assertEqual(self.presenter.start_calls, 1)
        self.assertEqual(self.presenter.discover_calls, 0)

        self.presenter.snapshot_changed.emit(
            FakeSnapshot(
                state=LiveSessionState.RUNNING,
                applied=AppliedLiveConfiguration(requested=applied, applied=applied),
                quality=FakeQuality(),
            )
        )
        QTest.keyClick(scene, Qt.Key.Key_Space)
        self.assertEqual(self.presenter.stop_calls, 1)

        self.presenter.snapshot_changed.emit(
            FakeSnapshot(state=LiveSessionState.DISCONNECTED, quality=FakeQuality())
        )
        QTest.keyClick(scene, Qt.Key.Key_Space)
        self.assertEqual(self.presenter.discover_calls, 0)

    def test_tab_leaves_manual_uri_for_explicit_actions_without_issuing_commands(self) -> None:
        manual_action = next(
            button
            for button in self.workspace.findChildren(QPushButton)
            if button.accessibleName() == text("live.uri.use.name")
        )
        uri_input = self.workspace._manual_uri.input
        uri_input.setFocus(Qt.FocusReason.TabFocusReason)
        self.app.processEvents()

        QTest.keyClick(uri_input, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertIs(self.app.focusWidget(), manual_action)

        QTest.keyClick(manual_action, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertIs(self.app.focusWidget(), self.workspace._primary_action)
        self.assertEqual(self.presenter.discover_calls, 0)
        self.assertEqual(self.presenter.manual_uris, [])
        self.assertEqual(self.presenter.configurations, [])
        self.assertEqual(self.presenter.start_calls, 0)
        self.assertEqual(self.presenter.stop_calls, 0)
        self.assertEqual(self.workspace.visualization.spectrum_scene.property("ui2FocusRing"), True)

    def test_manual_uri_is_checked_before_the_presenter_is_called(self) -> None:
        self.workspace._manual_uri.set_value("not a uri")
        self.workspace._select_manual_uri()
        self.assertEqual(self.presenter.manual_uris, [])
        self.assertTrue(self.workspace._manual_uri_error.isVisible())
        self.assertIn("URI", self.workspace._manual_uri_error.text())
        self.workspace._manual_uri.set_value("USB:")
        self.workspace._select_manual_uri()
        self.assertEqual(self.presenter.manual_uris, ["usb:"])
        self.assertFalse(self.workspace._manual_uri_error.isVisible())

    def test_only_declared_configuration_fields_are_constructed_and_applied(self) -> None:
        applied = LiveConfiguration(profile_id="existing-profile")
        self.presenter.snapshot_changed.emit(
            FakeSnapshot(
                state=LiveSessionState.CONNECTED,
                applied=AppliedLiveConfiguration(requested=applied, applied=applied),
                quality=FakeQuality(),
            )
        )
        self.workspace._center_mhz.setValue(433.92)
        self.workspace._sample_rate_mhz.setValue(10.0)
        self.workspace._gain_db.setValue(22.0)
        self.workspace._backend.setCurrentIndex(self.workspace._backend.findData("cpu"))
        self.workspace._apply_button.click()
        self.assertEqual(len(self.presenter.configurations), 1)
        configuration = self.presenter.configurations[0]
        self.assertIsInstance(configuration, LiveConfiguration)
        assert isinstance(configuration, LiveConfiguration)
        self.assertEqual(configuration.center_hz, 433_920_000.0)
        self.assertEqual(configuration.sample_rate_hz, 10_000_000.0)
        self.assertEqual(configuration.gain_db, 22.0)
        self.assertEqual(configuration.backend, BackendKind.CPU)
        self.assertEqual(configuration.profile_id, "existing-profile")
        self.assertFalse(self.workspace._recording.isEnabled())

    def test_primary_action_applies_dirty_configuration_before_start(self) -> None:
        requested = LiveConfiguration(center_hz=433_920_000.0)
        applied = LiveConfiguration(center_hz=434_000_000.0)
        self.presenter.snapshot_changed.emit(
            FakeSnapshot(
                state=LiveSessionState.CONNECTED,
                applied=AppliedLiveConfiguration(requested=requested, applied=applied),
                quality=FakeQuality(),
            )
        )
        self.workspace._primary_action.click()
        self.assertEqual(len(self.presenter.configurations), 1)
        self.assertEqual(self.presenter.start_calls, 0)
        self.assertEqual(self.workspace._primary_action.text(), "Применить настройки")

    def test_local_configuration_is_distinct_from_applied_and_can_be_cancelled(self) -> None:
        applied = LiveConfiguration(center_hz=434_000_000.0, profile_id="applied-profile")
        self.presenter.snapshot_changed.emit(
            FakeSnapshot(
                state=LiveSessionState.CONNECTED,
                applied=AppliedLiveConfiguration(requested=applied, applied=applied),
                quality=FakeQuality(),
            )
        )
        self.workspace._center_mhz.setValue(433.92)
        self.assertTrue(self.workspace._cancel_button.isEnabled())
        self.assertEqual(self.workspace._primary_action.text(), "Применить настройки")
        self.assertIn("не применены", self.workspace._configuration_status.text())
        self.workspace._cancel_button.click()
        self.assertEqual(self.workspace._center_mhz.value(), 434.0)
        self.assertFalse(self.workspace._cancel_button.isEnabled())
        self.assertIn("applied-profile", self.workspace._configuration_status.text())
        self.workspace._center_mhz.setValue(433.92)
        self.workspace._primary_action.click()
        self.assertEqual(len(self.presenter.configurations), 1)
        self.assertEqual(self.presenter.start_calls, 0)

    def test_snapshot_error_is_visible_without_an_invented_recovery_command(self) -> None:
        self.presenter.snapshot_changed.emit(
            FakeSnapshot(
                state=LiveSessionState.ERROR,
                quality=FakeQuality(),
                error="Приёмник не ответил",
                error_kind="stream_start_failed",
            )
        )
        self.assertTrue(self.workspace._error_banner.isVisible())
        self.assertIn("Приёмник не ответил", self.workspace._error_banner.accessibleDescription())
        self.assertIn("stream_start_failed", self.workspace._error_banner.accessibleDescription())
        self.assertFalse(self.workspace._error_banner._action.isVisible())
        self.assertFalse(self.workspace._primary_action.isEnabled())

    def test_explicit_published_spectrum_persistence_and_waterfall_render_without_inference(self) -> None:
        frequencies = np.linspace(433_000_000.0, 434_000_000.0, 16)
        values = np.linspace(-110.0, -30.0, 16, dtype=np.float32)
        frequencies.setflags(write=False)
        values.setflags(write=False)
        spectrum = type("Spectrum", (), {"frequencies_hz": frequencies, "values": values})()
        density = PersistenceDensityFrame(
            density=np.array(((0.0, 0.5), (0.2, 1.0)), dtype=np.float32),
            frequency_edges_hz=np.linspace(433_000_000.0, 434_000_000.0, 3),
            level_edges=np.linspace(-120.0, -20.0, 3),
            value_mode=DensityValueMode.PROBABILITY,
            level_unit="dBm",
        )
        waterfall = WaterfallLineFrame(
            values=np.array((-110.0, -80.0), dtype=np.float32),
            frequency_edges_hz=np.linspace(433_000_000.0, 434_000_000.0, 3),
            timestamp_ns=1_000_000_000,
            configuration_generation=1,
            unit_label="dBm",
        )
        snapshot = FakeSnapshot(
            state=LiveSessionState.RUNNING,
            applied=AppliedLiveConfiguration(
                requested=LiveConfiguration(), applied=LiveConfiguration()
            ),
            quality=FakeQuality(),
            spectrum=spectrum,
            persistence=density,
            waterfall_line=waterfall,
        )
        self.presenter.render_ready.emit(snapshot)
        self.assertIs(self.workspace.visualization.spectrum_scene.latest_frame.frequencies_hz, frequencies)
        self.assertEqual(self.workspace.visualization.spectrum_scene.persistence_metrics.image_uploads, 1)
        self.assertEqual(self.workspace.visualization.waterfall_pane.history_rows, 1)

    def test_mutable_or_unitless_optional_spectrum_is_rejected_without_repair(self) -> None:
        frequencies = np.array((433_000_000.0, 434_000_000.0), dtype=np.float64)
        values = np.array((-90.0, -80.0), dtype=np.float32)
        mutable = type("Spectrum", (), {"frequencies_hz": frequencies, "values": values})()
        self.presenter.render_ready.emit(
            FakeSnapshot(state=LiveSessionState.RUNNING, quality=FakeQuality(), spectrum=mutable)
        )
        self.assertIsNone(self.workspace.visualization.spectrum_scene.latest_frame)
        frequencies.setflags(write=False)
        values.setflags(write=False)
        immutable = type("Spectrum", (), {"frequencies_hz": frequencies, "values": values})()
        self.presenter.render_ready.emit(
            FakeSnapshot(
                state=LiveSessionState.RUNNING,
                quality=FakeQuality(),
                spectrum=immutable,
                unit="",
            )
        )
        self.assertIsNone(self.workspace.visualization.spectrum_scene.latest_frame)

    def test_definition_is_external_and_factory_is_lazy(self) -> None:
        definition = live_workspace_definition(self.model)
        self.assertEqual(definition.workspace_id, "live")
        self.assertEqual(definition.label, "Приём")
        page = definition.workspace_factory()
        self.assertIsInstance(page, LiveWorkspaceV2)
        page.close()
        page.deleteLater()

    def test_live_inspector_shows_only_applied_public_fields_and_frame_presence(self) -> None:
        applied = LiveConfiguration(
            center_hz=433_920_000.0,
            sample_rate_hz=10_000_000.0,
            gain_db=22.0,
            backend=BackendKind.CPU,
            profile_id="inspector-profile",
        )
        definition = live_workspace_definition(self.model)
        inspector = definition.inspector_factory()
        self.presenter.snapshot_changed.emit(
            FakeSnapshot(
                state=LiveSessionState.CONNECTED,
                applied=AppliedLiveConfiguration(requested=applied, applied=applied),
                quality=FakeQuality(),
            )
        )
        self.app.processEvents()
        applied_readout = inspector.findChild(QLabel, "v2-live-inspector-applied")
        frame_readout = inspector.findChild(QLabel, "v2-live-inspector-frames")
        self.assertIsNotNone(applied_readout)
        self.assertIsNotNone(frame_readout)
        assert applied_readout is not None and frame_readout is not None
        self.assertIn("433.920 МГц", applied_readout.text())
        self.assertIn("inspector-profile", applied_readout.text())
        self.assertIn("Спектр: не опубликован", frame_readout.text())
        inspector.close()
        inspector.deleteLater()

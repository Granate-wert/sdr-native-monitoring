"""P16UI-04 Live Monitor presenter, workspace, dialog and profile tests."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import ClassVar
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl.sdr.contracts import (
    CalibrationStatus,
    ComputeBackendKind,
    DetectorType,
    DeviceConfig,
    DspConfig,
    GainMode,
    PersistenceConfig,
    SpectrumUnit,
    WindowType,
)
from esw_dfl.sdr.controller import LiveControllerState, LiveControllerUpdate
from esw_dfl.sdr.fake_live_service import (
    FakeLiveConfig,
    FakeLiveService,
    fake_capabilities,
)
from esw_dfl.sdr.fixed_band import FixedBandOptions
from esw_dfl.sdr.live_profile import DeviceProfile, DeviceProfileStore, ProfileStoreError
from esw_dfl.sdr.pluto import PlutoContextSummary
from esw_dfl.ui.i18n import LocaleId, Translator, validate_catalogs
from esw_dfl.ui.live_dialog import DeviceDiscoveryDialog
from esw_dfl.ui.live_discovery import (
    DeviceKind,
    DiscoveredDevice,
    DiscoveryError,
    discover_devices,
    parse_manual_uri,
)
from esw_dfl.ui.live_presenter import LiveMonitorPresenter
from esw_dfl.ui.live_workspace import LiveMonitorWorkspace


def _options(
    *,
    center_hz: float = 2.401e9,
    rate_hz: float = 3.0e6,
    bandwidth_hz: float = 3.0e6,
    gain_db: float = 30.0,
    fft_size: int = 1024,
    hop_size: int = 512,
    backend: ComputeBackendKind = ComputeBackendKind.CPU,
    allow_fallback: bool = True,
    unit: SpectrumUnit = SpectrumUnit.DBFS_BIN,
) -> FixedBandOptions:
    device = DeviceConfig(
        source_id="fake-1",
        context_uri="ip:fake",
        center_frequency_hz=center_hz,
        sample_rate_hz=rate_hz,
        analog_bandwidth_hz=bandwidth_hz,
        gain_mode=GainMode.MANUAL,
        manual_gain_db=gain_db,
    )
    calibrated = unit in (SpectrumUnit.DBM, SpectrumUnit.DBM_BIN, SpectrumUnit.DBM_HZ)
    dsp = DspConfig(
        fft_size=fft_size,
        hop_size=hop_size,
        window=WindowType.HANN,
        detector=DetectorType.PEAK,
        unit=unit,
        calibration_status=(
            CalibrationStatus.APPLIED if calibrated else CalibrationStatus.UNCALIBRATED
        ),
        calibration_profile_id="cal-1" if calibrated else None,
    )
    return FixedBandOptions(
        device=device,
        dsp=dsp,
        persistence=PersistenceConfig(),
        backend=backend,
        allow_runtime_fallback=allow_fallback,
    )


def _presenter(
    config: FakeLiveConfig | None = None,
    **kwargs: object,
) -> LiveMonitorPresenter:
    cfg = config if config is not None else FakeLiveConfig()
    return LiveMonitorPresenter(
        service_factory=lambda uri: FakeLiveService(uri, config=cfg),
        capabilities_provider=lambda uri: fake_capabilities(cfg),
        poll_interval_s=float(kwargs.pop("poll_interval_s", 0.01)),
        **kwargs,  # type: ignore[arg-type]
    )


def _wait_for(predicate, timeout_s: float = 4.0, step_s: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return bool(predicate())


class LiveMonitorPresenterTests(unittest.TestCase):
    """Presenter owns one FakeLiveService session and publishes snapshots."""

    def tearDown(self) -> None:
        if hasattr(self, "_presenter"):
            self._presenter.close()

    def _make(self, config: FakeLiveConfig | None = None) -> LiveMonitorPresenter:
        self._presenter = _presenter(config)
        return self._presenter

    # ------------------------------------------------------------------
    # Connect / validation
    # ------------------------------------------------------------------
    def test_connect_accepts_valid_options(self) -> None:
        presenter = self._make()
        errors = presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        self.assertEqual(errors, [])
        self.assertTrue(presenter.connected)
        self.assertIsNotNone(presenter.requested_options)
        self.assertEqual(presenter.snapshot.state, LiveControllerState.CREATED)
        self.assertGreater(len(presenter.snapshot.requested_applied), 0)

    def test_connect_rejects_center_outside_tuning_range(self) -> None:
        presenter = self._make()
        errors = presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(center_hz=100.0e9),
        )
        self.assertNotEqual(errors, [])
        self.assertTrue(any("tuning range" in message for message in errors))
        self.assertFalse(presenter.connected)

    def test_connect_rejects_gain_outside_range(self) -> None:
        presenter = self._make()
        errors = presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(gain_db=200.0),
        )
        self.assertTrue(any("gain" in message for message in errors))
        self.assertFalse(presenter.connected)

    def test_connect_reports_capabilities_probe_failure(self) -> None:
        def broken_provider(uri: str) -> None:
            raise RuntimeError("probe boom")

        presenter = LiveMonitorPresenter(
            service_factory=lambda uri: FakeLiveService(uri),
            capabilities_provider=broken_provider,  # type: ignore[arg-type]
            poll_interval_s=0.01,
        )
        self._presenter = presenter
        errors = presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("capabilities probe failed", errors[0])
        self.assertFalse(presenter.connected)

    def test_connect_without_capabilities_is_structural_only(self) -> None:
        presenter = LiveMonitorPresenter(
            service_factory=lambda uri: FakeLiveService(uri),
            capabilities_provider=None,
            poll_interval_s=0.01,
        )
        self._presenter = presenter
        errors = presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(center_hz=100.0e9),
        )
        self.assertEqual(errors, [])  # ranges are NOT_VERIFIED without caps

    # ------------------------------------------------------------------
    # Lifecycle: start / stop / close
    # ------------------------------------------------------------------
    def test_start_reaches_running_and_publishes_applied(self) -> None:
        presenter = self._make()
        self.assertEqual(presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        ), [])
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        # The RUNNING publication carries the applied config; the metrics
        # arrive with the first poll-loop update.
        self.assertTrue(_wait_for(
            lambda: presenter.poll().frame_rate_hz > 0.0
        ))
        snapshot = presenter.snapshot
        self.assertEqual(snapshot.state, LiveControllerState.RUNNING)
        self.assertFalse(snapshot.stale)
        self.assertIsNotNone(snapshot.backend)
        self.assertEqual(snapshot.backend.active, ComputeBackendKind.CPU)
        self.assertGreater(snapshot.frame_rate_hz, 0.0)
        rows = {row.field: row for row in snapshot.requested_applied}
        self.assertIsNotNone(rows["center_frequency_hz"].applied)
        self.assertFalse(rows["center_frequency_hz"].pending)

    def test_stop_returns_to_stopped(self) -> None:
        presenter = self._make()
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        presenter.stop()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.STOPPED
        ))

    def test_close_active_stream_disconnects(self) -> None:
        presenter = self._make()
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        presenter.close()
        self.assertFalse(presenter.connected)
        self.assertIsNone(presenter.requested_options)
        self.assertEqual(presenter.snapshot.state, LiveControllerState.CREATED)

    def test_disconnect_resets_capabilities_cache(self) -> None:
        presenter = self._make()
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        self.assertIsNotNone(presenter.capabilities)
        presenter.disconnect()
        self.assertIsNone(presenter.capabilities)
        self.assertFalse(presenter.connected)

    # ------------------------------------------------------------------
    # Apply requested
    # ------------------------------------------------------------------
    def test_apply_requested_restarts_with_new_options(self) -> None:
        presenter = self._make()
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        errors = presenter.apply_requested(_options(fft_size=2048, hop_size=1024))
        self.assertEqual(errors, [])
        self.assertIsNotNone(presenter.requested_options)
        self.assertEqual(presenter.requested_options.dsp.fft_size, 2048)
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))

    def test_apply_requested_without_session_connects(self) -> None:
        presenter = self._make()
        errors = presenter.apply_requested(_options())
        self.assertEqual(errors, [])
        self.assertTrue(presenter.connected)

    def test_apply_requested_rejects_invalid_and_keeps_session(self) -> None:
        presenter = self._make()
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        errors = presenter.apply_requested(_options(center_hz=100.0e9))
        self.assertNotEqual(errors, [])
        self.assertTrue(presenter.connected)
        self.assertEqual(presenter.requested_options.dsp.fft_size, 1024)

    # ------------------------------------------------------------------
    # Applied adjustment (device rounds the requested frequency)
    # ------------------------------------------------------------------
    def test_applied_adjustment_is_surfaced_as_pending(self) -> None:
        config = FakeLiveConfig(center_frequency_step_hz=10.0e6)
        presenter = self._make(config)
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(center_hz=2.401e9),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        rows = {row.field: row for row in presenter.snapshot.requested_applied}
        center = rows["center_frequency_hz"]
        self.assertTrue(center.pending)
        self.assertEqual(center.applied, "2,400 GHz")
        self.assertEqual(center.requested, "2,401 GHz")

    # ------------------------------------------------------------------
    # Backend availability / fallback
    # ------------------------------------------------------------------
    def test_explicit_cuda_unavailable_without_fallback_errors(self) -> None:
        config = FakeLiveConfig(available_backends=(ComputeBackendKind.CPU,))
        presenter = self._make(config)
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(backend=ComputeBackendKind.CUDA, allow_fallback=False),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.ERROR
        ))
        self.assertIn("unavailable", presenter.snapshot.error or "")

    def test_cuda_unavailable_falls_back_to_cpu(self) -> None:
        config = FakeLiveConfig(available_backends=(ComputeBackendKind.CPU,))
        presenter = self._make(config)
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(backend=ComputeBackendKind.CUDA, allow_fallback=True),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        # fallback count is published with the first metrics update
        self.assertTrue(_wait_for(
            lambda: presenter.poll().backend is not None
            and presenter.poll().backend.fallback_count >= 1
        ))
        badge = presenter.snapshot.backend
        self.assertIsNotNone(badge)
        self.assertEqual(badge.active, ComputeBackendKind.CPU)
        self.assertGreaterEqual(badge.fallback_count, 1)
        self.assertIn("fallback", badge.note or "")
        rows = {row.field: row for row in presenter.snapshot.requested_applied}
        self.assertTrue(rows["backend"].pending)  # requested cuda, applied cpu

    def test_auto_backend_selects_first_available(self) -> None:
        config = FakeLiveConfig(
            available_backends=(ComputeBackendKind.CPU, ComputeBackendKind.CUDA)
        )
        presenter = self._make(config)
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(backend=ComputeBackendKind.AUTO),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        badge = presenter.snapshot.backend
        self.assertIsNotNone(badge)
        self.assertEqual(badge.active, ComputeBackendKind.CPU)
        self.assertIn("auto selected", badge.note or "")
        rows = {row.field: row for row in presenter.snapshot.requested_applied}
        self.assertFalse(rows["backend"].pending)

    # ------------------------------------------------------------------
    # Stale generation guard
    # ------------------------------------------------------------------
    def test_stale_update_from_previous_generation_is_flagged(self) -> None:
        presenter = self._make()
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        controller = presenter._controller  # white-box: presenter owns the queue
        self.assertIsNotNone(controller)
        controller._publish(LiveControllerUpdate(  # noqa: SLF001 - unit test
            generation=9999,
            state=LiveControllerState.RUNNING,
            emitted_at=time.monotonic(),
        ))
        snapshot = presenter.poll()
        self.assertTrue(snapshot.stale)

    # ------------------------------------------------------------------
    # Quality / calibration
    # ------------------------------------------------------------------
    def test_dropped_counters_surface_quality_items(self) -> None:
        config = FakeLiveConfig(
            dropped_iq_blocks_before=2,
            dropped_fft_frames_before=1,
        )
        presenter = self._make(config)
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        # quality items are derived from metrics, published one update later
        self.assertTrue(_wait_for(
            lambda: any(
                item.label == "IQ blocks dropped"
                for item in presenter.poll().quality
            )
        ))
        labels = {item.label for item in presenter.snapshot.quality}
        self.assertIn("IQ blocks dropped", labels)
        self.assertIn("FFT frames dropped", labels)
        self.assertIn("Engine health", labels)
        values = {item.label: item.value for item in presenter.snapshot.quality}
        self.assertEqual(values["IQ blocks dropped"], "2")
        self.assertEqual(values["FFT frames dropped"], "1")

    def test_calibration_badge_reflects_frame(self) -> None:
        config = FakeLiveConfig(
            calibration_status=CalibrationStatus.APPLIED,
            calibration_profile_id="cal-1",
        )
        presenter = self._make(config)
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(unit=SpectrumUnit.DBM),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        self.assertTrue(_wait_for(
            lambda: presenter.poll().calibration is not None
        ))
        badge = presenter.snapshot.calibration
        self.assertIsNotNone(badge)
        self.assertEqual(badge.status, CalibrationStatus.APPLIED)
        self.assertEqual(badge.profile_id, "cal-1")
        self.assertTrue(badge.applicable)

    def test_metrics_only_update_keeps_calibration_badge(self) -> None:
        config = FakeLiveConfig(
            calibration_status=CalibrationStatus.APPLIED,
            calibration_profile_id="cal-1",
            frames_per_poll=1,
            max_frames=2,
        )
        presenter = self._make(config)
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(unit=SpectrumUnit.DBM),
        )
        presenter.start()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        self.assertTrue(_wait_for(
            lambda: presenter.poll().calibration is not None
        ))
        # Once the fake frame budget is exhausted the controller still
        # publishes metrics-only updates; the badge must not blink away.
        self.assertTrue(_wait_for(
            lambda: presenter.poll().frame_rate_hz > 0.0
            and presenter.poll().calibration is not None
        ))
        self.assertEqual(presenter.snapshot.calibration.status, CalibrationStatus.APPLIED)

    # ------------------------------------------------------------------
    # Recording hook
    # ------------------------------------------------------------------
    def test_request_recording_toggles_hook_state(self) -> None:
        presenter = self._make()
        presenter.request_recording(True)
        self.assertIsNotNone(presenter.snapshot.recording)
        self.assertTrue(presenter.snapshot.recording.active)
        presenter.request_recording(False)
        self.assertFalse(presenter.snapshot.recording.active)

    # ------------------------------------------------------------------
    # Slow UI / 60 Hz budget
    # ------------------------------------------------------------------
    def test_idle_poll_returns_same_snapshot_object(self) -> None:
        presenter = self._make()
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        first = presenter.poll()
        for _ in range(50):
            self.assertIs(presenter.poll(), first)

    def test_60hz_poll_budget_stays_flat_when_idle(self) -> None:
        presenter = self._make()
        presenter.connect(
            source_id="fake-1",
            display_name="Fake Pluto",
            uri="ip:fake",
            options=_options(),
        )
        started = time.monotonic()
        for _ in range(5000):
            presenter.poll()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0)  # generous CI bound, ~60 Hz for 80 s

    # ------------------------------------------------------------------
    # Constructor validation
    # ------------------------------------------------------------------
    def test_constructor_rejects_non_positive_poll_interval(self) -> None:
        with self.assertRaises(ValueError):
            LiveMonitorPresenter(poll_interval_s=0.0)
        with self.assertRaises(ValueError):
            LiveMonitorPresenter(update_queue_capacity=0)


class LiveMonitorWorkspaceTests(unittest.TestCase):
    """Workspace renders presenter snapshots and wires user actions."""

    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._workspace: LiveMonitorWorkspace | None = None

    def tearDown(self) -> None:
        if self._workspace is not None:
            self._workspace.close()
            self._workspace.deleteLater()

    def _make(
        self,
        config: FakeLiveConfig | None = None,
        *,
        discovery=None,
    ) -> tuple[LiveMonitorWorkspace, LiveMonitorPresenter]:
        cfg = config if config is not None else FakeLiveConfig()
        presenter = LiveMonitorPresenter(
            service_factory=lambda uri: FakeLiveService(uri, config=cfg),
            capabilities_provider=lambda uri: fake_capabilities(cfg),
            poll_interval_s=0.01,
        )
        workspace = LiveMonitorWorkspace(
            presenter=presenter,
            discovery=discovery or (lambda: ("fake-1", "Fake Pluto", "ip:fake")),
            poll_interval_ms=16,
        )
        self._workspace = workspace
        return workspace, presenter

    # ------------------------------------------------------------------
    # Initial state
    # ------------------------------------------------------------------
    def test_initial_state_is_idle(self) -> None:
        workspace, _ = self._make()
        self.assertEqual(workspace.device_label.text(), workspace._tr.text("live.no_device"))  # noqa: SLF001
        self.assertEqual(workspace.connect_button.text(), workspace._tr.text("live.connect"))
        self.assertFalse(workspace.start_button.isEnabled())
        self.assertEqual(workspace._requested_applied_view.rowCount(), 0)  # noqa: SLF001
        self.assertEqual(workspace.markers, ())

    def test_build_options_reflects_default_controls(self) -> None:
        workspace, _ = self._make()
        options = workspace.build_options(source_id="fake-1", context_uri="ip:fake")
        self.assertEqual(options.device.center_frequency_hz, 2.401e9)
        self.assertEqual(options.device.sample_rate_hz, 3.0e6)
        self.assertEqual(options.device.analog_bandwidth_hz, 3.0e6)
        self.assertEqual(options.dsp.fft_size, 1024)
        self.assertEqual(options.dsp.hop_size, 512)
        self.assertEqual(options.backend, ComputeBackendKind.AUTO)

    # ------------------------------------------------------------------
    # Connect flow
    # ------------------------------------------------------------------
    def test_connect_flow_updates_device_and_applies_capabilities(self) -> None:
        workspace, presenter = self._make()
        workspace.connect_button.click()
        self.assertTrue(presenter.connected)
        self.assertEqual(workspace.device_label.text(), "Fake Pluto")
        self.assertEqual(workspace.connect_button.text(), workspace._tr.text("live.disconnect"))  # noqa: SLF001
        self.assertTrue(workspace.start_button.isEnabled())
        # capability-aware gain range (fake caps: 0..74.5 dB)
        self.assertEqual(workspace.gain_spin.maximum(), 74.5)

    def test_connect_cancelled_leaves_disconnected(self) -> None:
        workspace, presenter = self._make(discovery=lambda: None)
        workspace.connect_button.click()
        self.assertFalse(presenter.connected)
        self.assertEqual(workspace.connect_button.text(), workspace._tr.text("live.connect"))  # noqa: SLF001

    def test_connect_rejection_surfaces_error_badge(self) -> None:
        workspace, presenter = self._make()
        workspace.center_input.set_frequency_hz(100.0e9)  # outside fake tuning range
        workspace.connect_button.click()
        self.assertFalse(presenter.connected)
        self.assertIn("tuning range", workspace.health_badge.toolTip())

    def test_disconnect_flow_resets_device_label(self) -> None:
        workspace, presenter = self._make()
        workspace.connect_button.click()
        self.assertTrue(presenter.connected)
        workspace.connect_button.click()
        self.assertFalse(presenter.connected)
        self.assertEqual(workspace.device_label.text(), workspace._tr.text("live.no_device"))  # noqa: SLF001

    # ------------------------------------------------------------------
    # Apply / start / stop / record
    # ------------------------------------------------------------------
    def test_apply_button_updates_requested_options(self) -> None:
        workspace, presenter = self._make()
        workspace.connect_button.click()
        workspace.center_input.set_frequency_hz(2.5e9)
        workspace.apply_button.click()
        self.assertIsNotNone(presenter.requested_options)
        self.assertEqual(presenter.requested_options.device.center_frequency_hz, 2.5e9)

    def test_start_stop_button_toggles_lifecycle(self) -> None:
        workspace, presenter = self._make()
        workspace.connect_button.click()
        workspace.start_button.click()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        workspace._poll_presenter()  # noqa: SLF001 - offscreen timer never fires
        self.assertEqual(workspace.start_button.text(), workspace._tr.text("live.stop"))  # noqa: SLF001
        workspace.start_button.click()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.STOPPED
        ))
        workspace._poll_presenter()  # noqa: SLF001 - offscreen timer never fires
        self.assertEqual(workspace.start_button.text(), workspace._tr.text("live.start"))  # noqa: SLF001

    def test_record_button_toggles_hook(self) -> None:
        workspace, presenter = self._make()
        workspace.record_button.setChecked(True)
        self.assertTrue(presenter.snapshot.recording.active)
        workspace.record_button.setChecked(False)
        self.assertFalse(presenter.snapshot.recording.active)

    # ------------------------------------------------------------------
    # Markers
    # ------------------------------------------------------------------
    def test_markers_add_remove_and_delta(self) -> None:
        workspace, _ = self._make()
        workspace.add_marker_button.click()
        self.assertEqual(workspace.markers, (2.401e9,))
        workspace.center_input.frequency_accepted.emit(2.5e9)
        self.assertEqual(workspace.markers, (2.401e9, 2.5e9))
        self.assertNotEqual(workspace.marker_delta_label.text(), "")
        workspace.markers_list.setCurrentRow(0)
        workspace.remove_marker_button.click()
        self.assertEqual(workspace.markers, (2.5e9,))

    def test_add_marker_with_empty_input_does_not_crash(self) -> None:
        workspace, _ = self._make()
        workspace.center_input.setText("")
        workspace.add_marker_button.click()  # must not raise
        self.assertEqual(workspace.markers, ())

    # ------------------------------------------------------------------
    # Expert controls
    # ------------------------------------------------------------------
    def test_fft_change_disables_invalid_hop_choices(self) -> None:
        workspace, _ = self._make()
        workspace.fft_combo.setCurrentIndex(0)  # 256
        hop_model = workspace.hop_combo.model()
        hop_512_disabled = not hop_model.item(3).isEnabled()  # 512 > 256
        self.assertTrue(hop_512_disabled)
        workspace.fft_combo.setCurrentIndex(5)  # 4096
        self.assertTrue(hop_model.item(3).isEnabled())

    def test_capability_ranges_repopulate_gain_mode_choices(self) -> None:
        config = FakeLiveConfig(
            gain_modes=(GainMode.MANUAL, GainMode.SLOW_ATTACK)
        )
        workspace, _ = self._make(config)
        workspace.connect_button.click()
        modes = {
            workspace.gain_mode_combo.itemData(index)
            for index in range(workspace.gain_mode_combo.count())
        }
        self.assertEqual(
            modes,
            {GainMode.MANUAL.value, GainMode.SLOW_ATTACK.value},
        )

    # ------------------------------------------------------------------
    # Close / teardown
    # ------------------------------------------------------------------
    def test_close_stops_timer_and_disconnects_presenter(self) -> None:
        workspace, presenter = self._make()
        workspace.connect_button.click()
        workspace.start_button.click()
        self.assertTrue(_wait_for(
            lambda: presenter.poll().state is LiveControllerState.RUNNING
        ))
        workspace.close()
        self.assertFalse(workspace._timer.isActive())  # noqa: SLF001
        self.assertFalse(presenter.connected)


class DeviceDiscoveryDialogTests(unittest.TestCase):
    """Modal dialog: scan results, manual URIs and selection."""

    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        if hasattr(self, "_dialog"):
            self._dialog.close()
            self._dialog.deleteLater()

    def _make(self, scanner=None) -> DeviceDiscoveryDialog:
        self._dialog = DeviceDiscoveryDialog(locale=LocaleId.RU, scanner=scanner)
        return self._dialog

    def _ok_button(self, dialog: DeviceDiscoveryDialog):
        box = dialog.findChild(QDialogButtonBox)
        assert box is not None
        return box.button(QDialogButtonBox.StandardButton.Ok)

    def test_scan_lists_discovered_devices(self) -> None:
        devices = (
            DiscoveredDevice(uri="ip:192.168.2.1", description="Pluto A", kind=DeviceKind.IP),
            DiscoveredDevice(uri="usb:1.2.3", description="Pluto B", kind=DeviceKind.USB),
        )
        dialog = self._make(scanner=lambda: devices)
        dialog.scan_button.click()
        self.assertEqual(dialog.device_list.count(), 2)
        self.assertIn("2", dialog.status_label.text())

    def test_scan_error_surfaces_status(self) -> None:
        def broken() -> tuple[DiscoveredDevice, ...]:
            raise RuntimeError("hardware gone")

        dialog = self._make(scanner=broken)
        dialog.scan_button.click()  # must not crash
        self.assertEqual(dialog.device_list.count(), 0)
        self.assertIn("Ошибка", dialog.status_label.text())

    def test_empty_scan_surfaces_status(self) -> None:
        dialog = self._make(scanner=lambda: ())
        dialog.scan_button.click()
        self.assertEqual(dialog.device_list.count(), 0)
        self.assertEqual(dialog.status_label.text(), dialog._tr.text("live.dialog.empty"))  # noqa: SLF001

    def test_selection_builds_discovered_device(self) -> None:
        devices = (
            DiscoveredDevice(uri="ip:192.168.2.1", description="Pluto A", kind=DeviceKind.IP),
        )
        dialog = self._make(scanner=lambda: devices)
        dialog.scan_button.click()
        dialog.device_list.setCurrentRow(0)
        selected = dialog.selected_device()
        self.assertIsNotNone(selected)
        self.assertEqual(selected.uri, "ip:192.168.2.1")
        self.assertEqual(selected.kind, DeviceKind.IP)

    def test_manual_uri_accepts_and_selects(self) -> None:
        dialog = self._make()
        dialog.uri_input.setText("ip:192.168.2.9")
        dialog.add_button.click()
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        selected = dialog.selected_device()
        self.assertIsNotNone(selected)
        self.assertEqual(selected.uri, "ip:192.168.2.9")
        self.assertEqual(selected.kind, DeviceKind.MANUAL)

    def test_manual_uri_invalid_shows_status(self) -> None:
        dialog = self._make()
        dialog.uri_input.setText("ftp://not-a-pluto")
        dialog.add_button.click()
        self.assertNotEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual(dialog.status_label.text(), dialog._tr.text("live.dialog.uri_invalid"))  # noqa: SLF001

    def test_ok_without_selection_prompts(self) -> None:
        dialog = self._make()
        self._ok_button(dialog).click()
        self.assertNotEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual(dialog.status_label.text(), dialog._tr.text("live.dialog.select_prompt"))  # noqa: SLF001


class LiveDiscoveryTests(unittest.TestCase):
    """URI parsing and GUI-safe discovery facade."""

    def test_parse_manual_uri_accepts_canonical_forms(self) -> None:
        self.assertEqual(parse_manual_uri("usb:1.2.3"), "usb:1.2.3")
        self.assertEqual(parse_manual_uri("ip:192.168.2.1"), "ip:192.168.2.1")
        self.assertEqual(parse_manual_uri("local:default"), "local:default")
        self.assertEqual(parse_manual_uri("  ip:192.168.2.1  "), "ip:192.168.2.1")

    def test_parse_manual_uri_rejects_invalid_forms(self) -> None:
        for invalid in ("", "   ", "ftp://host", "usb:", "ip:1 2", "pluto"):
            self.assertIsNone(parse_manual_uri(invalid), invalid)

    def test_discover_devices_uses_injected_scanner(self) -> None:
        def scanner(_filter: str) -> tuple[PlutoContextSummary, ...]:
            return (
                PlutoContextSummary(uri="ip:192.168.2.1", description="Pluto A"),
                PlutoContextSummary(uri="usb:1.2.3", description="Pluto B"),
            )

        devices = discover_devices(filter="usb,ip", scanner=scanner)
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].kind, DeviceKind.IP)
        self.assertEqual(devices[1].kind, DeviceKind.USB)

    def test_discover_devices_wraps_scanner_failure(self) -> None:
        def broken(_filter: str) -> tuple[PlutoContextSummary, ...]:
            raise OSError("no libiio")

        with self.assertRaises(DiscoveryError) as context:
            discover_devices(filter="usb,ip", scanner=broken)
        self.assertIn("device scan failed", str(context.exception))

    def test_discovered_device_requires_uri(self) -> None:
        with self.assertRaises(ValueError):
            DiscoveredDevice(uri="", description="x", kind=DeviceKind.USB)


class DeviceProfileStoreTests(unittest.TestCase):
    """JSON profile persistence with atomic writes."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="p16ui04-profiles-")
        self._store = DeviceProfileStore(base_directory=Path(self._temporary.name))

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _profile(self, profile_id: str = "p1") -> DeviceProfile:
        return DeviceProfile(
            profile_id=profile_id,
            display_name="Fake Pluto",
            uri="ip:192.168.2.1",
            options=_options(backend=ComputeBackendKind.CUDA, fft_size=2048),
            notes="lab bench",
        )

    def test_save_load_round_trip(self) -> None:
        self._store.save([self._profile()])
        loaded = self._store.load()
        found = loaded.find("p1")
        self.assertIsNotNone(found)
        self.assertEqual(found.display_name, "Fake Pluto")
        self.assertEqual(found.uri, "ip:192.168.2.1")
        self.assertEqual(found.options.dsp.fft_size, 2048)
        self.assertEqual(found.options.backend, ComputeBackendKind.CUDA)

    def test_missing_file_returns_empty_collection(self) -> None:
        collection = self._store.load()
        self.assertEqual(collection.profiles, ())
        self.assertIsNone(collection.find("nope"))

    def test_corrupt_json_raises_profile_store_error(self) -> None:
        path = self._store.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ProfileStoreError):
            self._store.load()

    def test_atomic_write_leaves_no_part_file(self) -> None:
        self._store.save([self._profile()])
        part = self._store.file_path.with_suffix(self._store.file_path.suffix + ".part")
        self.assertFalse(part.exists())
        self.assertTrue(self._store.file_path.exists())

    def test_find_returns_none_for_unknown_id(self) -> None:
        self._store.save([self._profile("a"), self._profile("b")])
        collection = self._store.load()
        self.assertEqual(len(collection.profiles), 2)
        self.assertIsNone(collection.find("missing"))


class LiveMonitorI18nTests(unittest.TestCase):
    """Translation catalogs stay complete for the live workspace."""

    def test_catalogs_validate(self) -> None:
        validate_catalogs()  # must not raise

    def test_live_keys_translate_in_both_locales(self) -> None:
        for locale in (LocaleId.RU, LocaleId.EN):
            tr = Translator(locale)
            self.assertTrue(tr.text("live.connect"))
            self.assertTrue(tr.text("live.disconnect"))
            self.assertTrue(tr.text("live.start"))
            self.assertTrue(tr.text("live.stop"))
            self.assertTrue(tr.text("live.record"))
            self.assertTrue(tr.text("live.dialog.title"))
            self.assertTrue(tr.text("live.dialog.scan"))
            self.assertTrue(tr.text("live.marker.add"))
            self.assertTrue(tr.text("live.marker.delta", value="1 MHz"))


if __name__ == "__main__":
    unittest.main()

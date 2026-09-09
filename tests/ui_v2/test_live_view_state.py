from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unittest

import numpy as np

from sdr_monitor.domain import BackendKind, CalibrationQuality, LiveSessionState
from sdr_monitor.ui.v2.i18n import text
from sdr_monitor.ui.v2.state.app_state import AppViewState, UiPresentationPreferences, UiWorkspace
from sdr_monitor.ui.v2.state.live_view_state import (
    CalibrationPresentation,
    LiveAction,
    PersistencePresentation,
    build_live_view_state,
)


@dataclass(frozen=True, slots=True)
class FakeDevice:
    label: str = "Pluto RX"


@dataclass(frozen=True, slots=True)
class FakeConfiguration:
    profile_id: str | None = None
    persistence_enabled: bool = True


@dataclass(frozen=True, slots=True)
class FakeApplied:
    requested: FakeConfiguration
    applied: FakeConfiguration


@dataclass(frozen=True, slots=True)
class FakeQuality:
    calibration: CalibrationQuality = CalibrationQuality.UNCALIBRATED
    backend: BackendKind = BackendKind.CPU
    fallback_reason: str | None = None
    dropped_blocks: int = 0


@dataclass(frozen=True, slots=True)
class FakePerformance:
    source_blocks_dropped: int = 0
    acquisition_queue_blocks_dropped: int = 0
    fft_frames_dropped: int = 0
    snapshots_superseded: int = 0
    bridge_frames_coalesced: int = 0


@dataclass(frozen=True, slots=True)
class FakeSpectrum:
    timestamp_ns: int
    frequencies_hz: np.ndarray
    values: np.ndarray
    dropped_fft_frames_before: int = 0


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    state: LiveSessionState
    device: FakeDevice | None = None
    applied: FakeApplied | None = None
    quality: FakeQuality = FakeQuality()
    unit: str = "dBFS/bin"
    error: str | None = None
    error_kind: str | None = None
    spectrum: FakeSpectrum | None = None
    persistence: object | None = None
    performance: FakePerformance = FakePerformance()

    @property
    def reports_dbm(self) -> bool:
        return self.unit.casefold().startswith("dbm") and self.quality.calibration is CalibrationQuality.CALIBRATED


def make_snapshot(
    state: LiveSessionState,
    *,
    calibrated: CalibrationQuality = CalibrationQuality.UNCALIBRATED,
    unit: str = "dBFS/bin",
    spectrum: FakeSpectrum | None = None,
    persistence: object | None = None,
    error: str | None = None,
    error_kind: str | None = None,
    requested: FakeConfiguration | None = None,
    applied: FakeConfiguration | None = None,
    fallback_reason: str | None = None,
    performance: FakePerformance = FakePerformance(),
) -> FakeSnapshot:
    requested_configuration = requested or FakeConfiguration()
    applied_configuration = applied or requested_configuration
    return FakeSnapshot(
        state=state,
        device=FakeDevice(),
        applied=FakeApplied(requested_configuration, applied_configuration),
        quality=FakeQuality(calibration=calibrated, fallback_reason=fallback_reason),
        unit=unit,
        error=error,
        error_kind=error_kind,
        spectrum=spectrum,
        persistence=persistence,
        performance=performance,
    )


class LiveViewStateTests(unittest.TestCase):
    def test_required_state_matrix_is_explicit_and_headless(self) -> None:
        spectrum = self._spectrum()
        cases = (
            ("no-device", None, "Нет устройства", "Приём не запущен", LiveAction.DISCOVER),
            (
                "selected-disconnected",
                make_snapshot(LiveSessionState.DISCONNECTED),
                "Нет устройства",
                "Приём не запущен",
                LiveAction.DISCOVER,
            ),
            (
                "connecting",
                make_snapshot(LiveSessionState.CONNECTING),
                "Подключение…",
                "Приём не запущен",
                LiveAction.NONE,
            ),
            (
                "ready",
                make_snapshot(LiveSessionState.CONNECTED),
                "Устройство готово",
                "Приём не запущен",
                LiveAction.START,
            ),
            (
                "starting",
                make_snapshot(LiveSessionState.STARTING),
                "Устройство готово",
                "Запуск приёма…",
                LiveAction.NONE,
            ),
            (
                "running-uncalibrated",
                make_snapshot(LiveSessionState.RUNNING, spectrum=spectrum),
                "Устройство готово",
                "Приём",
                LiveAction.STOP,
            ),
            (
                "stopping",
                make_snapshot(LiveSessionState.STOPPING),
                "Устройство готово",
                "Остановка…",
                LiveAction.NONE,
            ),
            (
                "frozen-last-frame",
                make_snapshot(LiveSessionState.CONNECTED, spectrum=spectrum),
                "Устройство готово",
                "Остановлено / последний кадр",
                LiveAction.START,
            ),
        )
        for name, snapshot, connection, acquisition, action in cases:
            with self.subTest(name=name):
                view_state = build_live_view_state(snapshot, now_ns=2_500_000_000)
                self.assertEqual(view_state.connection_label, connection)
                self.assertEqual(view_state.acquisition_label, acquisition)
                self.assertEqual(view_state.primary_action, action)

    def test_calibration_is_honest_about_dbm_and_profile(self) -> None:
        calibrated = build_live_view_state(
            make_snapshot(LiveSessionState.RUNNING, calibrated=CalibrationQuality.CALIBRATED, unit="dBm"),
        )
        self.assertEqual(calibrated.calibration, CalibrationPresentation.CALIBRATED)
        self.assertEqual(calibrated.unit_label, "dBm")

        mislabeled = build_live_view_state(
            make_snapshot(LiveSessionState.RUNNING, calibrated=CalibrationQuality.CALIBRATED),
        )
        self.assertEqual(mislabeled.calibration, CalibrationPresentation.UNCALIBRATED)
        self.assertEqual(mislabeled.unit_label, "dBFS/bin")

        mismatch = build_live_view_state(
            make_snapshot(LiveSessionState.RUNNING, calibrated=CalibrationQuality.MISMATCH, unit="dBFS/bin"),
        )
        self.assertEqual(mismatch.calibration, CalibrationPresentation.MISMATCH)
        self.assertEqual(mismatch.calibration_label, "КАЛ: НЕСОВПАДЕНИЕ")

        selected = build_live_view_state(
            make_snapshot(
                LiveSessionState.CONNECTED,
                requested=FakeConfiguration(profile_id="field-cal"),
            ),
        )
        self.assertEqual(selected.calibration, CalibrationPresentation.PROFILE_SELECTED)

    def test_error_kind_selects_only_honest_recovery_intent(self) -> None:
        cases = {
            "device_not_found": LiveAction.DISCOVER,
            "connection_failed": LiveAction.RETRY,
            "configuration_rejected": LiveAction.REVIEW_CONFIGURATION,
            "stream_start_failed": LiveAction.RETRY,
            "stream_stalled": LiveAction.RETRY,
            "backend_failed": LiveAction.RETRY,
            "internal": LiveAction.RETRY,
        }
        for kind, action in cases.items():
            with self.subTest(kind=kind):
                view_state = build_live_view_state(
                    make_snapshot(LiveSessionState.ERROR, error="failure", error_kind=kind),
                )
                self.assertEqual(view_state.connection_label, "Ошибка")
                self.assertEqual(view_state.primary_action, action)
                self.assertEqual(view_state.error_label, "failure")
                self.assertEqual(view_state.primary_action_enabled, action is LiveAction.DISCOVER)

    def test_dirty_age_persistence_fallback_and_loss_are_separate(self) -> None:
        snapshot = make_snapshot(
            LiveSessionState.RUNNING,
            requested=FakeConfiguration(profile_id="requested", persistence_enabled=True),
            applied=FakeConfiguration(profile_id="applied", persistence_enabled=True),
            spectrum=self._spectrum(dropped_fft_frames_before=2),
            fallback_reason="CUDA unavailable",
            performance=FakePerformance(
                source_blocks_dropped=3,
                acquisition_queue_blocks_dropped=5,
                fft_frames_dropped=7,
                snapshots_superseded=11,
                bridge_frames_coalesced=13,
            ),
        )
        view_state = build_live_view_state(snapshot, now_ns=2_500_000_000)
        self.assertTrue(view_state.configuration_dirty)
        self.assertEqual(view_state.data_age_ms, 1_500.0)
        self.assertEqual(view_state.data_age_label, "1.5 с")
        self.assertEqual(view_state.persistence, PersistencePresentation.WAITING_FOR_FRAME)
        self.assertEqual(view_state.backend_label, "CPU fallback: CUDA unavailable")
        self.assertTrue(view_state.loss.analytical_loss)
        self.assertTrue(view_state.loss.presentation_supersession)
        self.assertEqual(view_state.loss.fft_frames, 7)
        self.assertEqual(view_state.loss.publication_frames, 11)
        self.assertEqual(view_state.loss.bridge_frames, 13)

    def test_published_frame_with_unknown_clock_is_not_labelled_as_no_frame(self) -> None:
        state = build_live_view_state(
            make_snapshot(LiveSessionState.RUNNING, spectrum=self._spectrum()),
        )
        self.assertTrue(state.has_spectrum)
        self.assertIsNone(state.data_age_ms)
        self.assertEqual(state.data_age_label, text("live_state.age.unknown"))

    def test_legacy_quality_and_frame_loss_remain_visible_with_zero_performance_fields(self) -> None:
        snapshot = FakeSnapshot(
            state=LiveSessionState.RUNNING,
            quality=FakeQuality(dropped_blocks=4),
            spectrum=self._spectrum(dropped_fft_frames_before=2),
        )
        view_state = build_live_view_state(snapshot, now_ns=2_500_000_000)
        self.assertEqual(view_state.loss.source_blocks, 4)
        self.assertEqual(view_state.loss.fft_frames, 2)
        self.assertTrue(view_state.loss.analytical_loss)

        active = build_live_view_state(
            make_snapshot(LiveSessionState.RUNNING, persistence=object()),
        )
        self.assertEqual(active.persistence, PersistencePresentation.ACTIVE)
        disabled = build_live_view_state(
            make_snapshot(
                LiveSessionState.CONNECTED,
                requested=FakeConfiguration(persistence_enabled=False),
            ),
        )
        self.assertEqual(disabled.persistence, PersistencePresentation.DISABLED)

    def test_spectrum_arrays_are_retained_by_identity_and_never_mutated(self) -> None:
        spectrum = self._spectrum()
        original_frequency_bytes = spectrum.frequencies_hz.tobytes()
        original_value_bytes = spectrum.values.tobytes()
        view_state = build_live_view_state(make_snapshot(LiveSessionState.RUNNING, spectrum=spectrum))
        self.assertIs(view_state.spectrum, spectrum)
        self.assertIs(view_state.spectrum.frequencies_hz, spectrum.frequencies_hz)
        self.assertIs(view_state.spectrum.values, spectrum.values)
        self.assertEqual(spectrum.frequencies_hz.tobytes(), original_frequency_bytes)
        self.assertEqual(spectrum.values.tobytes(), original_value_bytes)
        with self.assertRaises(ValueError):
            spectrum.values[0] = 0.0

    def test_ui_state_is_presentation_only(self) -> None:
        view_state = AppViewState(active_workspace=UiWorkspace.LIVE)
        self.assertEqual(view_state.preferences.display_fps, 60)
        with self.assertRaises(ValueError):
            UiPresentationPreferences(display_fps=61)
        with self.assertRaises(ValueError):
            UiPresentationPreferences(theme_id=" ")
        with self.assertRaises(ValueError):
            UiPresentationPreferences(locale_id="de")

    def test_v2_sources_do_not_import_protected_backend_owners(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for relative in (
            "sdr_monitor/ui/v2/state/live_view_state.py",
            "sdr_monitor/ui/v2/view_models/live_view_model.py",
            "sdr_monitor/ui/v2/composition.py",
            "sdr_monitor/ui/v2/product_live.py",
            "sdr_monitor/ui/v2/workspaces/home.py",
            "sdr_monitor/ui/v2/workspaces/live.py",
        ):
            with self.subTest(relative=relative):
                source = (root / relative).read_text(encoding="utf-8")
                self.assertNotIn("from sdr_monitor.services", source)
                self.assertNotIn("from sdr_monitor.application", source)
                self.assertNotIn("from sdr_monitor.ui.presenters", source)
                self.assertNotIn("from sdr_monitor.ui.workspaces", source)
        for relative in (
            "sdr_monitor/ui/v2/waterfall/bounded_ring.py",
            "sdr_monitor/ui/v2/waterfall/contracts.py",
            "sdr_monitor/ui/v2/waterfall/pane.py",
            "sdr_monitor/ui/v2/waterfall/spectrum_view.py",
        ):
            with self.subTest(relative=relative):
                source = (root / relative).read_text(encoding="utf-8")
                self.assertNotIn("from sdr_monitor.services", source)
                self.assertNotIn("from sdr_monitor.application", source)
                self.assertNotIn("from sdr_monitor.domain", source)
                self.assertNotIn("from sdr_monitor.ui.presenters", source)
                self.assertNotIn("from sdr_monitor.ui.workspaces", source)

    @staticmethod
    def _spectrum(*, dropped_fft_frames_before: int = 0) -> FakeSpectrum:
        frequencies = np.array([99.0, 100.0, 101.0], dtype=np.float64)
        values = np.array([-100.0, -90.0, -95.0], dtype=np.float32)
        frequencies.setflags(write=False)
        values.setflags(write=False)
        return FakeSpectrum(
            timestamp_ns=1_000_000_000,
            frequencies_hz=frequencies,
            values=values,
            dropped_fft_frames_before=dropped_fft_frames_before,
        )

"""P16UI-07 calibration and measurement presentation tests."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

from esw_dfl.domain import SourceDescriptor
from esw_dfl.models import MeasurementQuality
from esw_dfl.sdr.calibration_store import (
    CalibrationApplicationStatus,
    CalibrationPoint,
    CalibrationProfile,
    CalibrationProfileStore,
    CalibrationSignature,
)
from esw_dfl.sdr.contracts import (
    CalibrationStatus,
    DetectorType,
    PrecisionMode,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
    QualityFlag,
    WindowType,
)
from esw_dfl.sdr.measurements import LiveMeasurementAdapter
from esw_dfl.ui.app_shell import AppShell
from esw_dfl.ui.calibration_presenter import CalibrationPresenter
from esw_dfl.ui.calibration_workspace import CalibrationWorkspace
from esw_dfl.ui.i18n import LocaleId
from esw_dfl.ui.measurement_presenter import MeasurementPresenter
from esw_dfl.ui.measurements_panel import MeasurementPanel
from esw_dfl.ui.state import WorkspaceId


def _signature(*, backend: str = "cpu", serial: str = "S1") -> CalibrationSignature:
    return CalibrationSignature(
        device_serial=serial,
        backend=backend,
        rf_port_path="rx",
        sample_rate_hz=1_000_000.0,
        analog_bandwidth_hz=800_000.0,
        gain_mode="manual",
        manual_gain_db=10.0,
        window_normalization_version="p09-v1",
        fft_unit_convention="dBFS/bin",
        frontend_chain="fixture",
        reference_plane="rf_input",
    )


def _profile(*, profile_id: str = "fixture", version: int = 1, backend: str = "cpu") -> CalibrationProfile:
    return CalibrationProfile(
        profile_id=profile_id,
        profile_version=version,
        signature=_signature(backend=backend),
        reference_plane="rf_input",
        points=(
            CalibrationPoint(100.0, -50.0, -51.0, 1.0, 1.0),
            CalibrationPoint(200.0, -40.0, -43.0, 3.0, 2.0),
            CalibrationPoint(300.0, -30.0, -35.0, 5.0, 3.0),
        ),
        created_at="2026-08-02T00:00:00+00:00",
    )


def _frame(*, source_id: str = "fixture", config_generation: int = 7) -> SpectrumFrame:
    return SpectrumFrame(
        source=SourceDescriptor(SourceType.SYNTHETIC, source_id, "P16 fixture"),
        frame_sequence=41,
        first_sample_index=0,
        timestamp_ns=1_700_000_000_000_000_000,
        config_generation=config_generation,
        center_frequency_hz=100.0,
        sample_rate_hz=9.0,
        analog_bandwidth_hz=9.0,
        fft_bin_width_hz=1.0,
        enbw_hz=1.0,
        nominal_rbw_hz=1.0,
        fft_size=9,
        hop_size=4,
        window=WindowType.HANN,
        detector=DetectorType.AVERAGE_POWER,
        precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
        unit=SpectrumUnit.DBM_BIN,
        frequencies_hz=np.arange(9, dtype=np.float64),
        values=np.asarray([-60, -60, -60, -10, -10, -60, -60, -60, -60], dtype=np.float32),
        calibration_status=CalibrationStatus.APPLIED,
        calibration_profile_id="fixture",
        quality_flags=QualityFlag.NONE,
        estimated_uncertainty_db=0.25,
    )


class CalibrationPresentationTests(unittest.TestCase):
    def test_profile_browser_hides_source_path_and_exposes_applicability_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(directory)
            store.save(_profile())
            presenter = CalibrationPresenter(store, settings=_signature(), frequency_hz=150.0)
            snapshot = presenter.snapshot
            self.assertEqual(snapshot.applicability, CalibrationApplicationStatus.INTERPOLATED)
            self.assertEqual(len(snapshot.comparison), 11)
            self.assertNotIn(directory, repr(snapshot))
            self.assertEqual(snapshot.profiles[0].point_count, 3)

    def test_active_profile_is_blocked_for_mismatch_and_allowed_for_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(directory)
            store.save(_profile())
            presenter = CalibrationPresenter(store, settings=_signature(serial="wrong"))
            ok, reason = presenter.select_active_profile()
            self.assertFalse(ok)
            self.assertIsNotNone(reason)
            presenter.set_current_settings(_signature())
            ok, reason = presenter.select_active_profile()
            self.assertTrue(ok)
            self.assertIsNone(reason)
            self.assertEqual(presenter.snapshot.active_profile_id, "fixture")

    def test_csv_preview_then_finalize_creates_immutable_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "calibration.csv"
            csv_path.write_text(
                "frequency_hz,reference_dbm,measured_dbfs,correction_db,uncertainty_db\n"
                "100,-50,-51,1,1\n200,-40,-43,3,2\n",
                encoding="utf-8",
            )
            presenter = CalibrationPresenter(CalibrationProfileStore(root / "profiles"), settings=_signature())
            preview = presenter.preview_csv(csv_path, profile_id="imported", profile_version=1)
            self.assertTrue(preview.valid)
            self.assertEqual(preview.source_name, csv_path.name)
            self.assertEqual(preview.points[1], (200.0, 3.0, 2.0))
            self.assertEqual(presenter.finalize_import(), (True, None))
            self.assertEqual(presenter.snapshot.profiles[0].profile_id, "imported")
            presenter.preview_csv(csv_path, profile_id="imported", profile_version=1)
            self.assertEqual(presenter.finalize_import()[0], True)

    def test_invalid_csv_preview_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text("frequency_hz,correction_db\n100,1\n", encoding="utf-8")
            presenter = CalibrationPresenter(CalibrationProfileStore(Path(directory) / "profiles"), settings=_signature())
            preview = presenter.preview_csv(path, profile_id="bad", profile_version=1)
            self.assertFalse(preview.valid)
            self.assertTrue(preview.errors)
            self.assertEqual(presenter.snapshot.import_preview, preview)


class MeasurementPresentationTests(unittest.TestCase):
    def test_cards_show_quality_uncertainty_and_same_frame_provenance(self) -> None:
        adapter = LiveMeasurementAdapter(_frame())
        power = adapter.channel_power(2.5, 4.5)
        peak = adapter.peak(limit=1)
        presenter = MeasurementPresenter()
        self.assertTrue(presenter.set_results((power, peak)))
        self.assertEqual(len(presenter.snapshot.cards), 2)
        for card in presenter.snapshot.cards:
            self.assertEqual(card.quality, MeasurementQuality.EXACT)
            self.assertIn("0,25", card.uncertainty)
            self.assertEqual(card.frame, "41 / config 7")
            self.assertEqual(card.calibration, "applied")

    def test_mixed_frame_result_is_rejected_without_contradictory_labels(self) -> None:
        presenter = MeasurementPresenter()
        first = LiveMeasurementAdapter(_frame()).peak(limit=1)
        second = LiveMeasurementAdapter(_frame(config_generation=8)).peak(limit=1)
        self.assertTrue(presenter.publish(first))
        self.assertFalse(presenter.publish(second))
        self.assertEqual(presenter.snapshot.cards[0].frame, "41 / config 7")
        self.assertIsNotNone(presenter.snapshot.error)

    def test_cpu_and_cuda_ui_formatting_is_backend_agnostic_and_locale_aware(self) -> None:
        cpu = LiveMeasurementAdapter(_frame(source_id="cpu")).channel_power(2.5, 4.5)
        cuda = LiveMeasurementAdapter(_frame(source_id="cuda")).channel_power(2.5, 4.5)
        ru = MeasurementPresenter(locale=LocaleId.RU).card_from_result(cpu)
        en = MeasurementPresenter(locale=LocaleId.EN).card_from_result(cuda)
        self.assertEqual((ru.unit, ru.quality), (en.unit, en.quality))
        self.assertNotEqual(ru.value, en.value)
        self.assertIn(".", en.uncertainty)

    def test_qt_panel_and_calibration_workspace_render_offline(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(directory)
            store.save(_profile())
            calibration = CalibrationWorkspace(CalibrationPresenter(store, settings=_signature()))
            measurement = MeasurementPanel()
            shell = AppShell(
                calibration_factory=lambda: calibration,
                measurement_panel_factory=lambda: measurement,
            )
            shell.set_active_workspace(WorkspaceId.CALIBRATION)
            self.assertIs(shell.attached_workspace(WorkspaceId.CALIBRATION), calibration)
            self.assertIs(shell.bottom_tools.widget(0), measurement)
            shell.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()

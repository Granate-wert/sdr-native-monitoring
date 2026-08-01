"""P16UI-05 Wideband Sweep presenter, workspace, profile and shell tests."""

from __future__ import annotations

from dataclasses import replace
import inspect
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import ClassVar
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QEvent
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl.sdr.contracts import QualityFlag, SweepConfig
from esw_dfl.sdr.fake_sweep_service import FakeSweepConfig, FakeSweepService
from esw_dfl.sdr.sweep import SweepPlannerOptions
from esw_dfl.sdr.sweep_profile import SweepProfile, SweepProfileStore
from esw_dfl.ui.app_shell import AppShell
from esw_dfl.ui.i18n import LocaleId, Translator, validate_catalogs
from esw_dfl.ui.state import WorkspaceId
from esw_dfl.ui.sweep_presenter import SweepPresenter
from esw_dfl.ui.sweep_state import SweepRunStatus
from esw_dfl.ui.sweep_workspace import SweepSpectrumView, SweepWorkspace


def _config(
    *,
    settling_time_seconds: float = 0.0,
    overlap_hz: float = 2.0e6,
) -> SweepConfig:
    return SweepConfig(
        start_frequency_hz=100.0e6,
        stop_frequency_hz=118.0e6,
        sample_rate_hz=16.0e6,
        analog_bandwidth_hz=12.0e6,
        overlap_hz=overlap_hz,
        fft_size=256,
        hop_size=128,
        dwell_frames=1,
        settling_time_seconds=settling_time_seconds,
    )


def _presenter(config: FakeSweepConfig | None = None) -> SweepPresenter:
    fake_config = config if config is not None else FakeSweepConfig(
        sample_rate_hz=16.0e6,
        fft_size=256,
        hop_size=128,
        dwell_frames=1,
    )
    return SweepPresenter(
        service_factory=lambda _uri: FakeSweepService(fake_config),
        poll_batch_size=8,
        idle_timeout_s=1.0,
        sweep_id=16,
    )


def _wait_for(predicate, timeout_s: float = 4.0, step_s: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return bool(predicate())


def _terminal(status: SweepRunStatus) -> bool:
    return status in {
        SweepRunStatus.CANCELLED,
        SweepRunStatus.COMPLETED,
        SweepRunStatus.FAILED,
    }


class SweepPresenterTests(unittest.TestCase):
    """The presenter owns planning, worker execution, stitching, and shutdown."""

    def tearDown(self) -> None:
        if hasattr(self, "_presenter"):
            self._presenter.close()

    def _make(self, config: FakeSweepConfig | None = None) -> SweepPresenter:
        self._presenter = _presenter(config)
        return self._presenter

    # ------------------------------------------------------------------
    # Plan / lifecycle
    # ------------------------------------------------------------------
    def test_plan_builds_no_gap_preview_without_a_device(self) -> None:
        presenter = self._make()

        self.assertEqual(presenter.plan(_config()), [])
        snapshot = presenter.plan_snapshot

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.coverage_gaps_hz, ())
        self.assertEqual(len(snapshot.segments), 2)
        self.assertEqual(presenter.snapshot.run.status, SweepRunStatus.PLANNED)

    def test_plan_surfaces_planner_error(self) -> None:
        presenter = self._make()

        errors = presenter.plan(_config(), SweepPlannerOptions(edge_margin_hz=6.0e6))

        self.assertNotEqual(errors, [])
        self.assertIsNotNone(presenter.plan_snapshot)
        self.assertIsNotNone(presenter.plan_snapshot.error)
        self.assertIn("usable RF bandwidth", presenter.plan_snapshot.error)

    def test_run_without_plan_is_rejected(self) -> None:
        presenter = self._make()

        self.assertEqual(presenter.run("ip:fake"), ["no plan is built"])

    def test_run_with_plan_completes_with_stitched_frame(self) -> None:
        presenter = self._make()
        presenter.plan(_config())

        self.assertEqual(presenter.run("ip:fake"), [])

        self.assertTrue(_wait_for(lambda: _terminal(presenter.poll().run.status)))
        self.assertEqual(presenter.poll().run.status, SweepRunStatus.COMPLETED)
        self.assertIsNotNone(presenter.last_frame)
        self.assertGreater(presenter.snapshot.result.quality.overlap_bins, 0)
        self.assertEqual(len(presenter.snapshot.result.quality.seams), 1)

    def test_eta_is_published_during_run(self) -> None:
        presenter = self._make(FakeSweepConfig(
            sample_rate_hz=16.0e6,
            fft_size=256,
            hop_size=128,
            dwell_frames=1,
            settling_time_seconds=0.2,
        ))
        presenter.plan(_config(settling_time_seconds=0.2))
        self.assertEqual(presenter.run("ip:fake"), [])
        self.assertTrue(_wait_for(lambda: presenter.poll().run.eta_s is not None, timeout_s=2.0))
        presenter.cancel()
    def test_cancel_transitions_to_cancelled(self) -> None:
        presenter = self._make(FakeSweepConfig(
            sample_rate_hz=16.0e6,
            fft_size=256,
            hop_size=128,
            dwell_frames=1,
            settling_time_seconds=0.5,
        ))
        presenter.plan(_config(settling_time_seconds=0.5))
        presenter.run("ip:fake")

        presenter.cancel()

        self.assertEqual(presenter.poll().run.status, SweepRunStatus.CANCELLING)
        self.assertTrue(_wait_for(
            lambda: presenter.poll().run.status is SweepRunStatus.CANCELLED,
        ))

    def test_poll_is_cached_when_idle_and_constructor_validates(self) -> None:
        presenter = self._make()
        first = presenter.poll()

        for _ in range(50):
            self.assertIs(presenter.poll(), first)

        with self.assertRaises(ValueError):
            SweepPresenter(poll_batch_size=0)
        with self.assertRaises(ValueError):
            SweepPresenter(idle_timeout_s=0.0)

    def test_close_joins_cancelled_worker(self) -> None:
        presenter = self._make(FakeSweepConfig(
            sample_rate_hz=16.0e6,
            fft_size=256,
            hop_size=128,
            dwell_frames=1,
            settling_time_seconds=0.5,
        ))
        presenter.plan(_config(settling_time_seconds=0.5))
        presenter.run("ip:fake")

        presenter.close()

        self.assertFalse(presenter.running)
        self.assertIsNone(presenter._thread)  # noqa: SLF001 - shutdown ownership check


class SweepWorkspaceTests(unittest.TestCase):
    """The offscreen workspace renders immutable presenter snapshots only."""

    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="p16ui05-profiles-")
        self._workspace: SweepWorkspace | None = None

    def tearDown(self) -> None:
        if self._workspace is not None:
            self._workspace.close()
            self._workspace.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self._temporary.cleanup()

    def _make(self, config: FakeSweepConfig | None = None) -> tuple[SweepWorkspace, SweepPresenter]:
        presenter = _presenter(config)
        workspace = SweepWorkspace(
            presenter=presenter,
            locale=LocaleId.EN,
            uri_provider=lambda: "ip:fake",
            profile_store=SweepProfileStore(base_directory=Path(self._temporary.name)),
            poll_interval_ms=16,
        )
        self._workspace = workspace
        return workspace, presenter

    @staticmethod
    def _configure_two_segment_sweep(workspace: SweepWorkspace) -> None:
        workspace.start_input.set_frequency_hz(100.0e6)
        workspace.stop_input.set_frequency_hz(118.0e6)
        workspace.overlap_input.set_frequency_hz(2.0e6)
        workspace.rate_input.set_frequency_hz(16.0e6)
        workspace.bandwidth_input.set_frequency_hz(12.0e6)
        workspace.fft_combo.setCurrentIndex(workspace.fft_combo.findData(256))
        workspace.hop_combo.setCurrentIndex(workspace.hop_combo.findData(128))
        workspace.dwell_spin.setValue(1)

    def _complete(self, workspace: SweepWorkspace, presenter: SweepPresenter) -> None:
        workspace._on_plan_clicked()  # noqa: SLF001 - exercise button slot offscreen
        workspace._on_run_clicked()  # noqa: SLF001 - exercise button slot offscreen
        self.assertTrue(_wait_for(lambda: _terminal(presenter.poll().run.status)))
        workspace._poll_presenter()  # noqa: SLF001 - offscreen timer never fires

    # ------------------------------------------------------------------
    # Plan preview / validation
    # ------------------------------------------------------------------
    def test_plan_validation_surfaces_invalid_range_without_crashing(self) -> None:
        workspace, presenter = self._make()
        workspace.start_input.setText("")

        workspace._on_plan_clicked()  # noqa: SLF001 - invalid GUI input path

        self.assertIsNone(workspace.build_config())
        self.assertIn(
            workspace._tr.text("sweep.plan.invalid_range"),  # noqa: SLF001
            workspace.findChild(QLabel, "sweepErrorLabel").text(),
        )
        self.assertIsNone(presenter.plan_snapshot)

    def test_invalid_overlap_is_rejected_by_config_builder(self) -> None:
        workspace, _ = self._make()
        workspace.overlap_input.set_frequency_hz(3.1e6)

        workspace._on_plan_clicked()  # noqa: SLF001 - invalid GUI input path

        self.assertIsNone(workspace.build_config())
        self.assertNotEqual(workspace.findChild(QLabel, "sweepErrorLabel").text(), "")

    def test_plan_preview_has_no_gaps_and_populates_diagram(self) -> None:
        workspace, presenter = self._make()

        workspace._on_plan_clicked()  # noqa: SLF001 - exercise button slot

        self.assertIsNotNone(presenter.plan_snapshot)
        self.assertEqual(presenter.plan_snapshot.coverage_gaps_hz, ())
        self.assertNotEqual(workspace.segments(), ())
        self.assertEqual(workspace._segment_diagram.segments(), workspace.segments())  # noqa: SLF001
        self.assertIn("Segments:", workspace.findChild(QLabel, "sweepPlanSummaryLabel").text())

    # ------------------------------------------------------------------
    # Execution / result quality
    # ------------------------------------------------------------------
    def test_cancellation_reaches_cancelled_status(self) -> None:
        workspace, presenter = self._make(FakeSweepConfig(
            sample_rate_hz=3.0e6,
            fft_size=1024,
            hop_size=512,
            dwell_frames=1,
            settling_time_seconds=0.5,
        ))
        workspace.settling_spin.setValue(0.5)
        workspace._on_plan_clicked()  # noqa: SLF001
        workspace._on_run_clicked()  # noqa: SLF001

        workspace._on_cancel_clicked()  # noqa: SLF001

        self.assertTrue(_wait_for(
            lambda: presenter.poll().run.status is SweepRunStatus.CANCELLED,
        ))
        workspace._poll_presenter()  # noqa: SLF001 - offscreen timer never fires
        self.assertEqual(workspace.status_text(), workspace._tr.text("sweep.run.cancelled"))  # noqa: SLF001

    def test_missing_segment_is_explicit_in_stitched_workspace_result(self) -> None:
        workspace, presenter = self._make(FakeSweepConfig(
            sample_rate_hz=16.0e6,
            fft_size=256,
            hop_size=128,
            dwell_frames=1,
            fail_reconfigure_at=1,
        ))
        self._configure_two_segment_sweep(workspace)

        self._complete(workspace, presenter)

        frame = presenter.last_frame
        self.assertEqual(presenter.snapshot.run.status, SweepRunStatus.FAILED)
        self.assertIsNotNone(frame)
        self.assertEqual(presenter.snapshot.result.quality.missing_bins, 193)
        self.assertEqual(int(np.count_nonzero(np.isnan(frame.values))), 96)
        self.assertEqual(
            int(np.count_nonzero(frame.quality_flags_per_bin & np.uint16(QualityFlag.MISSING_SEGMENT))),
            193,
        )
        self.assertIn("Missing bins: 193", workspace.findChild(QLabel, "sweepResultSummaryLabel").text())
        self.assertIs(workspace.spectrum_view.frame, frame)

    def test_quality_strip_renders_missing_and_overlap_flags(self) -> None:
        workspace, presenter = self._make()
        self._configure_two_segment_sweep(workspace)
        self._complete(workspace, presenter)
        frame = presenter.last_frame
        self.assertIsNotNone(frame)
        flags = frame.quality_flags_per_bin.copy()
        flags[0] |= np.uint16(QualityFlag.MISSING_SEGMENT)
        flagged = replace(frame, quality_flags_per_bin=flags)

        workspace.spectrum_view.set_frame(flagged)
        pixmap = QPixmap(640, 260)
        workspace.spectrum_view.render(pixmap)

        self.assertIs(workspace.spectrum_view.frame, flagged)
        self.assertFalse(flagged.values.flags.writeable)
        self.assertTrue(np.any(flags & np.uint16(QualityFlag.STITCH_OVERLAP)))
        self.assertTrue(np.any(flags & np.uint16(QualityFlag.MISSING_SEGMENT)))

    def test_seam_display_populates_localized_rows_after_overlap_sweep(self) -> None:
        workspace, presenter = self._make()
        self._configure_two_segment_sweep(workspace)

        self._complete(workspace, presenter)

        seams = workspace.seam_view
        self.assertEqual(presenter.snapshot.run.status, SweepRunStatus.COMPLETED)
        self.assertEqual(seams.rowCount(), 1)
        self.assertTrue(all(
            seams.item(0, column) is not None and seams.item(0, column).text()
            for column in range(seams.columnCount())
        ))
        self.assertEqual(
            seams.horizontalHeaderItem(0).text(),
            workspace._tr.text("sweep.result.column_seam"),  # noqa: SLF001
        )

    # ------------------------------------------------------------------
    # Protected presentation boundaries
    # ------------------------------------------------------------------
    def test_workspace_contains_no_package_label_text(self) -> None:
        workspace, _ = self._make()
        text_widgets = (
            workspace.findChildren(QLabel)
            + workspace.findChildren(QPushButton)
            + workspace.findChildren(QGroupBox)
        )
        texts = [
            widget.title() if isinstance(widget, QGroupBox) else widget.text()
            for widget in text_widgets
        ]
        texts.extend(
            workspace.seam_view.horizontalHeaderItem(column).text()
            for column in range(workspace.seam_view.columnCount())
        )

        self.assertEqual(workspace.accessibleName(), "sweep_workspace")
        self.assertFalse(any("P13" in text for text in texts))

    def test_acquisition_remains_in_presenter_worker_not_workspace(self) -> None:
        workspace, presenter = self._make(FakeSweepConfig(
            sample_rate_hz=3.0e6,
            fft_size=1024,
            hop_size=512,
            dwell_frames=1,
            settling_time_seconds=0.5,
        ))
        source = inspect.getsource(sys.modules[SweepWorkspace.__module__])
        workspace.settling_spin.setValue(0.5)
        workspace._on_plan_clicked()  # noqa: SLF001

        started = time.monotonic()
        workspace._on_run_clicked()  # noqa: SLF001
        snapshot = presenter.poll()

        self.assertLess(time.monotonic() - started, 0.1)
        self.assertNotIn("SweepExecutor", source)
        self.assertNotIn("FixedBandEngineService", source)
        self.assertIn(snapshot.run.status, {SweepRunStatus.RUNNING, SweepRunStatus.COMPLETED})

    def test_renderer_keeps_stitched_frame_identity_without_restitching(self) -> None:
        workspace, presenter = self._make()
        self._configure_two_segment_sweep(workspace)
        self._complete(workspace, presenter)
        frame = presenter.last_frame
        self.assertIsNotNone(frame)
        source = inspect.getsource(SweepSpectrumView)
        custom_grid = replace(frame, frequencies_hz=frame.frequencies_hz * 1.000001)

        workspace.spectrum_view.set_frame(custom_grid)
        workspace.spectrum_view.grab()

        self.assertIs(workspace.spectrum_view.frame, custom_grid)
        self.assertIn("frame.frequencies_hz", source)
        self.assertNotIn("stitch_sweep", source)
        self.assertNotIn("SweepExecutor", source)


class SweepProfileStoreTests(unittest.TestCase):
    """Sweep profile persistence remains atomic and independent of Qt state."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="p16ui05-profile-store-")
        self._store = SweepProfileStore(base_directory=Path(self._temporary.name))

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_save_and_load_round_trip(self) -> None:
        profile = SweepProfile("wideband", "Wideband", _config(), SweepPlannerOptions())

        self._store.save([profile])
        loaded = self._store.load()

        self.assertEqual(loaded.find("wideband"), profile)
        self.assertFalse(self._store.file_path.with_suffix(".json.part").exists())


class SweepAppShellIntegrationTests(unittest.TestCase):
    """The shell lazily attaches one factory-built Wideband Sweep workspace."""

    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        if hasattr(self, "_workspace"):
            self._workspace.close()
            self._workspace.deleteLater()
        if hasattr(self, "_shell"):
            self._shell.close()
            self._shell.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_sweep_workspace_factory_attaches_once(self) -> None:
        self._workspace = SweepWorkspace(presenter=_presenter(), locale=LocaleId.EN)
        self._shell = AppShell(sweep_workspace_factory=lambda: self._workspace)

        self._shell.set_active_workspace(WorkspaceId.WIDEBAND_SWEEP)
        first = self._shell.attached_workspace(WorkspaceId.WIDEBAND_SWEEP)
        self._shell.set_active_workspace(WorkspaceId.WIDEBAND_SWEEP)

        self.assertIsInstance(first, SweepWorkspace)
        self.assertIs(first, self._workspace)
        self.assertIs(self._shell.attached_workspace(WorkspaceId.WIDEBAND_SWEEP), first)


class SweepWorkspaceI18nTests(unittest.TestCase):
    """Translation catalogs stay complete for the Wideband Sweep workspace."""

    def test_catalogs_validate(self) -> None:
        validate_catalogs()

    def test_sweep_keys_translate_in_both_locales(self) -> None:
        for locale in (LocaleId.RU, LocaleId.EN):
            self.assertTrue(Translator(locale).text("sweep.plan.button"))
            self.assertTrue(Translator(locale).text("sweep.run.start"))
            self.assertTrue(Translator(locale).text("sweep.result.column_seam"))


if __name__ == "__main__":
    unittest.main()

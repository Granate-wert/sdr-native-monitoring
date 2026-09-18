"""Transient hidden-page gates retain measurement state, not a render backlog."""
from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, PersistenceDensityFrame
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from sdr_monitor.ui.v2.waterfall import SpectrumWaterfallView, WaterfallLineFrame
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal


class HiddenPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.stack = QStackedWidget()
        self.view = SpectrumWaterfallView(settings=self.settings)
        self.stack.addWidget(self.view)
        self.stack.addWidget(QWidget())
        self.stack.resize(1100, 700)
        self.stack.show()
        self.app.processEvents()
        self.scene = self.view.spectrum_scene
        self.pane = self.view.waterfall_pane

    def tearDown(self):
        self.stack.close()
        self.stack.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def hide(self):
        self.stack.setCurrentIndex(1)
        self.app.processEvents()
        self.assertFalse(self.view.isVisible())

    def show(self):
        self.stack.setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.view.isVisible())

    def density(self, value):
        return PersistenceDensityFrame(
            np.full((2, 4), value, np.float32), np.arange(5.) + 99.5e6,
            np.array([-100., -50., 0.]), DensityValueMode.PROBABILITY, "dBFS/bin")

    def publish(self, frame):
        bundle = bundle_from_sweep(frame)
        self.scene.set_frame(bundle)
        self.scene.sweep_coverage.accept(ContinuousSweepDisplaySnapshot(
            frame if hasattr(frame, "state") else None, ContinuousSweepDisplayMetrics(),
            frame if hasattr(frame, "revision") else None))
        self.pane.set_sweep_line(waterfall_line_from_sweep(frame))
        return bundle

    def test_hidden_sweep_keeps_gap_history_but_no_projection_or_image_upload(self):
        self.publish(terminal())
        self.scene.place_marker("M1", 101e6)
        self.scene.view_box.setXRange(100e6, 102e6, padding=0)
        self.hide()
        image_uploads = self.pane.metrics.image_uploads
        with patch("sdr_monitor.ui.v2.spectrum.scene.peak_preserving_envelope") as envelope, \
             patch.object(self.scene.sweep_coverage.state, "project") as project:
            for sequence in range(2, 22):
                self.publish(progress(sequence, 1))
                latest = self.publish(terminal(sequence, gap=True))
            self.app.processEvents()
            self.assertEqual(envelope.call_count, 0)
            self.assertEqual(project.call_count, 0)
        self.assertIs(self.scene.latest_frame, latest)
        self.assertEqual(self.pane.metrics.image_uploads, image_uploads)
        self.assertEqual(self.pane.history_rows, 21)
        stamps = self.pane._renderer.sweep_stamps()
        self.assertEqual(sum(stamp.state.value == "gap" for stamp in stamps), 20)
        self.show()
        self.assertGreater(self.pane.metrics.image_uploads, image_uploads)
        self.assertEqual(self.pane.history_rows, 21)
        self.assertIs(self.scene.latest_frame, latest)
        self.assertTrue(np.isnan(self.scene.trace_envelope(TraceKind.CURRENT).values).any())
        self.assertEqual(self.scene.markers[0].value, -65)
        np.testing.assert_allclose(self.scene.view_box.viewRange()[0], [100e6, 102e6])

    def test_hidden_density_keeps_only_latest_and_clear_cannot_resurrect(self):
        self.scene.set_persistence_frame(self.density(.1))
        self.hide()
        overlay = self.scene._persistence
        uploads = overlay.metrics.image_uploads
        with patch.object(overlay, "_render_image") as render:
            for value in (.2, .3, .4):
                self.scene.set_persistence_frame(self.density(value))
            overlay.set_logarithmic(False)
            overlay.flush_pending(now_ns=10**20)
            self.app.processEvents()
            self.assertEqual(render.call_count, 0)
        self.assertEqual(overlay.metrics.image_uploads, uploads)
        self.assertFalse(overlay._timer.isActive())
        self.show()
        self.assertEqual(overlay.metrics.image_uploads, uploads + 1)
        np.testing.assert_allclose(overlay.image_item.image, .4)
        self.hide()
        self.scene.clear_measurement()
        uploads = overlay.metrics.image_uploads
        self.show()
        self.assertEqual(overlay.metrics.image_uploads, uploads)
        self.assertIsNone(overlay.latest_view)
        self.assertFalse(overlay.image_item.isVisible())

    def test_user_hidden_layers_stay_hidden_after_page_roundtrip(self):
        self.view.set_waterfall_visible(False)
        self.scene.set_persistence_visible(False)
        self.view.flush_settings()
        self.hide()
        self.publish(terminal())
        self.scene.set_persistence_frame(self.density(.5))
        uploads = self.pane.metrics.image_uploads
        self.show()
        self.assertFalse(self.pane.render_visible)
        self.assertTrue(self.pane.isHidden())
        self.assertFalse(self.scene._persistence_visible.isChecked())
        self.assertEqual(self.pane.metrics.image_uploads, uploads)
        self.assertEqual(self.scene.persistence_metrics.image_uploads, 0)
        self.view.flush_settings()
        self.assertFalse(self.settings.value("ui_v2/live/waterfall/v1/visible", type=bool))

    def test_hidden_rtbw_rows_keep_acquisition_time_and_epoch(self):
        self.hide()
        for sequence in range(1, 11):
            self.pane.set_line(WaterfallLineFrame(
                np.array([-90., -80.]), np.array([99.5, 100.5, 101.5]),
                sequence * 1_000_000_000, 1, "dBFS/bin"))
        self.assertEqual(self.pane.history_rows, 10)
        np.testing.assert_array_equal(self.pane._renderer.timestamps_ns(),
                                      np.arange(1, 11) * 1_000_000_000)
        self.assertEqual(self.pane.metrics.image_uploads, 0)
        self.show()
        self.assertEqual(self.pane.history_rows, 10)
        self.hide()
        self.publish(replace(progress(), epoch=99))
        self.show()
        self.assertEqual(self.pane.history_rows, 1)
        self.assertEqual(self.scene.latest_frame.identity.acquisition_epoch, 99)


class HiddenAnalyzerProductTests(unittest.TestCase):
    def test_navigation_keeps_actual_polling_and_stop_without_hidden_plot_work(self):
        from tests import test_app02_analyzer_workspace_product as fixture
        from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
        from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode

        harness = fixture.AnalyzerWorkspaceProductTests("runTest")
        harness.setUpClass()
        harness.setUp()
        try:
            harness.select_and_apply()
            page = harness.page
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
            canvas = page.visualization
            scene, pane = canvas.spectrum_scene, canvas.waterfall_pane
            snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), progress())
            with patch.object(_FakeAnalyzerDisplay, "poll_latest", return_value=snapshot) as poll:
                page.primary.click()
                harness.wait(lambda: pane.history_rows == 1)
                harness.shell.select_workspace("calibration")
                harness.app.processEvents()
                self.assertFalse(canvas.isVisible())
                baseline_uploads = pane.metrics.image_uploads
                with patch("sdr_monitor.ui.v2.spectrum.scene.peak_preserving_envelope") as envelope, \
                     patch.object(scene.sweep_coverage.state, "project") as project:
                    poll.return_value = ContinuousSweepDisplaySnapshot(
                        terminal(), ContinuousSweepDisplayMetrics(completed_lines=1), progress(2, 2))
                    harness.wait(lambda: pane.history_rows == 2)
                    self.assertEqual(envelope.call_count, 0)
                    self.assertEqual(project.call_count, 0)
                    self.assertEqual(pane.metrics.image_uploads, baseline_uploads)
                    self.assertEqual(harness.events, ["sweep-start"])
                    poll.return_value = ContinuousSweepDisplaySnapshot(
                        terminal(2, gap=True), ContinuousSweepDisplayMetrics(gapped_lines=1))
                    harness.composition.analyzer_view_model.stop()
                    harness.wait(lambda: harness.composition.analyzer_presenter.can_close())
                    self.assertEqual(envelope.call_count, 0)
                harness.shell.select_workspace("analyzer")
                harness.app.processEvents()
                self.assertTrue(canvas.isVisible())
                self.assertEqual(scene.latest_frame.spectrum.sequence, 2)
                self.assertEqual(pane._renderer.sweep_stamps()[-1].state.value, "gap")
                self.assertEqual(harness.events, ["sweep-start", "sweep-stop"])
                self.assertFalse(harness.composition.analyzer_view_model.state.running)
                self.assertIsNotNone(scene.trace_envelope(TraceKind.CURRENT))
        finally:
            try:
                harness.tearDown()
            finally:
                harness.doCleanups()

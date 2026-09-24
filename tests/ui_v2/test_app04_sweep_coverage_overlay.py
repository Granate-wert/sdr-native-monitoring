"""Actual V2 composition + native reduced assembler output; no RF/EXE claim."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
from sdr_monitor.ui.v2.design import ThemeId, tokens_for_theme
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.spectrum.sweep_coverage import CURRENT, MISSING, PREVIOUS
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.state.prepared_sweep import prepare_sweep_snapshot
from tests import test_app02_analyzer_workspace_product as fixture


class SweepCoverageOverlayTests(unittest.TestCase):
    def setUp(self):
        from sdr_monitor import _sdr_native as native
        raw = native._make_test_sweep_position_frames()
        self.early = replace(_to_domain_progress(raw[0]), sequence=5)
        self.final, self.gap, self.empty = [_to_domain_line(frame) for frame in raw[1:]]
        # History has a strong peak; current measurements must never inherit it.
        self.previous = replace(self.final, values_db=np.array([-80, -20, -80, -80, -80, -80, -80, -80, -80]))
        self.addCleanup(set_active_locale, current_locale())
        self.harness = fixture.AnalyzerWorkspaceProductTests("runTest")
        self.harness.setUpClass()
        self.harness.setUp()
        self.model = self.harness.composition.analyzer_view_model
        self.model.select_mode(AnalyzerMode.SWEEP)
        self.model._on_running(True)
        self.scene = self.harness.page.visualization.spectrum_scene
        self.layer = self.scene.sweep_coverage

    def tearDown(self):
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()

    def publish(self, line=None, progress=None):
        snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(), progress)
        # This is a deterministic presentation fixture, not a worker/RF test.
        self.model._on_sweep_snapshot(prepare_sweep_snapshot(snapshot, snapshot.analyzer_bundle))
        expected = self.scene.latest_frame
        self.harness.wait(lambda: self.scene.displayed_frame is expected
                          and self.layer.projection is not None
                          and self.layer._projection_key == self.scene._viewport())

    def test_coalesced_terminal_and_next_partial_history_never_feeds_markers_or_scale(self):
        self.publish(self.previous, self.early)
        self.assertIs(self.layer.state.current, self.early)
        self.assertIs(self.layer.state.previous, self.previous)
        self.assertEqual(self.layer.projection.states.tolist(), [PREVIOUS] * 4 + [CURRENT] * 5)
        np.testing.assert_equal(self.layer.projection.history.values, [-80, -20, -80, -80] + [np.nan] * 5)
        marker = self.scene.move_selected_marker_to_peak()
        self.assertEqual(marker.value, -70)
        self.assertGreaterEqual(marker.frequency_hz, 104e6)
        self.assertLess(self.scene.view_box.viewRange()[1][1], -20)
        self.assertIs(self.scene.latest_frame.spectrum, self.early)
        self.assertEqual(self.harness.events, [])

    def test_first_partial_terminal_cancel_empty_stop_and_epoch(self):
        self.publish(progress=self.early)
        self.assertEqual(self.layer.projection.states.tolist(), [MISSING] * 4 + [CURRENT] * 5)
        self.assertIsNone(self.layer.state.previous)
        self.publish(self.previous, self.early)
        self.publish(replace(self.gap, sequence=5))
        self.assertEqual(self.layer.projection.states.tolist(), [CURRENT] * 5 + [PREVIOUS] * 4)
        self.publish(replace(self.empty, sequence=6))
        self.assertEqual(self.layer.projection.states.tolist(), [PREVIOUS] * 5 + [MISSING] * 4)
        self.model._on_running(False)
        self.assertIsNotNone(self.layer.state.current)  # Stop retains known display, not RX activity.
        self.model._on_running(True)
        self.publish(progress=replace(self.early, epoch=99))
        self.assertIsNone(self.layer.state.previous)
        self.assertEqual(self.layer.projection.states.tolist(), [MISSING] * 4 + [CURRENT] * 5)
        self.model._on_running(False)
        self.model.select_mode(AnalyzerMode.RTBW)
        self.assertIsNone(self.layer.state.current)
        self.assertIsNone(self.layer.projection)
        self.assertFalse(self.layer.strip.isVisible())

    def test_zoom_locale_theme_repeat_and_resize_keep_fixed_item_budget(self):
        self.publish(self.previous, self.early)
        count = len(self.scene.plot_item.items)
        with patch.object(self.layer.state, "project", wraps=self.layer.state.project) as project:
            for _ in range(20):
                self.publish(self.previous, self.early)
            self.assertEqual(project.call_count, 0)
        for theme in ThemeId:
            self.harness.shell.set_theme(theme)
            for locale in UiLocale:
                self.harness.shell.select_appearance_locale(locale)
                self.harness.app.processEvents()
                self.assertEqual(self.layer.label.toPlainText(), text("analyzer.coverage.legend"))
                self.assertIn(text("analyzer.coverage.previous", sequence=1), self.layer.label.toolTip())
                self.assertFalse(self.harness.shell.grab().isNull())
        self.scene.view_box.setXRange(105e6, 107e6, padding=0)
        self.harness.wait(lambda: self.layer._projection_key == self.scene._viewport())
        self.assertTrue(np.all(self.layer.projection.states == CURRENT))
        self.scene.view_box.setXRange(100e6, 102e6, padding=0)
        self.harness.wait(lambda: self.layer._projection_key == self.scene._viewport())
        self.assertIn(-20, self.layer.projection.history.values)
        self.harness.shell.resize(1920, 1080)
        self.harness.app.processEvents()
        self.assertEqual(len(self.scene.plot_item.items), count)
        self.assertEqual(self.harness.events, [])

    def test_no_stale_marker_when_current_is_empty_even_with_history(self):
        self.publish(self.previous)
        self.assertEqual(self.scene.move_selected_marker_to_peak().value, -20)
        self.publish(replace(self.empty, sequence=5))
        self.assertIs(self.layer.state.previous, self.previous)
        self.assertEqual(self.scene.markers, ())
        self.assertIsNone(self.scene.move_selected_marker_to_peak())
        self.assertTrue(self.layer.history.isVisible())
        self.scene.clear_measurement()
        self.assertIsNone(self.layer.state.previous)
        self.assertEqual(self.layer.strip.runs, [])

    def test_previous_pass_is_opaque_distinct_segmented_contour_without_data_change(self):
        self.publish(self.previous, self.early)
        original = self.layer.projection.history
        for theme in ThemeId:
            self.layer.set_theme(theme)
            token = tokens_for_theme(theme).scientific
            pen = self.layer.history.curve.opts["pen"]
            self.assertEqual(pen.style(), Qt.PenStyle.SolidLine)
            self.assertEqual(pen.color().name().lower(), token.previous_sweep.lower())
            self.assertEqual(pen.color().alpha(), 255)
            self.assertEqual(self.layer.history.curve.opts["segmentedLineMode"], "on")
            self.assertFalse(self.layer.history.curve.opts["antialias"])
            self.assertEqual(self.layer.history.curve.opts["connect"], "finite")
            self.assertNotIn(token.previous_sweep.lower(), {
                token.current_spectrum.lower(), token.average.lower(), token.max_hold.lower(),
                token.min_hold.lower(),
            })
            x, y = self.layer.history.getData()
            np.testing.assert_array_equal(x, original.frequencies_hz)
            np.testing.assert_array_equal(y, original.values)
            self.assertTrue(np.isnan(y[4:]).all())
            for locale in UiLocale:
                self.layer.set_locale(locale)
                scope = text("analyzer.coverage.scope", locale)
                self.assertIn(scope, self.layer.history.toolTip())
                self.assertNotIn("Пунктирная", scope)
                self.assertNotIn("Dashed", scope)

    def test_segmented_history_does_not_bridge_nonfinite_samples_in_raster(self):
        curve = self.layer.history.curve
        curve.setData(np.array([5., 15., 25., 35., 45., 55., 65., 75.]),
                      np.array([10., 10., np.nan, np.nan, 10., 10., np.inf, 10.]),
                      connect="finite")
        image = QImage(81, 21, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#000000"))
        painter = QPainter(image)
        try:
            curve.paint(painter, None, None)
        finally:
            painter.end()
        self.assertNotEqual(image.pixelColor(10, 10), QColor("#000000"))
        self.assertNotEqual(image.pixelColor(50, 10), QColor("#000000"))
        self.assertEqual(image.pixelColor(30, 10), QColor("#000000"))
        self.assertEqual(image.pixelColor(70, 10), QColor("#000000"))

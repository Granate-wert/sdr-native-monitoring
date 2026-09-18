"""Actual V2 composition + native reduced assembler output; no RF/EXE claim."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
from sdr_monitor.ui.v2.design import ThemeId
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
        self.assertTrue(np.all(self.layer.projection.states == CURRENT))
        self.scene.view_box.setXRange(100e6, 102e6, padding=0)
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

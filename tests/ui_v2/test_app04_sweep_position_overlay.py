"""Real native reduced fixture -> public adapter -> shared scene, no RF device."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_acquisition import SweepSegmentPosition
from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.state.prepared_sweep import prepare_sweep_snapshot
from sdr_monitor.ui.v2.workspaces.analyzer_inspector import AnalyzerInspector
from tests import test_app02_analyzer_workspace_product as fixture


class SweepPositionOverlayTests(unittest.TestCase):
    def setUp(self):
        from sdr_monitor import _sdr_native as native
        raw = native._make_test_sweep_position_frames()
        self.early = _to_domain_progress(raw[0])
        self.final, self.gap, self.empty = [_to_domain_line(frame) for frame in raw[1:]]
        self.addCleanup(set_active_locale, current_locale())
        self.harness = fixture.AnalyzerWorkspaceProductTests("runTest")
        self.harness.setUpClass()
        self.harness.setUp()
        self.model = self.harness.composition.analyzer_view_model
        self.model.select_mode(AnalyzerMode.SWEEP)
        self.model._on_running(True)
        self.scene = self.harness.page.visualization.spectrum_scene
        self.layer = self.scene._sweep_position

    def tearDown(self):
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()

    def publish(self, line=None, progress=None):
        snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(), progress)
        # This is a deterministic presentation fixture, not a worker/RF test.
        self.model._on_sweep_snapshot(prepare_sweep_snapshot(snapshot, snapshot.analyzer_bundle))

    def test_reordered_progress_terminal_stop_and_mode_reset(self):
        self.publish(progress=self.early)
        self.assertEqual(self.layer.region.getRegion(), (104e6, 108e6))
        self.assertTrue(self.layer.region.isVisible())
        self.publish(self.final, self.early)
        self.assertEqual(self.layer.position.segment_index, 0)
        self.assertEqual(self.layer.region.getRegion(), (100e6, 104e6))
        self.model._on_running(False)
        self.assertEqual(self.layer.position.segment_index, 0)  # Retained measurement, not active tuning.
        self.assertTrue(self.model.select_mode(AnalyzerMode.RTBW))
        self.assertIsNone(self.layer.position)
        self.assertFalse(self.layer.region.isVisible())
        self.assertEqual(self.harness.events, [])

    def test_cancel_gap_keeps_last_admission_empty_gap_and_old_producer_clear(self):
        self.publish(self.gap)
        self.assertEqual(self.layer.position.segment_index, 0)
        self.publish(self.empty)
        self.assertIsNone(self.layer.position)
        self.publish(progress=self.early)
        self.publish(progress=replace(self.early, last_admitted_segment=None))
        self.assertIsNone(self.layer.position)
        self.assertFalse(self.layer.label.isVisible())

    def test_same_position_reuses_items_no_autorange_and_zoom_hides_offscreen_label(self):
        self.publish(progress=self.early)
        count = len(self.scene.plot_item.items)
        with patch.object(self.layer.region, "setRegion", wraps=self.layer.region.setRegion) as update:
            for sequence in range(2, 25):
                self.publish(progress=replace(self.early, sequence=sequence))
            self.assertEqual(update.call_count, 0)
        self.assertEqual(len(self.scene.plot_item.items), count)
        self.scene.view_box.setXRange(100e6, 102e6, padding=0)
        self.assertFalse(self.layer.label.isVisible())
        self.scene.view_box.setXRange(105e6, 107e6, padding=0)
        self.assertTrue(self.layer.label.isVisible())
        self.assertEqual(self.layer.label.pos().x(), 105e6)
        bounds = self.scene.view_box.viewRange()
        self.layer.set_position(SweepSegmentPosition(1, 12, 1, 1e12))
        self.harness.app.processEvents()
        self.assertEqual(self.scene.view_box.viewRange(), bounds)
        with self.assertRaises(TypeError):
            self.layer.set_position((1, 12, 1, 2))

    def test_theme_locale_inspector_and_epoch_have_no_rf_side_effect(self):
        self.publish(progress=self.early)
        for theme in ThemeId:
            self.scene.set_theme(theme)
        for locale in UiLocale:
            self.harness.shell.select_appearance_locale(locale)
            self.harness.app.processEvents()
            self.assertEqual(self.layer.label.toPlainText(), text("analyzer.position.short", index=1))
        self.harness.shell._inspector_toggle.click()
        self.harness.app.processEvents()
        panel = self.harness.shell._narrow_inspector_drawer.findChild(AnalyzerInspector)
        self.assertIn(text("analyzer.position.detail", index=1, lower="104", upper="108"), panel.summary.text())
        self.publish(progress=replace(self.early, epoch=99, last_admitted_segment=None))
        self.assertIsNone(self.layer.position)
        self.assertEqual(self.harness.events, [])

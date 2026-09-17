"""Shared spectrum layers from native-owned rolling statistics; no physical RX."""
from dataclasses import replace
import importlib
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
from sdr_monitor.ui.v2.state.analyzer_layers import persistence_density_from_sweep
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product_fixture
from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
from tests.test_app04_sweep_statistics import statistics_fixture
from tests.ui_v2.test_app04_progressive_waterfall import progress


class SweepStatisticsLayerTests(unittest.TestCase):
    def test_adapter_keeps_native_density_physical_edges_units_and_unknown_cells(self):
        stats = statistics_fixture()
        frame = persistence_density_from_sweep(stats)
        self.assertIs(frame.density, stats.probability)
        self.assertIs(frame.frequency_edges_hz, stats.density_frequency_edges_hz)
        self.assertEqual(frame.level_unit, "dBFS/bin")
        np.testing.assert_array_equal(frame.level_edges, [-100, -50, 0])
        self.assertTrue(np.isnan(frame.density[:, -1]).all())

    def test_parent_rejects_auxiliary_future_snapshot_before_qt(self):
        with self.assertRaises(ValueError):
            replace(progress(), statistics=replace(statistics_fixture(), newest_pass_sequence=2))


class CompiledSweepStatisticsCompositionTests(unittest.TestCase):
    def test_native_statistics_reach_same_canvas_and_clear_on_mode_change(self):
        native = importlib.import_module("sdr_monitor._sdr_native")
        partial, final = native._make_test_sweep_statistics_frames()
        partial, final = _to_domain_progress(partial), _to_domain_line(final)
        harness = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        harness.app = QApplication.instance() or QApplication([])
        harness.setUp()
        try:
            harness.select_and_apply()
            page = harness.page
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
            snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), partial)
            with patch.object(_FakeAnalyzerDisplay, "poll_latest", return_value=snapshot) as poll:
                page.primary.click()
                harness.wait(lambda: page._last_statistics_key is not None)
                scene = page.visualization.spectrum_scene
                self.assertIs(scene._trace_views[TraceKind.AVERAGE].values, partial.statistics.average_db)
                with patch.object(scene, "set_persistence_frame", wraps=scene.set_persistence_frame) as upload:
                    harness.composition.analyzer_presenter._poll()
                    self.assertEqual(upload.call_count, 0, "unchanged native histogram reuploaded")
                    poll.return_value = ContinuousSweepDisplaySnapshot(final, ContinuousSweepDisplayMetrics())
                    harness.composition.analyzer_presenter._poll()
                    self.assertEqual(upload.call_count, 1)
                self.assertEqual(page.visualization.waterfall_pane.history_rows, 1)
                page.primary.click()
                harness.wait(lambda: harness.composition.analyzer_presenter.can_close())
                self.assertIn(TraceKind.AVERAGE, scene._trace_views)
                self.assertIn("16", page.status.text())
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.RTBW))
            self.assertNotIn(TraceKind.AVERAGE, scene._trace_views)
            self.assertIsNone(page._last_statistics_key)
            self.assertEqual(harness.events, ["sweep-start", "sweep-stop"])
        finally:
            try:
                harness.tearDown()
            finally:
                harness.doCleanups()

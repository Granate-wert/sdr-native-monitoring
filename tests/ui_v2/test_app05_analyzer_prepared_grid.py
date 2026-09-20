"""Reuse worker-validated owned grids on GUI, never borrowed buffer identity."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state.prepared_sweep import SweepSnapshotPreparer, prepare_sweep_snapshot
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.workspaces import analyzer
from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal
from tests.ui_v2.test_app05_prepared_live import measurement


class AnalyzerPreparedGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = f = product.AnalyzerWorkspaceProductTests("runTest")
        f.app = self.app
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.addCleanup(f.tearDown)
        self.page = f.page
        self.prepare = SweepSnapshotPreparer(PresentationAllocationBudget())

    def state(self, frame, *, preparer=None, line=None):
        snap = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(), frame)
        prepared = (preparer or self.prepare)(snap, snap.analyzer_bundle)
        return replace(self.page.model.state, mode=AnalyzerMode.SWEEP, running=True,
                       bundle=prepared.analyzer_bundle, sweep_snapshot=snap, prepared_sweep=prepared)

    def test_new_prepared_sweep_frames_reuse_grid_without_gui_scan_or_history_reset(self):
        first = self.state(progress())
        second = self.state(progress(2), line=terminal(1))
        self.assertIs(first.prepared_sweep.spectrum.measurement_grid,
                      second.prepared_sweep.spectrum.measurement_grid)
        self.page._render(first)
        pane = self.page.visualization.waterfall_pane
        equal = Mock(wraps=np.array_equal)
        with patch.object(analyzer, "np", SimpleNamespace(array_equal=equal)), \
             patch.object(pane, "clear_history", wraps=pane.clear_history) as cleared:
            self.page._render(second)
            self.assertEqual(equal.call_count, 0)
            cleared.assert_not_called()
        self.assertEqual(pane.history_rows, 2)
        self.assertIs(self.page.visualization.spectrum_scene.latest_frame, second.bundle)

    def test_mutated_readonly_alias_uses_owned_previous_grid_to_reset_history(self):
        backing = progress().frequencies_hz.copy()
        borrowed = backing.view()
        borrowed.setflags(write=False)
        first = self.state(replace(progress(), frequencies_hz=borrowed))
        self.page._render(first)
        old_grid = first.prepared_sweep.spectrum.measurement_grid
        backing += 125  # valid regular grid, but both old/new identities now see the mutation
        second = self.state(replace(progress(2), frequencies_hz=borrowed))
        self.assertIsNot(old_grid, second.prepared_sweep.spectrum.measurement_grid)
        np.testing.assert_array_equal(first.bundle.identity.frequencies_hz, second.bundle.identity.frequencies_hz)
        pane = self.page.visualization.waterfall_pane
        with patch.object(pane, "clear_history", wraps=pane.clear_history) as cleared:
            self.page._render(second)
            cleared.assert_called_once_with(reset_kind=True)
        self.assertEqual(pane.history_rows, 1)
        np.testing.assert_array_equal(old_grid, progress().frequencies_hz)

    def test_foreign_prepared_bundle_is_rejected_before_resetting_existing_history(self):
        first = self.state(progress())
        self.page._render(first)
        shifted = progress(2).frequencies_hz + 125
        shifted.setflags(write=False)
        second = self.state(replace(progress(2), frequencies_hz=shifted))
        invalid = replace(second, prepared_sweep=first.prepared_sweep)
        pane = self.page.visualization.waterfall_pane
        with patch.object(pane, "clear_history", wraps=pane.clear_history) as cleared:
            with self.assertRaisesRegex(ValueError, "exact publication"):
                self.page._render(invalid)
            cleared.assert_not_called()
        self.assertIs(self.page.visualization.spectrum_scene.latest_frame, first.bundle)
        self.assertEqual(pane.history_rows, 1)

    def test_new_cache_and_unprepared_packets_compare_content_and_epoch_still_resets(self):
        first = self.state(progress())
        self.page._render(first)
        other = SweepSnapshotPreparer(PresentationAllocationBudget())
        second = self.state(progress(2), preparer=other)
        self.assertIsNot(first.prepared_sweep.spectrum.measurement_grid,
                         second.prepared_sweep.spectrum.measurement_grid)
        third = self.state(progress(3), preparer=other)
        pane = self.page.visualization.waterfall_pane
        equal = Mock(wraps=np.array_equal)
        with patch.object(analyzer, "np", SimpleNamespace(array_equal=equal)), \
             patch.object(pane, "clear_history", wraps=pane.clear_history) as cleared:
            self.page._render(second)
            self.page._render(replace(third, prepared_sweep=None))
            self.assertEqual(equal.call_count, 2)
            cleared.assert_not_called()
            changed = self.state(replace(progress(4), epoch=8), preparer=other)
            self.page._render(changed)
            cleared.assert_called_once_with(reset_kind=True)

    def test_actual_rtbw_worker_delivery_reuses_grid_for_new_bundles(self):
        f = self.fixture
        f.select_and_apply()
        f.presenter._poll_timer.stop()
        equal = Mock(wraps=np.array_equal)
        sources = []
        with patch.object(analyzer, "np", SimpleNamespace(array_equal=equal)):
            for number in range(1, 5):
                snapshot = measurement(f, number)
                f.presenter._emit_snapshot(snapshot)
                f.wait(lambda: f.composition.view_model.state.spectrum is snapshot.spectrum
                       and f.presenter._preparation_future is None)
                sources.append(self.page._last_bundle)
        self.assertEqual(len({id(bundle) for bundle in sources}), 4)
        self.assertEqual(equal.call_count, 0)
        self.assertIs(self.page.visualization.spectrum_scene.latest_frame, sources[-1])

    def test_prepared_without_cache_falls_back_and_changed_interior_clears_history(self):
        first = self.state(progress())
        self.page._render(first)
        second = self.state(progress(2))
        uncached = prepare_sweep_snapshot(second.sweep_snapshot, second.bundle)
        self.assertIsNone(uncached.spectrum.measurement_grid)
        equal = Mock(wraps=np.array_equal)
        with patch.object(analyzer, "np", SimpleNamespace(array_equal=equal)):
            self.page._render(replace(second, prepared_sweep=uncached))
            self.assertEqual(equal.call_count, 1)
        shifted = progress(3).frequencies_hz.copy()
        shifted[1] += .25
        shifted.setflags(write=False)
        changed = self.state(replace(progress(3), frequencies_hz=shifted))
        pane = self.page.visualization.waterfall_pane
        with patch.object(pane, "clear_history", wraps=pane.clear_history) as cleared:
            self.page._render(changed)
            cleared.assert_any_call(reset_kind=True)
        self.assertIs(self.page.visualization.spectrum_scene.latest_frame, changed.bundle)
        # Irregular Sweep grid is still an explicit optional Waterfall error.
        self.assertTrue(self.page._sweep_waterfall_error)
        self.assertEqual(pane.history_rows, 0)

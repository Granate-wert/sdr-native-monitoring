"""Comparison-grid copies are admitted on workers, reused and actually charged."""
import gc
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget, PresentationBudgetExceeded
from sdr_monitor.ui.v2.spectrum.grid_baseline import MeasurementGridCache
from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays
from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app05_prepared_live import measurement


class GridBaselineTests(unittest.TestCase):
    def test_refusal_precedes_copy(self):
        class NoCopy(np.ndarray):
            def copy(self, *args, **kwargs):
                raise AssertionError("copied before reservation")
        grid = np.arange(10, dtype=np.float64).view(NoCopy)
        budget = PresentationAllocationBudget(1)
        cache = MeasurementGridCache(budget)
        with self.assertRaises(PresentationBudgetExceeded):
            cache.prepare(grid)
        self.assertIsNone(cache.baseline)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_equal_grid_reuses_owned_readonly_baseline_and_old_new_lifetimes_count(self):
        budget = PresentationAllocationBudget(160)
        cache = MeasurementGridCache(budget)
        grid = np.arange(10, dtype=np.float64)
        first = cache.prepare(grid)
        self.assertIs(cache.prepare(grid.copy()), first)
        self.assertFalse(np.shares_memory(first, grid))
        self.assertFalse(first.flags.writeable)
        second = cache.prepare(grid + 1)
        self.assertEqual(budget.snapshot().observed_bytes, 160)
        with self.assertRaises(PresentationBudgetExceeded):
            cache.prepare(grid + 2)
        self.assertIs(cache.baseline, second)
        del first
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 80)
        cache.clear()
        self.assertEqual(budget.snapshot().observed_bytes, 80)
        del second
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 0)


class ProductGridBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.f = product.AnalyzerWorkspaceProductTests("runTest")
        self.f.app = self.app
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown)
        self.f.select_and_apply()

    def test_actual_product_builds_grid_off_gui_and_scene_reuses_it(self):
        f = self.f
        threads = []
        original = MeasurementGridCache.prepare
        def prepare(cache, frequencies):
            threads.append(threading.get_ident())
            return original(cache, frequencies)
        with patch.object(MeasurementGridCache, "prepare", prepare):
            first = measurement(f)
            f.presenter._emit_snapshot(first)
            f.wait(lambda: f.composition.view_model.state.spectrum is first.spectrum)
            baseline = f.composition.view_model.state.prepared_spectrum.measurement_grid
            self.assertIs(f.page.visualization.spectrum_scene._measurement_grid, baseline)
            second = measurement(f, 2)
            f.presenter._emit_snapshot(second)
            f.wait(lambda: f.composition.view_model.state.spectrum is second.spectrum)
            self.assertIs(f.composition.view_model.state.prepared_spectrum.measurement_grid, baseline)
            self.assertIs(f.page.visualization.spectrum_scene._measurement_grid, baseline)
        self.assertTrue(threads)
        self.assertTrue(all(value != threading.get_ident() for value in threads))

    def test_grid_failure_publishes_same_array_free_omission_on_both_channels(self):
        f = self.f
        source = measurement(f)
        budget = f.composition.allocation_budget
        budget.limit_bytes = sum(retained_arrays(source).values())
        delivered, errors = [], []
        f.presenter.snapshot_changed.connect(delivered.append)
        f.presenter.task_failed.connect(errors.append)
        f.presenter._emit_snapshot(source)
        f.wait(lambda: bool(delivered))
        snapshot = f.composition.view_model.state.snapshot
        self.assertIs(snapshot, delivered[-1])
        self.assertIsNotNone(snapshot.presentation_omission)
        self.assertEqual(retained_arrays(snapshot), {})
        self.assertIsNone(f.presenter._snapshot_preparer._grid.baseline)
        self.assertIsNone(f.page.visualization.spectrum_scene.latest_frame)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertEqual(errors, [])
        self.assertIsNotNone(source.spectrum)


if __name__ == "__main__":
    unittest.main()

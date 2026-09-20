"""Scalar geometry reuse requires exact owned-baseline content, not pointer trust."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot, ContinuousSweepDisplayMetrics
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget, PresentationBudgetExceeded
from sdr_monitor.ui.v2.spectrum.grid_baseline import MeasurementGridCache
from sdr_monitor.ui.v2.state import analyzer_layers as layers
from sdr_monitor.ui.v2.state.prepared_sweep import SweepSnapshotPreparer
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal
from tests.ui_v2.test_app05_prepared_live import measurement
from tests import test_app02_analyzer_workspace_product as product


class GridGeometryReuseTests(unittest.TestCase):
    def test_refused_replacement_preserves_old_scalar_and_reserves_no_extra_bytes(self):
        budget = PresentationAllocationBudget(80)
        cache = MeasurementGridCache(budget)
        first = np.arange(10, dtype=np.float64)
        baseline = cache.prepare(first)
        self.assertEqual(cache.regular_spacing(first, layers._regular_spacing), 1)
        with self.assertRaises(PresentationBudgetExceeded):
            cache.prepare(first * 2)
        self.assertIs(cache.baseline, baseline)
        self.assertEqual(cache._regular_spacing, 1)
        self.assertEqual(budget.snapshot().observed_bytes, 80)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_equal_content_reuses_scalar_without_new_arrays_or_owner(self):
        budget = PresentationAllocationBudget(1000)
        cache = MeasurementGridCache(budget)
        grid = np.arange(10, dtype=np.float64)
        baseline = cache.prepare(grid)
        with patch.object(layers, "_regular_spacing", wraps=layers._regular_spacing) as checked:
            for centers in (grid, grid.copy(), np.repeat(grid, 2)[::2]):
                self.assertEqual(cache.regular_spacing(centers, layers._regular_spacing), 1)
            self.assertEqual(checked.call_count, 1)
        self.assertIs(cache.baseline, baseline)
        self.assertEqual(budget.snapshot().observed_bytes, baseline.nbytes)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_readonly_view_with_mutated_backing_does_not_reuse_stale_spacing(self):
        cache = MeasurementGridCache(PresentationAllocationBudget())
        backing = np.arange(10, dtype=np.float64)
        view = backing.view()
        view.setflags(write=False)
        baseline = cache.prepare(view)
        self.assertEqual(cache.regular_spacing(view, layers._regular_spacing), 1)
        backing[4] += .1
        with self.assertRaisesRegex(ValueError, "regular physical grid"):
            cache.regular_spacing(view, layers._regular_spacing)
        backing[:] = np.arange(10) * 2 + 100
        self.assertEqual(cache.regular_spacing(view, layers._regular_spacing), 2)
        self.assertIs(cache.baseline, baseline)  # mismatched row never allocates a second baseline
        np.testing.assert_array_equal(baseline, np.arange(10))

    def test_replacement_clear_and_failed_regular_validation_do_not_keep_old_scalar(self):
        cache = MeasurementGridCache(PresentationAllocationBudget())
        first = np.arange(10, dtype=np.float64)
        with patch.object(layers, "_regular_spacing", wraps=layers._regular_spacing) as checked:
            cache.prepare(first)
            self.assertEqual(cache.regular_spacing(first, layers._regular_spacing), 1)
            second = first * 2
            cache.prepare(second)
            self.assertEqual(cache.regular_spacing(second, layers._regular_spacing), 2)
            bad = second.copy()
            bad[4] += .1
            cache.prepare(bad)
            with self.assertRaises(ValueError):
                cache.regular_spacing(bad, layers._regular_spacing)
            self.assertIsNone(cache._regular_spacing)
            cache.clear()
            self.assertIsNone(cache.baseline)
            self.assertEqual(cache.regular_spacing(first, layers._regular_spacing), 1)
            self.assertIsNone(cache._regular_spacing)
            self.assertEqual(checked.call_count, 4)

    def test_exact_uncached_row_oracle_across_sizes_dtypes_and_gaps(self):
        for size in (2, 2048, 2049, 65539):
            for dtype in (np.float32, np.float64):
                with self.subTest(size=size, dtype=dtype):
                    centers = np.arange(size, dtype=dtype) * 32 + 100e6
                    values = np.sin(np.arange(size)).astype(np.float32)
                    values[0] = np.nan
                    values[-1] = -np.inf
                    if size > 2:
                        values[size // 2] = np.inf
                    cache = MeasurementGridCache(PresentationAllocationBudget())
                    cache.prepare(centers)
                    expected = layers._waterfall_projection(centers, values)
                    for _ in range(2):
                        actual = layers._waterfall_projection(centers, values, grid_cache=cache)
                        for left, right in zip(actual, expected):
                            np.testing.assert_array_equal(left, right)

    def test_normal_sweep_preparer_reuses_same_grid_across_terminal_and_preview(self):
        prepare = SweepSnapshotPreparer(PresentationAllocationBudget())
        with patch.object(layers, "_regular_spacing", wraps=layers._regular_spacing) as checked:
            for number in range(1, 8):
                snap = ContinuousSweepDisplaySnapshot(terminal(number), ContinuousSweepDisplayMetrics(),
                                                       progress(number + 1))
                result = prepare(snap, snap.analyzer_bundle)
                self.assertEqual(len(result.waterfall_rows), 2)
                self.assertIsNone(result.waterfall_error)
            self.assertEqual(checked.call_count, 1)
        self.assertIs(result.spectrum.measurement_grid, prepare._grid.baseline)
        self.assertEqual(prepare.allocation_budget.snapshot().reserved_bytes, 0)

    def test_different_terminal_grid_uses_own_edges_and_nonregular_is_optional_failure(self):
        prepare = SweepSnapshotPreparer(PresentationAllocationBudget())
        shifted = replace(terminal(), frequencies_hz=terminal().frequencies_hz + 500e6)
        # The domain packet already rejects mixed grids, before presentation.
        with self.assertRaisesRegex(ValueError, "must share"):
            ContinuousSweepDisplaySnapshot(shifted, ContinuousSweepDisplayMetrics(), progress(2))
        prepare._grid.prepare(progress().frequencies_hz)
        for _ in range(2):
            actual = layers.waterfall_line_from_sweep(shifted, grid_cache=prepare._grid)
            expected = layers.waterfall_line_from_sweep(shifted)
            np.testing.assert_array_equal(actual.row.frequency_edges_hz, expected.row.frequency_edges_hz)
        bad = shifted.frequencies_hz.copy()
        bad[2] += 100
        bad.setflags(write=False)
        snap = ContinuousSweepDisplaySnapshot(replace(shifted, frequencies_hz=bad),
            ContinuousSweepDisplayMetrics(), replace(progress(2), frequencies_hz=bad))
        result = prepare(snap, snap.analyzer_bundle)
        self.assertIsNotNone(result.spectrum)
        self.assertIs(result.analyzer_bundle.spectrum, snap.progress)
        self.assertEqual(result.waterfall_rows, ())
        self.assertIn("regular physical grid", result.waterfall_error)
        self.assertIsNone(result.snapshot.presentation_omission)


class ProductGridGeometryReuseTests(unittest.TestCase):
    def test_normal_live_composition_reuses_geometry_only_off_gui(self):
        f = product.AnalyzerWorkspaceProductTests("runTest")
        f.app = QApplication.instance() or QApplication([])
        f.setUp()
        threads = []
        original = layers._regular_spacing
        def validate(values):
            threads.append(threading.get_ident())
            return original(values)
        try:
            f.select_and_apply()
            f.presenter._poll_timer.stop()  # this test supplies exact manual offers
            with patch.object(layers, "_regular_spacing", side_effect=validate):
                for number in range(1, 5):
                    snapshot = measurement(f, number)
                    f.presenter._emit_snapshot(snapshot)
                    f.wait(lambda: f.composition.view_model.state.spectrum is snapshot.spectrum
                           and f.presenter._preparation_future is None)
            # RTBW builds layers before its first spectrum baseline: first
            # fallback, then one validated scalar; further frames reuse it.
            self.assertEqual(len(threads), 2)
            self.assertTrue(all(value != threading.get_ident() for value in threads))
            self.assertIs(f.presenter._snapshot_preparer._grid, f.presenter._snapshot_preparer._layers._grid)
        finally:
            f.tearDown()
            f.doCleanups()


if __name__ == "__main__":
    unittest.main()

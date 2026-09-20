"""Exact former adapter oracle, bounded scratch and no trusted source cache."""
from dataclasses import dataclass, replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum import contracts
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.grid_baseline import MeasurementGridCache


@dataclass(frozen=True)
class Frame:
    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str = "dBm"


def old_grid_error(grid):
    """Independent old full-array expressions, including dtype arithmetic."""
    with np.errstate(all="ignore"):
        if not np.all(np.isfinite(grid)):
            return "spectrum frame frequency grid must be finite"
        if np.any(np.diff(grid) <= 0.0):
            return "spectrum frame frequency grid must be strictly increasing"
    return None


class SpectrumValidationTests(unittest.TestCase):
    def assert_oracle(self, grid):
        original = grid.copy()
        frame = Frame(grid, np.zeros(grid.size))
        expected = old_grid_error(grid)
        with np.errstate(all="ignore"):
            if expected:
                with self.assertRaises(ValueError) as raised:
                    contracts.adapt_spectrum_frame(frame)
                self.assertEqual(str(raised.exception), expected)
            else:
                view = contracts.adapt_spectrum_frame(frame)
                self.assertIs(view.source_frame, frame)
                self.assertTrue(np.shares_memory(view.frequencies_hz, grid))
                self.assertTrue(np.shares_memory(view.values, frame.values))
                self.assertEqual(view.unit_label, "dBm")
        np.testing.assert_array_equal(grid, original)

    def test_every_chunk_seam_and_final_bin_matches_old_full_grid_oracle(self):
        size = 131077
        for index in (0, 1, 65535, 65536, 65537, 131071, 131072, size - 1):
            for defect in (np.nan, np.inf, -np.inf, -1., 0.):
                grid = np.arange(size, dtype=np.float64)
                grid[index] = defect
                with self.subTest(index=index, defect=defect):
                    self.assert_oracle(grid)

    def test_later_nonfinite_keeps_priority_over_earlier_order_error(self):
        grid = np.arange(131077, dtype=np.float64)
        grid[2] = -10
        grid[-1] = np.nan
        self.assertEqual(old_grid_error(grid), "spectrum frame frequency grid must be finite")
        self.assert_oracle(grid)

    def test_dtypes_strides_singleton_and_extreme_diff_semantics_are_unchanged(self):
        for dtype in (np.float16, np.float32, np.float64, np.int8, np.int64,
                      np.uint8, np.uint64, np.complex64, np.complex128):
            for grid in (np.arange(100, dtype=dtype), np.arange(100, dtype=dtype)[::3],
                         np.arange(100, dtype=dtype)[::-1], np.array([1], dtype=dtype)):
                with self.subTest(dtype=dtype, size=grid.size, strides=grid.strides):
                    self.assert_oracle(grid)
        maximum = np.finfo(np.float64).max
        for grid in (np.array([-maximum, maximum]), np.array([maximum, -maximum]),
                     np.array([np.iinfo(np.int64).min, np.iinfo(np.int64).max]),
                     np.array([0., -0.])):
            self.assert_oracle(grid)

    def test_randomized_strided_grids_match_original_validation(self):
        rng = np.random.default_rng(982)
        for number in range(200):
            size = int(rng.choice([1, 73, 65535, 65536, 65537, 131077]))
            backing = np.arange(size * 2, dtype=np.float64)
            grid = backing[::2]
            if number % 4:
                grid[int(rng.integers(size))] = rng.choice([np.nan, np.inf, -np.inf, -10., 0.])
            with self.subTest(number=number, size=size):
                self.assert_oracle(grid)

    def test_full_validation_scratch_never_uses_full_large_grid(self):
        grid = np.arange(2000003, dtype=np.float64)
        finite, diff = np.isfinite, np.diff
        finite_sizes, diff_sizes = [], []
        def check_finite(chunk):
            finite_sizes.append(chunk.size)
            return finite(chunk)
        def check_diff(chunk):
            diff_sizes.append(chunk.size)
            return diff(chunk)
        with patch.object(contracts.np, "isfinite", check_finite), \
             patch.object(contracts.np, "diff", check_diff):
            contracts.adapt_spectrum_frame(Frame(grid, np.zeros(grid.size)))
        self.assertGreater(len(diff_sizes), 1)
        self.assertTrue(all(size <= 65537 for size in finite_sizes + diff_sizes))
        self.assertEqual(sum(size - 1 for size in diff_sizes), grid.size - 1)

    def test_existing_baseline_does_not_hide_backing_mutation_or_bad_replacement(self):
        backing = np.arange(65540, dtype=np.float64)
        grid = backing.view()
        grid.setflags(write=False)
        values = np.zeros(grid.size)
        values.setflags(write=False)
        cache = MeasurementGridCache(PresentationAllocationBudget())
        frame = Frame(grid, values)
        first = contracts.PreparedSpectrumFrame(frame, grid_cache=cache)
        baseline = first.measurement_grid
        backing[65536] = -1
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            contracts.PreparedSpectrumFrame(frame, grid_cache=cache)
        self.assertIs(cache.baseline, baseline)
        self.assertEqual(baseline[65536], 65536)
        self.assertEqual(cache.budget.snapshot().reserved_bytes, 0)
        backing[:] = np.arange(grid.size) * 2
        second = contracts.PreparedSpectrumFrame(frame, grid_cache=cache)
        self.assertIsNot(second.measurement_grid, baseline)
        np.testing.assert_array_equal(second.measurement_grid, backing)

    def test_shape_unit_numeric_mutability_and_value_semantics_remain(self):
        grid = np.arange(4., dtype=np.float64)
        values = np.array([np.nan, np.inf, -np.inf, -7.])
        frame = Frame(grid, values)
        for source, message, error in (
                (replace(frame, unit=""), "non-empty unit", ValueError),
                (replace(frame, values=values[:2]), "equally sized", ValueError),
                (replace(frame, frequencies_hz=np.array(["a"] * 4)), "numeric", TypeError),
                (replace(frame, values=np.array(["a"] * 4)), "numeric", TypeError)):
            with self.subTest(message=message), self.assertRaisesRegex(error, message):
                contracts.adapt_spectrum_frame(source)
        with self.assertRaisesRegex(ValueError, "read-only"):
            contracts.PreparedSpectrumFrame(frame)
        grid.setflags(write=False)
        values.setflags(write=False)
        prepared = contracts.PreparedSpectrumFrame(frame)
        self.assertIs(prepared.view.source_frame, frame)
        self.assertEqual(prepared.finite_extent, (-7., -7.))
        np.testing.assert_array_equal(prepared.view.values, values)


if __name__ == "__main__":
    unittest.main()

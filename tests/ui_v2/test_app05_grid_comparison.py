"""Every comparison interval stays exact without a full-grid boolean scratch."""
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum import grid_baseline
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget


class GridComparisonTests(unittest.TestCase):
    def test_cache_prepare_and_spacing_compare_all_samples_in_bounded_chunks(self):
        cache = grid_baseline.MeasurementGridCache(PresentationAllocationBudget())
        source = np.arange(200_003, dtype=np.float64)
        source.setflags(write=False)
        baseline = cache.prepare(source)
        shapes = []
        original = np.array_equal

        def compare(left, right):
            shapes.append(left.size)
            self.assertLessEqual(left.size, 65536)
            return original(left, right)

        with patch.object(grid_baseline.np, "array_equal", side_effect=compare):
            self.assertIs(cache.prepare(source), baseline)
            self.assertEqual(cache.regular_spacing(source, lambda _: 1.), 1.)
        self.assertEqual(sum(shapes), source.size * 2)
        self.assertEqual(shapes, [65536, 65536, 65536, 3395] * 2)
        self.assertEqual(cache.budget.snapshot().reserved_bytes, 0)

    def test_array_equal_oracle_dtypes_strides_nan_empty_shapes_and_batch_seams(self):
        for dtype in (np.float32, np.float64, np.int64, np.uint64):
            for size in (0, 1, 65535, 65536, 65537, 131075):
                source = np.arange(size * 2, dtype=dtype)[::2]
                other = source.copy()
                with self.subTest(dtype=dtype, size=size):
                    self.assertEqual(grid_baseline._same_grid(source, other), np.array_equal(source, other))
                    for index in {0, 65535, 65536, size - 1}:
                        if 0 <= index < size:
                            other[index] += 1
                            self.assertFalse(grid_baseline._same_grid(source, other))
                            other[index] -= 1
        pairs = ((np.array([np.nan]), np.array([np.nan])),
                 (np.array([0., np.inf, -np.inf]), np.array([-0., np.inf, -np.inf])),
                 (np.array([1., 2.]), np.array([1, 2], dtype=np.int32)),
                 (np.array([1, 2]), np.array([[1, 2]])),
                 (np.arange(12).reshape(3, 4).T, np.arange(12).reshape(3, 4).T),
                 (np.array(3), np.array(3)))
        for left, right in pairs:
            self.assertEqual(grid_baseline._same_grid(left, right), np.array_equal(left, right))

    def test_readonly_alias_mutation_is_not_hidden_by_pointer_or_spacing_reuse(self):
        cache = grid_baseline.MeasurementGridCache(PresentationAllocationBudget())
        backing = np.arange(131075, dtype=np.float64)
        source = backing.view()
        source.setflags(write=False)
        baseline = cache.prepare(source)
        validate = unittest.mock.Mock(return_value=1.)
        self.assertEqual(cache.regular_spacing(source, validate), 1.)
        backing[65536] += .25
        self.assertEqual(cache.regular_spacing(source, validate), 1.)
        self.assertEqual(validate.call_count, 2)
        replaced = cache.prepare(source)
        self.assertIsNot(replaced, baseline)
        self.assertEqual(baseline[65536], 65536.)
        self.assertEqual(replaced[65536], 65536.25)
        self.assertIsNone(cache._regular_spacing)


if __name__ == "__main__":
    unittest.main()

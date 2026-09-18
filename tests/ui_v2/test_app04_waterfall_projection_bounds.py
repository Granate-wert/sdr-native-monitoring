"""Bounded Waterfall scratch must preserve exact physical/gap semantics."""
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.state import analyzer_layers as layers


class WaterfallProjectionBoundsTests(unittest.TestCase):
    def test_large_projection_matches_original_integer_center_assignment(self):
        rng = np.random.default_rng(97)
        for count in (2049, 2051, 4095, 4096, 4097, 8191, 131073, 2000000):
            with self.subTest(count=count):
                values = rng.normal(-80, 10, count).astype(np.float32)
                values[[0, count//3, count//2, count-1]] = [np.nan, -np.inf, np.inf, -np.inf]
                frequencies = 100e6 + np.arange(count, dtype=np.float64) * 125.
                bucket = ((2 * np.arange(count, dtype=np.int64) + 1) * 2048) // (2 * count)
                splits = np.flatnonzero(np.diff(bucket)) + 1
                expected = np.array([np.max(group) if np.all(np.isfinite(group)) else np.nan
                                     for group in np.split(values, splits)], dtype=np.float32)
                original = values.copy()
                actual, edges = layers._waterfall_projection(frequencies, values)
                np.testing.assert_array_equal(actual, expected)
                np.testing.assert_array_equal(values, original)
                expected_edges = frequencies[0] - 62.5 + np.arange(2049) * (count * 125. / 2048)
                expected_edges[-1] = frequencies[-1] + 62.5
                np.testing.assert_array_equal(edges, expected_edges)
                self.assertFalse(actual.flags.writeable)
                self.assertFalse(edges.flags.writeable)

    def test_full_resolution_edges_and_bucket_arrays_are_not_allocated(self):
        count = 2000000
        frequencies = 100e6 + np.arange(count, dtype=np.float64)
        values = np.full(count, -80., dtype=np.float32)
        with patch.object(layers, "_regular_edges", side_effect=AssertionError("full edges")), \
                patch.object(layers.np, "arange", wraps=np.arange) as arange, \
                patch.object(layers.np, "diff", wraps=np.diff) as diff:
            result, edges = layers._waterfall_projection(frequencies, values)
            self.assertEqual(result.size, 2048)
            self.assertEqual(edges.size, 2049)
            self.assertLessEqual(max(call.args[0] for call in arange.call_args_list), 2049)
            self.assertLessEqual(max(call.args[0].size for call in diff.call_args_list), 65537)
            # Every interval was checked, with no missing boundary interval.
            self.assertEqual(sum(call.args[0].size - 1 for call in diff.call_args_list), count - 1)

    def test_irregular_nonfinite_and_boundary_intervals_fail_closed(self):
        count = 131075
        values = np.full(count, -80., dtype=np.float32)
        for index in (0, 65535, 65536, 65537, 131072, count-1):
            for bad in (np.nan, np.inf, -.25):
                with self.subTest(index=index, bad=bad):
                    frequencies = np.arange(count, dtype=np.float64)
                    frequencies[index] = bad if not np.isfinite(bad) else frequencies[index] + bad
                    with self.assertRaises(ValueError):
                        layers._waterfall_projection(frequencies, values)

    def test_small_rows_keep_original_values_and_edges(self):
        for count in (2, 1024, 2048):
            frequencies = 100e6 + np.arange(count, dtype=np.float64)
            values = np.full(count, -80., dtype=np.float32)
            values.setflags(write=False)
            result, edges = layers._waterfall_projection(frequencies, values)
            self.assertIs(result, values)
            np.testing.assert_array_equal(edges, 100e6 - .5 + np.arange(count+1))

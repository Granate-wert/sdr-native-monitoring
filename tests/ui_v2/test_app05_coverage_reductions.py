"""Coverage boolean ownership and column-sized midpoint work stay exact."""
from dataclasses import replace
from math import ceil
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum import sweep_coverage as coverage
from tests.ui_v2.test_app04_batched_envelope import reference_envelope
from tests.ui_v2.test_app04_sweep_coverage import line, snapshot


class CoverageReductionTests(unittest.TestCase):
    def test_no_integer_ownership_counts_or_per_column_python_edges(self):
        state = coverage.SweepCoverageState()
        state.accept(snapshot(line(1, np.full(200_003, -80.))))
        values = np.full(200_003, np.nan)
        values[:100_000] = -np.inf
        state.accept(snapshot(line(2, values)))
        with patch.object(coverage.np, "count_nonzero", side_effect=AssertionError("unneeded count")), \
                patch.object(coverage, "_edge", wraps=coverage._edge) as edges:
            actual = state.project(0, 1e12, 1920)
        self.assertLessEqual(edges.call_count, 2)
        self.assertGreater(actual.states.size, 1000)

    def test_scalar_ownership_midpoints_and_history_match_for_clipped_irregular_grids(self):
        rng = np.random.default_rng(58204)
        for count in (3, 17, 65539):
            grid = 1e6 + np.cumsum(rng.uniform(.1, 3, count))
            old_values = rng.choice([-90., -40., -np.inf, np.nan], count)
            new_values = rng.choice([-80., -20., -np.inf, np.nan], count)
            old = replace(line(1, old_values), frequencies_hz=grid)
            current = replace(line(2, new_values), frequencies_hz=grid)
            state = coverage.SweepCoverageState()
            state.accept(snapshot(old))
            state.accept(snapshot(current))
            for width in (0, 1, 7, 2048, 99999):
                for left, right in ((0, 1e12), (grid[count // 3], grid[2 * count // 3]),
                                    (grid[-1] + 1, grid[-1] + 2)):
                    with self.subTest(count=count, width=width, left=left):
                        start = max(0, int(np.searchsorted(grid, left)) - 1)
                        stop = min(count, int(np.searchsorted(grid, right, side="right")) + 1)
                        size = ceil((stop - start) / max(1, min(2048, width)))
                        flags, edges, x, y = [], [coverage._edge(grid, start)], [], []
                        for begin in range(start, stop, size):
                            end = min(stop, begin + size)
                            current_owned = current.values_db[begin:end] < np.inf
                            history_owned = ~current_owned & (old.values_db[begin:end] < np.inf)
                            missing = ~current_owned & ~history_owned
                            flags.append((1 if current_owned.any() else 0)
                                         | (2 if history_owned.any() else 0) | (4 if missing.any() else 0))
                            draw = history_owned & np.isfinite(old.values_db[begin:end])
                            fx, fy = reference_envelope(grid[begin:end],
                                np.where(draw, old.values_db[begin:end], np.nan), 1)
                            x.extend(fx)
                            y.extend(fy)
                            edges.append(coverage._edge(grid, end))
                        actual = state.project(left, right, width)
                        for actual_array, expected in ((actual.states, flags), (actual.edges_hz, edges),
                                (actual.history.frequencies_hz, x), (actual.history.values, y)):
                            np.testing.assert_equal(actual_array, expected)
                            self.assertFalse(actual_array.flags.writeable)
                        self.assertLessEqual(actual.states.size, 2048)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.analysis import (
    integrated_power_db,
    local_peak_indices,
    occupied_bandwidth,
    power_average_db,
)


class AnalysisTests(unittest.TestCase):
    def test_power_average_uses_linear_domain(self) -> None:
        self.assertAlmostEqual(float(power_average_db(np.array([0.0, 10.0]))), 7.4036269)

    def test_integrated_power(self) -> None:
        self.assertAlmostEqual(integrated_power_db(np.array([0.0, 0.0])), 3.01029996)

    def test_occupied_bandwidth(self) -> None:
        frequencies = np.arange(5, dtype=float) * 1e6
        values = np.array([-100.0, 0.0, 10.0, 0.0, -100.0])
        low, high, width = occupied_bandwidth(frequencies, values, 0.90)
        self.assertEqual((low, high, width), (1e6, 3e6, 2e6))

    def test_local_peaks(self) -> None:
        np.testing.assert_array_equal(
            local_peak_indices(np.array([0.0, 2.0, 1.0, 3.0, 0.0])),
            np.array([1, 3]),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.domain import SpectrumTrace
from esw_dfl.processing import (
    acpr,
    build_lod_pyramid,
    channel_power,
    harmonic_analysis,
    minmax_lod,
    noise_floor,
    occupied_bandwidth,
    peak_search,
    peak_search_values,
    snr,
    x_db_bandwidth,
)


def trace(values: np.ndarray, start: float = 100.0, step: float = 10.0) -> SpectrumTrace:
    return SpectrumTrace(
        trace_id="t", name="test", start_frequency_hz=start,
        stop_frequency_hz=start + step * (len(values) - 1), frequency_step_hz=step,
        power_values=np.asarray(values, dtype=np.float32), detector="Sample", rbw_hz=step,
    )


class ExtendedProcessingTests(unittest.TestCase):
    def test_channel_power_sums_in_linear_domain(self) -> None:
        item = trace(np.full(5, -30.0))
        result = channel_power(item, 95.0, 145.0)
        self.assertAlmostEqual(result.integrated_dbm, -30.0 + 10.0 * np.log10(5), places=6)
        self.assertEqual(result.peak_frequency_hz, 100.0)
        self.assertFalse(result.approximate)

    def test_occupied_bandwidth_and_nan_handling(self) -> None:
        item = trace(np.array([np.nan, -80, -20, -80, np.inf]))
        result = occupied_bandwidth(item, 0.99)
        self.assertLessEqual(result.lower_hz, 120.0)
        self.assertGreaterEqual(result.upper_hz, 120.0)
        self.assertTrue(np.isfinite(result.total_power_dbm))

    def test_acpr_reports_both_adjacent_channels(self) -> None:
        item = trace(np.array([-70, -60, -20, -20, -20, -60, -70]), start=70.0)
        result = acpr(item, 100.0, 20.0, 30.0, 20.0)
        self.assertEqual([entry.name for entry in result], ["Main", "Lower 1", "Upper 1"])
        self.assertGreater(result[1].aclr_db, 0.0)

    def test_peak_search_distance_and_limit(self) -> None:
        item = trace(np.array([-50, -20, -50, -25, -50, -10, -50]))
        peaks = peak_search(item, minimum_distance_hz=20.0, limit=2)
        self.assertEqual(len(peaks), 2)
        self.assertEqual(peaks[0][1], -10.0)

    def test_peak_search_uses_center_of_flat_maximum(self) -> None:
        frequencies = np.arange(7, dtype=np.float64) * 10.0
        values = np.array([-50.0, -20.0, 3.0, 3.0, 3.0, -10.0, -40.0])
        peaks = peak_search_values(frequencies, values, limit=1)
        self.assertEqual(peaks, [(30.0, 3.0, 3)])

    def test_x_db_bandwidth_interpolates_crossings(self) -> None:
        item = trace(np.array([-20, -10, 0, -10, -20]))
        result = x_db_bandwidth(item, 3.0)
        self.assertAlmostEqual(result.left_hz, 117.0)
        self.assertAlmostEqual(result.right_hz, 123.0)
        self.assertAlmostEqual(result.bandwidth_hz, 6.0)

    def test_noise_floor_excludes_peaks_and_estimates_density(self) -> None:
        item = trace(np.array([-100, -99, -98, -20, -101, -100]))
        result = noise_floor(item)
        self.assertLess(result.mean_dbm, -95.0)
        self.assertIsNotNone(result.density_dbm_hz)

    def test_snr_returns_positive_signal_to_noise_ratio(self) -> None:
        item = trace(np.array([-100, -100, -20, -20, -100, -100]))
        result = snr(item, (115.0, 135.0), (95.0, 105.0))
        self.assertGreater(result.snr_db, 50.0)

    def test_harmonic_table_and_thd(self) -> None:
        item = trace(np.array([-100, -10, -30, -40, -100, -100, -100]), start=0.0)
        harmonics, thd = harmonic_analysis(item, 10.0, 3, search_width_hz=1.0)
        self.assertEqual(len(harmonics), 3)
        self.assertAlmostEqual(harmonics[1].relative_dbc, -20.0)
        self.assertIsNotNone(thd)

    def test_minmax_lod_never_loses_a_narrow_peak(self) -> None:
        x = np.arange(1024)
        y = np.zeros(1024)
        y[511] = 100.0
        lod_x, lod_y = minmax_lod(x, y, 256)
        self.assertIn(511, lod_x)
        self.assertEqual(np.max(lod_y), 100.0)
        self.assertEqual(set(build_lod_pyramid(x, y)), {1, 4, 16, 64, 256})


if __name__ == "__main__":
    unittest.main()

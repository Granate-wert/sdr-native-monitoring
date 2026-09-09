"""Bounded presentation-only conversion of native Analyzer layers."""

from __future__ import annotations

import unittest

import numpy as np

from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_spectrum


def _frame(size: int, values: np.ndarray | None = None) -> LiveSpectrumFrame:
    return LiveSpectrumFrame(
        sequence=1, timestamp_ns=7, center_frequency_hz=101e6,
        sample_rate_hz=float(size), fft_size=size, hop_size=max(1, size // 2),
        frequencies_hz=np.arange(size, dtype=np.float64) + 100e6,
        values=np.full(size, -100.0, dtype=np.float32) if values is None else values,
        unit="dBm", source_id="source-a", config_generation=2,
    )


class AnalyzerLayerTests(unittest.TestCase):
    def test_waterfall_lod_is_bounded_regular_and_preserves_bucket_peaks(self) -> None:
        for size in (1024, 4096, 16_384, 262_144):
            with self.subTest(size=size):
                values = np.full(size, -100.0, dtype=np.float32)
                values[size // 2] = -3.0
                values[size // 2 + 1] = -8.0
                frame = _frame(size, values)
                line = waterfall_line_from_spectrum(frame)
                self.assertLessEqual(line.values.size, 2048)
                self.assertEqual(line.frequency_edges_hz.size, line.values.size + 1)
                self.assertTrue(np.allclose(np.diff(line.frequency_edges_hz), np.diff(line.frequency_edges_hz)[0]))
                self.assertEqual(line.frequency_edges_hz[0], 99_999_999.5)
                self.assertEqual(line.frequency_edges_hz[-1], 100_000_000.0 + size - 0.5)
                self.assertEqual(float(np.nanmax(line.values)), -3.0)
                if size <= 2048:
                    self.assertIs(line.values, frame.values)
                else:
                    self.assertFalse(line.values.flags.writeable)

    def test_nan_unknowns_are_never_bridged_by_a_peak(self) -> None:
        values = np.full(4096, -100.0, dtype=np.float32)
        values[10] = np.nan
        values[11] = -2.0
        values[12] = -5.0
        line = waterfall_line_from_spectrum(_frame(4096, values))
        self.assertTrue(np.isnan(line.values[5]))
        self.assertEqual(float(line.values[6]), -5.0)

    def test_all_nan_and_nondivisible_native_width_remain_explicitly_unknown(self) -> None:
        unknown = waterfall_line_from_spectrum(_frame(4096, np.full(4096, np.nan, dtype=np.float32)))
        self.assertTrue(np.all(np.isnan(unknown.values)))
        size = 2051
        values = np.full(size, -100.0, dtype=np.float32)
        values[-1] = -1.0
        line = waterfall_line_from_spectrum(_frame(size, values))
        self.assertEqual(line.values.size, 2048)
        self.assertEqual(float(line.values[-1]), -1.0)
        self.assertTrue(np.allclose(np.diff(line.frequency_edges_hz), np.diff(line.frequency_edges_hz)[0]))


if __name__ == "__main__":
    unittest.main()

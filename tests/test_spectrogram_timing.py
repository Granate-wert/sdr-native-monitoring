"""Tests for timestamp-based frame-period statistics."""

from __future__ import annotations

import unittest

import numpy as np

from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import SpectrogramIndex, compute_frame_period_statistics


class FramePeriodStatisticsTests(unittest.TestCase):
    def _index(self, timestamps: list[float]) -> SpectrogramIndex:
        info = SpectrogramInfo(
            "waterfall", "Waterfall", "RT", "Spectrum", "Spectrogram", "stream", 10, 1001, 1e9, 2e9
        )
        ts = np.asarray(timestamps, dtype=np.float64)
        return SpectrogramIndex(
            info,
            line_indices=np.arange(ts.size, dtype=np.int64),
            timestamps=ts,
            offsets=np.zeros(ts.size, dtype=np.int64),
            lengths=np.ones(ts.size, dtype=np.int32),
        )

    def test_empty_index_returns_zero_count(self) -> None:
        index = self._index([])
        stats = compute_frame_period_statistics(index)
        self.assertEqual(stats.count, 0)
        self.assertIsNone(stats.median_s)

    def test_single_timestamp_returns_count_one(self) -> None:
        index = self._index([1.0])
        stats = compute_frame_period_statistics(index)
        self.assertEqual(stats.count, 0)
        self.assertIsNone(stats.median_s)

    def test_uniform_period_matches_exact_median(self) -> None:
        index = self._index([0.0, 8.192e-05, 2 * 8.192e-05, 3 * 8.192e-05])
        stats = compute_frame_period_statistics(index)
        self.assertEqual(stats.count, 3)
        median_s = stats.median_s
        assert median_s is not None
        self.assertAlmostEqual(median_s, 8.192e-05, places=12)
        min_s = stats.min_s
        assert min_s is not None
        self.assertAlmostEqual(min_s, 8.192e-05, places=12)
        max_s = stats.max_s
        assert max_s is not None
        self.assertAlmostEqual(max_s, 8.192e-05, places=12)

    def test_negative_and_zero_deltas_are_ignored(self) -> None:
        index = self._index([0.0, 0.0, 8.192e-05, 8.192e-05, 4 * 8.192e-05])
        stats = compute_frame_period_statistics(index)
        self.assertEqual(stats.count, 2)
        min_s = stats.min_s
        assert min_s is not None
        self.assertAlmostEqual(min_s, 8.192e-05, places=12)
        max_s = stats.max_s
        assert max_s is not None
        self.assertAlmostEqual(max_s, 3 * 8.192e-05, places=12)

    def test_outliers_affect_p95_p99_not_median(self) -> None:
        base = 8.192e-05
        timestamps = [i * base for i in range(100)]
        timestamps[50] = timestamps[49] + 10 * base  # single outlier
        index = self._index(timestamps)
        stats = compute_frame_period_statistics(index)
        # The large step creates one positive outlier and one negative delta.
        self.assertEqual(stats.count, 98)
        median_s = stats.median_s
        assert median_s is not None
        self.assertAlmostEqual(median_s, base, places=12)
        max_s = stats.max_s
        assert max_s is not None
        self.assertGreater(max_s, base)
        p95_s = stats.p95_s
        assert p95_s is not None
        self.assertGreater(p95_s, base)
        p99_s = stats.p99_s
        assert p99_s is not None
        self.assertGreaterEqual(p99_s, p95_s)

    def test_non_finite_timestamps_are_ignored(self) -> None:
        index = self._index([0.0, 8.192e-05, float("nan"), 2 * 8.192e-05])
        stats = compute_frame_period_statistics(index)
        self.assertEqual(stats.count, 2)
        median_s = stats.median_s
        assert median_s is not None
        self.assertAlmostEqual(median_s, 8.192e-05, places=12)


if __name__ == "__main__":
    unittest.main()

"""RTBW physical-grid predicate keeps exact zero-rtol allclose semantics."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain import analyzer
from sdr_monitor.domain.live import LiveSnapshot, LiveSessionState, LiveSpectrumFrame


def snapshot(n=65536, center=2.4e9, rate=61.44e6):
    frame = LiveSpectrumFrame(sequence=1, timestamp_ns=1, center_frequency_hz=center,
        sample_rate_hz=rate, fft_size=n, hop_size=n,
        frequencies_hz=center + (np.arange(n) - n // 2) * (rate / n),
        values=np.full(n, -70, np.float32), unit="dBFS/bin")
    return LiveSnapshot(generation=0, sequence=1, state=LiveSessionState.RUNNING, spectrum=frame)


class RtbwGridTransferTests(unittest.TestCase):
    def test_finite_zero_rtol_path_reuses_expected_grid_scratch_not_generic_allclose(self):
        source = snapshot()
        before = source.spectrum.frequencies_hz.copy()
        with patch.object(analyzer.np, "allclose", side_effect=AssertionError("generic allclose scratch")):
            result = analyzer.bundle_from_live(source)
        self.assertIs(result.spectrum, source.spectrum)
        np.testing.assert_array_equal(before, source.spectrum.frequencies_hz)

    def test_exact_boundary_decisions_match_original_allclose_without_mutation(self):
        for n, center, rate in ((3, 0., 1.), (4096, 2.4e9, 61.44e6),
                                (65536, 2.4e9, 61.44e6), (17, -1e12, 1e9)):
            source = snapshot(n, center, rate)
            expected = source.spectrum.frequencies_hz.copy()
            tolerance = max((rate / n) * 1e-7, abs(float(np.spacing(center))) * 8)
            middle = n // 2
            boundary = expected[middle] + tolerance
            for value in (expected[middle], np.nextafter(boundary, -np.inf),
                          boundary, np.nextafter(boundary, np.inf), expected[middle] + 2 * tolerance):
                storage = np.repeat(expected, 2)
                grid = storage[::2]  # valid ascending but strided input
                grid[middle] = value
                accepted = np.allclose(grid, expected, rtol=0., atol=tolerance)
                changed = replace(source, spectrum=replace(source.spectrum, frequencies_hz=grid))
                before = storage.copy()
                if accepted:
                    self.assertIs(analyzer.bundle_from_live(changed).spectrum, changed.spectrum)
                else:
                    with self.assertRaisesRegex(ValueError, "match center"):
                        analyzer.bundle_from_live(changed)
                np.testing.assert_array_equal(storage, before)

    def test_nonfinite_frequency_error_still_precedes_invalid_values(self):
        source = snapshot(16)
        for value in (np.nan, np.inf, -np.inf):
            grid, values = source.spectrum.frequencies_hz.copy(), source.spectrum.values.copy()
            grid[5], values[5] = value, np.inf
            changed = replace(source, spectrum=replace(source.spectrum, frequencies_hz=grid, values=values))
            with self.assertRaisesRegex(ValueError, "finite and strictly ascending"):
                analyzer.bundle_from_live(changed)

    def test_extreme_spacing_retains_generic_special_value_fallback(self):
        with np.errstate(over="ignore", invalid="ignore"):
            source = snapshot(1, np.finfo(float).max, 1.)
            original = np.allclose
            with patch.object(analyzer.np, "allclose", wraps=original) as compare:
                self.assertIs(analyzer.bundle_from_live(source).spectrum, source.spectrum)
                self.assertEqual(compare.call_count, 1)
            source = snapshot(3, 0., 1e308)
            grid = np.array([1e308, 1.3e308, 1.6e308])
            changed = replace(source, spectrum=replace(source.spectrum,
                center_frequency_hz=np.finfo(float).max, frequencies_hz=grid))
            with self.assertRaisesRegex(ValueError, "match center"):
                analyzer.bundle_from_live(changed)


if __name__ == "__main__":
    unittest.main()

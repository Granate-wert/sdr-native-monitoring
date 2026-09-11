"""Decimation cannot draw measured continuity across unknown input bins."""

import unittest

import numpy as np

from sdr_monitor.ui.v2.spectrum import adapt_spectrum_frame, peak_preserving_envelope
from tests.ui_v2.test_spectrum_scene import SyntheticSpectrumFrame


class EnvelopeGapTests(unittest.TestCase):
    def test_decimated_gaps_at_bucket_edges_and_inside_preserve_adjacent_peak(self):
        for width in (1, 8, 32, 64):
            for gap in (0, 1, 7, 8, 31, 32, 63, 64, 255, 510, 511):
                with self.subTest(width=width, gap=gap):
                    values = np.full(512, -100.0)
                    values[gap] = np.nan
                    peak = gap + 1 if gap < 511 else gap - 1
                    values[peak] = -20
                    frame = SyntheticSpectrumFrame(np.arange(512, dtype=float), values)
                    trace = peak_preserving_envelope(adapt_spectrum_frame(frame), width)
                    self.assertIn(-20, trace.values)
                    self.assertLessEqual(trace.display_point_count, 9 * width)
                    self.assert_no_unknown_connection(trace, values)

    def test_many_subpixel_gaps_remain_bounded_and_do_not_bridge(self):
        values = np.full(4096, -100.0)
        values[1::2] = np.nan
        values[2048] = -10
        frame = SyntheticSpectrumFrame(np.arange(4096, dtype=float), values)
        trace = peak_preserving_envelope(adapt_spectrum_frame(frame), 16)
        self.assertLessEqual(trace.display_point_count, 9 * 16)
        self.assertIn(-10, trace.values)
        self.assert_no_unknown_connection(trace, values)

    def assert_no_unknown_connection(self, trace, original):
        previous = None
        for frequency, value in zip(trace.frequencies_hz, trace.values, strict=True):
            if not np.isfinite(value):
                previous = None
                continue
            index = int(frequency)
            self.assertEqual(value, original[index])
            if previous is not None:
                self.assertTrue(np.all(np.isfinite(original[previous:index + 1])))
            previous = index

"""Physical RTBW grid must substantiate declared frequency and resolution."""

from dataclasses import replace
import unittest
import numpy as np

from sdr_monitor.domain.live import LiveSpectrumFrame, LiveSnapshot, LiveSessionState
from sdr_monitor.domain.analyzer import bundle_from_live


class RtbwGeometryTests(unittest.TestCase):
    def test_invalid_size_spacing_and_center_are_rejected(self):
        frame = LiveSpectrumFrame(
            sequence=1, timestamp_ns=1, center_frequency_hz=2.4e9,
            sample_rate_hz=61.44e6, fft_size=4096, hop_size=2048,
            frequencies_hz=2.4e9 + (np.arange(4096) - 2048) * 15000.,
            values=np.zeros(4096), unit="dBFS/bin",
        )
        snapshot = LiveSnapshot(generation=0, sequence=1,
                                state=LiveSessionState.RUNNING, spectrum=frame)
        self.assertIs(bundle_from_live(snapshot).spectrum, frame)
        irregular = frame.frequencies_hz.copy()
        irregular[2000] += 500
        cases = (
            replace(frame, fft_size=8192),
            replace(frame, sample_rate_hz=30.72e6),
            replace(frame, center_frequency_hz=2.400001e9),
            replace(frame, frequencies_hz=irregular),
        )
        for malformed in cases:
            with self.subTest(fft=malformed.fft_size, center=malformed.center_frequency_hz):
                with self.assertRaisesRegex(ValueError, "frequency grid"):
                    bundle_from_live(replace(snapshot, spectrum=malformed))

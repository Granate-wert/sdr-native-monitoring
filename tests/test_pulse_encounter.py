"""Finite-window temporal probability, without guessing coverage from Fs/FPS."""

import unittest

from sdr_monitor.domain.identity import TimestampQuality
from sdr_monitor.domain.pulse_encounter import pulse_encounter_estimate


def estimate(windows, *, duration=10, overlap=0, quality=TimestampQuality.SYNTHETIC):
    return pulse_encounter_estimate(windows, pulse_start_horizon_ns=(0, 100),
        pulse_duration_ns=duration, minimum_overlap_ns=overlap, capture_timing_quality=quality,
        coverage_complete=True)


class PulseEncounterTests(unittest.TestCase):
    def test_blind_time_pulse_width_and_overlap_are_separate(self):
        self.assertEqual(estimate(((20, 40),)).temporal_fraction, 0.3)
        self.assertEqual(estimate(((20, 40),), overlap=5).temporal_fraction, 0.2)
        self.assertEqual(estimate(((20, 40),), overlap=11).temporal_fraction, 0)
        self.assertEqual(estimate((), duration=10).temporal_fraction, 0)
        self.assertEqual(estimate(((0, 100),)).temporal_fraction, 1)

    def test_union_not_sum_and_adjacent_capture_windows_are_merged(self):
        self.assertEqual(estimate(((20, 40), (35, 60))).temporal_fraction, 0.5)
        self.assertEqual(estimate(((40, 60), (20, 40)), overlap=10).temporal_fraction, 0.3)
        self.assertEqual(estimate(((10, 20), (25, 35))).temporal_fraction, 0.35)

    def test_horizon_is_for_pulse_start_and_extended_windows_are_clipped_after_shift(self):
        self.assertEqual(estimate(((105, 120),)).temporal_fraction, 0.05)
        self.assertEqual(estimate(((200, 300),)).temporal_fraction, 0)

    def test_no_rf_estimate_from_host_estimated_or_unknown_bounds(self):
        for quality in (TimestampQuality.UNKNOWN, TimestampQuality.ESTIMATED, TimestampQuality.REPLAY):
            self.assertIsNone(estimate(((0, 100),), quality=quality))
        hardware = estimate(((20, 40),), quality=TimestampQuality.HARDWARE)
        self.assertEqual(hardware.capture_timing_quality, TimestampQuality.HARDWARE)
        self.assertIn("not amplitude", hardware.scope)
        self.assertIsNone(pulse_encounter_estimate(((0, 100),), pulse_start_horizon_ns=(0, 100),
            pulse_duration_ns=10, capture_timing_quality=TimestampQuality.HARDWARE))

    def test_bad_contracts_rejected_not_truncated_or_guessed(self):
        for windows in (((5, 5),), ((-1, 5),), ((False, 5),), ((0, 1),) * 4097):
            with self.subTest(windows=windows[:2]), self.assertRaises(ValueError):
                estimate(windows)
        for duration, overlap in ((0, 0), (-1, 0), (True, 0), (1, -1), (1, False)):
            with self.assertRaises(ValueError):
                estimate((), duration=duration, overlap=overlap)


if __name__ == "__main__":
    unittest.main()

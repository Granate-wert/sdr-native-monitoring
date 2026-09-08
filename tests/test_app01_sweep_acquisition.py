"""Acquisition metadata retains unknown timing semantics and rejects false identity."""
from dataclasses import FrozenInstanceError, replace
import unittest

from sdr_monitor.domain.sweep_acquisition import SweepSegmentAcquisition, validate_acquisition


class SweepAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.record = SweepSegmentAcquisition(0, 7, 12, 256, 123, 61_440_000., 4096, 0x80000001)

    def test_immutable_and_unknown_distinct_from_empty(self):
        self.assertIsNone(validate_acquisition(None, ((0, 7),)))
        self.assertEqual(validate_acquisition((), ((0, 7),)), ())
        self.assertEqual(validate_acquisition((self.record,), ((0, 7),)), (self.record,))
        self.assertEqual(self.record.quality_flags, 0x80000001)
        with self.assertRaises(FrozenInstanceError):
            self.record.timestamp_ns = 0

    def test_bad_scalar_and_segment_identity_are_rejected(self):
        for changes in ({"timestamp_ns": -1}, {"timestamp_ns": True},
                        {"sample_rate_hz": float("nan")}, {"fft_size": 0},
                        {"quality_flags": 1 << 32}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.record, **changes)
        for records, expected in (((self.record,), ((0, 8),)),
                                  ((self.record,), ((1, 7),)),
                                  ((self.record, self.record), ((0, 7),))):
            with self.assertRaises(ValueError):
                validate_acquisition(records, expected)

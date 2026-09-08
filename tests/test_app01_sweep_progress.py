"""Negative admission checks for nonterminal native Sweep data."""
from dataclasses import replace
import unittest

import numpy as np

from sdr_monitor.domain.sweep_progress import SweepProgressFrame


def array(values, dtype):
    result = np.array(values, dtype=dtype)
    result.setflags(write=False)
    return result


def frame():
    return SweepProgressFrame(
        "rx-source", 1, 3, 1, "dBFS/bin",
        array([100., 101.], "float64"), array([-80., np.nan], "float32"),
        array([1, 4096], "uint32"), array([0, -1], "int32"), ((0, 11),), (1,),
    )


class SweepProgressTests(unittest.TestCase):
    def test_valid_frame_keeps_array_ownership_and_unknown_quality_bits(self):
        valid = frame()
        flags = array([0x80000001, 4096], "uint32")
        actual = replace(valid, quality_flags=flags)
        self.assertIs(actual.quality_flags, flags)
        self.assertIs(actual.values_db, valid.values_db)

    def test_invalid_metadata_and_coverage_fail_closed(self):
        valid = frame()
        changes = (
            {"revision": 2}, {"epoch": True}, {"unit": ""},
            {"acquired_segment_generations": ((0, 0),)},
            {"pending_segment_indices": (0,)}, {"pending_segment_indices": ()},
            {"values_db": np.array([-80., np.nan], dtype="float32")},
            {"frequencies_hz": array([101., 100.], "float64")},
            {"quality_flags": array([1, 0], "uint32")},
            {"quality_flags": array([4097, 4096], "uint32")},
            {"source_segment_indices": array([1, -1], "int32")},
            {"source_segment_indices": array([0, 0], "int32")},
            {"values_db": array([-80., np.inf], "float32")},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(valid, **change)

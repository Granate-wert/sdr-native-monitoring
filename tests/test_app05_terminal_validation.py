"""Terminal validation keeps exact power/grid rules and owned immutable copies."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain import sweep_lines as module
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState, SweepQualitySchema


class TerminalValidationTests(unittest.TestCase):
    def test_exact_full_array_oracle_at_seams_and_strides(self):
        for count in (2, 65535, 65536, 65537, 131073):
            for position in (0, count // 2, count - 1):
                for invalid in (None, np.nan, np.inf, -np.inf, 0.0):
                    frequency = np.arange(count, dtype=np.float64)
                    values = np.full(count, -np.inf, dtype=np.float32)
                    if invalid is not None:
                        frequency[position] = invalid
                        values[position] = invalid
                    if position % 2:
                        frequency = np.repeat(frequency, 2)[::2]
                        values = np.repeat(values, 2)[::2]
                    with self.subTest(count=count, position=position, invalid=invalid):
                        expected = bool(np.all(np.isfinite(frequency)) and np.all(np.diff(frequency) > 0))
                        self.assertEqual(module._valid_frequency_grid(frequency), expected)
                        self.assertEqual(module._has_unknown_power(values),
                                         bool(np.any(np.isnan(values) | np.isposinf(values))))
        for position in (65536, 131072):
            frequency = np.arange(131073, dtype=np.float64)
            frequency[position] = frequency[position - 1]
            self.assertFalse(module._valid_frequency_grid(frequency))

    def test_constructor_retains_own_readonly_copies_and_quality_conversion(self):
        count = 65537
        frequency = np.arange(count, dtype=np.float64)
        values = np.full(count, -np.inf, dtype=np.float32)
        quality = np.zeros(count, dtype=np.uint32)
        sources = np.zeros(count, dtype=np.int32)
        frame = SweepLineFrame(1, 1, 0, "test", SweepLineState.COMPLETE,
                               frequency, values, quality, sources, (), ((0, 1),), (), "dBFS/bin",
                               quality_schema=SweepQualitySchema.NATIVE_V5)
        for supplied, retained in zip((frequency, values, quality, sources),
                                     (frame.frequencies_hz, frame.values_db, frame.quality_flags,
                                      frame.source_segment_indices)):
            self.assertFalse(np.shares_memory(supplied, retained))
            self.assertFalse(retained.flags.writeable)
        self.assertEqual(frame.quality_flags.dtype, np.dtype("uint16"))
        values[:] = 12
        self.assertTrue(np.all(np.isneginf(frame.values_db)))
        values[:] = -80
        values[-1] = np.nan
        with self.assertRaisesRegex(ValueError, "must not hide gaps"):
            replace(frame, values_db=values)
        gap = replace(frame, state=SweepLineState.GAP, values_db=values)
        self.assertTrue(np.isnan(gap.values_db[-1]))
        with self.assertRaisesRegex(ValueError, "explicit gap evidence"):
            replace(frame, state=SweepLineState.GAP)
        quality[-1] = 4096
        with self.assertRaisesRegex(ValueError, "missing-segment flags"):
            replace(frame, quality_flags=quality)

    def test_scratch_is_chunked_and_extreme_finite_intervals_still_valid(self):
        frequency = np.arange(200003, dtype=np.float64)
        original = np.isfinite
        sizes = []
        def checked(value):
            sizes.append(value.size)
            return original(value)
        with patch.object(module.np, "isfinite", side_effect=checked):
            self.assertTrue(module._valid_frequency_grid(frequency))
        self.assertEqual(sizes, [65536, 65536, 65536, 3395])
        maximum = np.finfo(np.float64).max
        self.assertTrue(module._valid_frequency_grid(np.array([-maximum, maximum])))


if __name__ == "__main__":
    unittest.main()

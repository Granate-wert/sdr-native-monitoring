"""Terminal validation keeps exact power/grid rules and owned immutable copies."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain import sweep_lines as module
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState, SweepQualitySchema


class TerminalValidationTests(unittest.TestCase):
    def test_quality_narrowing_is_final_owned_buffer_not_recopied(self):
        # Native uint32, already-domain uint16, signed and strided inputs all
        # retain exactly the same immutable, mutation-isolated domain contract.
        for dtype in (np.uint8, np.uint16, np.uint32, np.uint64, np.int16, np.int64):
            with self.subTest(dtype=dtype):
                supplied = np.full(18, 3, dtype=dtype)[::2]
                copied_dtypes = []
                original = np.array

                def owned_copy(value, *args, **kwargs):
                    copied_dtypes.append(value.dtype)
                    return original(value, *args, **kwargs)

                with patch.object(module.np, "array", side_effect=owned_copy):
                    frame = SweepLineFrame(1, 2, 3, "source", SweepLineState.COMPLETE,
                        np.arange(9, dtype=np.float64), np.full(9, -80, dtype=np.float32),
                        supplied, np.zeros(9, dtype=np.int32), (), ((0, 1),), (), "dBFS/bin",
                        quality_schema=SweepQualitySchema.NATIVE_V5)
                self.assertNotIn(np.dtype("uint16"), copied_dtypes)
                self.assertEqual(frame.quality_flags.dtype, np.dtype("uint16"))
                self.assertTrue(frame.quality_flags.flags.owndata)
                self.assertFalse(frame.quality_flags.flags.writeable)
                self.assertFalse(np.shares_memory(supplied, frame.quality_flags))
                supplied[:] = 0
                np.testing.assert_array_equal(frame.quality_flags, np.full(9, 3, dtype=np.uint16))

    def test_quality_range_precedes_shape_and_frequency_errors(self):
        for invalid in (np.array([-1], dtype=np.int64),
                        np.array([65536], dtype=np.uint32),
                        np.array([2**63], dtype=np.uint64),
                        np.array([0.5], dtype=np.float64)):
            with self.subTest(dtype=invalid.dtype, value=invalid[0]):
                with self.assertRaisesRegex(ValueError, "unsigned 16-bit integer masks"):
                    SweepLineFrame(1, 1, 0, "source", SweepLineState.COMPLETE,
                        np.array([np.nan, 0.0]), np.zeros(2, dtype=np.float32),
                        invalid, np.zeros(2, dtype=np.int32), (), (), (), "dBFS/bin")

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

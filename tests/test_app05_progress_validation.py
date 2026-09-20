"""Exact former full-array oracle for bounded progressive Sweep validation."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain import sweep_progress as module
from sdr_monitor.domain.sweep_progress import SweepProgressFrame


def oracle(arrays, indices):
    with np.errstate(over="ignore", invalid="ignore"):
        if not np.all(np.isfinite(arrays[0])) or np.any(np.diff(arrays[0]) <= 0):
            return "invalid progress frequency grid"
    if np.any(np.isposinf(arrays[1])):
        return "progress values must be finite, zero power (-inf dB), or explicit NaN gaps"
    missing = np.isnan(arrays[1])
    flagged_missing = (arrays[2] & np.uint32(1 << 12)) != 0
    if not np.array_equal(missing, flagged_missing):
        return "progress NaN coverage must match native MissingSegment flags"
    if np.any(arrays[3][missing] != -1) or not np.all(np.isin(arrays[3][~missing], indices)):
        return "progress bin provenance must refer only to acquired segments"
    return None


def construct(arrays, indices):
    for array in arrays:
        array.setflags(write=False)
    return SweepProgressFrame("oracle", 1, 1, len(indices), "dBFS/bin", *arrays,
                              tuple((index, 1) for index in indices), (max(indices) + 1,))


def arrays_for(count, indices=(0,)):
    frequencies = np.arange(count, dtype=np.float64)
    values = np.full(count, -80, dtype=np.float32)
    quality = np.zeros(count, dtype=np.uint32)
    sources = np.resize(np.asarray(indices, dtype=np.int32), count)
    missing = np.arange(count) % 3 == 0
    values[missing] = np.nan
    quality[missing] = 1 << 12
    sources[missing] = -1
    values[1::7] = -np.inf
    # Explicit zero-power samples overwrite holes, so clear their missing flags.
    quality[1::7] = 0
    sources[1::7] = indices[0]
    return [frequencies, values, quality, sources]


class ProgressValidationTests(unittest.TestCase):
    def assert_oracle(self, arrays, indices):
        before = [a.copy() for a in arrays]
        expected = oracle(arrays, indices)
        if expected is None:
            frame = construct(arrays, indices)
            for supplied, retained in zip(arrays, (frame.frequencies_hz, frame.values_db,
                                                  frame.quality_flags, frame.source_segment_indices)):
                self.assertIs(supplied, retained)
        else:
            with self.assertRaises(ValueError) as error:
                construct(arrays, indices)
            self.assertEqual(str(error.exception), expected)
        for value, original in zip(arrays, before):
            np.testing.assert_array_equal(value, original)

    def test_chunk_seams_endpoints_and_error_precedence(self):
        for position in (0, 1, 65535, 65536, 65537, 131071, 131072):
            for defect in ("none", "grid", "positive_inf", "missing_flag", "missing_owner", "owner"):
                with self.subTest(position=position, defect=defect):
                    arrays = arrays_for(131073, (0, 3, 7))
                    if defect == "grid":
                        arrays[0][position] = np.nan
                    elif defect == "positive_inf":
                        arrays[1][position] = np.inf
                    elif defect == "missing_flag":
                        arrays[2][position] ^= 1 << 12
                    elif defect == "missing_owner":
                        arrays[1][position] = np.nan
                        arrays[2][position] = 1 << 12
                        arrays[3][position] = 0
                    elif defect == "owner":
                        arrays[1][position] = -np.inf
                        arrays[2][position] = 0
                        arrays[3][position] = 9
                    self.assert_oracle(arrays, [0, 3, 7])
        arrays = arrays_for(131073)
        arrays[3][0] = 42
        arrays[2][10] ^= 1 << 12
        arrays[1][100] = np.inf
        arrays[0][-1] = np.nan
        self.assert_oracle(arrays, [0])
        for position in (65536, 131072):
            arrays = arrays_for(131073)
            arrays[0][position] = arrays[0][position - 1]
            self.assert_oracle(arrays, [0])

    def test_randomized_strided_and_multisegment_oracle(self):
        rng = np.random.default_rng(70520)
        with patch.object(module, "_VALIDATION_BATCH", 7):
            for number in range(200):
                indices = [0] if number % 2 else [0, 3, 7]
                arrays = arrays_for(int(rng.integers(2, 200)), indices)
                index = int(rng.integers(0, arrays[0].size))
                if number % 5 == 0:
                    arrays[0][index] = np.inf
                elif number % 5 == 1:
                    arrays[2][index] ^= 1 << 12
                elif number % 5 == 2:
                    arrays[3][index] = 100
                if number % 3 == 0:
                    arrays = [np.repeat(a, 2)[::2] for a in arrays]
                with self.subTest(number=number):
                    self.assert_oracle(arrays, indices)

    def test_float_dtypes_extrema_and_large_segment_ids(self):
        for dtype in (np.float16, np.float32, np.float64):
            arrays = arrays_for(4)
            maximum = np.finfo(dtype).max
            arrays[0] = np.array([-maximum, -1, 1, maximum], dtype=dtype)
            arrays[1] = arrays[1].astype(dtype)
            self.assert_oracle(arrays, [0])
        for indices in ([0, 2**80], [2**80], [0, 2**31 - 1]):
            self.assert_oracle(arrays_for(64), indices)

    def test_array_scratch_operations_never_receive_full_grid(self):
        arrays = arrays_for(200003, (0, 3, 7))
        sizes = []
        original = np.isin
        def checked(values, indices, *args, **kwargs):
            sizes.append(values.size)
            return original(values, indices, *args, **kwargs)
        with patch.object(module.np, "isin", side_effect=checked):
            construct(arrays, [0, 3, 7])
        self.assertEqual(len(sizes), 4)
        self.assertLessEqual(max(sizes), module._VALIDATION_BATCH)

    def test_existing_geometry_and_mutability_guards_remain(self):
        frame = construct(arrays_for(64), [0])
        with self.assertRaisesRegex(ValueError, "immutable"):
            replace(frame, values_db=frame.values_db.copy())
        bad = frame.quality_flags.astype(np.uint16)
        bad.setflags(write=False)
        with self.assertRaisesRegex(ValueError, "array types"):
            replace(frame, quality_flags=bad)


if __name__ == "__main__":
    unittest.main()

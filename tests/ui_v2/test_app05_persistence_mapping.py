"""Exact transfer oracle and bounded mapping scratch, including strided buffers."""
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum import persistence_contracts as contracts


def reference(values, mode, logarithmic, maximum):
    out = np.zeros(values.shape, np.float32)
    np.copyto(out, values, where=np.isfinite(values))
    if mode is contracts.DensityValueMode.COUNT and maximum > 0:
        out /= maximum
    if logarithmic:
        np.multiply(out, contracts.LOG_DENSITY_GAIN, out=out)
        np.log1p(out, out=out)
        out /= np.log1p(contracts.LOG_DENSITY_GAIN)
    np.clip(out, 0, 1, out=out)
    return out


class PersistenceMappingTests(unittest.TestCase):
    def test_contiguous_integer_counts_and_wide_strided_rows_match_exactly(self):
        for dtype in (np.uint16, np.uint32, np.uint64, np.float32, np.float64):
            source = (np.arange(2 * 65539) % 37).astype(dtype)
            for values in (source, source[::-1], source.reshape(2, -1)):
                for logarithmic in (False, True):
                    target = np.empty(values.shape, np.float32)
                    expected = reference(values, contracts.DensityValueMode.COUNT, logarithmic, 36)
                    contracts._map_density_values_into(values, value_mode=contracts.DensityValueMode.COUNT,
                        logarithmic=logarithmic, count_maximum=36, out=target)
                    np.testing.assert_array_equal(target.view(np.uint32), expected.view(np.uint32))

    def test_exact_bits_on_dense_sparse_missing_and_chunk_seams(self):
        rng = np.random.default_rng(193)
        for dtype in (np.float32, np.float64):
            for sparse in (False, True):
                values = rng.random((3, 65539)).astype(dtype)
                if sparse:
                    values[0] = 0
                    values[2] = -0.0
                values[1, [0, 65534, 65535, 65536, -1]] = [np.nan, np.inf, -np.inf, 0, 1]
                values.setflags(write=False)
                for logarithmic in (False, True):
                    for mode in contracts.DensityValueMode:
                        with self.subTest(dtype=dtype, sparse=sparse, logarithmic=logarithmic, mode=mode):
                            expected = reference(values, mode, logarithmic, 7)
                            for source in (values, values[:, ::-1], values.T):
                                expected = reference(source, mode, logarithmic, 7)
                                backing = np.full((source.shape[0], source.shape[1] * 2), -999, np.float32)
                                target = backing[:, ::2]
                                contracts._map_density_values_into(source, value_mode=mode,
                                    logarithmic=logarithmic, count_maximum=7, out=target)
                                np.testing.assert_array_equal(target.view(np.uint32), expected.view(np.uint32))
                                np.testing.assert_array_equal(backing[:, 1::2], -999)

    def test_probability_finite_masks_never_cover_the_whole_large_matrix(self):
        values = np.ones((64, 65536), np.float32)
        target = np.empty_like(values)
        finite = np.isfinite
        sizes = []

        def bounded(array):
            sizes.append(array.size)
            self.assertLessEqual(array.size, 65536)
            return finite(array)

        with patch.object(contracts.np, "isfinite", side_effect=bounded):
            contracts._map_density_values_into(values, value_mode=contracts.DensityValueMode.PROBABILITY,
                logarithmic=True, count_maximum=1, out=target)
        self.assertEqual(sum(sizes), values.size)
        np.testing.assert_array_equal(target, 1)

    def test_zero_chunks_skip_log_without_changing_signed_zero_or_nonzero(self):
        values = np.zeros((4, 65536), np.float32)
        values[1] = -0.0
        values[2, -1] = .25
        target = np.empty_like(values)
        expected = reference(values, contracts.DensityValueMode.PROBABILITY, True, 1)
        log = np.log1p
        array_calls = []

        def observed(value, *args, **kwargs):
            if isinstance(value, np.ndarray):
                array_calls.append(value.size)
            return log(value, *args, **kwargs)

        with patch.object(contracts.np, "log1p", side_effect=observed):
            contracts._map_density_values_into(values, value_mode=contracts.DensityValueMode.PROBABILITY,
                logarithmic=True, count_maximum=1, out=target)
        self.assertEqual(array_calls, [65536])
        np.testing.assert_array_equal(target.view(np.uint32), expected.view(np.uint32))

    def test_invalid_output_still_fails_before_mapping(self):
        for output in (np.zeros((2, 3), np.float64), np.zeros((2, 2), np.float32)):
            with self.assertRaisesRegex(ValueError, "shape or dtype"):
                contracts._map_density_values_into(np.ones((2, 3)), value_mode=contracts.DensityValueMode.COUNT,
                    logarithmic=True, count_maximum=0, out=output)


if __name__ == "__main__":
    unittest.main()

"""Bounded full validation keeps rejection order, values and physical geometry."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.ui.v2.spectrum import persistence_contracts as contracts
from sdr_monitor.ui.v2.state.analyzer_layers import persistence_density_from_native


def frame(values, mode=contracts.DensityValueMode.PROBABILITY, **changes):
    fields = dict(density=values, frequency_edges_hz=np.arange(values.shape[1] + 1, dtype=float),
                  level_edges=np.arange(values.shape[0] + 1, dtype=float), value_mode=mode, level_unit="dBm")
    fields.update(changes)
    return contracts.PersistenceDensityFrame(**fields)


def raw(values):
    return LivePersistenceFrame(update_sequence=3, timestamp_ns=7, source_frame_sequence=5,
        power_min_db=-120, power_max_db=0, power_bins=values.shape[0], frequency_bins=values.shape[1],
        processed_frames=4, exponential_decay=False, frequencies_hz=np.arange(values.shape[1], dtype=float),
        density=values, probability_scale=.25, count_scale=1, unit="dBFS/bin")


def old_error(values, mode):
    if np.any(np.isfinite(values) & (values < 0)):
        return "persistence density must not contain negative values"
    if mode is contracts.DensityValueMode.PROBABILITY and np.any(np.isfinite(values) & (values > 1)):
        return "probability density must not exceed one"
    return None


class PersistenceValidationTests(unittest.TestCase):
    def bounded_finite(self):
        original = np.isfinite

        def finite(value):
            if isinstance(value, np.ndarray) and value.dtype == np.float32:
                self.assertLessEqual(value.size, 65536)
            return original(value)
        return patch.object(contracts.np, "isfinite", side_effect=finite)

    def test_constructor_density_masks_are_bounded(self):
        values = np.zeros((64, 65536), np.float32)
        with self.bounded_finite():
            result = frame(values)
        self.assertIs(result.density, values)

    def test_native_conversion_density_masks_are_bounded_and_result_owned(self):
        source = raw(np.full((64, 65536), 2, np.float32))
        with self.bounded_finite():
            result = persistence_density_from_native(source)
        self.assertIsNotNone(result)
        self.assertFalse(np.shares_memory(result.density, source.density))
        self.assertFalse(result.density.flags.writeable)
        np.testing.assert_array_equal(result.density, .5)
        np.testing.assert_array_equal(source.density, 2)
        np.testing.assert_array_equal(result.frequency_edges_hz, np.arange(65537) - .5)
        self.assertEqual(result.level_unit, source.unit)

    def test_oracle_covers_all_chunks_dtypes_missing_and_strides(self):
        for dtype in (np.float32, np.float64, np.complex64, np.int16, np.uint64):
            source = np.zeros((3, 65539), dtype)
            for example in ("valid", "over", "negative-after-over", "missing"):
                values = source.copy()
                if example in ("over", "negative-after-over"):
                    values[0, 0] = 2
                if example == "negative-after-over" and dtype != np.uint64:
                    values[-1, -1] = -1
                if example == "missing" and np.issubdtype(dtype, np.inexact):
                    values[1, 65534:65537] = [np.nan, np.inf, -np.inf]
                for density in (values, values.T, values[:, ::-1], np.asfortranarray(values)):
                    for mode in contracts.DensityValueMode:
                        expected = old_error(density, mode)
                        with self.subTest(dtype=dtype, example=example, shape=density.shape, mode=mode):
                            if expected:
                                with self.assertRaisesRegex(ValueError, expected):
                                    frame(density, mode)
                            else:
                                self.assertIs(frame(density, mode).density, density)

    def test_negative_after_upper_bound_keeps_global_error_priority(self):
        values = np.zeros((4, 65536), np.float32)
        values[0, 0], values[-1, -1] = 2, -1
        with self.assertRaisesRegex(ValueError, "negative"):
            frame(values)

    def test_geometry_errors_still_precede_public_density_errors(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            frame(np.full((2, 2), -1), frequency_edges_hz=np.array([0, 0, 2]))

    def test_adapter_range_failure_still_precedes_geometry_and_returns_none(self):
        source = raw(np.full((2, 3), -1, np.float32))
        source = replace(source, frequencies_hz=np.array([0, 1, 3]))
        self.assertIsNone(persistence_density_from_native(source))
        valid = replace(source, density=np.ones((2, 3), np.float32))
        with self.assertRaisesRegex(ValueError, "regular physical grid"):
            persistence_density_from_native(valid)

    def test_nonfinite_and_zero_scale_keep_existing_semantics(self):
        source = raw(np.array([[0, 1, np.nan], [np.inf, -np.inf, 4]], np.float32))
        for mode in contracts.DensityValueMode:
            result = persistence_density_from_native(source, value_mode=mode)
            scale = source.probability_scale if mode is contracts.DensityValueMode.PROBABILITY else source.count_scale
            np.testing.assert_array_equal(result.density, np.multiply(source.density, scale, dtype=np.float32))
        self.assertIsNone(persistence_density_from_native(replace(source, probability_scale=np.nan)))
        with np.errstate(invalid="ignore"):
            result = persistence_density_from_native(replace(source, probability_scale=0))
        np.testing.assert_array_equal(result.density, [[0, 0, np.nan], [np.nan, np.nan, 0]])


if __name__ == "__main__":
    unittest.main()

"""Exact-content frequency spacing reuse for ordered density validation."""

from __future__ import annotations

import unittest
import warnings
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.grid_baseline import MeasurementGridCache
from sdr_monitor.ui.v2.state import analyzer_layers
from sdr_monitor.ui.v2.state.analyzer_layer_cache import AnalyzerLayerCache


def persistence_frame(density: np.ndarray, frequencies: np.ndarray) -> LivePersistenceFrame:
    return LivePersistenceFrame(
        update_sequence=1,
        timestamp_ns=1,
        source_frame_sequence=1,
        power_min_db=-100.0,
        power_max_db=0.0,
        power_bins=density.shape[0],
        frequency_bins=density.shape[1],
        processed_frames=1,
        exponential_decay=False,
        frequencies_hz=frequencies,
        density=density,
        probability_scale=1.0,
        count_scale=1.0,
        unit="dBFS/bin",
    )


class DensityGridReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.budget = PresentationAllocationBudget(8 * 1024 * 1024)
        self.grid = np.arange(16, dtype=np.float64)
        self.cache = MeasurementGridCache(self.budget)
        self.cache.prepare(self.grid)

    def test_fp_clean_spacing_is_distinct_from_ordinary_spacing(self) -> None:
        calls = 0

        def validate(values: np.ndarray) -> float:
            nonlocal calls
            calls += 1
            return analyzer_layers._regular_spacing(values)

        # An ordinary Waterfall hit is not a density FP-clean certificate.
        self.assertEqual(self.cache.regular_spacing(self.grid, validate), 1.0)
        self.assertEqual(calls, 1)
        self.assertEqual(self.cache.regular_spacing(self.grid, validate, require_fp_clean=True), 1.0)
        self.assertEqual(calls, 2)  # The density path probes once despite the ordinary cache.
        self.assertEqual(self.cache.regular_spacing(self.grid.copy(), validate, require_fp_clean=True), 1.0)
        self.assertEqual(calls, 2)  # Exact content match permits only a clean density reuse.

    def test_borrowed_grid_mutation_and_irregular_mismatch_never_reuse(self) -> None:
        calls = 0

        def validate(values: np.ndarray) -> float:
            nonlocal calls
            calls += 1
            return analyzer_layers._regular_spacing(values)

        self.cache.regular_spacing(self.grid, validate, require_fp_clean=True)
        changed = self.grid.copy()
        changed[8] += 0.25
        with self.assertRaisesRegex(ValueError, "regular physical grid"):
            self.cache.regular_spacing(changed, validate, require_fp_clean=True)
        self.assertEqual(calls, 2)
        # A mismatch is validated but not cached against the owned baseline.
        with self.assertRaisesRegex(ValueError, "regular physical grid"):
            self.cache.regular_spacing(changed, validate, require_fp_clean=True)
        self.assertEqual(calls, 3)

    def test_clear_replacement_and_independent_cache_drop_clean_spacing(self) -> None:
        calls = 0

        def validate(values: np.ndarray) -> float:
            nonlocal calls
            calls += 1
            return analyzer_layers._regular_spacing(values)

        self.cache.regular_spacing(self.grid, validate, require_fp_clean=True)
        self.cache.regular_spacing(self.grid, validate, require_fp_clean=True)
        self.assertEqual(calls, 1)
        self.cache.prepare(self.grid + 2.0)
        self.cache.regular_spacing(self.grid + 2.0, validate, require_fp_clean=True)
        self.assertEqual(calls, 2)
        self.cache.clear()
        self.cache.prepare(self.grid + 2.0)
        self.cache.regular_spacing(self.grid + 2.0, validate, require_fp_clean=True)
        self.assertEqual(calls, 3)

        other = MeasurementGridCache(self.budget)
        other.prepare(self.grid + 2.0)
        other.regular_spacing(self.grid + 2.0, validate, require_fp_clean=True)
        self.assertEqual(calls, 4)

    def test_warm_ordinary_entry_replays_extreme_grid_under_caller_callback_policy(self) -> None:
        extreme = np.array([-1.0e308, 1.0e308], dtype=np.float64)
        cache = MeasurementGridCache(self.budget)
        cache.prepare(extreme)
        with np.errstate(over="ignore"):
            self.assertTrue(np.isinf(cache.regular_spacing(extreme, analyzer_layers._regular_spacing)))

        def invoke(validate_call, policy: str):
            events: list[tuple[str, int]] = []

            def callback(error: str, flag: int) -> None:
                events.append((error, flag))

            old_errors = np.seterr(over=policy)
            old_callback = np.seterrcall(callback)
            seen: list[warnings.WarningMessage]
            try:
                with warnings.catch_warnings(record=True) as seen:
                    warnings.simplefilter("always")
                    try:
                        value = validate_call()
                        outcome = ("value", value)
                    except Exception as error:  # Compare the exact original validator outcome.
                        outcome = (type(error), str(error))
                policy_after = np.geterr().copy()
                callback_after = np.geterrcall()
            finally:
                np.seterr(**old_errors)
                np.seterrcall(old_callback)
            return outcome, events, [(item.category, str(item.message)) for item in seen], policy_after, (
                callback_after is callback)

        expected_call = invoke(lambda: analyzer_layers._regular_spacing(extreme), "call")
        actual_call = invoke(
            lambda: cache.regular_spacing(extreme, analyzer_layers._regular_spacing,
                                          require_fp_clean=True), "call")
        self.assertEqual(actual_call, expected_call)
        self.assertEqual(actual_call[3]["over"], "call")
        self.assertTrue(actual_call[4])
        self.assertEqual(len(actual_call[1]), 2)

        expected_warn = invoke(lambda: analyzer_layers._regular_spacing(extreme), "warn")
        actual_warn = invoke(
            lambda: cache.regular_spacing(extreme, analyzer_layers._regular_spacing,
                                          require_fp_clean=True), "warn")
        self.assertEqual(actual_warn, expected_warn)
        self.assertEqual(actual_warn[3]["over"], "warn")
        self.assertEqual(len(actual_warn[2]), 2)

        expected_raise = invoke(lambda: analyzer_layers._regular_spacing(extreme), "raise")
        actual_raise = invoke(
            lambda: cache.regular_spacing(extreme, analyzer_layers._regular_spacing,
                                          require_fp_clean=True), "raise")
        self.assertEqual(actual_raise, expected_raise)
        self.assertIs(actual_raise[0][0], FloatingPointError)
        self.assertEqual(actual_raise[3]["over"], "raise")

        callback_error = RuntimeError("caller callback failed")

        def raising_callback(_error: str, _flag: int) -> None:
            raise callback_error

        old_errors = np.seterr(over="call")
        old_callback = np.seterrcall(raising_callback)
        try:
            with self.assertRaises(RuntimeError) as caught:
                cache.regular_spacing(extreme, analyzer_layers._regular_spacing, require_fp_clean=True)
            self.assertIs(caught.exception, callback_error)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(np.geterr()["over"], "call")
            self.assertIs(np.geterrcall(), raising_callback)
        finally:
            np.seterr(**old_errors)
            np.seterrcall(old_callback)

    def test_density_validation_reuses_spacing_but_keeps_full_range_and_edges(self) -> None:
        frequencies = np.arange(64, dtype=np.float64)
        density = np.full((3, 64), 0.5, dtype=np.float32)
        frame = persistence_frame(density, frequencies)
        grid_budget = PresentationAllocationBudget(8 * 1024 * 1024)
        grid = MeasurementGridCache(grid_budget)
        grid.prepare(frequencies)
        layers = AnalyzerLayerCache(PresentationAllocationBudget(8 * 1024 * 1024), grid_cache=grid)

        original_spacing = analyzer_layers._regular_spacing
        original_range = analyzer_layers.density_range_error
        with (patch.object(analyzer_layers, "_regular_spacing", wraps=original_spacing) as spacing,
              patch.object(analyzer_layers, "density_range_error", wraps=original_range) as range_check):
            self.assertTrue(layers.validate_persistence(frame))
            self.assertTrue(layers.validate_persistence(frame))
        # First input establishes the FP-clean spacing; the second exact-grid
        # validation still runs density range and edge construction but reuses it.
        self.assertEqual(spacing.call_count, 1)
        self.assertEqual(range_check.call_count, 2)

        from sdr_monitor.ui.v2.state.analyzer_layers import persistence_density_from_native

        converted = persistence_density_from_native(frame)
        self.assertIsNotNone(converted)
        np.testing.assert_array_equal(converted.frequency_edges_hz, np.arange(65) - 0.5)

    def test_density_range_failure_keeps_precedence_over_irregular_geometry(self) -> None:
        frequencies = np.array([0.0, 1.0, 2.0, 4.0], dtype=np.float64)
        density = np.full((2, 4), 0.5, dtype=np.float32)
        density[0, 0] = -0.25
        frame = persistence_frame(density, frequencies)
        grid = MeasurementGridCache(PresentationAllocationBudget(8 * 1024 * 1024))
        grid.prepare(frequencies)
        layers = AnalyzerLayerCache(PresentationAllocationBudget(8 * 1024 * 1024), grid_cache=grid)

        self.assertFalse(layers.validate_persistence(frame))


if __name__ == "__main__":
    unittest.main()

"""Exact-frame range reuse while geometry and allocation remain per call."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch
import warnings

import numpy as np

from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.services.native_persistence_storage import native_persistence_arrays
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationBudgetExceeded
from sdr_monitor.ui.v2.state import analyzer_layers
from sdr_monitor.ui.v2.state.analyzer_layer_cache import AnalyzerLayerCache
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)


class PersistenceStorageValidation178Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("SDR_APP07_READY_NATIVE"):
            raise unittest.SkipTest("requires the explicitly selected clean APP07-178 native")
        cls.native, _ = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def frame(self, value=.25, scale=1.0):
        image = np.full((2, 4), value, dtype=np.float32)
        axes = np.arange(4, dtype=np.float64) * 10.0 + 100.0
        snapshot = self.native._make_test_immutable_persistence_snapshot(image, axes)
        density, frequencies, receipt = native_persistence_arrays(self.native, snapshot)
        return LivePersistenceFrame(1, 1, 1, -100.0, 0.0, 2, 4, 1, False,
            frequencies, density, probability_scale=scale, unit="dBFS/bin", immutable_storage=receipt)

    def test_exact_same_frame_reuses_only_range_and_rechecks_geometry(self):
        cache = AnalyzerLayerCache(PresentationAllocationBudget(2 * 1024 * 1024))
        frame = self.frame()
        with patch.object(analyzer_layers, "density_range_error",
                          wraps=analyzer_layers.density_range_error) as range_check, \
             patch.object(analyzer_layers, "_regular_edges",
                          wraps=analyzer_layers._regular_edges) as geometry:
            self.assertTrue(cache.validate_persistence(frame))
            self.assertTrue(cache.validate_persistence(frame))
            self.assertEqual(range_check.call_count, 1)
            self.assertEqual(geometry.call_count, 2)
            changed = replace(frame, update_sequence=2)
            self.assertTrue(cache.validate_persistence(changed))
            self.assertEqual(range_check.call_count, 2)
            generic = replace(changed, immutable_storage=None)
            self.assertTrue(cache.validate_persistence(generic))
            self.assertEqual(range_check.call_count, 3)
        self.assertEqual(cache.allocation_budget.snapshot().reserved_bytes, 0)

    def test_fp_probe_replays_under_caller_policy_and_never_certifies_event(self):
        frame = self.frame(float(np.finfo(np.float32).max), scale=2.0)
        cache = AnalyzerLayerCache(PresentationAllocationBudget(2 * 1024 * 1024))
        callback_events = []
        original_call = np.geterrcall()
        original_policy = np.geterr().copy()
        try:
            np.seterr(over="call")
            np.seterrcall(lambda *event: callback_events.append(event))
            caller_policy = np.geterr().copy()
            caller_callback = np.geterrcall()
            with patch.object(analyzer_layers, "density_range_error",
                              wraps=analyzer_layers.density_range_error) as range_check:
                self.assertTrue(cache.validate_persistence(frame))
                self.assertTrue(cache.validate_persistence(frame))
                self.assertEqual(range_check.call_count, 2)
            self.assertEqual(np.geterr(), caller_policy)
            self.assertIs(np.geterrcall(), caller_callback)
            self.assertTrue(callback_events)

            np.seterr(over="raise")
            with self.assertRaises(FloatingPointError):
                cache.validate_persistence(frame)
            self.assertEqual(np.geterr()["over"], "raise")

            np.seterr(over="warn")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.assertTrue(cache.validate_persistence(frame))
            self.assertTrue(caught)
            self.assertEqual(np.geterr()["over"], "warn")

            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                with self.assertRaises(RuntimeWarning):
                    cache.validate_persistence(frame)
            self.assertEqual(np.geterr(), {**caller_policy, "over": "warn"})
        finally:
            np.seterrcall(original_call)
            np.seterr(**original_policy)
        self.assertEqual(cache.allocation_budget.snapshot().reserved_bytes, 0)

    def test_fp_fallback_replays_the_later_validation_batch(self):
        image = np.full((17, 4096), .25, dtype=np.float32)
        image[0, 0] = -.25
        image[-1, -1] = np.finfo(np.float32).max
        axes = np.linspace(100.0, 200.0, 4096, dtype=np.float64)
        snapshot = self.native._make_test_immutable_persistence_snapshot(image, axes)
        density, frequencies, receipt = native_persistence_arrays(self.native, snapshot)
        frame = LivePersistenceFrame(1, 1, 1, -100.0, 0.0, 17, 4096, 1, False,
            frequencies, density, probability_scale=2.0, unit="dBFS/bin", immutable_storage=receipt)
        cache = AnalyzerLayerCache(PresentationAllocationBudget(4 * 1024 * 1024))
        events = []
        original_call = np.geterrcall()
        original_policy = np.geterr().copy()
        try:
            np.seterr(over="call")
            np.seterrcall(lambda *event: events.append(event))
            with patch.object(analyzer_layers, "density_range_error",
                              wraps=analyzer_layers.density_range_error) as range_check:
                self.assertFalse(cache.validate_persistence(frame))
                self.assertEqual(range_check.call_count, 2)
            self.assertEqual(len(events), 1)
            self.assertEqual(np.geterr()["over"], "call")
        finally:
            np.seterrcall(original_call)
            np.seterr(**original_policy)
        self.assertEqual(cache.allocation_budget.snapshot().reserved_bytes, 0)

    def test_selected_reserve_and_conversion_failures_clear_same_frame_certificate(self):
        frame = self.frame()
        cache = AnalyzerLayerCache(PresentationAllocationBudget(2 * 1024 * 1024))
        with patch.object(analyzer_layers, "density_range_error",
                          wraps=analyzer_layers.density_range_error) as range_check:
            self.assertTrue(cache.validate_persistence(frame))
            cache.allocation_budget.limit_bytes = 1
            with self.assertRaises(PresentationBudgetExceeded):
                cache.persistence(frame)
            cache.allocation_budget.limit_bytes = 2 * 1024 * 1024
            self.assertTrue(cache.validate_persistence(frame))
            self.assertEqual(range_check.call_count, 2)

            with patch("sdr_monitor.ui.v2.state.analyzer_layer_cache.persistence_density_from_native",
                       side_effect=RuntimeError("selected conversion sentinel")):
                with self.assertRaisesRegex(RuntimeError, "selected conversion sentinel"):
                    cache.persistence(frame)
            self.assertTrue(cache.validate_persistence(frame))
            self.assertEqual(range_check.call_count, 3)
        self.assertEqual(cache.allocation_budget.snapshot().reserved_bytes, 0)

    def test_cached_validation_budget_refusal_clears_certificate(self):
        frame = self.frame()
        cache = AnalyzerLayerCache(PresentationAllocationBudget(2 * 1024 * 1024))
        with patch.object(analyzer_layers, "density_range_error",
                          wraps=analyzer_layers.density_range_error) as range_check:
            self.assertTrue(cache.validate_persistence(frame))
            cache.allocation_budget.limit_bytes = 1
            with self.assertRaises(PresentationBudgetExceeded):
                cache.validate_persistence(frame)
            cache.allocation_budget.limit_bytes = 2 * 1024 * 1024
            self.assertTrue(cache.validate_persistence(frame))
            self.assertEqual(range_check.call_count, 2)
        self.assertEqual(cache.allocation_budget.snapshot().reserved_bytes, 0)

    def test_false_or_fp_unclean_cached_validation_never_keeps_certificate(self):
        for outcome in ((False, True), (True, False)):
            with self.subTest(outcome=outcome):
                frame = self.frame()
                cache = AnalyzerLayerCache(PresentationAllocationBudget(2 * 1024 * 1024))
                self.assertTrue(cache.validate_persistence(frame))
                with patch("sdr_monitor.ui.v2.state.analyzer_layer_cache._validate_native_persistence_display",
                           return_value=outcome):
                    self.assertEqual(cache.validate_persistence(frame), outcome[0])
                with patch.object(analyzer_layers, "density_range_error",
                                  wraps=analyzer_layers.density_range_error) as range_check:
                    self.assertTrue(cache.validate_persistence(frame))
                    self.assertEqual(range_check.call_count, 1)
                self.assertEqual(cache.allocation_budget.snapshot().reserved_bytes, 0)

    def test_density_error_still_precedes_geometry_on_every_new_frame(self):
        cache = AnalyzerLayerCache(PresentationAllocationBudget(2 * 1024 * 1024))
        valid = self.frame()
        self.assertTrue(cache.validate_persistence(valid))
        bad_density = valid.density.copy()
        bad_density[0, 0] = -0.5
        bad_density.setflags(write=False)
        bad_axes = valid.frequencies_hz.copy()
        bad_axes[2] += 1.0
        bad_axes.setflags(write=False)
        malformed = LivePersistenceFrame(2, 2, 2, -100.0, 0.0, 2, 4, 1, False,
            bad_axes, bad_density)
        self.assertFalse(cache.validate_persistence(malformed))
        self.assertEqual(cache.allocation_budget.snapshot().reserved_bytes, 0)


if __name__ == "__main__":
    unittest.main()

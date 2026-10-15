"""Conformance of selected output with the existing native/range/grid owners."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from sdr_monitor.services.native_persistence_storage import native_persistence_arrays
from sdr_monitor.ui.v2.spectrum import contracts as spectrum_contracts
from sdr_monitor.ui.v2.spectrum import persistence_contracts
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationBudgetExceeded
from sdr_monitor.ui.v2.state import analyzer_layer_cache, analyzer_layers
import sdr_monitor.ui.v2_pane_presentation as pane_presentation
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)
from tests.ui_v2.test_app07_ordered_display_preparation import Stage, make_ordered_fixture


class SelectedDensityIntegration182Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("SDR_APP07_READY_NATIVE"):
            raise unittest.SkipTest("requires the explicitly selected clean APP07 native")
        cls.native, _ = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def _owned_density_deliveries(self):
        fixture = make_ordered_fixture(count=1)
        first = fixture.delivery(1, 1, refs=True)
        original = first.bundle.persistence
        snapshot = self.native._make_test_immutable_persistence_snapshot(
            original.density, original.frequencies_hz)
        density, axes, receipt = native_persistence_arrays(self.native, snapshot)
        owned = replace(original, density=density, frequencies_hz=axes, immutable_storage=receipt)
        deliveries = [replace(first, bundle=replace(first.bundle, persistence=owned))]
        density_ref = first.layer_obligation_refs[-1]
        for sequence in (126, 252):
            later = fixture.delivery(1, sequence, refs=True, density=False)
            deliveries.append(replace(
                later,
                bundle=replace(later.bundle, persistence=owned),
                layer_obligation_refs=(*later.layer_obligation_refs, density_ref),
            ))
        return fixture, owned, tuple(deliveries)

    def test_selected_outputs_coexist_with_genuine_range_and_spacing_reuse(self):
        fixture, owned, deliveries = self._owned_density_deliveries()
        pending = None
        with patch.object(analyzer_layers, "density_range_error",
                          wraps=analyzer_layers.density_range_error) as range_check, \
             patch.object(persistence_contracts, "density_range_error",
                          wraps=persistence_contracts.density_range_error) as selected_range_check, \
             patch.object(spectrum_contracts, "finite_value_extent",
                          wraps=spectrum_contracts.finite_value_extent) as extent, \
             patch.object(analyzer_layer_cache, "waterfall_line_from_spectrum",
                          wraps=analyzer_layer_cache.waterfall_line_from_spectrum) as waterfall, \
             patch.object(pane_presentation, "validate_large_waterfall_geometry",
                          wraps=pane_presentation.validate_large_waterfall_geometry) as geometry, \
             patch.object(analyzer_layers, "_regular_spacing",
                          wraps=analyzer_layers._regular_spacing) as spacing:
            for delivery in deliveries:
                current = fixture.preparer.validate_rtbw_display(delivery)
                self.assertTrue(current.density_available)
                if pending is not None:
                    self.assertEqual(fixture.budget.snapshot().reserved_bytes, 2 * (2048 * 12 + 8))
                    pending.release()
                    self.assertEqual(fixture.budget.snapshot().reserved_bytes, 2048 * 12 + 8)
                pending = current
            self.assertEqual(range_check.call_count, 16)
            self.assertEqual(selected_range_check.call_count, 0)
            self.assertEqual(extent.call_count, 0)
            self.assertEqual(waterfall.call_count, 0)
            self.assertEqual(geometry.call_count, 3)
            self.assertEqual(spacing.call_count, 1)
            grid = fixture.preparer._grids["pane-1"]
            self.assertTrue(grid._regular_spacing_fp_clean)
            selected = fixture.preparer.materialize_rtbw_display(pending)
            pending = None
            self.assertEqual(extent.call_count, 1)
            self.assertEqual(waterfall.call_count, 1)
            self.assertEqual(selected_range_check.call_count, 1)
            self.assertEqual(spacing.call_count, 2)
            self.assertIs(selected.delivery, deliveries[-1])
            self.assertIs(selected.delivery.bundle.persistence, owned)
            self.assertEqual(selected.spectrum.view.source_frame.spectrum.sequence, 252)
            self.assertEqual(selected.waterfall.sequence, 252)
            self.assertEqual(len(deliveries[0].layer_obligation_refs), 2)
            np.testing.assert_array_equal(
                selected.persistence.density,
                np.multiply(owned.density, owned.probability_scale, dtype=np.float32),
            )
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_native_cached_input_row_reservation_refusal_releases_pending_lease(self):
        fixture, _, deliveries = self._owned_density_deliveries()
        delivery = deliveries[0]
        pending = None
        cache = fixture.preparer._layers["pane-1"]
        original_reserve = cache.reserve_waterfall
        reserve_calls = 0
        original_limit = fixture.budget.limit_bytes

        def refuse_second_row(frame):
            nonlocal reserve_calls
            reserve_calls += 1
            before = fixture.budget.snapshot()
            fixture.budget.limit_bytes = before.observed_bytes + before.reserved_bytes
            return original_reserve(frame)

        try:
            with patch.object(analyzer_layers, "density_range_error",
                              wraps=analyzer_layers.density_range_error) as range_check, \
                 patch.object(analyzer_layers, "_regular_spacing",
                              wraps=analyzer_layers._regular_spacing) as spacing:
                pending = fixture.preparer.validate_rtbw_display(delivery)
                before = fixture.budget.snapshot()
                self.assertEqual(before.reserved_bytes, 2048 * 12 + 8)
                self.assertTrue(fixture.preparer._grids["pane-1"]._regular_spacing_fp_clean)
                with patch.object(cache, "reserve_waterfall", side_effect=refuse_second_row) as reserve:
                    with self.assertRaises(PresentationBudgetExceeded):
                        fixture.preparer.validate_rtbw_display(delivery)
                self.assertEqual(reserve_calls, 1)
                self.assertEqual(reserve.call_count, 1)
                self.assertEqual(range_check.call_count, 16)
                self.assertEqual(spacing.call_count, 1)
                self.assertTrue(fixture.preparer._grids["pane-1"]._regular_spacing_fp_clean)
                self.assertEqual(fixture.budget.snapshot().reserved_bytes, 2048 * 12 + 8)
        finally:
            fixture.budget.limit_bytes = original_limit
            if pending is not None:
                pending.release()
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_known_stale_shared_peer_skips_optional_coalescing_and_keeps_ordered_custody(self):
        fixture = make_ordered_fixture(topology="paired")
        stale = fixture.delivery(2, 1, refs=True)
        anchor = fixture.delivery(2, 126, refs=True)
        fixture.poll("resource-1", (anchor,))
        peer = fixture.delivery(1, 1, refs=True)
        self.assertTrue(fixture.queue.rtbw_delivery_is_stale(stale))

        original = fixture.preparer.validate_rtbw_display
        with patch.object(fixture.preparer, "validate_rtbw_display", wraps=original) as validate, \
             patch.object(fixture.preparer, "can_coalesce_rtbw_display",
                          wraps=fixture.preparer.can_coalesce_rtbw_display) as coalesce:
            fixture.poll("resource-1", (peer, stale))
        self.assertEqual(validate.call_count, 2)
        self.assertIs(validate.call_args_list[0].args[0], peer)
        self.assertIs(validate.call_args_list[1].args[0], stale)
        self.assertEqual(coalesce.call_count, 0)
        self.assertEqual(fixture.queue.metrics().stale_rejected, 1)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)
        for ref in (peer.obligation_ref, *peer.layer_obligation_refs):
            self.assertIn((ref, Stage.PREPARED), fixture.stages)
        for ref in (stale.obligation_ref, *stale.layer_obligation_refs):
            self.assertIn((ref, Stage.QUEUE_REJECTED), fixture.stages)
        self.assertEqual(fixture.ledger.snapshot().accounting_failures, 0)


if __name__ == "__main__":
    unittest.main()

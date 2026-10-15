"""Production deferred PaneDeliveryPreparer path with a genuine native receipt."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from sdr_monitor.services.native_persistence_storage import native_persistence_arrays
from sdr_monitor.ui.v2.spectrum import persistence_contracts
from sdr_monitor.ui.v2.state import analyzer_layers
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)
from tests.ui_v2.test_app07_ordered_display_preparation import make_ordered_fixture


class PersistenceStorageProduct178Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("SDR_APP07_READY_NATIVE"):
            raise unittest.SkipTest("requires the explicitly selected clean APP07-178 native")
        cls.native, _ = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def test_real_deferred_preparer_hits_only_range_and_selected_materialization_is_full(self):
        fixture = make_ordered_fixture(count=1)
        original = fixture.delivery(1, 1, refs=True)
        old_frame = original.bundle.persistence
        snapshot = self.native._make_test_immutable_persistence_snapshot(
            old_frame.density, old_frame.frequencies_hz)
        density, axes, receipt = native_persistence_arrays(self.native, snapshot)
        native_frame = replace(old_frame, density=density, frequencies_hz=axes,
                               immutable_storage=receipt)
        first_delivery = replace(original, bundle=replace(original.bundle, persistence=native_frame))
        later = []
        for sequence in (126, 252):
            delivery = fixture.delivery(1, sequence)
            later.append(replace(delivery, bundle=replace(delivery.bundle, persistence=native_frame)))
        later = tuple(later)
        deliveries = (first_delivery, *later)

        with patch.object(analyzer_layers, "density_range_error",
                          wraps=analyzer_layers.density_range_error) as range_check, \
             patch.object(persistence_contracts, "density_range_error",
                          wraps=persistence_contracts.density_range_error) as selected_range_check, \
             patch.object(analyzer_layers, "_regular_edges",
                          wraps=analyzer_layers._regular_edges) as geometry:
            pending = fixture.preparer.validate_rtbw_display(deliveries[0])
            self.assertTrue(pending.density_available)
            self.assertEqual(range_check.call_count, 16)
            pending = fixture.preparer.validate_rtbw_display(deliveries[1])
            self.assertTrue(pending.density_available)
            pending = fixture.preparer.validate_rtbw_display(deliveries[2])
            self.assertTrue(pending.density_available)
            self.assertEqual(range_check.call_count, 16)
            self.assertEqual(geometry.call_count, 3)
            prepared = fixture.preparer.materialize_rtbw_display(pending)
            self.assertIsNotNone(prepared.persistence)
            self.assertEqual(range_check.call_count, 16)
            self.assertEqual(selected_range_check.call_count, 1)
            self.assertIs(prepared.delivery, deliveries[-1])
            self.assertIs(prepared.delivery.bundle.persistence, native_frame)
            self.assertEqual(prepared.spectrum.view.source_frame.spectrum.sequence, 252)
            self.assertIs(native_frame.layer_ready, old_frame.layer_ready)
            self.assertEqual(len(first_delivery.layer_obligation_refs), 2)
            np.testing.assert_array_equal(prepared.persistence.density,
                np.multiply(native_frame.density, native_frame.probability_scale, dtype=np.float32))
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)
        self.assertLessEqual(fixture.budget.snapshot().peak_bytes, fixture.budget.limit_bytes)

    def test_new_frame_replacement_clears_successful_range_entry(self):
        fixture = make_ordered_fixture(count=1)
        original = fixture.delivery(1, 1)
        old_frame = original.bundle.persistence
        snapshot = self.native._make_test_immutable_persistence_snapshot(
            old_frame.density, old_frame.frequencies_hz)
        density, axes, receipt = native_persistence_arrays(self.native, snapshot)
        frame = replace(old_frame, density=density, frequencies_hz=axes, immutable_storage=receipt)
        delivery = replace(original, bundle=replace(original.bundle, persistence=frame))
        fixture.preparer.validate_rtbw_display(delivery)
        changed = replace(frame)
        changed_delivery = replace(delivery, bundle=replace(delivery.bundle, persistence=changed))
        with patch.object(analyzer_layers, "density_range_error",
                          wraps=analyzer_layers.density_range_error) as range_check:
            fixture.preparer.validate_rtbw_display(changed_delivery)
        self.assertEqual(range_check.call_count, 16)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)


if __name__ == "__main__":
    unittest.main()

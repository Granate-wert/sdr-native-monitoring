"""Finite selected-output preparation boundary; source-only tests."""

from dataclasses import replace
import unittest
import warnings
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum import contracts
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationBudgetExceeded
from sdr_monitor.ui.v2.state import analyzer_layers
from sdr_monitor.ui.v2_pane_presentation import DeferredRtbwDisplayInput, ValidatedRtbwDisplayInput
from sdr_monitor.domain.analyzer import RtbwFrameMetadata
from tests.ui_v2.test_app07_ordered_display_preparation import Stage, make_ordered_fixture


def _grid_fallback_fixture(kind):
    fixture = make_ordered_fixture(count=1)
    delivery = fixture.delivery(1, 1, refs=False)
    binding = fixture.preparer.bindings[delivery.pane_id]
    if kind == "subnormal":
        step = float(np.nextafter(0.0, 1.0))
        center = float(2048 * step)
        rate = float(4096 * step)
    elif kind == "extreme":
        center, rate = 1.0e13, 1.0e9
        step = rate / 4096
    elif kind == "negative":
        center, rate = 1.0e6, 2.0e7
        step = rate / 4096
    elif kind == "custom":
        center, rate = 1.44e8, 2.0e7
        step = rate / 4096
    else:
        raise ValueError("unknown fallback grid case")
    grid = center + (np.arange(4096, dtype=np.float64) - 2048) * step
    grid.setflags(write=False)
    source = replace(delivery.bundle.spectrum, center_frequency_hz=center,
                     sample_rate_hz=rate, frequencies_hz=grid)
    if kind == "custom":
        class UnitLabel(str):
            pass
        source = replace(source, unit=UnitLabel(source.unit))
    bundle = replace(delivery.bundle, spectrum=source,
                     rtbw=RtbwFrameMetadata(center, rate, 4096, 2048),
                     persistence=None, identity=None)
    crop = replace(binding.crop, start_hz=float(grid[1000]), stop_hz=float(grid[3000]))
    binding = replace(binding, crop=crop, capture_start_hz=float(grid[0]),
                      capture_stop_hz=float(grid[-1]))
    fixture.preparer.bindings[delivery.pane_id] = binding
    return fixture, replace(delivery, crop=crop, bundle=bundle)


class SelectedOutputPreparationTests(unittest.TestCase):
    def test_three_inputs_validate_each_but_construct_selected_products_once(self):
        fixture = make_ordered_fixture(count=1)
        deliveries = tuple(fixture.delivery(1, seq, refs=True) for seq in (1, 126, 252))
        original_validate = fixture.preparer.validate_rtbw_display
        with patch.object(fixture.preparer, "validate_rtbw_display", wraps=original_validate) as validate, \
             patch.object(contracts, "finite_value_extent", wraps=contracts.finite_value_extent) as extent, \
             patch.object(analyzer_layers, "_reduce_waterfall_columns",
                          wraps=analyzer_layers._reduce_waterfall_columns) as reduce:
            fixture.poll("resource-1", deliveries)
        self.assertEqual(validate.call_count, 3)
        self.assertEqual(extent.call_count, 1)
        self.assertEqual(reduce.call_count, 1)
        packet = fixture.queue.drain()[0]
        self.assertIs(packet.delivery, deliveries[-1])
        self.assertIs(packet.spectrum.view.source_frame, deliveries[-1].bundle)
        self.assertEqual(packet.spectrum.finite_extent, (-81.0, -81.0))
        self.assertEqual(packet.waterfall.sequence, 252)
        np.testing.assert_array_equal(packet.waterfall.values, np.full(2048, -81, np.float32))
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_nonfinite_values_take_the_original_full_path(self):
        for nonfinite in (np.nan, np.inf, -np.inf):
            with self.subTest(nonfinite=nonfinite):
                fixture = make_ordered_fixture(count=1)
                delivery = fixture.delivery(1, 1)
                backing = delivery.bundle.spectrum.values.copy()
                borrowed = backing.view()
                borrowed.setflags(write=False)
                changed = replace(delivery.bundle.spectrum, values=borrowed)
                delivery = replace(delivery, bundle=replace(delivery.bundle,
                    spectrum=changed, identity=None))
                # Inject after bundle admission so +infinity is tested as a
                # changed borrowed source, not rejected by fixture creation.
                backing[111] = np.float32(nonfinite)
                token = fixture.preparer.validate_rtbw_display(delivery)
                self.assertIsInstance(token, ValidatedRtbwDisplayInput)
                self.assertIsNotNone(token.waterfall)
                self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_quality_barriers_keep_each_candidate_on_the_full_path(self):
        fixture = make_ordered_fixture(count=1)
        deliveries = tuple(fixture.delivery(1, seq, refs=True, quality=quality)
                           for seq, quality in ((1, 0), (126, 8192), (252, 0)))
        prepared = []
        original_offer = fixture.queue.offer
        with patch.object(contracts, "finite_value_extent", wraps=contracts.finite_value_extent) as extent:
            with patch.object(fixture.queue, "offer",
                              side_effect=lambda packet: (prepared.append(packet), original_offer(packet))[1]):
                fixture.poll("resource-1", deliveries)
        self.assertEqual(extent.call_count, 3)
        self.assertEqual([packet.delivery for packet in prepared], list(deliveries))
        for delivery in deliveries:
            for ref in (delivery.obligation_ref, *delivery.layer_obligation_refs):
                self.assertIn((ref, Stage.PREPARED), fixture.stages)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_selected_full_revalidation_observes_borrowed_backing_changes(self):
        fixture = make_ordered_fixture(count=1)
        delivery = fixture.delivery(1, 1)
        backing = delivery.bundle.spectrum.values.copy()
        # The source publishes a read-only view while retaining the producer's
        # writable backing store, as a genuinely borrowed buffer may do.
        borrowed = backing.view()
        borrowed.setflags(write=False)
        frame = replace(delivery.bundle.spectrum, values=borrowed)
        delivery = replace(delivery, bundle=replace(delivery.bundle, spectrum=frame, identity=None))
        token = fixture.preparer.validate_rtbw_display(delivery)
        self.assertIsInstance(token, DeferredRtbwDisplayInput)
        backing[:] = np.float32(42.0)
        packet = fixture.preparer.materialize_rtbw_display(token)
        token.release()
        self.assertEqual(packet.spectrum.finite_extent, (42.0, 42.0))
        np.testing.assert_array_equal(packet.waterfall.values, np.full(2048, 42, np.float32))
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_selected_full_revalidation_rechecks_borrowed_grid(self):
        fixture = make_ordered_fixture(count=1)
        delivery = fixture.delivery(1, 1)
        backing = delivery.bundle.spectrum.frequencies_hz.copy()
        borrowed = backing.view()
        borrowed.setflags(write=False)
        frame = replace(delivery.bundle.spectrum, frequencies_hz=borrowed)
        delivery = replace(delivery, bundle=replace(delivery.bundle, spectrum=frame, identity=None))
        token = fixture.preparer.validate_rtbw_display(delivery)
        self.assertIsInstance(token, DeferredRtbwDisplayInput)
        backing[10] = backing[9]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            fixture.preparer.materialize_rtbw_display(token)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_selected_full_revalidation_refuses_newly_writable_borrowed_grid(self):
        fixture = make_ordered_fixture(count=1)
        delivery = fixture.delivery(1, 1)
        backing = delivery.bundle.spectrum.frequencies_hz.copy()
        borrowed = backing.view()
        borrowed.setflags(write=False)
        frame = replace(delivery.bundle.spectrum, frequencies_hz=borrowed)
        delivery = replace(delivery, bundle=replace(delivery.bundle, spectrum=frame, identity=None))
        token = fixture.preparer.validate_rtbw_display(delivery)
        self.assertIsInstance(token, DeferredRtbwDisplayInput)
        # LiveSpectrumFrame retains its own flattened view; make that exact
        # published view writable while keeping the original backing alive.
        frame.frequencies_hz.setflags(write=True)
        with self.assertRaisesRegex(ValueError, "must be read-only"):
            fixture.preparer.materialize_rtbw_display(token)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_finite_extremes_subnormals_and_signed_zero_remain_selected(self):
        fixture = make_ordered_fixture(count=1)
        delivery = fixture.delivery(1, 1)
        frame = delivery.bundle.spectrum
        values = frame.values.copy()
        values[0] = np.finfo(np.float32).max
        values[1] = np.finfo(np.float32).smallest_subnormal
        values[2] = np.float32(-0.0)
        values[3] = np.float32(0.0)
        values.setflags(write=False)
        changed = replace(frame, values=values)
        delivery = replace(delivery, bundle=replace(delivery.bundle, spectrum=changed, identity=None))
        token = fixture.preparer.validate_rtbw_display(delivery)
        self.assertIsInstance(token, DeferredRtbwDisplayInput)
        packet = fixture.preparer.materialize_rtbw_display(token)
        self.assertEqual(packet.spectrum.finite_extent, (-84.0, float(np.finfo(np.float32).max)))
        self.assertTrue(np.isfinite(packet.waterfall.values).all())
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_next_row_reserves_before_old_pending_credit_is_released(self):
        fixture = make_ordered_fixture(count=1)
        first = fixture.delivery(1, 1)
        token = fixture.preparer.validate_rtbw_display(first)
        self.assertIsInstance(token, DeferredRtbwDisplayInput)
        row_bytes = 2048 * 12 + 8
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, row_bytes)
        second = fixture.delivery(1, 126, density=False)
        self.assertTrue(fixture.budget.admit_sources(second.bundle))
        observed = fixture.budget.snapshot().observed_bytes
        fixture.budget.limit_bytes = observed + row_bytes * 2 - 1
        with self.assertRaises(PresentationBudgetExceeded):
            fixture.preparer.validate_rtbw_display(second)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, row_bytes)
        token.release()
        token.release()
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_abandoned_direct_token_releases_lease_without_gc_collection(self):
        fixture = make_ordered_fixture(count=1)
        token = fixture.preparer.validate_rtbw_display(fixture.delivery(1, 1))
        self.assertIsInstance(token, DeferredRtbwDisplayInput)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 2048 * 12 + 8)
        del token
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_cache_reset_invalidates_token_and_closes_lease(self):
        fixture = make_ordered_fixture(count=1)
        token = fixture.preparer.validate_rtbw_display(fixture.delivery(1, 1))
        self.assertIsInstance(token, DeferredRtbwDisplayInput)
        fixture.preparer.clear_resource("resource-1")
        with self.assertRaisesRegex(ValueError, "cache reset"):
            fixture.preparer.materialize_rtbw_display(token)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_full_path_fallback_keeps_numpy_policy_and_error_callback(self):
        class Log:
            def __init__(self):
                self.messages = []

            def write(self, value):
                self.messages.append(value)

        for policy in ("ignore", "warn", "raise", "call", "log"):
            with self.subTest(policy=policy):
                def run(*, selected_boundary):
                    fixture = make_ordered_fixture(count=1)
                    delivery = fixture.delivery(1, 1)
                    backing = delivery.bundle.spectrum.values.copy()
                    borrowed = backing.view()
                    borrowed.setflags(write=False)
                    frame = replace(delivery.bundle.spectrum, values=borrowed)
                    delivery = replace(delivery, bundle=replace(delivery.bundle,
                        spectrum=frame, identity=None))
                    # The bundle admits a quiet NaN; the producer-owned backing
                    # changes it to signaling NaN only after admission.
                    backing.view(np.uint32)[17] = np.uint32(0x7F800001)
                    callbacks = []
                    callback = Log() if policy == "log" else (lambda *args: callbacks.append(args))
                    old_policy, old_callback = np.geterr(), np.geterrcall()
                    try:
                        np.seterr(all=policy)
                        np.seterrcall(callback)
                        outcome = None
                        with warnings.catch_warnings(record=True) as caught:
                            warnings.simplefilter("always")
                            try:
                                token = fixture.preparer._prepare(
                                    delivery, defer_persistence=True,
                                    defer_selected_outputs=selected_boundary)
                                self.assertIsInstance(token, ValidatedRtbwDisplayInput)
                                outcome = ("prepared", token.spectrum.finite_extent)
                            except BaseException as error:
                                outcome = (type(error), str(error))
                            observed = (outcome, tuple(callbacks),
                                tuple(callback.messages) if policy == "log" else (),
                                tuple((type(w.message), str(w.message)) for w in caught),
                                np.geterr().copy(), np.geterrcall() is callback)
                        self.assertEqual(np.geterr(), {name: policy for name in ("divide", "over", "under", "invalid")})
                        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)
                        return observed
                    finally:
                        np.seterr(**old_policy)
                        np.seterrcall(old_callback)

                self.assertEqual(run(selected_boundary=True), run(selected_boundary=False))

    def test_tiny_and_extreme_grid_fallbacks_match_full_path_fp_oracle(self):
        class Log:
            def __init__(self):
                self.messages = []

            def write(self, message):
                self.messages.append(message)

        def run(kind, *, selected_boundary, policy):
            fixture, delivery = _grid_fallback_fixture(kind)
            callbacks = []
            callback = Log() if policy == "log" else (lambda *args: callbacks.append(args))
            old_policy, old_callback = np.geterr(), np.geterrcall()
            try:
                np.seterr(all=policy)
                np.seterrcall(callback)
                outcome = None
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    try:
                        token = fixture.preparer._prepare(
                            delivery, defer_persistence=True,
                            defer_selected_outputs=selected_boundary)
                        self.assertIsInstance(token, ValidatedRtbwDisplayInput)
                        outcome = ("prepared", token.spectrum.finite_extent,
                                   token.waterfall.values.shape)
                    except BaseException as error:
                        outcome = (type(error), str(error))
                    observed = (outcome, tuple(callbacks), tuple(callback.messages) if policy == "log" else (),
                        tuple((type(w.message), str(w.message)) for w in caught), np.geterr().copy(),
                        np.geterrcall() is callback)
                self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)
                return observed
            finally:
                np.seterr(**old_policy)
                np.seterrcall(old_callback)

        for kind in ("subnormal", "extreme", "negative", "custom"):
            for policy in ("ignore", "warn", "raise", "call", "log"):
                with self.subTest(kind=kind, policy=policy):
                    original = run(kind, selected_boundary=False, policy=policy)
                    optimized_fallback = run(kind, selected_boundary=True, policy=policy)
                    self.assertEqual(optimized_fallback, original)


if __name__ == "__main__":
    unittest.main()

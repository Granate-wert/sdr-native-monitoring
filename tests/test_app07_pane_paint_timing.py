"""Measured-clock and exact-view timing guards. No SDR/Qt/performance claim."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import os
from time import perf_counter_ns
import unittest

from sdr_monitor.domain.analytical_ready import ReadyClockBracket, ReadyClockMapping, ReadyHostBounds
from sdr_monitor.domain.host_clock import HostClockKind, HostClockScope
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryEvent, PaneDeliveryStage as Stage
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView
from sdr_monitor.domain.pane_paint_timing import PanePaintReturnReceipt, PaintTimingState, evaluate_ready_to_paint
from sdr_monitor.services.pane_delivery_ledger import HOST_GRAPH_SCALAR_BUDGET, PaneDeliveryLedger
from sdr_monitor.services.native_ready_bridge import NativeReadyBridge
from tests.test_app07_pane_delivery_obligations import identity
from tests.test_app07_pane_analytical_identity import owner_scope, ready
from tests.test_app07_native_ready_bridge import native_with_clock, receipt
from tests import test_app07_layer_ready_admission as layer_frames
from tests.test_app07_pane_layer_custody import packet, bind


def timed_identity(pane="one", offer=1, *, pid=None, known=True):
    scope = owner_scope(host_process_id=os.getpid() if pid is None else pid)
    clock = HostClockScope(HostClockKind.PERF_COUNTER_NS, scope.host_process_id) if known else None
    bounds = ReadyHostBounds(ReadyClockBracket(10_000, 1000, 1010),
                             ReadyClockBracket(11_000, 1100, 1110), clock)
    original = replace(ready(scope, offer=offer), mapping=ReadyClockMapping.BOUNDED, host_bounds=bounds)
    return identity(pane, owner_scope=scope, ready=original)


def scheduled(ledger, original=None, *, view=PaneDeliveryView.SPECTRUM):
    ref = ledger.admit("one", timed_identity() if original is None else original, view=view)
    for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED, Stage.QUEUE_DRAINED,
                  Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED):
        assert ledger.note(ref, stage)
    return ref


def paint_event(*, before=1200, after=1250, recorded=9000, known=True):
    ref = scheduled(PaneDeliveryLedger(("one",)), timed_identity(known=known))
    clock = HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid())
    paint = PanePaintReturnReceipt(ref, clock, before, after)
    return PaneDeliveryEvent(8, ref, Stage.PAINT_RETURNED, recorded, clock, paint)


class PaintTimingTests(unittest.TestCase):
    def test_ready_to_observed_return_excludes_later_bookkeeping(self):
        event = paint_event()
        timing = evaluate_ready_to_paint(event)
        self.assertIs(timing.state, PaintTimingState.BOUNDED)
        self.assertEqual(timing.base_return_elapsed_ns, (90, 250))
        self.assertEqual(timing.after_return_sample_elapsed_ns, (140, 250))
        self.assertEqual(timing.bookkeeping_after_sample_ns, 7750)
        with self.assertRaises(FrozenInstanceError):
            event.paint_return.before_paint_ns = 0

    def test_overlap_is_signed_uncertainty_not_clipped_or_midpoint(self):
        timing = evaluate_ready_to_paint(paint_event(before=1050, after=1090))
        self.assertIs(timing.state, PaintTimingState.ORDER_UNCERTAIN)
        self.assertEqual(timing.base_return_elapsed_ns, (-60, 90))
        self.assertEqual(timing.after_return_sample_elapsed_ns, (-20, 90))

    def test_quantized_equal_samples_remain_a_bracket_not_precise_qt_exit(self):
        timing = evaluate_ready_to_paint(paint_event(before=1200, after=1200))
        self.assertEqual(timing.base_return_elapsed_ns, (90, 200))
        self.assertEqual(timing.after_return_sample_elapsed_ns, (90, 200))

    def test_legacy_event_never_borrows_bookkeeping_as_paint_receipt(self):
        event = paint_event()
        result = evaluate_ready_to_paint(replace(event, paint_return=None))
        self.assertIs(result.state, PaintTimingState.MISSING_PAINT_RECEIPT)
        self.assertIsNone(result.base_return_elapsed_ns)
        self.assertIsNone(result.after_return_sample_elapsed_ns)

    def test_unknown_clock_and_unknown_native_mapping_stay_unknown(self):
        self.assertIs(evaluate_ready_to_paint(paint_event(known=False)).state,
                      PaintTimingState.UNKNOWN_HOST_CLOCK)
        ledger = PaneDeliveryLedger(("one",))
        ref = scheduled(ledger, identity(owner_scope=owner_scope(host_process_id=os.getpid()),
            ready=ready(owner_scope(host_process_id=os.getpid()))))
        paint = PanePaintReturnReceipt(ref, HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid()), 1200, 1250)
        event = PaneDeliveryEvent(8, ref, Stage.PAINT_RETURNED, None, paint_return=paint)
        self.assertIs(evaluate_ready_to_paint(event).state, PaintTimingState.UNMAPPED_READY)

    def test_foreign_view_graph_run_and_activation_cannot_borrow_receipt(self):
        event = paint_event()
        changes = (dict(graph_instance_id="other"), dict(sequence=event.ref.sequence + 1),
                   dict(view=PaneDeliveryView.WATERFALL),
                   dict(identity=replace(event.ref.identity, pane_id="two")),
                   dict(identity=replace(event.ref.identity, host_run_serial=2)),
                   dict(identity=replace(event.ref.identity, host_activation_serial=2)))
        for change in changes:
            with self.subTest(change=change):
                changed = replace(event, ref=replace(event.ref, **change))
                self.assertIs(evaluate_ready_to_paint(changed).state, PaintTimingState.INVALID_BOUNDARY)

    def test_sweep_progress_terminal_and_density_keep_their_own_ready_interval(self):
        from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef
        variants = ((layer_frames.progress_frame(), PaneDeliveryView.SPECTRUM),
                    (layer_frames.terminal_frame(), PaneDeliveryView.WATERFALL),
                    (replace(layer_frames.density_frame(), receiver_id="RX2", native_accumulation_sequence=3,
                             accumulation_id="fake-live-session"),
                     PaneDeliveryView.PERSISTENCE))
        for frame, view in variants:
            with self.subTest(view=view):
                scope = (owner_scope(source_id=frame.source_id, receiver_id=frame.receiver_id,
                    configuration_generation=frame.config_generation, acquisition_epoch=frame.acquisition_epoch)
                    if view is PaneDeliveryView.PERSISTENCE else None)
                mapped, journal = packet(frame, scope=scope)
                original = bind(mapped, (journal,), admitted_density_scope=scope)
                clock = HostClockScope(HostClockKind.PERF_COUNTER_NS, original.ready.host_process_id)
                bounds = ReadyHostBounds(ReadyClockBracket(100, 1000, 1010),
                                         ReadyClockBracket(200, 1100, 1110), clock)
                original = replace(original, ready=replace(original.ready,
                    mapping=ReadyClockMapping.BOUNDED, host_bounds=bounds))
                ref = PaneDeliveryObligationRef("graph", 1, original, view)
                paint = PanePaintReturnReceipt(ref, clock, 1200, 1250)
                event = PaneDeliveryEvent(1, ref, Stage.PAINT_RETURNED, 9000, clock, paint)
                self.assertEqual(evaluate_ready_to_paint(event).after_return_sample_elapsed_ns, (140, 250))
                with self.assertRaises(ValueError):
                    replace(original.ready, host_bounds=replace(bounds,
                        host_clock=HostClockScope(HostClockKind.PERF_COUNTER_NS, clock.process_id + 1)))

    def test_clock_order_contradiction_is_not_repaired(self):
        for event in (paint_event(before=800, after=900), paint_event(recorded=1249)):
            result = evaluate_ready_to_paint(event)
            self.assertIs(result.state, PaintTimingState.CLOCK_ORDER_INVALID)
            self.assertIsNone(result.after_return_sample_elapsed_ns)
        event = paint_event()
        self.assertIs(evaluate_ready_to_paint(replace(event, host_clock=None, host_perf_ns=None)).state,
                      PaintTimingState.BOUNDED)
        self.assertIsNone(evaluate_ready_to_paint(replace(event, host_clock=None)).bookkeeping_after_sample_ns)

    def test_foreign_process_clock_declared_types_and_regression_refuse(self):
        event = paint_event()
        foreign = HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid() + 1)
        with self.assertRaises(ValueError):
            replace(event.paint_return, host_clock=foreign)
        with self.assertRaises(ValueError):
            replace(event.ref.identity.ready, host_bounds=replace(event.ref.identity.ready.host_bounds, host_clock=foreign))
        with self.assertRaises(ValueError):
            HostClockScope("python_perf_counter_ns", os.getpid())
        with self.assertRaises(ValueError):
            HostClockScope(HostClockKind.PERF_COUNTER_NS, True)
        for changes in (dict(before_paint_ns=True), dict(sampled_after_return_ns=1199),
                        dict(sampled_after_return_ns=1 << 63), dict(before_paint_ns=-1)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(event.paint_return, **changes)
        self.assertIs(evaluate_ready_to_paint(replace(event, host_clock=foreign)).state,
                      PaintTimingState.FOREIGN_HOST_CLOCK)

    def test_default_bridge_tags_retained_builtin_custom_clock_does_not(self):
        native = native_with_clock()
        bridge = NativeReadyBridge(native)
        bridge.begin()
        bridge.sample()
        mapping, bounds = bridge.map_native_clock(receipt())
        self.assertIs(mapping, ReadyClockMapping.BOUNDED)
        self.assertEqual(bounds.host_clock, HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid()))
        injected = NativeReadyBridge(native_with_clock(), host_clock=lambda: perf_counter_ns())
        injected.begin()
        injected.sample()
        self.assertIsNone(injected.map_native_clock(receipt())[1].host_clock)

    def test_default_ledger_retains_original_sample_separate_from_its_stamp(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = scheduled(ledger)
        clock = HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid())
        paint = PanePaintReturnReceipt(ref, clock, perf_counter_ns(), perf_counter_ns())
        self.assertTrue(ledger.note(ref, Stage.PAINT_RETURNED, paint_return=paint))
        snapshot = ledger.snapshot()
        event = snapshot.events[-1]
        self.assertIs(event.paint_return, paint)
        self.assertGreaterEqual(event.host_perf_ns, paint.sampled_after_return_ns)
        self.assertEqual(snapshot.host_clock, clock)
        self.assertEqual(snapshot.panes[0].terminal, 1)
        self.assertFalse(ledger.note(ref, Stage.PAINT_RETURNED, paint_return=paint))
        self.assertLessEqual(snapshot.retained_scalar_bytes, HOST_GRAPH_SCALAR_BUDGET)

    def test_custom_ledger_does_not_tag_custom_stamps(self):
        ledger = PaneDeliveryLedger(("one",), now_ns=lambda: 9000)
        ref = scheduled(ledger)
        paint = PanePaintReturnReceipt(ref, HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid()), 1200, 1250)
        self.assertTrue(ledger.note(ref, Stage.PAINT_RETURNED, paint_return=paint))
        event = ledger.snapshot().events[-1]
        self.assertIsNone(event.host_clock)
        self.assertIsNone(ledger.snapshot().host_clock)
        self.assertIsNone(evaluate_ready_to_paint(event).bookkeeping_after_sample_ns)

    def test_foreign_receipt_or_wrong_stage_refused_without_terminal_mutation(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = scheduled(ledger)
        foreign = paint_event().paint_return
        self.assertFalse(ledger.note(ref, Stage.PAINT_RETURNED, paint_return=foreign))
        self.assertFalse(ledger.note(ref, Stage.STOP_CLEARED, paint_return=foreign))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.panes[0].pending, 1)
        self.assertEqual(snapshot.panes[0].terminal, 0)
        self.assertEqual(snapshot.accounting_failures, 2)
        self.assertTrue(ledger.note(ref, Stage.STOP_CLEARED))

    def test_maximum_identity_paint_telemetry_obeys_existing_graph_budget(self):
        ledger = PaneDeliveryLedger(("one",))
        clock = HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid())
        for offer in range(1, 70):
            original = replace(timed_identity(offer=offer), physical_stream_resource_id="x" * 4096,
                               capture_id="y" * 4096, receiver_endpoint_id="z" * 4096)
            ref = scheduled(ledger, original)
            self.assertTrue(ledger.note(ref, Stage.PAINT_RETURNED,
                paint_return=PanePaintReturnReceipt(ref, clock, 1200, 1250)))
        snapshot = ledger.snapshot()
        self.assertLessEqual(snapshot.retained_scalar_bytes, HOST_GRAPH_SCALAR_BUDGET)
        self.assertGreater(snapshot.event_evictions + snapshot.event_drops, 0)
        self.assertEqual(snapshot.panes[0].terminal, 69)


if __name__ == "__main__":
    unittest.main()

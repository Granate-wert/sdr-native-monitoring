"""Retained host interval statistics, never hardware/Qt/DWM qualification."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import itertools
import unittest

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView
from sdr_monitor.domain.pane_paint_statistics import summarize_pane_paint_snapshot
from sdr_monitor.domain.pane_paint_timing import PanePaintReturnReceipt, PaintTimingState
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from tests.test_app07_pane_paint_timing import paint_event


def snapshot_with(*events, **changes):
    snapshot = PaneDeliveryLedger(("one",)).snapshot()
    if events:
        snapshot = replace(snapshot, graph_instance_id=events[0].ref.graph_instance_id)
    return replace(snapshot, events=tuple(events), **changes)


def distinct_event(event, sequence, **identity_changes):
    ref = replace(event.ref, sequence=sequence,
                  identity=replace(event.ref.identity, **identity_changes))
    paint = replace(event.paint_return, ref=ref) if event.paint_return else None
    return replace(event, sequence=sequence, ref=ref, paint_return=paint)


class PaintStatisticsTests(unittest.TestCase):
    def test_empty_window_is_unknown_not_zero_latency(self):
        report = summarize_pane_paint_snapshot(snapshot_with())
        self.assertEqual(report.groups, ())
        self.assertEqual(report.coverage.retained_events, 0)
        self.assertFalse(report.lifetime_complete)

    def test_original_interval_and_bookkeeping_not_midpoint(self):
        report = summarize_pane_paint_snapshot(snapshot_with(paint_event()))
        group, = report.groups
        self.assertEqual(group.timed_returns, 1)
        self.assertEqual(group.base_return.p50_ns, (90, 250))
        self.assertEqual(group.after_return_sample.p99_ns, (140, 250))
        self.assertEqual(group.state_counts, ((PaintTimingState.BOUNDED, 1),))
        self.assertFalse(report.lifetime_complete)
        with self.assertRaises(FrozenInstanceError):
            group.timed_returns = 2

    def test_signed_overlap_is_preserved_in_every_quantile(self):
        group, = summarize_pane_paint_snapshot(snapshot_with(paint_event(before=1050, after=1090))).groups
        self.assertEqual(group.base_return.p50_ns, (-60, 90))
        self.assertEqual(group.after_return_sample.p95_ns, (-20, 90))
        self.assertEqual(group.state_counts, ((PaintTimingState.ORDER_UNCERTAIN, 1),))

    def test_nearest_rank_integer_bounds_no_interpolation(self):
        first = paint_event()
        events = []
        for index in range(1, 11):
            event = distinct_event(first, index)
            paint = replace(event.paint_return, before_paint_ns=1200 + index * 100,
                            sampled_after_return_ns=1250 + index * 100)
            events.append(replace(event, paint_return=paint))
        group, = summarize_pane_paint_snapshot(snapshot_with(*events)).groups
        self.assertEqual(group.base_return.p50_ns, (590, 750))
        self.assertEqual(group.base_return.p95_ns, (1090, 1250))
        self.assertEqual(group.base_return.p99_ns, (1090, 1250))
        self.assertEqual(group.timed_returns, 10)

    def test_bounds_enclose_nearest_rank_for_all_possible_small_assignments(self):
        first = paint_event()
        events = tuple(distinct_event(first, i + 1) for i in range(3))
        events = tuple(replace(event, paint_return=replace(event.paint_return,
            before_paint_ns=1200 + i * 40, sampled_after_return_ns=1350 + i * 10))
            for i, event in enumerate(events))
        group, = summarize_pane_paint_snapshot(snapshot_with(*events)).groups
        intervals = [(90 + i * 40, 350 + i * 10) for i in range(3)]
        for assignment in itertools.product(*[(low, (low + high) // 2, high) for low, high in intervals]):
            for rank, bounds in ((1, group.base_return.p50_ns), (2, group.base_return.p95_ns),
                                 (2, group.base_return.p99_ns)):
                self.assertLessEqual(bounds[0], sorted(assignment)[rank])
                self.assertGreaterEqual(bounds[1], sorted(assignment)[rank])

    def test_unknown_invalid_and_missing_not_silently_removed(self):
        first = paint_event()
        unknown = distinct_event(paint_event(known=False), 2)
        unknown = replace(unknown, ref=replace(unknown.ref, graph_instance_id=first.ref.graph_instance_id))
        unknown = replace(unknown, paint_return=replace(unknown.paint_return, ref=unknown.ref))
        missing = replace(distinct_event(first, 3), paint_return=None)
        invalid = replace(distinct_event(first, 4), host_perf_ns=1)
        group, = summarize_pane_paint_snapshot(snapshot_with(first, unknown, missing, invalid)).groups
        self.assertEqual(group.paint_returns, 4)
        self.assertEqual(group.timed_returns, 1)
        self.assertEqual(sum(count for _, count in group.state_counts), 4)
        self.assertIn((PaintTimingState.UNKNOWN_HOST_CLOCK, 1), group.state_counts)
        self.assertIn((PaintTimingState.MISSING_PAINT_RECEIPT, 1), group.state_counts)
        self.assertIn((PaintTimingState.CLOCK_ORDER_INVALID, 1), group.state_counts)

    def test_all_unknown_group_has_none_quantiles(self):
        group, = summarize_pane_paint_snapshot(snapshot_with(replace(paint_event(), paint_return=None))).groups
        self.assertEqual(group.timed_returns, 0)
        self.assertIsNone(group.base_return)
        self.assertIsNone(group.after_return_sample)

    def test_exact_run_activation_source_endpoint_and_view_stay_separate(self):
        first = distinct_event(paint_event(), 1)
        events = [first]
        for index, changes in enumerate((dict(pane_id="two"), dict(host_run_serial=2),
            dict(host_activation_serial=2), dict(receiver_endpoint_id="rx2"),
            dict(physical_stream_resource_id="other"), dict(capture_id="next")), 2):
            events.append(distinct_event(first, index, **changes))
        view = distinct_event(first, 8)
        ref = replace(view.ref, view=PaneDeliveryView.WATERFALL)
        events.append(replace(view, ref=ref, paint_return=replace(view.paint_return, ref=ref)))
        report = summarize_pane_paint_snapshot(snapshot_with(*events))
        self.assertEqual(len(report.groups), 8)
        self.assertTrue(all(group.paint_returns == 1 for group in report.groups))

    def test_eviction_loss_duplicate_and_pending_counters_are_reported_not_filled(self):
        snap = snapshot_with(paint_event(), record_evictions=10, event_evictions=20, event_drops=3,
                             duplicate_events=4, accounting_failures=5, clock_failures=6)
        report = summarize_pane_paint_snapshot(snap)
        self.assertEqual(report.coverage.event_evictions, 20)
        self.assertEqual(report.coverage.event_drops, 3)
        self.assertEqual(report.coverage.record_evictions, 10)
        self.assertEqual(report.coverage.duplicate_events, 4)
        self.assertEqual(report.coverage.accounting_failures, 5)
        self.assertEqual(report.coverage.clock_failures, 6)
        self.assertEqual(report.coverage.panes, snap.panes)
        self.assertEqual(report.groups[0].paint_returns, 1)
        self.assertFalse(report.lifetime_complete)

    def test_foreign_graph_duplicate_event_or_double_paint_ref_is_explicit_refusal(self):
        first = paint_event()
        foreign = distinct_event(first, 2)
        foreign = replace(foreign, ref=replace(foreign.ref, graph_instance_id="foreign"))
        for events in ((first, first), (first, replace(first, sequence=2)), (first, foreign)):
            with self.subTest(events=len(events)), self.assertRaises(ValueError):
                summarize_pane_paint_snapshot(snapshot_with(*events))

    def test_nonpaint_events_are_population_not_latency(self):
        first = paint_event()
        pending = replace(distinct_event(first, 2), stage=Stage.UI_ADMITTED, paint_return=None)
        report = summarize_pane_paint_snapshot(snapshot_with(first, pending))
        self.assertEqual(report.coverage.retained_events, 2)
        self.assertEqual(report.groups[0].paint_returns, 1)

    def test_source_epoch_and_producer_scopes_are_not_pooled(self):
        first = distinct_event(paint_event(), 1)
        events = [first]
        for index, changes in enumerate((dict(acquisition_epoch=2), dict(source_id="another")), 2):
            old = first.ref.identity
            scope = replace(old.owner_scope, **changes)
            ready_changes = {"acquisition_epoch": scope.acquisition_epoch, "source_id": scope.source_id}
            changed = distinct_event(first, index, owner_scope=scope,
                                     ready=replace(old.ready, **ready_changes))
            events.append(changed)
        events.append(distinct_event(first, 4, ready=replace(first.ref.identity.ready, producer_instance_id=42)))
        self.assertEqual(len(summarize_pane_paint_snapshot(snapshot_with(*events)).groups), 4)

    def test_sweep_and_density_use_original_layer_ready_not_detector_time(self):
        from sdr_monitor.domain.analytical_ready import ReadyClockBracket, ReadyClockMapping, ReadyHostBounds
        from sdr_monitor.domain.host_clock import HostClockKind, HostClockScope
        from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryEvent, PaneDeliveryObligationRef
        from tests import test_app07_layer_ready_admission as frames
        from tests.test_app07_pane_layer_custody import packet, bind
        from tests.test_app07_pane_analytical_identity import owner_scope
        events = []
        variants = ((frames.progress_frame(), PaneDeliveryView.SPECTRUM),
                    (frames.terminal_frame(), PaneDeliveryView.SPECTRUM),
                    (replace(frames.density_frame(), receiver_id="RX2", native_accumulation_sequence=3,
                             accumulation_id="fake-live-session"), PaneDeliveryView.PERSISTENCE))
        for index, (frame, view) in enumerate(variants, 1):
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
            ref = PaneDeliveryObligationRef("same-graph", index, original, view)
            paint = PanePaintReturnReceipt(ref, clock, 1200, 1250)
            events.append(PaneDeliveryEvent(index, ref, Stage.PAINT_RETURNED, 9000, clock, paint))
        report = summarize_pane_paint_snapshot(snapshot_with(*events))
        self.assertEqual(len(report.groups), 3)
        self.assertEqual(len({group.scope.ready_kind for group in report.groups}), 3)
        self.assertTrue(all(group.base_return.p50_ns == (90, 250) for group in report.groups))

    def test_actual_session_report_does_not_enter_owner_or_lease(self):
        from tests.test_app07_pane_resource_session import FakeOwner, PaneResourceSessionTests
        from tests.test_app07_shared_capture_schedule import group, pane
        from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
        fixture = PaneResourceSessionTests()
        fixture.setUp()
        owner = FakeOwner("device")
        session = fixture.session((group("device", "rx"),),
                                  (pane("one", "rx", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),),
                                  {"device": owner})
        before = session.pane_delivery_ledger_snapshot()
        report = session.pane_paint_timing_summary()
        self.assertEqual(report.coverage.retained_events, 0)
        self.assertEqual(owner.events, [])
        self.assertFalse(owner.running)
        self.assertEqual(fixture.leases.active_resource_count, 0)
        self.assertEqual(before, session.pane_delivery_ledger_snapshot())

    def test_invalid_coverage_is_refused_not_corrected(self):
        for changes in (dict(event_drops=-1), dict(clock_failures=True), dict(supported=1)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                summarize_pane_paint_snapshot(snapshot_with(**changes))

    def test_unbounded_or_mutable_snapshot_refuses_before_statistics(self):
        from sdr_monitor.domain.pane_paint_statistics import (
            PAINT_SNAPSHOT_EVENT_LIMIT, PAINT_SNAPSHOT_RECORD_LIMIT,
        )
        from sdr_monitor.services.pane_delivery_ledger import EVENT_CAPACITY, RECORD_CAPACITY
        self.assertEqual(PAINT_SNAPSHOT_EVENT_LIMIT, EVENT_CAPACITY)
        self.assertEqual(PAINT_SNAPSHOT_RECORD_LIMIT, RECORD_CAPACITY)
        first = paint_event()
        for changes in (dict(events=[first]), dict(records=[]),
                        dict(events=(first,) * (EVENT_CAPACITY + 1)),
                        dict(records=(None,) * (RECORD_CAPACITY + 1)),
                        dict(panes=(None,) * 5), dict(views=(None,) * 13)):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                summarize_pane_paint_snapshot(replace(snapshot_with(first), **changes))

    def test_actual_ledger_snapshot_is_not_mutated_and_evicted_window_is_partial(self):
        from tests.test_app07_pane_paint_timing import scheduled, timed_identity
        from sdr_monitor.domain.host_clock import HostClockKind, HostClockScope
        import os
        ledger = PaneDeliveryLedger(("one",), now_ns=lambda: 9000)
        clock = HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid())
        for offer in range(1, 40):
            ref = scheduled(ledger, timed_identity(offer=offer))
            self.assertTrue(ledger.note(ref, Stage.PAINT_RETURNED,
                paint_return=PanePaintReturnReceipt(ref, clock, 1200, 1250)))
        before = ledger.snapshot()
        report = summarize_pane_paint_snapshot(before)
        self.assertEqual(before, ledger.snapshot())
        self.assertGreater(report.coverage.event_evictions, 0)
        self.assertLess(report.groups[0].paint_returns, before.panes[0].terminal)
        self.assertFalse(report.lifetime_complete)


if __name__ == "__main__":
    unittest.main()

"""Bounded scalar custody + actual graph admission, no physical/paint proof."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from concurrent.futures import ThreadPoolExecutor
import unittest

from sdr_monitor.domain.pane_analytical_identity import PaneAnalyticalIdentity
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.services.pane_delivery_ledger import (
    EVENT_CAPACITY, HOST_GRAPH_SCALAR_BUDGET, RECORD_CAPACITY, PaneDeliveryLedger,
)
from tests import test_app07_pane_analytical_identity as fixtures
from tests import test_app07_pane_resource_session as session_fixtures


def identity(pane="one", offer=1, **changes):
    scope = fixtures.owner_scope()
    values = dict(owner_scope=scope, ready=fixtures.ready(scope, offer=offer),
        physical_stream_resource_id="resource", capture_id="capture", receiver_endpoint_id="rx",
        pane_id=pane, host_run_serial=1, host_activation_serial=1)
    values.update(changes)
    return PaneAnalyticalIdentity(**values)


class PaneDeliveryLedgerTests(unittest.TestCase):
    def assert_conserved(self, snapshot):
        self.assertLessEqual(snapshot.retained_scalar_bytes, snapshot.reserved_bytes)
        self.assertLessEqual(len(snapshot.records), RECORD_CAPACITY)
        self.assertLessEqual(len(snapshot.events), EVENT_CAPACITY)
        for pane in snapshot.panes:
            self.assertEqual(pane.qualified_admissions,
                             pane.pending + pane.terminal + pane.untracked_admissions)
            self.assertGreaterEqual(pane.pending, 0)

    def test_same_offer_distinct_pane_obligations_no_fft_double_count(self):
        ledger = PaneDeliveryLedger(("one", "two"))
        first = ledger.admit("one", identity())
        second = ledger.admit("two", identity("two"))
        self.assertNotEqual(first.sequence, second.sequence)
        self.assertEqual(first.identity.ready, second.identity.ready)
        self.assertIsNone(ledger.admit("one", identity()))
        snapshot = ledger.snapshot()
        self.assertEqual([pane.qualified_admissions for pane in snapshot.panes], [1, 1])
        self.assertEqual(snapshot.panes[0].duplicate_admissions, 1)
        self.assert_conserved(snapshot)
        with self.assertRaises(FrozenInstanceError):
            first.sequence = 20

    def test_actual_transition_chain_terminal_once_and_foreign_graph_refuses(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", identity())
        foreign = PaneDeliveryLedger(("one",)).admit("one", identity())
        self.assertFalse(ledger.note(foreign, Stage.PREPARING))
        self.assertFalse(ledger.note(replace(ref, identity=identity(offer=2)), Stage.PREPARING))
        self.assertFalse(ledger.note(ref, Stage.PAINT_RETURNED))
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED, Stage.QUEUE_DRAINED,
                      Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED, Stage.PAINT_RETURNED):
            self.assertTrue(ledger.note(ref, stage))
        self.assertFalse(ledger.note(ref, Stage.PAINT_RETURNED))
        self.assertFalse(ledger.note(ref, Stage.STOP_CLEARED))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.panes[0].terminal, 1)
        self.assertEqual(snapshot.duplicate_events, 1)
        self.assertEqual(snapshot.accounting_failures, 4)
        self.assert_conserved(snapshot)

    def test_custody_stop_only_cancels_unclaimed_in_affected_resource(self):
        ledger = PaneDeliveryLedger(("one", "two", "three"))
        first = ledger.admit("one", identity())
        preparing = ledger.admit("two", identity("two"))
        other = ledger.admit("three", identity("three", physical_stream_resource_id="peer"))
        self.assertTrue(ledger.note(preparing, Stage.PREPARING))
        ledger.cancel_unclaimed("resource")
        states = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual(states[first], Stage.ADMISSION_CANCELLED)
        self.assertEqual(states[preparing], Stage.PREPARING)
        self.assertEqual(states[other], Stage.ADMITTED)
        self.assertTrue(ledger.note(preparing, Stage.PREPARATION_CANCELLED))
        self.assert_conserved(ledger.snapshot())

    def test_namespace_stale_changed_and_unsupported_are_not_synthetic_receipts(self):
        ledger = PaneDeliveryLedger(("one",))
        ledger.admit("one", identity(offer=5))
        self.assertIsNone(ledger.admit("one", identity(offer=4)))
        self.assertIsNone(ledger.admit("one", identity(offer=6, capture_id="foreign")))
        changed = identity(offer=5)
        self.assertIsNone(ledger.admit("one", replace(changed,
            ready=replace(changed.ready, ready_native_ns=100))))
        self.assertIsNone(ledger.admit("one", None))
        self.assertIsNone(ledger.admit("missing", identity()))
        self.assertFalse(ledger.note(None, Stage.QUEUED))
        self.assertFalse(ledger.note(ledger.snapshot().records[0].ref, "preparing"))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.panes[0].unqualified_deliveries, 1)
        self.assertEqual(snapshot.panes[0].qualified_admissions, 1)
        self.assert_conserved(snapshot)
        unsupported = PaneDeliveryLedger(tuple(f"pane-{i}" for i in range(5)))
        self.assertIsNone(unsupported.admit("pane-0", None))
        self.assertFalse(unsupported.snapshot().supported)

    def test_pressure_drops_telemetry_not_pending_custody(self):
        ledger = PaneDeliveryLedger(("one",))
        for offer in range(1, 700):
            ledger.admit("one", identity(offer=offer))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.panes[0].qualified_admissions, 699)
        self.assertGreater(snapshot.panes[0].untracked_admissions, 0)
        self.assertEqual(snapshot.panes[0].terminal, 0)
        self.assertEqual(snapshot.record_evictions, 0)
        self.assertGreater(snapshot.event_evictions + snapshot.event_drops, 0)
        self.assertIsNone(ledger.admit("one", identity(offer=699)))
        self.assert_conserved(ledger.snapshot())

    def test_terminal_eviction_and_same_offer_not_reminted_after_eviction(self):
        ledger = PaneDeliveryLedger(("one", "two"))
        old = ledger.admit("two", identity("two"))
        ledger.note(old, Stage.ADMISSION_CANCELLED)
        for offer in range(1, 700):
            ref = ledger.admit("one", identity(offer=offer))
            self.assertIsNotNone(ref)
            ledger.note(ref, Stage.ADMISSION_CANCELLED)
        snapshot = ledger.snapshot()
        self.assertGreater(snapshot.record_evictions, 0)
        self.assertFalse(ledger.note(old, Stage.ADMISSION_CANCELLED))
        self.assertIsNone(ledger.admit("two", identity("two")))
        self.assert_conserved(ledger.snapshot())

    def test_large_identity_budget_and_dedup_no_overflow_no_fabricated_cancellation(self):
        ledger = PaneDeliveryLedger(("one",))
        huge = identity(capture_id="x" * 4096, receiver_endpoint_id="y" * 4096,
                        physical_stream_resource_id="z" * 4096)
        for offer in range(1, 80):
            ledger.admit("one", replace(huge, ready=fixtures.ready(huge.owner_scope, offer=offer)))
        self.assert_conserved(ledger.snapshot())
        self.assertEqual(ledger.snapshot().reserved_bytes, HOST_GRAPH_SCALAR_BUDGET)

    def test_clock_failure_regression_explicit_none_cached_read_inert(self):
        values = iter((20, 19, None, 25))
        calls = []
        def clock():
            calls.append(1)
            return next(values)
        ledger = PaneDeliveryLedger(("one",), now_ns=clock)
        ref = ledger.admit("one", identity())
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED):
            self.assertTrue(ledger.note(ref, stage))
        snapshot = ledger.snapshot()
        self.assertEqual([event.host_perf_ns for event in snapshot.events], [20, None, None, 25])
        self.assertEqual(snapshot.clock_failures, 2)
        self.assertEqual(ledger.snapshot(), snapshot)
        self.assertEqual(len(calls), 4)
        self.assert_conserved(snapshot)

    def test_concurrent_same_custody_is_once_not_four_pending_subtractions(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", identity())
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = tuple(pool.map(lambda _: ledger.note(ref, Stage.PREPARING), range(16)))
        self.assertEqual(sum(results), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = tuple(pool.map(lambda _: ledger.note(ref, Stage.PREPARATION_FAILED), range(16)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(ledger.snapshot().duplicate_events, 30)
        self.assert_conserved(ledger.snapshot())


class PaneGraphObligationTests(unittest.TestCase):
    def make(self, shared=False):
        helper = fixtures.PaneAnalyticalIdentityTests(methodName="runTest")
        self.addCleanup(helper.doCleanups)
        return (*helper.make(shared), helper)

    def test_actual_shared_admission_mints_only_after_validation_and_no_remint(self):
        session, owner, activation, helper = self.make(shared=True)
        good = helper.bundle(owner)
        bad = helper.bundle(owner, replace(good.spectrum.detector_ready, owner_run_id="foreign"))
        self.assertEqual(session.accept_frame(activation, "rx", bad), ())
        self.assertEqual(session.pane_delivery_ledger_snapshot().records, ())
        items = session.accept_frame(activation, "rx", good)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item.obligation_ref.identity is item.analytical_identity for item in items))
        self.assertNotEqual(items[0].obligation_ref, items[1].obligation_ref)
        repeated = session.accept_frame(activation, "rx", good)
        self.assertTrue(all(item.obligation_ref is None for item in repeated))
        self.assertEqual(len(session.pane_delivery_ledger_snapshot().records), 2)
        with self.assertRaises(ValueError):
            replace(items[0], obligation_ref=items[1].obligation_ref)

    def test_stop_admitted_cancelled_prepared_retained_for_actual_ui_clear(self):
        session, owner, activation, helper = self.make(shared=True)
        items = session.accept_frame(activation, "rx", helper.bundle(owner))
        ref = items[0].obligation_ref
        self.assertTrue(session.record_pane_delivery_stage(ref, Stage.PREPARING))
        self.assertTrue(session.record_pane_delivery_stage(ref, Stage.PREPARED))
        session.stop_resource("device")
        snapshot = session.pane_delivery_ledger_snapshot()
        self.assertEqual([record.stage for record in snapshot.records],
                         [Stage.PREPARED, Stage.ADMISSION_CANCELLED])
        self.assertFalse(session.record_pane_delivery_stage(items[1].obligation_ref, Stage.PREPARING))
        self.assertTrue(session.record_pane_delivery_stage(ref, Stage.PREPARATION_CANCELLED))

    def test_restart_and_foreign_graph_never_settle_new_delivery(self):
        session, owner, activation, helper = self.make()
        old = session.accept_frame(activation, "rx", helper.bundle(owner))[0].obligation_ref
        session.stop_resource("device")
        session.rearm_resource("device")
        activation = session.start_resource("device")
        owner = session._runtimes["device"].owner
        fresh = session.accept_frame(activation, "rx", helper.bundle(owner))[0].obligation_ref
        self.assertEqual(fresh.identity.host_run_serial, 2)
        self.assertNotEqual(old, fresh)
        self.assertFalse(session.record_pane_delivery_stage(old, Stage.PREPARING))
        self.assertTrue(session.record_pane_delivery_stage(fresh, Stage.PREPARING))
        other, other_owner, other_activation, other_helper = self.make()
        foreign = other.accept_frame(other_activation, "rx", other_helper.bundle(other_owner))[0].obligation_ref
        self.assertFalse(session.record_pane_delivery_stage(foreign, Stage.PREPARING))

    def test_unknown_ready_accepted_but_native_spectrum_ticket_not_fabricated(self):
        session, owner, activation, helper = self.make()
        bundle = helper.bundle(owner, replace(fixtures.ready(owner.scope), owner_run_id=None))
        delivered = session.accept_frame(activation, "rx", bundle)[0]
        self.assertIsNone(delivered.obligation_ref)
        snapshot = session.pane_delivery_ledger_snapshot()
        self.assertEqual(snapshot.panes[0].unqualified_deliveries, 1)
        self.assertEqual(snapshot.records, ())
        self.assertTrue(owner.running)

    def test_poll_failure_closes_routing_and_cancels_only_unclaimed_delivery(self):
        session, owner, activation, helper = self.make()
        ref = session.accept_frame(activation, "rx", helper.bundle(owner))[0].obligation_ref
        owner.fail_poll = True
        with self.assertRaises(fixtures.PaneResourceError):
            session.poll_resource("device")
        self.assertEqual(session.pane_delivery_ledger_snapshot().records[0].stage, Stage.ADMISSION_CANCELLED)
        self.assertFalse(session.record_pane_delivery_stage(ref, Stage.PREPARING))

    def test_actual_sweep_admission_is_not_native_fft_or_fake_layer_ready(self):
        helper = session_fixtures.PaneResourceSessionTests(methodName="runTest")
        helper.setUp()
        owner = session_fixtures.FakeOwner("device")
        session = helper.session((session_fixtures.group("device", "rx"),),
            (session_fixtures.pane("one", "rx", 100e6, 108e6,
                session_fixtures.ReceiverBindingMode.DEDICATED_PARALLEL),), {"device": owner})
        self.addCleanup(session.stop_all)
        session.apply()
        activation = session.start_resource("device")
        delivered = session.accept_frame(activation, "rx", session_fixtures.frame(
            "device:source", owner.admission_epoch - 1, 100e6, 108e6))
        self.assertEqual(len(delivered), 1)
        self.assertIsNone(delivered[0].obligation_ref)
        snapshot = session.pane_delivery_ledger_snapshot()
        self.assertEqual(snapshot.panes[0].unqualified_deliveries, 1)
        self.assertEqual(snapshot.records, ())
        self.assertTrue(owner.running)


if __name__ == "__main__":
    unittest.main()

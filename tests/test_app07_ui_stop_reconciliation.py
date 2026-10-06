"""Confirmed receiver Stop and actual UI relinquishment are two boundaries."""
from __future__ import annotations

from dataclasses import replace
from threading import Event, get_ident
from typing import cast
import unittest
from unittest.mock import patch

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef, PaneDeliveryStage as Stage
from sdr_monitor.services.pane_delivery_ledger import (
    HOST_GRAPH_SCALAR_BUDGET, RECORD_CAPACITY, PaneDeliveryLedger,
)
from sdr_monitor.services.pane_resource_session import PaneResourceError
from tests import test_app07_pane_delivery_obligations as fixtures
from tests import test_app07_pane_resource_pump as pump_fixtures


def advance(ledger, ref, end=Stage.UI_ADMITTED):
    for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                  Stage.QUEUE_DRAINED, Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED):
        assert ledger.note(ref, stage)
        if stage is end:
            break


class UiStopReconciliationTests(unittest.TestCase):
    def assert_conserved(self, ledger):
        snapshot = ledger.snapshot()
        self.assertLessEqual(len(snapshot.records), RECORD_CAPACITY)
        self.assertLessEqual(snapshot.retained_scalar_bytes, HOST_GRAPH_SCALAR_BUDGET)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.duplicate_events, 0)
        for pane in snapshot.panes:
            self.assertEqual(pane.qualified_admissions, pane.pending + pane.terminal + pane.untracked_admissions)
        return snapshot

    def test_capture_all_ui_stages_not_only_refs_currently_attached_to_views(self):
        ledger = PaneDeliveryLedger(("one", "peer"))
        refs = tuple(ledger.admit("one", fixtures.identity(offer=offer)) for offer in range(1, 7))
        peer = ledger.admit("peer", fixtures.identity("peer", physical_stream_resource_id="peer-resource"))
        assert peer is not None and all(ref is not None for ref in refs)
        for ref, stage in zip(refs[:3], (Stage.QUEUE_DRAINED, Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED), strict=True):
            advance(ledger, ref, stage)
        advance(ledger, refs[3])
        self.assertTrue(ledger.note(refs[3], Stage.UI_REJECTED))
        self.assertTrue(ledger.note(refs[4], Stage.PREPARING))
        advance(ledger, peer)
        before = ledger.snapshot()
        captured = ledger.retained_ui_refs("resource")
        self.assertEqual(len(captured), 3)
        self.assertTrue(all(a is b for a, b in zip(captured, refs[:3], strict=True)))
        self.assertEqual(before, ledger.snapshot())
        self.assertEqual(ledger.reconcile_ui_stop_cleared(captured), captured)
        self.assertIs(ledger.delivery_stage_if_retained(peer), Stage.UI_ADMITTED)
        self.assertIs(ledger.delivery_stage_if_retained(refs[4]), Stage.PREPARING)
        self.assertIs(ledger.delivery_stage_if_retained(refs[5]), Stage.ADMITTED)
        self.assert_conserved(ledger)

    def test_stale_captured_boundary_does_not_reterminalize_returned_ref_or_newer_work(self):
        ledger = PaneDeliveryLedger(("one",))
        old = ledger.admit("one", fixtures.identity())
        assert old is not None
        advance(ledger, old)
        captured = ledger.retained_ui_refs("resource")
        self.assertTrue(ledger.note(old, Stage.PAINT_SCHEDULED))
        self.assertTrue(ledger.note(old, Stage.PAINT_RETURNED))
        newer = ledger.admit("one", fixtures.identity(offer=2, host_run_serial=2))
        assert newer is not None
        advance(ledger, newer)
        before = ledger.snapshot()
        self.assertEqual(ledger.reconcile_ui_stop_cleared(captured), ())
        self.assertEqual(before, ledger.snapshot())
        self.assertIs(ledger.delivery_stage_if_retained(newer), Stage.UI_ADMITTED)
        self.assert_conserved(ledger)

    def test_repeated_ack_and_repeated_original_in_one_batch_are_exactly_once(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", fixtures.identity())
        assert ref is not None
        advance(ledger, ref)
        self.assertEqual(ledger.reconcile_ui_stop_cleared((ref, ref)), (ref,))
        before = ledger.snapshot()
        self.assertEqual(ledger.reconcile_ui_stop_cleared((ref,)), ())
        self.assertEqual(before, ledger.snapshot())
        self.assertEqual(sum(event.stage is Stage.STOP_CLEARED for event in before.events), 1)
        self.assertEqual(before.panes[0].pending, 0)
        self.assert_conserved(ledger)

    def test_copy_foreign_and_changed_identity_cannot_settle_original(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", fixtures.identity())
        foreign = PaneDeliveryLedger(("one",)).admit("one", fixtures.identity())
        assert ref is not None and foreign is not None
        advance(ledger, ref)
        before = ledger.snapshot()
        self.assertEqual(ledger.reconcile_ui_stop_cleared((replace(ref), foreign,
            replace(ref, identity=fixtures.identity(offer=2)))), ())
        self.assertEqual(before, ledger.snapshot())
        self.assertIs(ledger.delivery_stage_if_retained(ref), Stage.UI_ADMITTED)

    def test_aged_terminal_missing_does_not_hide_low_pending_and_no_new_budget(self):
        ledger = PaneDeliveryLedger(("one", "two"))
        pending = ledger.admit("two", fixtures.identity("two"))
        old = ledger.admit("one", fixtures.identity())
        assert pending is not None and old is not None
        advance(ledger, pending)
        self.assertTrue(ledger.note(old, Stage.ADMISSION_CANCELLED))
        for offer in range(2, 700):
            ref = ledger.admit("one", fixtures.identity(offer=offer))
            assert ref is not None
            self.assertTrue(ledger.note(ref, Stage.ADMISSION_CANCELLED))
        self.assertGreater(ledger.snapshot().record_evictions, 0)
        self.assertIsNone(ledger.delivery_stage_if_retained(old))
        self.assertEqual(ledger.retained_ui_refs("resource"), (pending,))
        self.assertEqual(ledger.reconcile_ui_stop_cleared((old, pending)), (pending,))
        self.assert_conserved(ledger)

    def test_invalid_batch_is_refused_before_any_transition(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", fixtures.identity())
        assert ref is not None
        advance(ledger, ref)
        before = ledger.snapshot()
        for invalid in ([ref], (ref, None), (ref,) * (RECORD_CAPACITY + 1)):
            with self.assertRaises(ValueError):
                ledger.reconcile_ui_stop_cleared(cast(tuple[PaneDeliveryObligationRef, ...], invalid))
            self.assertEqual(before, ledger.snapshot())

    def test_no_recursive_equality_weight_snapshot_or_hardware_dependency(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", fixtures.identity())
        assert ref is not None
        advance(ledger, ref)
        with patch.object(PaneDeliveryObligationRef, "__eq__", side_effect=AssertionError("deep equality")), \
                patch.object(ledger, "snapshot", side_effect=AssertionError("full snapshot")), \
                patch("sdr_monitor.services.pane_delivery_ledger._weight", side_effect=AssertionError("recursive weight")):
            captured = ledger.retained_ui_refs("resource")
            self.assertIs(captured[0], ref)
            settled = ledger.reconcile_ui_stop_cleared(captured)
            self.assertIs(settled[0], ref)
        snapshot = self.assert_conserved(ledger)
        event = snapshot.events[-1]
        self.assertIs(event.stage, Stage.STOP_CLEARED)
        self.assertIsNone(event.paint_return)  # Not a successful paint receipt.

    def test_session_confirmed_stop_captures_unattached_unknown_but_does_not_clear_it(self):
        fixture = fixtures.PaneGraphObligationTests("run")
        self.addCleanup(fixture.doCleanups)
        session, owner, activation, helper = fixture.make()
        self.addCleanup(session.stop_all)
        delivery = session.accept_frame(activation, "rx", helper.bundle(owner))[0]
        ref = delivery.obligation_ref
        assert ref is not None
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            self.assertTrue(session.record_pane_delivery_stage(ref, stage))
        with session._delivery_ledger._lock:
            self.assertIsNone(session.pane_delivery_stage_if_retained(ref))
        with self.assertRaises(PaneResourceError):
            session.pane_ui_stop_refs_after_stop("device")
        session.stop_resource("device")
        captured = session.pane_ui_stop_refs_after_stop("device")
        self.assertEqual(captured, (ref,))
        self.assertIs(session.pane_delivery_stage_if_retained(ref), Stage.UI_ADMITTED)
        # The caller now actually relinquishes the captured UI references.
        # The acknowledgement is ledger-only, even after owners are closed.
        with patch.object(session, "_runtimes", {}):
            self.assertEqual(session.reconcile_ui_stop_cleared(captured), (ref,))
            self.assertEqual(session.reconcile_ui_stop_cleared(captured), ())
        self.assertIs(session.pane_delivery_stage_if_retained(ref), Stage.STOP_CLEARED)
        self.assert_conserved(session._delivery_ledger)

    def test_failed_stop_and_rearm_do_not_authorize_capture(self):
        fixture = fixtures.PaneGraphObligationTests("run")
        self.addCleanup(fixture.doCleanups)
        session, owner, _activation, _helper = fixture.make()
        self.addCleanup(session.stop_all)
        with patch.object(owner, "stop_capture_and_wait", side_effect=RuntimeError("mock stop refusal")):
            with self.assertRaises(PaneResourceError):
                session.stop_resource("device")
            with self.assertRaises(PaneResourceError):
                session.pane_ui_stop_refs_after_stop("device")
        session.stop_resource("device")
        self.assertEqual(session.pane_ui_stop_refs_after_stop("device"), ())
        session.rearm_resource("device")
        with self.assertRaises(PaneResourceError):
            session.pane_ui_stop_refs_after_stop("device")

    def test_shared_resource_capture_keeps_both_panes_and_never_samples_an_owner(self):
        fixture = fixtures.PaneGraphObligationTests("run")
        self.addCleanup(fixture.doCleanups)
        session, owner, activation, helper = fixture.make(shared=True)
        self.addCleanup(session.stop_all)
        deliveries = session.accept_frame(activation, "rx", helper.bundle(owner))
        refs = tuple(ref for delivery in deliveries
                     for ref in (delivery.obligation_ref, *delivery.layer_obligation_refs) if ref is not None)
        self.assertEqual(len(refs), 4)  # Two panes, Spectrum and Waterfall per pane.
        for ref in refs:
            for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                          Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
                self.assertTrue(session.record_pane_delivery_stage(ref, stage))
        session.stop_resource("device")
        before = session.pane_delivery_ledger_snapshot()
        with patch.object(owner, "stop_capture_and_wait", side_effect=AssertionError("hidden Stop")), \
                patch.object(session, "_clock_sample_s", side_effect=AssertionError("clock")), \
                patch.object(session, "pane_delivery_ledger_snapshot", side_effect=AssertionError("full snapshot")):
            captured = session.pane_ui_stop_refs_after_stop("device")
            self.assertEqual(len(captured), 4)
            self.assertEqual({ref.identity.pane_id for ref in captured}, {ref.identity.pane_id for ref in refs})
        self.assertEqual(before, session.pane_delivery_ledger_snapshot())
        self.assertEqual(len(session.reconcile_ui_stop_cleared(captured)), 4)
        self.assert_conserved(session._delivery_ledger)

    def test_already_done_stop_uses_existing_off_thread_worker_for_capture_and_ack(self):
        _layout, session, _leases, owners, _queue, pump = pump_fixtures.PaneResourcePumpTests.make_plan(1)
        session.apply()
        pump.activate()
        try:
            pump.stop_resource("device-1").result(timeout=3)
            stopped = pump.stop_resource("device-1")
            self.assertTrue(stopped.done())  # add_done_callback would be inline.
            calls = []
            capture = session.pane_ui_stop_refs_after_stop
            reconcile = session.reconcile_ui_stop_cleared

            def observed_capture(resource_id):
                calls.append(("capture", get_ident()))
                return capture(resource_id)

            def observed_reconcile(refs):
                calls.append(("ack", get_ident()))
                return reconcile(refs)

            with patch.object(session, "pane_ui_stop_refs_after_stop", observed_capture), \
                    patch.object(session, "reconcile_ui_stop_cleared", observed_reconcile):
                refs = pump.capture_ui_stop_refs("device-1").result(timeout=3)
                self.assertEqual(pump.reconcile_ui_stop_cleared("device-1", refs).result(timeout=3), ())
            self.assertEqual([kind for kind, _thread in calls], ["capture", "ack"])
            self.assertTrue(all(thread != get_ident() for _kind, thread in calls))
            self.assertTrue(all(thread == pump._workers["device-1"]._thread.ident for _kind, thread in calls))
            self.assertFalse(owners["device-1"].running)
            self.assertFalse(any(event[0] == "start" for event in owners["device-1"].events))
        finally:
            for future in pump.stop_all().values():
                future.result(timeout=3)
            pump.join_after_stop(3)

    def test_pending_stop_capture_blocks_start_and_join_and_does_not_accept_peer_batch(self):
        _layout, session, _leases, _owners, _queue, pump = pump_fixtures.PaneResourcePumpTests.make_plan(1)
        session.apply()
        pump.activate()
        entered, release = Event(), Event()
        capture = session.pane_ui_stop_refs_after_stop

        def held_capture(resource_id):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test-only capture wait was not released")
            return capture(resource_id)

        try:
            with self.assertRaises(RuntimeError):
                pump.capture_ui_stop_refs("device-1")  # Applied is not stopped.
            pump.stop_resource("device-1").result(timeout=3)
            with patch.object(session, "pane_ui_stop_refs_after_stop", held_capture):
                future = pump.capture_ui_stop_refs("device-1")
                self.assertTrue(entered.wait(3))
                self.assertFalse(future.cancel())
                with self.assertRaises(RuntimeError):
                    pump.start_resource("device-1")
                with self.assertRaises(RuntimeError):
                    pump.join_after_stop(3)
                release.set()
                self.assertEqual(future.result(timeout=3), ())
            peer = PaneDeliveryLedger(("peer",)).admit("peer", fixtures.identity("peer"))
            assert peer is not None
            with self.assertRaises(ValueError):
                pump.reconcile_ui_stop_cleared("device-1", (peer,))
        finally:
            release.set()
            for future in pump.stop_all().values():
                future.result(timeout=3)
            pump.join_after_stop(3)
        with self.assertRaises(RuntimeError):
            pump.capture_ui_stop_refs("device-1")


if __name__ == "__main__":
    unittest.main()

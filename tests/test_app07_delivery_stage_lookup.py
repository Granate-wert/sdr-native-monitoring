"""Exact retained-token status is not a high-water guess or RF authority."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import cast
import unittest
from unittest.mock import patch

from sdr_monitor.domain.pane_delivery_obligation import (
    PaneDeliveryObligationRef, PaneDeliveryStage as Stage,
)
from sdr_monitor.services.pane_delivery_ledger import (
    HOST_GRAPH_SCALAR_BUDGET, RECORD_CAPACITY, PaneDeliveryLedger,
)
from tests import test_app07_pane_delivery_obligations as fixtures

identity = fixtures.identity


class DeliveryStageLookupTests(unittest.TestCase):
    def test_low_pending_is_distinct_from_newer_terminal(self):
        ledger = PaneDeliveryLedger(("one",))
        refs = tuple(ledger.admit("one", identity(offer=offer)) for offer in range(1, 7))
        self.assertTrue(all(ref is not None for ref in refs))
        for ref in refs:
            assert ref is not None
            for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                          Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
                self.assertTrue(ledger.note(ref, stage))
        for index in (3, 5):
            self.assertTrue(ledger.note(refs[index], Stage.UI_REJECTED))
        before = ledger.snapshot()
        for index in (2, 4):
            self.assertIs(ledger.delivery_stage_if_retained(refs[index]), Stage.UI_ADMITTED)
        for index in (3, 5):
            self.assertIs(ledger.delivery_stage_if_retained(refs[index]), Stage.UI_REJECTED)
        self.assertEqual(before, ledger.snapshot())

    def test_exact_original_only_foreign_altered_and_equal_copy_are_unknown(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", identity())
        foreign = PaneDeliveryLedger(("one",)).admit("one", identity())
        assert ref is not None
        before = ledger.snapshot()
        self.assertIs(ledger.delivery_stage_if_retained(ref), Stage.ADMITTED)
        for candidate in (foreign, replace(ref), replace(ref, identity=identity(offer=2)),
                          cast(PaneDeliveryObligationRef, None)):
            self.assertIsNone(ledger.delivery_stage_if_retained(candidate))
        self.assertEqual(before, ledger.snapshot())

    def test_pending_survives_terminal_eviction_without_new_storage(self):
        ledger = PaneDeliveryLedger(("one", "two"))
        pending = ledger.admit("two", identity("two"))
        old = ledger.admit("one", identity())
        assert pending is not None and old is not None
        self.assertTrue(ledger.note(old, Stage.ADMISSION_CANCELLED))
        for offer in range(2, 700):
            ref = ledger.admit("one", identity(offer=offer))
            assert ref is not None
            self.assertTrue(ledger.note(ref, Stage.ADMISSION_CANCELLED))
        before = ledger.snapshot()
        self.assertGreater(before.record_evictions, 0)
        self.assertIs(ledger.delivery_stage_if_retained(pending), Stage.ADMITTED)
        self.assertIsNone(ledger.delivery_stage_if_retained(old))
        self.assertEqual(before, ledger.snapshot())
        self.assertLessEqual(len(before.records), RECORD_CAPACITY)
        self.assertLessEqual(before.retained_scalar_bytes, HOST_GRAPH_SCALAR_BUDGET)

    def test_contended_read_returns_unknown_without_waiting_or_mutation(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", identity())
        assert ref is not None
        before = ledger.snapshot()
        with ThreadPoolExecutor(max_workers=1) as worker:
            with ledger._lock:
                # Completion while the caller still holds the lock proves a
                # nonwaiting read, NOT a numerical UI/performance bound.
                result = worker.submit(ledger.delivery_stage_if_retained, ref).result(timeout=2)
                self.assertIsNone(result)
        self.assertEqual(before, ledger.snapshot())
        self.assertIs(ledger.delivery_stage_if_retained(ref), Stage.ADMITTED)

    def test_no_clock_snapshot_weight_or_recursive_reference_comparison(self):
        calls = []

        def clock():
            calls.append(1)
            return 10

        ledger = PaneDeliveryLedger(("one",), now_ns=clock)
        ref = ledger.admit("one", identity())
        assert ref is not None
        before = ledger.snapshot()
        with patch.object(PaneDeliveryObligationRef, "__eq__", side_effect=AssertionError("deep equality")), \
                patch.object(ledger, "snapshot", side_effect=AssertionError("snapshot allocation")), \
                patch("sdr_monitor.services.pane_delivery_ledger._weight",
                      side_effect=AssertionError("recursive budget traversal")):
            for _ in range(20):
                self.assertIs(ledger.delivery_stage_if_retained(ref), Stage.ADMITTED)
                self.assertIsNone(ledger.delivery_stage_if_retained(replace(ref)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(before, ledger.snapshot())

    def test_observation_is_not_a_transition_or_reservation(self):
        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", identity())
        assert ref is not None
        self.assertIs(ledger.delivery_stage_if_retained(ref), Stage.ADMITTED)
        self.assertTrue(ledger.note(ref, Stage.ADMISSION_CANCELLED))
        self.assertIs(ledger.delivery_stage_if_retained(ref), Stage.ADMISSION_CANCELLED)
        self.assertEqual(ledger.snapshot().panes[0].terminal, 1)

    def test_real_session_facade_uses_same_ledger_and_never_owner_poll(self):
        fixture = fixtures.PaneGraphObligationTests("run")
        session, owner, activation, helper = fixture.make()
        try:
            ref = session.accept_frame(activation, "rx", helper.bundle(owner))[0].obligation_ref
            assert ref is not None
            before = session.pane_delivery_ledger_snapshot()
            with patch.object(session, "_runtimes", {}):
                self.assertIs(session.pane_delivery_stage_if_retained(ref), Stage.ADMITTED)
                self.assertEqual(before, session.pane_delivery_ledger_snapshot())
            session.stop_resource("device")
            self.assertIs(session.pane_delivery_stage_if_retained(ref), Stage.ADMISSION_CANCELLED)
        finally:
            session.stop_all()


if __name__ == "__main__":
    unittest.main()

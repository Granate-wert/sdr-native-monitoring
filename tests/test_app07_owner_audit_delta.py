"""Bounded audit delta vs pre-optimization oracle; software, not RF proof."""
from collections import Counter
from dataclasses import dataclass, replace
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdr_monitor.domain.analytical_journal import (
    JournalEvent, JournalEventKind, JournalState, OwnerIdCoverage, OwnerIdCoverageState,
)
from sdr_monitor.services import native_owner_journal as journal_module
from sdr_monitor.services.owner_event_audit import OwnerEventAudit
from tests.test_app07_owner_events import JournalV2, KindV2, module_v2
from tests.test_app07_owner_journal import scope
from tests.test_app07_owner_journal_sizes import oracle


OWNER_KINDS = frozenset(list(JournalEventKind)[4:])


class OracleAudit:
    """Pre-OP02 retained-state scan contract, intentionally no delta/cache."""
    def __init__(self):
        self.offers = {}
        self.failures = 0
        self.capacity_exceeded = False

    def consume(self, events):
        for event in events:
            previous = self.offers.get(event.offer_sequence)
            if event.kind is JournalEventKind.OFFERED:
                if previous is not None:
                    self.failures += 1
                elif len(self.offers) == 256:
                    self.capacity_exceeded = True
                else:
                    self.offers[event.offer_sequence] = (event, event.kind)
                continue
            if previous is None:
                self.failures += 1
                continue
            original, state = previous
            same_ref = (event.producer_instance_id == original.producer_instance_id
                and event.configuration_generation == original.configuration_generation
                and event.ready_native_ns == original.ready_native_ns
                and event.clock_regressed == original.clock_regressed)
            allowed = ((state is JournalEventKind.OFFERED and event.kind in (
                JournalEventKind.HANDED_OFF, JournalEventKind.SUPERSEDED, JournalEventKind.CANCELLED))
                or (state is JournalEventKind.HANDED_OFF and event.kind in OWNER_KINDS))
            if not same_ref or not allowed:
                self.failures += 1
                continue
            self.offers[event.offer_sequence] = (original, event.kind)

    def snapshot(self, counters, presentation, *, stopped=False, host_evictions=0, evidence_failed=False):
        if counters is None or counters.event_contract_version != 2 or presentation is None:
            return OwnerIdCoverage()
        states = tuple(state for _, state in self.offers.values())
        retired = sum(state is not JournalEventKind.OFFERED for state in states)
        owner = sum(state in OWNER_KINDS for state in states)
        state = OwnerIdCoverageState.ACTIVE
        incomplete = (self.failures or self.capacity_exceeded or counters.events_lost or host_evictions
            or presentation.accounting_failures or evidence_failed)
        if stopped or incomplete:
            state = OwnerIdCoverageState.INCOMPLETE
        if stopped and not incomplete and (
                counters.outstanding == 0 and counters.events_pending == 0
                and len(states) == retired == counters.offered and owner == counters.handed_off
                and sum(item is JournalEventKind.SUPERSEDED for item in states) == counters.producer_superseded
                and sum(item is JournalEventKind.CANCELLED for item in states) == counters.producer_cancelled
                and owner == counters.owner_disposition_events == presentation.classified_handoffs
                and all(sum(item is kind for item in states) == getattr(presentation, name) for kind, name in (
                    (JournalEventKind.OWNER_FORWARDED, "forwarded"),
                    (JournalEventKind.OWNER_SUPERSEDED, "superseded"),
                    (JournalEventKind.OWNER_COALESCED, "coalesced"),
                    (JournalEventKind.OWNER_CANCELLED, "cancelled"),
                    (JournalEventKind.OWNER_CADENCE_SUPPRESSED, "cadence_suppressed")))):
            state = OwnerIdCoverageState.COMPLETE
        return OwnerIdCoverage(state, len(states), retired, owner, self.failures, self.capacity_exceeded)


class AuditDeltaTests(unittest.TestCase):
    def make(self, reserved=0):
        journal = journal_module.NativeOwnerJournal(module_v2(), 4096, reserved_host_bytes=reserved)
        journal.begin(scope())
        return journal, JournalV2()

    def event(self, offer, kind, **changes):
        return replace(JournalEvent(event_sequence=offer, producer_instance_id=1,
            offer_sequence=offer, configuration_generation=5, ready_native_ns=10000 + offer,
            clock_regressed=False, kind=kind), **changes)

    def assert_audit(self, actual, expected, counters, presentation):
        states = Counter(state for _, state in expected.offers.values())
        self.assertEqual(actual._state_counts, {kind: states[kind] for kind in JournalEventKind})
        self.assertEqual(actual.scalar_payload, tuple(expected.offers.values()))
        self.assertEqual(actual.scalar_payload_bytes, oracle(actual.scalar_payload))
        self.assertEqual(sum(actual._state_counts.values()), len(actual.scalar_payload))
        for stopped in (False, True):
            for host_evictions, failed in ((0, False), (1, False), (0, True)):
                kwargs = dict(stopped=stopped, host_evictions=host_evictions, evidence_failed=failed)
                self.assertEqual(actual.snapshot(counters, presentation, **kwargs),
                    expected.snapshot(counters, presentation, **kwargs))

    def test_every_valid_kind_non_fifo_and_every_prefix_matches_scan_oracle(self):
        journal, native = self.make()
        for kind in tuple(KindV2)[4:]:
            native.offer(decision=kind)
        native.offer(retire=KindV2.ProducerSuperseded)
        native.offer(retire=KindV2.ProducerCancelled)
        journal.drain(native.read)
        final = journal.current()
        events = final.events
        # Both FIFO and all offers -> reverse retirement -> reverse owner order.
        order = (events, tuple(sorted(events, key=lambda item: (
            0 if item.kind is JournalEventKind.OFFERED else
            1 if item.kind in (JournalEventKind.HANDED_OFF, JournalEventKind.SUPERSEDED,
                JournalEventKind.CANCELLED) else 2, -item.offer_sequence))))
        for sequence in order:
            actual, expected = OwnerEventAudit(), OracleAudit()
            for event in sequence:
                before = actual.payload_revision
                actual.consume((event,))
                expected.consume((event,))
                self.assertEqual(actual.payload_revision, before + 1)
                self.assert_audit(actual, expected, final.counters, final.native_presentation)
            self.assertIs(actual.snapshot(final.counters, final.native_presentation,
                stopped=True).state, OwnerIdCoverageState.COMPLETE)

    def test_duplicates_unknown_refs_illegal_transitions_do_not_invalidate_payload(self):
        journal, native = self.make()
        native.offer(2)
        journal.drain(native.read)
        value = journal.current()
        actual, expected = OwnerEventAudit(), OracleAudit()
        offered = self.event(1, JournalEventKind.OFFERED)
        sequence = (offered, offered,
            self.event(99, JournalEventKind.HANDED_OFF),
            self.event(1, JournalEventKind.OWNER_FORWARDED),
            self.event(1, JournalEventKind.HANDED_OFF, producer_instance_id=2),
            self.event(1, JournalEventKind.HANDED_OFF, configuration_generation=6),
            self.event(1, JournalEventKind.HANDED_OFF, ready_native_ns=999),
            self.event(1, JournalEventKind.HANDED_OFF, clock_regressed=True),
            self.event(1, JournalEventKind.HANDED_OFF),
            self.event(1, JournalEventKind.OWNER_COALESCED),
            self.event(1, JournalEventKind.OWNER_CANCELLED))
        for event in sequence:
            before = dict(expected.offers)
            revision = actual.payload_revision
            actual.consume((event,))
            expected.consume((event,))
            self.assertEqual(actual.payload_revision, revision + int(before != expected.offers))
            self.assert_audit(actual, expected, value.counters, value.native_presentation)
        self.assertEqual(actual._failures, 8)

    def test_overflow_never_evicts_or_fabricates_complete_and_zero_batch_no_revision(self):
        journal, native = self.make()
        native.offer(100)
        journal.drain(native.read)
        value = journal.current()
        actual, expected = OwnerEventAudit(), OracleAudit()
        for offer in range(1, 301):
            event = self.event(offer, JournalEventKind.OFFERED)
            actual.consume((event,))
            expected.consume((event,))
            self.assert_audit(actual, expected, value.counters, value.native_presentation)
        self.assertEqual(actual.payload_revision, 256)
        self.assertEqual(len(actual.scalar_payload), 256)
        self.assertTrue(actual._capacity_exceeded)
        actual.consume(())
        self.assertEqual(actual.payload_revision, 256)

    def test_counter_loss_pending_failure_and_unsupported_are_checked_on_every_snapshot(self):
        journal, native = self.make()
        native.offer()
        journal.drain(native.read)
        value = journal.current()
        actual, expected = journal._id_audit, OracleAudit()
        expected.consume(value.events)
        self.assert_audit(actual, expected, value.counters, value.native_presentation)
        variants = ((None, value.native_presentation), (value.counters, None),
            (value.counters, replace(value.native_presentation, accounting_failures=1)),
            (replace(value.counters, events_lost=1, events_drained=2,
                first_lost_event_sequence=1, last_lost_event_sequence=1), value.native_presentation),
            (replace(value.counters, events_pending=1, events_drained=2), value.native_presentation),
            (replace(value.counters, event_contract_version=1, owner_disposition_events=0,
                events_generated=2, events_drained=2), value.native_presentation),
            (value.counters, replace(value.native_presentation, forwarded=0, coalesced=1)))
        for counters, presentation in variants:
            self.assert_audit(actual, expected, counters, presentation)
        # Same audit revision, different counter snapshot: never cache COMPLETE.
        self.assertIs(actual.snapshot(value.counters, value.native_presentation,
            stopped=True).state, OwnerIdCoverageState.COMPLETE)
        self.assertIs(actual.snapshot(value.counters, value.native_presentation,
            stopped=True, evidence_failed=True).state, OwnerIdCoverageState.INCOMPLETE)

    def test_full256_empty_polls_no_event_resizing_or_payload_materialization(self):
        journal, native = self.make()
        native.offer(256)
        for _ in range(3):
            journal.drain(native.read)
        self.assertEqual(journal._size_charge.audit_revision, 768)
        counts = Counter()
        original = journal_module._scalar_bytes
        def counted(value):
            counts[type(value)] += 1
            return original(value)
        with patch.object(journal_module, "_scalar_bytes", counted), patch.object(
                OwnerEventAudit, "scalar_payload", new_callable=unittest.mock.PropertyMock,
                side_effect=AssertionError("unchanged audit payload was materialized")):
            for _ in range(8):
                journal.drain(native.read)
            journal.finish(native.read)
        self.assertEqual(counts[JournalEvent], 0)
        self.assertEqual(journal.current().drain_failures, 0)
        self.assertIs(journal.current().state, JournalState.FINAL)
        # Host evictions must still mark incomplete, despite no failures.
        self.assertIs(journal.current().owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)

    def test_payload_bytes_exact_and_metadata_additional_for_each_changed_batch(self):
        for reservation in (0, journal_module.HOST_SCALAR_BUDGET // 4):
            journal, native = self.make(reservation)
            for _ in range(20):
                native.offer()
                journal.drain(native.read)
                audit = journal._id_audit
                charge = journal._charge_for(journal.current())
                self.assertEqual(charge.audit_revision, audit.payload_revision)
                self.assertEqual(charge.audit_bytes, oracle(audit.scalar_payload))
                old_index = (sys.getsizeof(audit._offers) + sys.getsizeof(audit._failures)
                    + sys.getsizeof(audit._capacity_exceeded))
                self.assertGreater(audit.index_storage_bytes, old_index)
                self.assertEqual(len(audit._state_counts), len(JournalEventKind))
                original_payload = (2 * oracle(journal.current()) + oracle(audit.scalar_payload)
                    + old_index + journal_module.BATCH_CAPACITY * 512)
                self.assertGreater(journal._budget_bytes(journal.current(), charge), original_payload)
            self.assertEqual(journal._host_budget, journal_module.HOST_SCALAR_BUDGET - reservation)

    def test_untyped_string_alias_fallback_keeps_original_identity_semantics(self):
        journal, native = self.make()
        native.offer(retire=KindV2.ProducerSuperseded)
        journal.drain(native.read)
        value = journal.current()
        actual, expected = OwnerEventAudit(), OracleAudit()
        event = self.event(1, JournalEventKind.OFFERED)
        alias = SimpleNamespace(producer_instance_id=1, offer_sequence=1,
            configuration_generation=5, ready_native_ns=10001, clock_regressed=False,
            kind=str(JournalEventKind.SUPERSEDED))
        actual.consume((event, alias))
        expected.consume((event, alias))
        self.assertFalse(actual.payload_cacheable)
        self.assertEqual(actual.snapshot(value.counters, value.native_presentation, stopped=True),
            expected.snapshot(value.counters, value.native_presentation, stopped=True))
        self.assertIs(actual.snapshot(value.counters, value.native_presentation,
            stopped=True).state, OwnerIdCoverageState.INCOMPLETE)

    def test_mutable_subclass_audit_payload_falls_back_and_remeasures(self):
        @dataclass(frozen=True)
        class Extended(JournalEvent):
            child: object = None
        journal, _ = self.make()
        child = []
        event = Extended(1, 1, 1, 5, 10001, False, JournalEventKind.OFFERED, child)
        journal._id_audit.consume((event,))
        self.assertFalse(journal._id_audit.payload_cacheable)
        self.assertIsNone(journal._id_audit.scalar_payload_bytes)
        first = journal._charge_for(journal.current())
        self.assertIsNone(first.audit_bytes)
        first_bytes = journal._budget_bytes(journal.current(), first)
        child.extend(range(1000))
        second = journal._charge_for(journal.current())
        self.assertIsNone(second.audit_bytes)
        self.assertEqual(first.audit_revision, second.audit_revision)
        self.assertGreater(journal._budget_bytes(journal.current(), second), first_bytes)

    def test_integer_width_clock_and_kind_size_deltas_equal_recursive_oracle(self):
        actual = OwnerEventAudit()
        for n in (1, (1 << 30) - 1, 1 << 30, (1 << 60) - 1, 1 << 60, (1 << 64) - 1):
            offered = JournalEvent(n, n, n, n, -(1 << 63), False, JournalEventKind.OFFERED)
            actual.consume((offered,))
            self.assertEqual(actual.scalar_payload_bytes, oracle(actual.scalar_payload))
            for kind in (JournalEventKind.HANDED_OFF, JournalEventKind.OWNER_CADENCE_SUPPRESSED):
                actual.consume((replace(offered, kind=kind),))
                self.assertEqual(actual.scalar_payload_bytes, oracle(actual.scalar_payload))
        self.assertEqual(actual.payload_revision, 18)
        self.assertEqual(actual._failures, 0)

    def test_new_audit_start_after_terminal_resets_counts_charge_and_qualifies_again(self):
        journal, native = self.make()
        for run in range(10):
            native.offer()
            journal.finish(native.read)
            self.assertIs(journal.current().owner_id_coverage.state, OwnerIdCoverageState.COMPLETE)
            old = journal._id_audit
            journal.prepare()
            journal.begin(scope(run=f"new-{run}"))
            self.assertIsNot(journal._id_audit, old)
            self.assertEqual(journal._id_audit.payload_revision, 0)
            self.assertTrue(journal._id_audit.payload_cacheable)
            self.assertEqual(journal._size_charge.audit_revision, 0)
            self.assertEqual(journal._size_charge.audit_bytes, oracle(()))
            self.assertTrue(all(value == 0 for value in journal._id_audit._state_counts.values()))
            self.assertLessEqual(len(journal._size_charge.history_bytes), 4)
            native = JournalV2()

    def test_threshold_refusal_keeps_truthful_audit_and_does_not_commit_new_snapshot(self):
        journal, native = self.make()
        native.offer()
        journal.drain(native.read)
        old = journal.current()
        charge = journal._charge_for(old)
        # Empty poll has identical-sized counters/coverage; metadata included.
        size = journal._budget_bytes(old, charge)
        journal._host_budget = size - 1
        journal.drain(native.read)
        self.assertIs(journal.current().state, JournalState.INCOMPLETE)
        self.assertEqual(journal.current().drain_failures, 1)
        self.assertIs(journal.current().events, old.events)
        self.assertEqual(journal._size_charge.audit_bytes, oracle(journal._id_audit.scalar_payload))
        self.assertIs(journal.current().owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)


if __name__ == "__main__":
    unittest.main()

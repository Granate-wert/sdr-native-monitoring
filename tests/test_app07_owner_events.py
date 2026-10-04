"""Original owner IDs in bounded protocol2 evidence; mock, not RF/paint."""
from dataclasses import replace
from enum import Enum
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.analytical_journal import (
    JournalCounters, JournalState, OwnerIdCoverageState,
)
from sdr_monitor.services.native_owner_journal import EVENT_CAPACITY, NativeOwnerJournal, owner_journal_capacity
from tests.test_app07_owner_journal import Journal, protocol, scope


class KindV2(Enum):
    Offered = 1
    HandedOff = 2
    ProducerSuperseded = 3
    ProducerCancelled = 4
    OwnerForwarded = 5
    OwnerSuperseded = 6
    OwnerCoalesced = 7
    OwnerCancelled = 8
    OwnerCadenceSuppressed = 9


_FIELDS = ("forwarded", "superseded", "coalesced", "cancelled", "cadence_suppressed")
_KINDS = tuple(KindV2)[4:]


class JournalV2(Journal):
    def __init__(self):
        super().__init__()
        self.owner_counts = dict.fromkeys(_FIELDS, 0)

    def offer(self, count=1, *, decision=KindV2.OwnerForwarded, retire=KindV2.HandedOff):
        for _ in range(count):
            self.offered += 1
            self._event(self.offered, KindV2.Offered, 10000 + self.offered)
            self._event(self.offered, retire, 10000 + self.offered)
            if retire is KindV2.HandedOff:
                self.handed += 1
                if decision is not None:
                    self.owner(self.offered, decision)
            elif retire is KindV2.ProducerSuperseded:
                self.superseded += 1
            else:
                self.cancelled += 1

    def owner(self, offer, kind, *, ready=None):
        self.owner_counts[_FIELDS[_KINDS.index(kind)]] += 1
        self._event(offer, kind, 10000 + offer if ready is None else ready)

    def read(self, count):
        batch = super().read(count)
        batch.summary.owner_disposition_events = sum(self.owner_counts.values())
        batch.summary.presentation = SimpleNamespace(supported=True, accounting_failures=0, **self.owner_counts)
        return batch


def module_v2():
    module = protocol()
    module.OWNER_ANALYTICAL_READY_CONTRACT_VERSION = 2
    module.OWNER_PRESENTATION_DISPOSITION_CONTRACT_VERSION = 1
    module.AnalyticalReadyEventKind = KindV2
    return module


class OwnerEventsTests(unittest.TestCase):
    def make(self):
        consumer = NativeOwnerJournal(module_v2(), EVENT_CAPACITY)
        consumer.begin(scope())
        return consumer, JournalV2()

    def test_strict_version_admission_and_legacy_not_id_complete(self):
        self.assertEqual(owner_journal_capacity(module_v2()), EVENT_CAPACITY)
        for version in (True, 3, "2"):
            module = module_v2()
            module.OWNER_ANALYTICAL_READY_CONTRACT_VERSION = version
            self.assertEqual(owner_journal_capacity(module), 0)
        module = protocol()
        module.OWNER_ANALYTICAL_READY_CONTRACT_VERSION = 2
        self.assertEqual(owner_journal_capacity(module), 0)  # Missing enum/owner protocol
        module = module_v2()
        module.OWNER_PRESENTATION_DISPOSITION_CONTRACT_VERSION = True
        self.assertEqual(owner_journal_capacity(module), 0)
        consumer = NativeOwnerJournal(protocol(), EVENT_CAPACITY)
        consumer.begin(scope())
        old = Journal()
        old.offer()
        consumer.finish(old.read)
        self.assertIs(consumer.current().owner_id_coverage.state, OwnerIdCoverageState.UNSUPPORTED)

    def test_all_owner_kinds_and_producer_retirements_original_ids(self):
        consumer, native = self.make()
        for kind in _KINDS:
            native.offer(decision=kind)
        native.offer(retire=KindV2.ProducerSuperseded)
        native.offer(retire=KindV2.ProducerCancelled)
        consumer.finish(native.read)
        value = consumer.current()
        self.assertIs(value.state, JournalState.FINAL)
        self.assertIs(value.owner_id_coverage.state, OwnerIdCoverageState.COMPLETE)
        self.assertEqual((value.owner_id_coverage.retained_offers, value.owner_id_coverage.owner_dispositions), (7, 5))
        self.assertEqual((value.counters.events_generated, value.counters.owner_disposition_events), (19, 5))
        for i in range(5):
            a, b, c = value.events[3*i:3*i+3]
            self.assertEqual((a.offer_sequence, a.ready_native_ns), (c.offer_sequence, c.ready_native_ns))
            self.assertEqual(a.ready_native_ns, b.ready_native_ns)
        with self.assertRaises(ValueError):
            replace(value.counters, owner_disposition_events=0)

    def test_non_fifo_owner_dispositions_and_inflight_not_complete(self):
        consumer, native = self.make()
        native.offer(3, decision=None)
        consumer.drain(native.read)
        self.assertIs(consumer.current().owner_id_coverage.state, OwnerIdCoverageState.ACTIVE)
        native.owner(3, KindV2.OwnerForwarded)
        native.owner(1, KindV2.OwnerCoalesced)
        native.owner(2, KindV2.OwnerSuperseded)
        consumer.finish(native.read)
        # Replaced event window remains incomplete, even with consistent native scalar counts.
        self.assertIs(consumer.current().owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)
        self.assertEqual(consumer.current().owner_id_coverage.owner_dispositions, 3)

    def test_duplicate_terminal_does_not_settle_another_offer(self):
        consumer, native = self.make()
        native.offer(2, decision=None)
        native.owner(1, KindV2.OwnerForwarded)
        native.owner(1, KindV2.OwnerCancelled)  # scalar total2, offer2 remains unsettled
        consumer.finish(native.read)
        value = consumer.current()
        self.assertEqual(value.native_owner_handoffs_unclassified, 0)
        self.assertIs(value.owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)
        self.assertEqual((value.owner_id_coverage.owner_dispositions, value.owner_id_coverage.transition_failures), (1, 1))

    def test_native_terminal_with_missing_owner_decision_is_not_id_complete(self):
        consumer, native = self.make()
        native.offer(2, decision=None)
        consumer.finish(native.read)
        value = consumer.current()
        self.assertIs(value.state, JournalState.FINAL)  # Confirmed native terminal, not custody completeness.
        self.assertIs(value.owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)
        self.assertEqual(value.native_owner_handoffs_unclassified, 2)
        self.assertEqual(value.owner_id_coverage.owner_dispositions, 0)

    def test_mismatched_ref_and_unknown_id_are_not_repaired(self):
        for operation in ("ready", "unknown"):
            with self.subTest(operation=operation):
                consumer, native = self.make()
                native.offer(2, decision=None)
                native.owner(1 if operation == "ready" else 99, KindV2.OwnerForwarded,
                             ready=999 if operation == "ready" else None)
                consumer.finish(native.read)
                value = consumer.current()
                if operation == "ready":
                    self.assertIs(value.owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)
                    self.assertEqual(value.owner_id_coverage.transition_failures, 1)
                    self.assertEqual(value.owner_id_coverage.owner_dispositions, 0)
                else:
                    self.assertEqual(value.drain_failures, 1)  # outside native offer denominator

    def test_native_loss_host_eviction_and_capacity_stay_bounded(self):
        for count in (100, 300, 2000):
            with self.subTest(count=count):
                consumer, native = self.make()
                native.offer(count)
                consumer.finish(native.read)
                value = consumer.current()
                self.assertIs(value.owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)
                self.assertLessEqual(value.owner_id_coverage.retained_offers, 256)
                self.assertLessEqual(len(consumer._id_audit.scalar_payload), 256)
                self.assertGreater(value.host_window_events_evicted, 0)
                self.assertEqual(value.owner_id_coverage.capacity_exceeded, count > 256)
                self.assertEqual(value.counters.events_lost > 0, count == 2000)

    def test_missing_v2_counter_and_bad_conservation_refuse(self):
        for bad in (None, True, -1, "1", 0):
            consumer, native = self.make()
            native.offer()
            def read(count):
                batch = native.read(count)
                if bad is None:
                    del batch.summary.owner_disposition_events
                else:
                    batch.summary.owner_disposition_events = bad
                return batch
            consumer.finish(read)
            self.assertIs(consumer.current().state, JournalState.INCOMPLETE)
            self.assertEqual(consumer.current().drain_failures, 1)

    def test_release_failure_and_fresh_scope_reset(self):
        consumer, native = self.make()
        native.offer()
        def fail():
            raise RuntimeError("final release failed")
        consumer.finish(native.read, before_capture=fail)
        self.assertIs(consumer.current().owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)
        consumer.begin(scope(run="next"))
        second = JournalV2()
        second.offer()
        consumer.finish(second.read)
        self.assertIs(consumer.current().owner_id_coverage.state, OwnerIdCoverageState.COMPLETE)

    def test_counter_version_and_forged_complete_snapshot_refuse(self):
        consumer, native = self.make()
        native.offer()
        consumer.finish(native.read)
        value = consumer.current()
        with self.assertRaises(ValueError):
            replace(value, native_stop_confirmed=False)
        with self.assertRaises(ValueError):
            replace(value, host_window_events_evicted=1)
        with self.assertRaises(ValueError):
            replace(value.counters, event_contract_version=1)
        with self.assertRaises(ValueError):
            JournalCounters(**{**{name: getattr(value.counters, name) for name in value.counters.__dataclass_fields__},
                               "event_contract_version": 3})

    def test_post_terminal_bad_evidence_remains_incomplete_without_throw(self):
        consumer, native = self.make()
        native.offer()
        consumer.finish(native.read)
        self.assertIs(consumer.current().owner_id_coverage.state, OwnerIdCoverageState.COMPLETE)
        def bad(_count):
            raise RuntimeError("competing/broken reader")
        consumer.drain(bad)
        self.assertIs(consumer.current().state, JournalState.INCOMPLETE)
        self.assertIs(consumer.current().owner_id_coverage.state, OwnerIdCoverageState.INCOMPLETE)
        self.assertEqual(consumer.current().drain_failures, 1)


if __name__ == "__main__":
    unittest.main()

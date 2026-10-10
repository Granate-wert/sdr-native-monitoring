"""Exact conservative payload oracle and bounded metadata, not RF acceptance."""
from collections import Counter
from dataclasses import dataclass, fields, is_dataclass, replace
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdr_monitor.domain.analytical_journal import (
    AdapterDispositionCounters, AdapterPacketDisposition, JournalEvent, JournalEventKind,
    JournalState, OwnerJournalSnapshot,
)
from sdr_monitor.services import native_owner_journal as module
from tests.test_app07_owner_journal import Journal, protocol, scope
from tests.test_app07_owner_events import JournalV2, module_v2


def oracle(value):
    size = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        size += sum(oracle(getattr(value, item.name)) for item in fields(value))
    elif isinstance(value, tuple):
        size += sum(oracle(item) for item in value)
    return size


class JournalSizeTests(unittest.TestCase):
    def make(self, v2=False, reserved=0):
        journal = module.NativeOwnerJournal(module_v2() if v2 else protocol(),
            module.EVENT_CAPACITY, reserved_host_bytes=reserved)
        journal.begin(scope())
        return journal, JournalV2() if v2 else Journal()

    def assert_charge(self, journal, candidate=None):
        candidate = journal.current() if candidate is None else candidate
        charge = journal._charge_for(candidate)
        original = (oracle(candidate) + oracle(journal.current())
            + sum(oracle(value) for value in journal.terminal_history())
            + oracle(journal._id_audit.scalar_payload) + journal._id_audit.index_storage_bytes
            + module.BATCH_CAPACITY * 512)
        metadata = (oracle(journal._size_charge) + oracle(charge)
            + sys.getsizeof(journal.__dict__) + oracle("_size_charge"))
        self.assertEqual(journal._budget_bytes(candidate, charge), original + metadata)
        self.assertGreater(metadata, 0)
        self.assertEqual(module._snapshot_bytes(candidate, charge.events_bytes), oracle(candidate))
        self.assertEqual(len(charge.history_bytes), len(journal.terminal_history()))
        return original + metadata, original

    def test_oracle_empty_new_batches_histories_and_v2_audit(self):
        for v2 in (False, True):
            consumer, native = self.make(v2)
            for run in range(8):
                self.assert_charge(consumer)
                for count in (1, 32, 128):
                    native.offer(count)
                    while native.events:
                        consumer.drain(native.read)
                        self.assertIs(consumer.current().state, JournalState.ACTIVE)
                        self.assert_charge(consumer)
                    consumer.drain(native.read)
                    self.assert_charge(consumer)
                consumer.finish(native.read)
                self.assert_charge(consumer)
                self.assertIs(consumer.current().state, JournalState.FINAL)
                consumer.prepare()
                consumer.begin(scope(run=f"run-{run}"))
                native = JournalV2() if v2 else Journal()
            self.assertEqual(len(consumer._size_charge.history_bytes), 4)

    def test_retained_event_and_history_not_recursively_resized_on_empty_poll(self):
        consumer, native = self.make()
        for run in range(5):
            native.offer(128)
            consumer.finish(native.read)
            consumer.prepare()
            consumer.begin(scope(run=f"run-{run}"))
            native = Journal()
        native.offer(128)
        consumer.drain(native.read)
        counts = Counter()
        original = module._scalar_bytes
        def counted(value):
            counts[type(value)] += 1
            return original(value)
        with patch.object(module, "_scalar_bytes", counted):
            for _ in range(8):
                consumer.drain(native.read)
        self.assertEqual(counts[JournalEvent], 0)
        self.assertEqual(counts[OwnerJournalSnapshot], 0)
        self.assertGreater(counts[module.JournalCounters], 0)
        self.assertEqual(consumer.current().drain_failures, 0)
        self.assert_charge(consumer)

    def test_integer_width_unicode_and_duplicate_positions(self):
        consumer, _ = self.make()
        for n in (1, (1 << 30) - 1, 1 << 30, (1 << 60) - 1, 1 << 60, (1 << 64) - 1):
            event = JournalEvent(n, n, n, n, min(n, (1 << 63) - 1), False, JournalEventKind.OFFERED)
            value = OwnerJournalSnapshot(scope=replace(scope(), source_id="ЖÄ🙂" * 100),
                state=JournalState.ACTIVE, events=(event, event), host_window_events_evicted=n)
            consumer._replace_snapshot(value)
            self.assertEqual(consumer._size_charge.events_bytes, oracle(value.events))
            self.assert_charge(consumer, replace(value, drain_failures=n))
            # Same reference in two positions is deliberately charged twice.
            self.assertEqual(oracle(value.events), sys.getsizeof(value.events) + 2 * oracle(event))

    def test_subclasses_and_nested_mutation_keep_oracle_fallback(self):
        @dataclass(frozen=True)
        class ExtendedEvent(JournalEvent):
            child: object = None
        consumer, _ = self.make()
        child = []
        event = ExtendedEvent(1, 1, 1, 5, 1, False, JournalEventKind.OFFERED, child)
        value = OwnerJournalSnapshot(scope=scope(), state=JournalState.INCOMPLETE,
            events=(event,), native_stop_confirmed=True)
        consumer._replace_snapshot(value)
        self.assertIsNone(consumer._size_charge.events_bytes)
        first, _ = self.assert_charge(consumer)
        child.extend(range(1000))
        second, _ = self.assert_charge(consumer)
        self.assertGreater(second, first)
        consumer.prepare()
        consumer.begin(scope(run="next"))
        self.assertIsNone(consumer._size_charge.history_bytes[0])
        first, _ = self.assert_charge(consumer)
        child.extend(range(1000))
        second, _ = self.assert_charge(consumer)
        self.assertGreater(second, first)

    def test_subclass_snapshot_cannot_hide_extra_fields(self):
        @dataclass(frozen=True)
        class ExtendedSnapshot(OwnerJournalSnapshot):
            child: object = None
        consumer, _ = self.make()
        value = ExtendedSnapshot(scope=scope(), state=JournalState.ACTIVE, child=("Ж", 1 << 100))
        consumer._replace_snapshot(value)
        self.assert_charge(consumer)

    def test_drain_threshold_includes_metadata_and_refuses_before_commit(self):
        for offset in (0, -1):
            consumer, native = self.make()
            native.offer(3)
            consumer.drain(native.read)
            before = consumer.current()
            exact, payload = self.assert_charge(consumer)
            self.assertGreater(exact, payload)
            consumer._host_budget = exact + offset
            consumer.drain(native.read)
            self.assertEqual(consumer.current().drain_failures, int(offset < 0))
            self.assertEqual(consumer.current().counters, before.counters)
            self.assertEqual(consumer.current().events, before.events)
            self.assertIs(consumer.current().state, JournalState.INCOMPLETE if offset < 0 else JournalState.ACTIVE)
            self.assert_charge(consumer)

    def test_adapter_threshold_metadata_and_failure_updates_charge(self):
        for offset in (0, -1):
            consumer, _ = self.make()
            before = consumer.current()
            candidate = replace(before, adapter=AdapterDispositionCounters(
                batches=1, unqualified_batches=1, unqualified_packets=1))
            exact, _ = self.assert_charge(consumer, candidate)
            consumer._host_budget = exact + offset
            consumer.observe_adapter_result(SimpleNamespace(), 0, AdapterPacketDisposition.PUBLISHED,
                expected_scope=before.scope)
            value = consumer.current()
            self.assertEqual(value.adapter.binding_failures, int(offset < 0))
            self.assertEqual(value.adapter.batches, int(offset == 0))
            self.assert_charge(consumer)

    def test_shared_reservation_lifecycle_and_metadata_hold_no_graph_roots(self):
        for receiver in ("RX1", "RX2"):
            consumer, native = self.make(reserved=module.HOST_SCALAR_BUDGET // 4)
            self.assertEqual(consumer._host_budget, module.HOST_SCALAR_BUDGET * 3 // 4)
            consumer._replace_snapshot(replace(consumer.current(), scope=scope(receiver=receiver)))
            for run in range(20):
                native.offer(128)
                consumer.finish(native.read)
                self.assertIs(consumer.current().state, JournalState.FINAL)
                self.assert_charge(consumer)
                consumer.prepare()
                consumer.begin(scope(run=f"run-{run}", receiver=receiver))
                native = Journal()
            self.assertIs(type(consumer._size_charge.events_bytes), int)
            self.assertEqual(len(consumer._size_charge.history_bytes), 4)
            self.assertTrue(all(type(x) is int for x in consumer._size_charge.history_bytes))

    def test_conversion_failure_release_failure_and_reset_preserve_charge(self):
        consumer, native = self.make(v2=True)
        native.offer(3)
        consumer.drain(native.read)
        consumer.drain(lambda _: (_ for _ in ()).throw(RuntimeError("conversion")))
        self.assert_charge(consumer)
        consumer.finish(native.read, before_capture=lambda: (_ for _ in ()).throw(RuntimeError("release")))
        self.assertTrue(consumer.current().native_stop_confirmed)
        self.assertTrue(consumer.current().native_presentation_release_failed)
        self.assert_charge(consumer)
        consumer.prepare()
        self.assertEqual(consumer.current().events, ())
        self.assertEqual(consumer._size_charge.events_bytes, oracle(()))
        self.assert_charge(consumer)


if __name__ == "__main__":
    unittest.main()

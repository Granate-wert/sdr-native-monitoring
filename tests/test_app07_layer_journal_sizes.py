"""Exact duplicate-charge and local scalar-work checks for the layer journal."""
from dataclasses import dataclass, replace
import sys
import unittest
from unittest.mock import patch

from sdr_monitor.domain.analytical_journal import OwnerJournalScope
from sdr_monitor.domain.layer_journal import (
    LayerCreationCounters, LayerCreationEvent, LayerJournalSnapshot, LayerJournalState, SweepLayerScope,
)
from sdr_monitor.domain.layer_ready import LayerReadyKind
from sdr_monitor.services import native_layer_journal as module
from tests.test_app07_product_layer_bridge import Kinds, batch, raw_ref, setup


def oracle(value):
    size = sys.getsizeof(value)
    if hasattr(type(value), "__dataclass_fields__") and not isinstance(value, type):
        size += sum(oracle(getattr(value, item.name)) for item in value.__dataclass_fields__.values())
    elif isinstance(value, tuple):
        size += sum(oracle(item) for item in value)
    return size


class LayerJournalSizeTests(unittest.TestCase):
    def test_duplicate_charge_matches_recursive_oracle_with_unicode_and_integer_widths(self):
        _, _, scope, journal, _ = setup()
        scope = replace(scope, clock_scope_id="clock-ЖÄ🙂" * 12, source_id="源🙂" * 80)
        counters = LayerCreationCounters(41, 3, 0, 64, 0, 3, 0, 0, 0)
        refs = (
            raw_ref(1, ready_native_ns=-(1 << 63)),
            raw_ref(2, producer_instance_id=(1 << 64) - 1, creation_sequence=(1 << 60),
                    ready_native_ns=(1 << 63) - 1),
        )
        events = tuple(journal._event(ref) for ref in refs)
        old = replace(journal.current(), scope=scope, state=LayerJournalState.ACTIVE, counters=counters,
                      events=events)
        candidate = replace(old, drain_failures=(1 << 60), events=events + events)
        retained = events + events
        payload, metadata = module._drain_scalar_accounting(old, candidate, retained)
        self.assertEqual(payload, oracle(old) + oracle(candidate) + oracle(retained))
        self.assertGreater(metadata, 0)
        # Duplicate positions are deliberately charged, not deduplicated.
        self.assertEqual(oracle(retained), sys.getsizeof(retained)
                         + 2 * (oracle(events) - sys.getsizeof(events)))

    def test_sweep_scope_and_density_snapshot_use_exact_conservative_sizes(self):
        native, _, base, _, _ = setup()
        scope = SweepLayerScope(base.clock_scope_id, base.host_process_id, "sweep-run", "source", "RX2",
                                "session-Ж" * 16, None, minimum_epoch=17)
        journal = module.NativeLayerJournal(native, 64, density=False)
        journal.begin(scope)
        event = LayerCreationEvent(LayerReadyKind.SWEEP_TERMINAL, 41, 1, 150, False, 81, 2, 0,
                                   0, 0, 0, 0)
        counters = LayerCreationCounters(41, 1, 0, 64, 0, 1, 0, 0, 0)
        old = replace(journal.current(), state=LayerJournalState.ACTIVE, scope=scope,
                      counters=counters, events=(event,))
        candidate = replace(old, scope=replace(scope, acquisition_epoch=81), events=(event, event))
        retained = old.events + (event,)
        payload, metadata = module._drain_scalar_accounting(old, candidate, retained)
        self.assertEqual(payload, oracle(old) + oracle(candidate) + oracle(retained))
        self.assertGreater(metadata, 0)

    def test_subclass_and_mutable_nested_values_fall_back_to_original_oracle(self):
        @dataclass(frozen=True)
        class ExtendedEvent(LayerCreationEvent):
            extra: object = None

        child = ["mutable", 1 << 100]
        event = ExtendedEvent(LayerReadyKind.DENSITY, 41, 1, 150, False, 0, 0, 0,
                              7, 4, 20, 3, child)
        snapshot = LayerJournalSnapshot(state=LayerJournalState.ACTIVE, events=(event,))
        memo = {}
        size, reusable = module._memoized_scalar_bytes(snapshot, memo)
        self.assertEqual(size, oracle(snapshot))
        self.assertFalse(reusable)
        child.append("changed")
        later, _ = module._memoized_scalar_bytes(snapshot, {})
        self.assertEqual(later, oracle(snapshot))
        self.assertNotEqual(size, later)

    def test_metaclass_equality_spoof_cannot_enter_exact_type_memo(self):
        class SpoofMeta(type):
            def __eq__(cls, other):
                return other in (OwnerJournalScope, int) or type.__eq__(cls, other)

            __hash__ = type.__hash__

        @dataclass(frozen=True, slots=True)
        class SpoofedScope(OwnerJournalScope, metaclass=SpoofMeta):
            pass

        scope = SpoofedScope("clock-spoof", 123, "run-spoof", "source-spoof", "RX1", "session-spoof", 7, 5)
        self.assertTrue(isinstance(scope, OwnerJournalScope))
        self.assertIsNot(type(scope), OwnerJournalScope)
        self.assertEqual(type(scope), OwnerJournalScope)  # metaclass equality deliberately spoofs membership
        journal = module.NativeLayerJournal(object(), 0)
        journal.begin(scope)  # existing public type admission remains isinstance-based

        snapshot = LayerJournalSnapshot(state=LayerJournalState.ARMED, scope=scope)
        memo = {}
        measured, reusable = module._memoized_scalar_bytes(snapshot, memo)
        self.assertEqual(measured, oracle(snapshot))
        self.assertFalse(reusable)
        self.assertNotIn(id(scope), memo)
        self.assertNotIn(id(snapshot), memo)

        class SpoofInt(int, metaclass=SpoofMeta):
            pass

        value = SpoofInt(7)
        self.assertEqual(type(value), int)  # leaf metaclass spoofs the old tuple-membership test
        scalar_memo = {}
        self.assertEqual(module._memoized_scalar_bytes(value, scalar_memo), (oracle(value), False))
        self.assertEqual(scalar_memo, {})

    def test_empty_new_batches_do_not_rescan_retained_event_graph(self):
        _, _, _, journal, _ = setup()
        journal.drain(lambda _: batch([raw_ref()]))
        counts = {}
        original = module._scalar_bytes

        def counted(value):
            counts[type(value)] = counts.get(type(value), 0) + 1
            return original(value)

        stable = journal.current()
        with patch.object(module, "_scalar_bytes", counted):
            for _ in range(8):
                journal.drain(lambda _: batch([], created=1, drained=1))
        self.assertEqual(journal.current().state, LayerJournalState.ACTIVE)
        self.assertEqual(journal.current().counters, stable.counters)
        self.assertEqual(journal.current().events, stable.events)
        self.assertEqual(counts.get(LayerCreationEvent, 0), 0)
        self.assertEqual(counts.get(LayerJournalSnapshot, 0), 0)
        self.assertEqual(counts.get(tuple, 0), 0)

    def test_full_native_batches_and_evicted_host_window_still_fit_existing_reservation(self):
        for density in (True, False):
            native, _, base, _, _ = setup()
            journal = module.NativeLayerJournal(native, 64, density=density)
            if not density:
                journal.begin(SweepLayerScope(base.clock_scope_id, base.host_process_id, "run", "source", "RX1",
                                              "session", 0))
            else:
                journal.begin(base)
            for start in (1, 33):
                refs = [raw_ref(i, kind=Kinds.Density if density else Kinds.SweepTerminal,
                    sweep_epoch=0, line_sequence=0 if density else 2,
                    revision=0, config_generation=7 if density else 0, update_sequence=4 if density else 0,
                    source_frame_sequence=20 if density else 0, accumulation_sequence=3 if density else 0)
                    for i in range(start, start + 32)]
                journal.drain(lambda _, refs=refs, end=start + 31: batch(refs, created=end, drained=end))
                self.assertIs(journal.current().state, LayerJournalState.ACTIVE)
                self.assertEqual(len(journal.current().events), 32)
            self.assertEqual(journal.current().counters.created, 64)
            self.assertEqual(journal.current().host_events_evicted, 32)
            expected_budget = (module.HOST_LAYER_RESERVATION if density
                               else module.sweep_layer_host_reservation(1))
            self.assertEqual(journal._host_budget, expected_budget)

    def test_scope_change_recomputes_sizes_and_metadata_stays_in_budget_charge(self):
        _, _, scope, journal, _ = setup()
        event = journal._event(raw_ref())
        counters = LayerCreationCounters(41, 1, 0, 64, 0, 1, 0, 0, 0)
        old = replace(journal.current(), scope=scope, state=LayerJournalState.ACTIVE,
                      counters=counters, events=(event,))
        retained = old.events + ()
        candidate = replace(old, events=retained)
        payload, metadata = module._drain_scalar_accounting(old, candidate, retained)
        self.assertEqual(payload, oracle(old) + oracle(candidate) + oracle(retained))
        changed = replace(old, scope=replace(scope, source_id="new-source-🙂" * 100))
        changed_payload, changed_metadata = module._drain_scalar_accounting(changed, candidate, retained)
        self.assertEqual(changed_payload, oracle(changed) + oracle(candidate) + oracle(retained))
        self.assertGreater(changed_payload, payload)
        self.assertGreater(metadata, 0)
        self.assertGreater(changed_metadata, 0)

    def test_exact_budget_boundary_includes_temporary_memo_without_undercharge(self):
        for offset in (0, -1):
            _, _, _, journal, _ = setup()
            old = journal.current()
            raw = raw_ref()
            event = journal._event(raw)
            counters = LayerCreationCounters(41, 1, 0, 64, 0, 1, 0, 0, 0)
            retained = old.events + (event,)
            candidate = replace(old, state=LayerJournalState.ACTIVE, counters=counters,
                                 events=retained, host_events_evicted=0)
            payload, metadata = module._drain_scalar_accounting(old, candidate, retained)
            required = (payload + metadata + module.LAYER_BATCH_CAPACITY * 1024
                        + module.LAYER_RECEIPT_WINDOW * journal._receipt_budget
                        + journal._processing_cache_budget)
            journal._host_budget = required + offset
            journal.drain(lambda _: batch([raw]))
            value = journal.current()
            self.assertEqual(value.state, LayerJournalState.ACTIVE if offset == 0 else LayerJournalState.INCOMPLETE)
            self.assertEqual(value.events, (event,) if offset == 0 else ())
            self.assertEqual(value.drain_failures, int(offset < 0))

    def test_receipt_finish_prepare_and_rebegin_keep_accounting_epoch_local(self):
        _, bridge, scope, journal, identity = setup()
        raw = raw_ref()
        journal.drain(lambda _: batch([raw]))
        self.assertIsNotNone(journal.receipt(raw, identity, bridge))
        after_receipt = journal.current()
        self.assertEqual(after_receipt.matched_refs, 1)
        payload, metadata = module._drain_scalar_accounting(
            after_receipt, replace(after_receipt, receipt_attempts=after_receipt.receipt_attempts + 1),
            after_receipt.events)
        self.assertEqual(payload, oracle(after_receipt)
                         + oracle(replace(after_receipt, receipt_attempts=after_receipt.receipt_attempts + 1))
                         + oracle(after_receipt.events))
        self.assertGreater(metadata, 0)
        journal.finish(lambda _: batch([], created=1, drained=1))
        self.assertTrue(journal.current().native_stop_confirmed)
        journal.prepare(capacity=64)
        journal.begin(replace(scope, owner_run_id="new-run", session_id="new-session"))
        journal.drain(lambda _: batch([raw_ref(producer_instance_id=42)], producer=42))
        self.assertIs(journal.current().state, LayerJournalState.ACTIVE)
        self.assertEqual((journal.current().counters.producer_instance_id,
                          journal.current().events[0].creation_sequence), (42, 1))


if __name__ == "__main__":
    unittest.main()

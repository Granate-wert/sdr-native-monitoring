"""Actual batch accounting against ALL native handoffs; no paint/RF proof."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.analytical_journal import (
    AdapterDispositionCounters, AdapterPacketDisposition as Outcome, JournalState,
)
from sdr_monitor.services.native_owner_journal import EVENT_CAPACITY, NativeOwnerJournal
from tests.test_app07_owner_journal import Journal, Kind, protocol, scope


def packet(native, offer=None, **changes):
    ref = SimpleNamespace(**vars(native.events[-1].ref))
    if offer is not None:
        ref.offer_sequence = offer
        ref.ready_native_ns = 10000 + offer
    for name, value in changes.items():
        setattr(ref, name, value)
    return SimpleNamespace(analytical_ready=ref)


class AdapterDispositionTests(unittest.TestCase):
    def make(self, count=5):
        consumer = NativeOwnerJournal(protocol(), EVENT_CAPACITY)
        owner = scope()
        consumer.begin(owner)
        native = Journal()
        native.offer(count)
        latest = packet(native)
        consumer.drain(native.read)
        return consumer, native, owner, latest

    def observe(self, consumer, owner, frame, coalesced=0, outcome=Outcome.PUBLISHED):
        consumer.observe_adapter_result(frame, coalesced, outcome, expected_scope=owner)

    def test_all_handoffs_denominator_not_survivors_or_paints(self):
        consumer, native, owner, latest = self.make()
        reads = native.calls
        self.observe(consumer, owner, latest, 2)
        value = consumer.current()
        self.assertEqual(native.calls, reads, "observer must not drain/poll native")
        self.assertEqual(value.counters.offered, 5)
        self.assertEqual(value.adapter.qualified_drained_packets, 3)
        self.assertEqual(value.adapter_native_handoffs_unclassified, 2)
        self.assertFalse(value.adapter_handoff_reconciled)
        consumer.finish(native.read)
        final = consumer.current()
        self.assertEqual(final.adapter, value.adapter)
        self.assertFalse(final.adapter_handoff_reconciled)
        self.assertEqual(final.adapter.last_ready_native_ns, 10005)

    def test_native_terminal_and_three_actual_outcomes_reconcile(self):
        consumer, native, owner, frame = self.make(1)
        self.observe(consumer, owner, frame)
        for outcome in (Outcome.REJECTED, Outcome.CANCELLED):
            native.offer()
            frame = packet(native)
            consumer.drain(native.read)
            self.observe(consumer, owner, frame, outcome=outcome)
        consumer.finish(native.read)
        value = consumer.current()
        self.assertIs(value.state, JournalState.FINAL)
        self.assertEqual((value.adapter.published_packets, value.adapter.rejected_packets,
                          value.adapter.cancelled_packets), (1, 1, 1))
        self.assertEqual(value.adapter_native_handoffs_unclassified, 0)
        self.assertTrue(value.adapter_handoff_reconciled)  # boundary only, never pane/paint
        self.assertEqual(value.adapter.batches, 3)

    def test_invalid_size_foreign_producer_and_duplicate_are_evidence_failures(self):
        for changes, coalesced in (({}, True), ({}, -1), ({"producer_instance_id": 88}, 0),
                                   ({"config_generation": 8}, 0), ({"config_generation": True}, 0),
                                   ({"producer_instance_id": True}, 0), ({"ready_native_ns": True}, 0),
                                   ({"offer_sequence": 99}, 0)):
            with self.subTest(changes=changes, coalesced=coalesced):
                consumer, native, owner, frame = self.make(1)
                for name, value in changes.items():
                    setattr(frame.analytical_ready, name, value)
                self.observe(consumer, owner, frame, coalesced)
                value = consumer.current()
                self.assertIs(value.state, JournalState.ACTIVE)
                self.assertEqual(value.adapter.binding_failures, 1)
                self.assertEqual(value.adapter.qualified_drained_packets, 0)
                self.assertEqual(value.adapter_native_handoffs_unclassified, 1)
                consumer.finish(native.read)
                self.assertIs(consumer.current().state, JournalState.FINAL)
                self.assertFalse(consumer.current().adapter_handoff_reconciled)
        consumer, _, owner, frame = self.make(1)
        self.observe(consumer, owner, frame)
        self.observe(consumer, owner, frame)
        self.assertEqual(consumer.current().adapter.published_packets, 1)
        self.assertEqual(consumer.current().adapter.binding_failures, 1)

    def test_excess_coalescing_not_forced_to_fit_denominator(self):
        consumer, _, owner, frame = self.make(1)
        self.observe(consumer, owner, frame, 5)
        self.assertEqual(consumer.current().adapter.binding_failures, 1)
        self.assertEqual(consumer.current().adapter.qualified_drained_packets, 0)
        self.assertEqual(consumer.current().counters.handed_off, 1)

    def test_host_budget_refusal_preserves_native_and_acquisition_evidence(self):
        from unittest.mock import patch

        consumer, native, owner, frame = self.make(1)
        with patch("sdr_monitor.services.native_owner_journal._scalar_bytes", return_value=1_048_576):
            self.observe(consumer, owner, frame)
        value = consumer.current()
        self.assertIs(value.state, JournalState.ACTIVE)
        self.assertEqual(value.adapter.binding_failures, 1)
        self.assertEqual(value.adapter.qualified_drained_packets, 0)
        consumer.finish(native.read)
        self.assertIs(consumer.current().state, JournalState.FINAL)
        self.assertFalse(consumer.current().adapter_handoff_reconciled)

    def test_missing_identity_and_incomplete_native_journal_are_unknown(self):
        consumer, native, owner, frame = self.make(4)
        self.observe(consumer, owner, SimpleNamespace(), 2)
        value = consumer.current()
        self.assertEqual((value.adapter.unqualified_batches, value.adapter.unqualified_packets), (1, 3))
        self.assertEqual(value.adapter_native_handoffs_unclassified, 4)
        consumer.drain(lambda _: object())  # native failure remains separate
        self.observe(consumer, owner, frame)
        value = consumer.current()
        self.assertIs(value.state, JournalState.INCOMPLETE)
        self.assertEqual(value.adapter.unqualified_packets, 4)
        self.assertEqual(value.adapter.published_packets, 0)
        self.assertEqual(native.offered, 4)

    def test_foreign_equal_scope_or_old_run_not_relabelled_and_final_is_immutable(self):
        consumer, native, owner, frame = self.make(1)
        self.observe(consumer, replace(owner), frame)
        self.assertIsNone(consumer.current().adapter)
        self.observe(consumer, owner, frame)
        consumer.finish(native.read)
        final = consumer.current()
        self.observe(consumer, owner, frame)
        self.assertIs(consumer.current(), final)
        fresh = scope(run="run-b")
        consumer.begin(fresh)
        self.observe(consumer, owner, frame)
        self.assertIsNone(consumer.current().adapter)
        self.assertEqual(consumer.terminal_history()[-1].adapter.published_packets, 1)

    def test_unsupported_unknown_not_fake_zero_or_callback(self):
        consumer = NativeOwnerJournal(object())
        owner = scope()
        consumer.begin(owner)
        self.observe(consumer, owner, object())
        self.assertIsNone(consumer.current().adapter)
        self.assertIsNone(consumer.current().adapter_native_handoffs_unclassified)
        self.assertIsNone(consumer.current().adapter_handoff_reconciled)

    def test_strict_scalar_contract_and_no_offer_gap_inference(self):
        for kwargs in (dict(batches=1), dict(published_packets=True), dict(last_offer_sequence=1),
                       dict(last_offer_sequence=1, last_ready_native_ns=0),
                       dict(unqualified_batches=1, batches=1, unqualified_packets=0),
                       dict(batches=1, published_packets=1, last_offer_sequence=1,
                            last_ready_native_ns=0, coalesced_packets=(1 << 64) - 1)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AdapterDispositionCounters(**kwargs)
        consumer, _, owner, frame = self.make(128)
        self.observe(consumer, owner, frame)
        self.assertEqual(consumer.current().adapter.published_packets, 1)
        self.assertEqual(consumer.current().adapter_native_handoffs_unclassified, 127)
        self.assertEqual(consumer.current().adapter.coalesced_packets, 0)
        with self.assertRaises(ValueError):
            replace(consumer.current(), adapter=replace(consumer.current().adapter, last_offer_sequence=129))

    def test_native_producer_cancel_and_supersession_not_adapter_dispositions(self):
        consumer, native, owner, frame = self.make(1)
        self.observe(consumer, owner, frame)
        native.offer(2, retire=Kind.ProducerSuperseded)
        native.offer(3, retire=Kind.ProducerCancelled)
        consumer.finish(native.read)
        value = consumer.current()
        self.assertEqual(value.counters.offered, 6)
        self.assertEqual((value.counters.producer_superseded, value.counters.producer_cancelled), (2, 3))
        self.assertTrue(value.adapter_handoff_reconciled)
        self.assertEqual(value.adapter.cancelled_packets, 0)

    def test_actual_rtl_service_worker_batch_and_terminal(self):
        from tests.test_app07_owner_journal import ServiceJournalTests
        from tests.test_app07_rtl_product_route import _Native, _FrameControl

        class Native(_Native):
            def create_rtl_runtime_control(n, _runtime, center, rate, *args, **kwargs):
                n.create_calls += 1
                n.control = control = _FrameControl(center, rate, args[6])
                journal = Journal(generation=args[6])
                journal.offer(3)
                raw = control.make_frame(3)
                raw.detector = args[8]  # SAME explicitly requested sample/peak profile
                raw.analytical_ready = packet(journal).analytical_ready
                pending = [raw]
                def drain():
                    return SimpleNamespace(frame=pending.pop(), coalesced_frames=2) if pending else (
                        SimpleNamespace(frame=None, coalesced_frames=0))
                control.drain_latest_spectrum_frame = drain
                control.drain_analytical_ready_events = journal.read
                return control

        fixture = ServiceJournalTests()
        service, exclusion = fixture.rtl(protocol(Native()))
        try:
            service.start()
            fixture.wait(lambda: service.analytical_journal_snapshot().adapter is not None)
            value = service.analytical_journal_snapshot()
            self.assertEqual((value.adapter.coalesced_packets, value.adapter.published_packets), (2, 1))
            self.assertEqual(value.adapter.binding_failures, 0)
            self.assertIsNone(service.stop().error)
            self.assertTrue(service.analytical_journal_snapshot().adapter_handoff_reconciled)
            self.assertFalse(exclusion.claimed)
        finally:
            service.stop()

    def test_actual_hackrf_worker_common_owner_batch_and_terminal(self):
        from tests.test_app07_owner_journal import ServiceJournalTests
        from tests.ui_v2.test_app06_hackrf_common_analyzer import graph, stage

        g = graph()
        protocol(g.native)
        g.hackrf._journal = NativeOwnerJournal(g.native, EVENT_CAPACITY)
        create = g.factory.create
        def factory(permit):
            control = create(permit)
            control.drain_coalesced = 2
            journal = Journal(generation=permit.plan.request.configuration_generation)
            def enrich(frame):
                journal.offer()
                frame.analytical_ready = packet(journal).analytical_ready
            control.frame_change = enrich
            control.drain_analytical_ready_events = journal.read
            return control
        g.factory.create = factory
        try:
            g.application.discover()
            g.application.select_device("source-hackrf")
            stage(g)
            g.application.start()
            ServiceJournalTests().wait(lambda: g.hackrf.analytical_journal_snapshot().adapter is not None)
            value = g.hackrf.analytical_journal_snapshot()
            self.assertGreater(value.adapter.published_packets, 0)
            self.assertEqual(value.adapter.coalesced_packets, 2 * value.adapter.published_packets)
            self.assertEqual(value.adapter.binding_failures, 0)
            self.assertIsNone(g.application.stop().error)
            self.assertTrue(g.hackrf.analytical_journal_snapshot().adapter_handoff_reconciled)
        finally:
            g.application.shutdown()


if __name__ == "__main__":
    unittest.main()

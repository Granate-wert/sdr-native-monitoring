"""Native owner scalar boundary; not per-offer, physical RX or paint acceptance."""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.analytical_journal import (
    AdapterPacketDisposition, JournalState, NativePresentationCounters,
)
from sdr_monitor.services.native_owner_journal import EVENT_CAPACITY, NativeOwnerJournal
from tests.test_app07_adapter_dispositions import packet
from tests.test_app07_owner_journal import Journal, protocol, scope


class NativePresentationTests(unittest.TestCase):
    def make(self, outcomes=None, count=5, version=1):
        module = protocol()
        module.OWNER_PRESENTATION_DISPOSITION_CONTRACT_VERSION = version
        consumer = NativeOwnerJournal(module, EVENT_CAPACITY)
        owner = scope()
        consumer.begin(owner)
        native = Journal()
        native.offer(count)
        values = dict(forwarded=0, superseded=0, coalesced=0, cancelled=0,
                      cadence_suppressed=0, accounting_failures=0)
        values.update(outcomes or {})

        def read(limit):
            batch = native.read(limit)
            batch.summary.presentation = SimpleNamespace(supported=True, **values)
            return batch

        return consumer, native, owner, values, read

    def test_all_handoffs_include_native_evictions_without_double_coalescing(self):
        consumer, native, owner, _, read = self.make(dict(superseded=2, coalesced=2, forwarded=1))
        latest = packet(native)
        consumer.drain(read)
        consumer.observe_adapter_result(latest, 2, AdapterPacketDisposition.PUBLISHED, expected_scope=owner)
        consumer.finish(read)
        value = consumer.current()
        self.assertEqual(value.counters.handed_off, 5)
        self.assertEqual(value.native_owner_handoffs_unclassified, 0)
        self.assertEqual(value.adapter_native_handoffs_unclassified, 2)  # historical direct-boundary residual
        self.assertTrue(value.owner_adapter_scalar_reconciled)
        self.assertFalse(value.adapter_handoff_reconciled)
        self.assertEqual(value.native_presentation.coalesced, value.adapter.coalesced_packets)

    def test_cadence_and_cancelled_do_not_become_RF_or_FFT_loss(self):
        consumer, _, _, _, read = self.make(dict(cadence_suppressed=3, cancelled=2))
        consumer.finish(read)
        value = consumer.current()
        self.assertEqual(value.native_owner_handoffs_unclassified, 0)
        self.assertEqual(value.counters.producer_superseded, 0)
        self.assertEqual(value.counters.producer_cancelled, 0)
        self.assertIsNone(value.owner_adapter_scalar_reconciled)  # no adapter observations

    def test_pending_and_unobserved_adapter_never_claim_complete_boundary(self):
        consumer, native, owner, _, read = self.make(dict(superseded=2, forwarded=2))
        latest = packet(native)
        consumer.drain(read)
        consumer.observe_adapter_result(latest, 0, AdapterPacketDisposition.PUBLISHED, expected_scope=owner)
        consumer.finish(read)
        self.assertEqual(consumer.current().native_owner_handoffs_unclassified, 1)
        self.assertFalse(consumer.current().owner_adapter_scalar_reconciled)

    def test_owner_and_adapter_coalescing_must_match_not_sum(self):
        consumer, native, owner, _, read = self.make(dict(superseded=3, coalesced=1, forwarded=1))
        latest = packet(native)
        consumer.drain(read)
        consumer.observe_adapter_result(latest, 0, AdapterPacketDisposition.PUBLISHED, expected_scope=owner)
        consumer.finish(read)
        self.assertFalse(consumer.current().owner_adapter_scalar_reconciled)

    def test_bad_native_scalars_version_and_overcount_fail_evidence_only(self):
        for outcomes, version in ((dict(forwarded=True), 1), (dict(forwarded=-1), 1),
                                  (dict(forwarded=6), 1), (dict(cancelled=1 << 64), 1),
                                  ({}, True), ({}, 2)):
            with self.subTest(outcomes=outcomes, version=version):
                consumer, _, _, _, read = self.make(outcomes, version=version)
                consumer.finish(read)
                value = consumer.current()
                self.assertIs(value.state, JournalState.INCOMPLETE)
                self.assertTrue(value.native_stop_confirmed)
                self.assertEqual(value.drain_failures, 1)
                self.assertIsNone(value.native_presentation)

    def test_monotonic_same_owner_counts_and_support_cannot_regress(self):
        consumer, _, _, values, read = self.make(dict(superseded=2))
        consumer.drain(read)
        original = consumer.current().native_presentation
        values['superseded'] = 1
        consumer.drain(read)
        self.assertIs(consumer.current().state, JournalState.INCOMPLETE)
        self.assertEqual(consumer.current().native_presentation, original)

    def test_accounting_failure_blocks_reconciliation_even_conserved_totals(self):
        consumer, native, owner, _, read = self.make(dict(forwarded=1, superseded=4, accounting_failures=1))
        latest = packet(native)
        consumer.drain(read)
        consumer.observe_adapter_result(latest, 0, AdapterPacketDisposition.PUBLISHED, expected_scope=owner)
        consumer.finish(read)
        self.assertFalse(consumer.current().owner_adapter_scalar_reconciled)

    def test_legacy_absence_is_unknown_and_cached_reads_are_inert(self):
        consumer = NativeOwnerJournal(protocol(), EVENT_CAPACITY)
        consumer.begin(scope())
        native = Journal()
        native.offer()
        consumer.finish(native.read)
        before = native.calls
        value = consumer.current()
        self.assertIsNone(value.native_presentation)
        self.assertIsNone(value.native_owner_handoffs_unclassified)
        self.assertIsNone(value.owner_adapter_scalar_reconciled)
        self.assertEqual(native.calls, before)

    def test_restart_archives_actual_terminal_counts_without_transfer(self):
        consumer, _, _, _, read = self.make(dict(cadence_suppressed=5))
        consumer.finish(read)
        consumer.begin(scope(run='run-b'))
        self.assertIsNone(consumer.current().native_presentation)
        self.assertEqual(consumer.terminal_history()[0].native_presentation.cadence_suppressed, 5)

    def test_unsupported_native_owner_cannot_fabricate_zero_or_nonzero_coverage(self):
        consumer, _, _, _, read = self.make()

        def unsupported(limit):
            result = read(limit)
            result.summary.presentation.supported = False
            return result

        consumer.finish(unsupported)
        self.assertIsNone(consumer.current().native_presentation)
        self.assertIsNone(consumer.current().native_owner_handoffs_unclassified)
        broken, _, _, _, bad_read = self.make(dict(forwarded=1))

        def invalid(limit):
            result = bad_read(limit)
            result.summary.presentation.supported = False
            return result

        broken.finish(invalid)
        self.assertIs(broken.current().state, JournalState.INCOMPLETE)

    def test_native_snapshot_scalars_are_immutable_and_uint64_bounded(self):
        value = NativePresentationCounters(forwarded=1)
        with self.assertRaises(FrozenInstanceError):
            value.forwarded = 2
        with self.assertRaises(ValueError):
            replace(value, superseded=(1 << 64) - 1)


if __name__ == '__main__':
    unittest.main()

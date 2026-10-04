"""Finite host consumer and SAME service owner seams; fake SDK, not RF acceptance."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from enum import Enum
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.domain.analytical_journal import (
    JournalCounters, JournalEventKind, JournalState, OwnerJournalScope,
)
from sdr_monitor.services.native_owner_journal import (
    BATCH_CAPACITY, EVENT_CAPACITY, NativeOwnerJournal, owner_journal_capacity,
)


class Kind(Enum):
    Offered = 1
    HandedOff = 2
    ProducerSuperseded = 3
    ProducerCancelled = 4


class Clock(Enum):
    NativeSteady = 1


class State(Enum):
    Monotonic = 1
    Regressed = 2


def protocol(native=None):
    native = native if native is not None else SimpleNamespace()
    native.OWNER_ANALYTICAL_READY_CONTRACT_VERSION = 1
    native.ANALYTICAL_READY_CONTRACT_VERSION = 1
    native.AnalyticalReadyEventKind = Kind
    native.AnalyticalReadyClock = Clock
    native.AnalyticalReadyClockState = State
    native.analytical_ready_clock_ns = time.monotonic_ns
    return native


def scope(generation=5, run="run-a", receiver=None):
    return OwnerJournalScope("clock-a", 123, run, "source-a", receiver, "session-a", generation, 1)


class Journal:
    """Atomic fake native scalar batch with independent lifetime counters."""
    def __init__(self, generation=5, producer=7):
        self.generation, self.producer = generation, producer
        self.events = []
        self.offered = self.handed = self.cancelled = self.superseded = 0
        self.generated = self.drained = self.lost = 0
        self.first_lost = self.last_lost = self.regressions = 0
        self.calls = 0
        self.last_ready = 0

    def _event(self, offer, kind, ready):
        self.generated += 1
        event = SimpleNamespace(event_sequence=self.generated, kind=kind,
            ref=SimpleNamespace(producer_instance_id=self.producer, offer_sequence=offer,
                config_generation=self.generation, ready_native_ns=ready,
                clock=Clock.NativeSteady, clock_state=State.Monotonic))
        if len(self.events) == EVENT_CAPACITY:
            self.lost += 1
            self.first_lost = self.first_lost or self.generated
            self.last_lost = self.generated
        else:
            self.events.append(event)

    def offer(self, count=1, *, retire=Kind.HandedOff):
        for _ in range(count):
            self.offered += 1
            self.last_ready = 10_000 + self.offered
            self._event(self.offered, Kind.Offered, self.last_ready)
            if retire is not None:
                self._event(self.offered, retire, self.last_ready)
                if retire is Kind.HandedOff:
                    self.handed += 1
                elif retire is Kind.ProducerCancelled:
                    self.cancelled += 1
                else:
                    self.superseded += 1

    def read(self, count):
        assert count == BATCH_CAPACITY
        self.calls += 1
        events, self.events = self.events[:count], self.events[count:]
        self.drained += len(events)
        return SimpleNamespace(events=events, summary=SimpleNamespace(supported=True,
            producer_instance_id=self.producer, offered=self.offered, handed_off=self.handed,
            producer_superseded=self.superseded, producer_cancelled=self.cancelled,
            outstanding=self.offered - self.handed - self.superseded - self.cancelled,
            clock_regressions=self.regressions, events_generated=self.generated,
            events_drained=self.drained, events_lost=self.lost,
            first_lost_event_sequence=self.first_lost, last_lost_event_sequence=self.last_lost,
            event_capacity=EVENT_CAPACITY, events_pending=len(self.events),
            event_storage_bytes=EVENT_CAPACITY * 64))


class OwnerJournalTests(unittest.TestCase):
    def make(self):
        journal = NativeOwnerJournal(protocol(), EVENT_CAPACITY)
        journal.begin(scope())
        return journal

    def test_cached_reads_and_legacy_are_inert(self):
        reader = Mock(side_effect=AssertionError("no native poll"))
        legacy = NativeOwnerJournal(object())
        legacy.begin(scope())
        legacy.drain(reader)
        legacy.finish(reader)
        self.assertIs(legacy.current().state, JournalState.UNSUPPORTED)
        self.assertTrue(legacy.current().native_stop_confirmed)
        self.assertEqual(legacy.terminal_history(), ())
        reader.assert_not_called()
        for version in (None, True, 0, 2):
            native = protocol()
            native.OWNER_ANALYTICAL_READY_CONTRACT_VERSION = version
            self.assertEqual(owner_journal_capacity(native), 0)
        for backend in ("cuda", "hip", "unknown"):
            self.assertEqual(owner_journal_capacity(protocol(), backend), 0)
        self.assertEqual(owner_journal_capacity(protocol(), "auto"), EVENT_CAPACITY)

    def test_original_time_scalar_immutable_and_empty_terminal_retention(self):
        consumer, native = self.make(), Journal()
        native.offer(3)
        consumer.drain(native.read)
        first = consumer.current()
        self.assertEqual((first.counters.offered, first.counters.handed_off), (3, 3))
        self.assertEqual([event.ready_native_ns for event in first.events],
                         [10001, 10001, 10002, 10002, 10003, 10003])
        self.assertIs(first.events[0].kind, JournalEventKind.OFFERED)
        with self.assertRaises(FrozenInstanceError):
            first.events[0].ready_native_ns = 0
        consumer.finish(native.read)
        final = consumer.current()
        self.assertIs(final.state, JournalState.FINAL)
        self.assertEqual(final.events, first.events)
        self.assertEqual(final.host_window_events_evicted, 0)
        self.assertEqual(native.calls, 2)
        self.assertIsNot(first, final)

    def test_replacement_counts_host_window_separately_from_native_loss(self):
        consumer, native = self.make(), Journal()
        native.offer(3)
        consumer.drain(native.read)
        native.offer(2)
        consumer.drain(native.read)
        value = consumer.current()
        self.assertEqual(value.host_window_events_evicted, 6)
        self.assertEqual((value.counters.offered, value.counters.events_lost), (5, 0))
        self.assertEqual(len(value.events), 4)

    def test_declared_drop_new_loss_and_bounded_final_drain(self):
        consumer, native = self.make(), Journal()
        native.offer(EVENT_CAPACITY // 2 + 3)
        consumer.finish(native.read)
        value = consumer.current()
        self.assertIs(value.state, JournalState.FINAL)  # terminal, not complete event history
        self.assertEqual(native.calls, EVENT_CAPACITY // BATCH_CAPACITY)
        self.assertEqual(value.counters.events_lost, 6)
        self.assertEqual(value.counters.events_drained, EVENT_CAPACITY)
        self.assertEqual(value.host_window_events_evicted, EVENT_CAPACITY - BATCH_CAPACITY)
        self.assertEqual(len(value.events), BATCH_CAPACITY)

    def test_declared_sequence_gap_is_not_fft_drop(self):
        consumer, native = self.make(), Journal()
        native.offer(2)
        native.events = native.events[2:]
        native.lost, native.first_lost, native.last_lost = 2, 1, 2
        consumer.finish(native.read)
        value = consumer.current()
        self.assertIs(value.state, JournalState.FINAL)
        self.assertEqual([event.event_sequence for event in value.events], [3, 4])
        self.assertEqual(value.counters.offered, 2)

    def test_competing_consumer_latches_incomplete_and_never_retries(self):
        consumer, native = self.make(), Journal()
        native.offer(2)
        native.drained = 1
        native.events.pop(0)  # another consumer took one record
        consumer.drain(native.read)
        consumer.drain(native.read)
        consumer.finish(native.read)
        self.assertEqual(native.calls, 1)
        self.assertIs(consumer.current().state, JournalState.INCOMPLETE)
        self.assertEqual(consumer.current().drain_failures, 1)
        self.assertTrue(consumer.current().native_stop_confirmed)

    def test_stale_foreign_duplicate_and_bad_clock_reject_atomically(self):
        changes = (
            lambda e: setattr(e.ref, "config_generation", 6),
            lambda e: setattr(e.ref, "producer_instance_id", 8),
            lambda e: setattr(e, "event_sequence", 0),
            lambda e: setattr(e.ref, "clock", "native"),
            lambda e: setattr(e, "kind", "offered"),
        )
        for change in changes:
            with self.subTest(change=change):
                consumer, native = self.make(), Journal()
                native.offer()
                change(native.events[0])
                consumer.drain(native.read)
                self.assertIsNone(consumer.current().counters)
                self.assertEqual(consumer.current().events, ())
                self.assertIs(consumer.current().state, JournalState.INCOMPLETE)

    def test_producer_switch_and_counter_regression_not_new_start(self):
        for mutation in (lambda n: setattr(n, "producer", 8),
                         lambda n: setattr(n, "handed", 0)):
            consumer, native = self.make(), Journal()
            native.offer()
            consumer.drain(native.read)
            before = consumer.current().counters
            mutation(native)
            consumer.drain(native.read)
            self.assertIs(consumer.current().state, JournalState.INCOMPLETE)
            self.assertEqual(consumer.current().counters, before)

    def test_outstanding_at_acknowledged_stop_remains_incomplete(self):
        consumer, native = self.make(), Journal()
        native.offer(retire=None)
        consumer.finish(native.read)
        self.assertIs(consumer.current().state, JournalState.INCOMPLETE)
        self.assertEqual(consumer.current().counters.outstanding, 1)

    def test_failed_factory_and_module_rebind_keep_terminal_history(self):
        consumer = self.make()
        with self.assertRaisesRegex(ValueError, "confirmed native Stop"):
            consumer.prepare()
        consumer.finish(lambda _: (_ for _ in ()).throw(RuntimeError("reader")))
        old = consumer.current()
        consumer.prepare(native=protocol(), capacity=EVENT_CAPACITY)
        consumer.finish(lambda _: None)  # failed factory, no native counters/epoch
        self.assertIs(consumer.current().state, JournalState.INCOMPLETE)
        self.assertIsNone(consumer.current().scope)
        consumer.prepare()
        self.assertEqual(consumer.terminal_history()[0], old)
        self.assertEqual(len(consumer.terminal_history()), 2)

    def test_terminal_history_capacity_and_restart_run_identity(self):
        consumer = NativeOwnerJournal(protocol(), EVENT_CAPACITY)
        for run in range(7):
            consumer.prepare()
            consumer.begin(scope(run=f"run-{run}"))
            native = Journal(producer=run + 1)
            native.offer()
            consumer.finish(native.read)
        self.assertEqual(len(consumer.terminal_history()), 4)
        self.assertEqual(consumer.current().terminal_windows_evicted, 2)
        self.assertEqual([x.scope.owner_run_id for x in consumer.terminal_history()],
                         ["run-2", "run-3", "run-4", "run-5"])

    def test_host_reservation_refuses_before_snapshot_commit(self):
        consumer, native = self.make(), Journal()
        native.offer()
        with patch("sdr_monitor.services.native_owner_journal._scalar_bytes", return_value=2_000_000):
            consumer.drain(native.read)
        self.assertIsNone(consumer.current().counters)
        self.assertIs(consumer.current().state, JournalState.INCOMPLETE)

    def test_full_batch_terminal_history_fits_finite_scalar_reservation(self):
        consumer = NativeOwnerJournal(protocol(), EVENT_CAPACITY)
        for run in range(7):
            consumer.prepare()
            consumer.begin(scope(run=f"run-{run}"))
            native = Journal(producer=run + 1)
            native.offer(128)
            consumer.finish(native.read)
            self.assertIs(consumer.current().state, JournalState.FINAL)
        self.assertEqual(len(consumer.terminal_history()), 4)

    def test_oversized_batch_and_native_reader_error_fail_without_retaining_proxy(self):
        for reader_factory in (
                lambda native: lambda count: SimpleNamespace(
                    summary=native.read(count).summary, events=[object()] * 257),
                lambda _native: lambda _count: (_ for _ in ()).throw(RuntimeError("SDK scalar read"))):
            consumer, native = self.make(), Journal()
            native.offer()
            consumer.finish(reader_factory(native))
            self.assertIs(consumer.current().state, JournalState.INCOMPLETE)
            self.assertEqual(consumer.current().events, ())
            self.assertTrue(consumer.current().native_stop_confirmed)

    def test_scope_and_counters_strict_scalar_validation(self):
        for changes in ({"source_id": "unknown"}, {"session_id": " bad"},
                        {"receiver_id": "source:rx2"}, {"source_id": "x\x00y"},
                        {"configuration_generation": True}, {"acquisition_epoch": 0}):
            with self.assertRaises(ValueError):
                replace(scope(), **changes)
        native = Journal()
        from dataclasses import fields
        raw = native.read(256).summary
        # Explicit legacy protocol1 defaults, not fabricated v2 native fields.
        value = JournalCounters(**{f.name: getattr(raw, f.name) for f in fields(JournalCounters)
            if f.name not in ("event_contract_version", "owner_disposition_events")})
        for changes in ({"offered": 1}, {"events_generated": 1}, {"event_storage_bytes": 0}):
            with self.assertRaises(ValueError):
                replace(value, **changes)


class ServiceJournalTests(unittest.TestCase):
    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("bounded same-owner journal completion")
            time.sleep(.002)

    def rtl(self, native):
        from tests.test_app07_rtl_product_route import RtlProductRouteTests, _Exclusion
        from sdr_monitor.domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
        from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
        from sdr_monitor.services.rtl_capability_provider import RTL_SOURCE_ID
        native, provider, inventory, selection = RtlProductRouteTests()._selected(native)
        exclusion = _Exclusion()
        service = RtlAnalyzerService(native, exclusion, lambda: inventory,
            lambda binding, _runtime: provider.provision_for(binding))
        service.bind_selection(selection)
        service.stage(RtlConfigurationPatch(RtlLiveRequest(150_000_000, 2_400_000,
            source_id=RTL_SOURCE_ID), selection.revision, 0))
        self.assertEqual(native.create_calls, 0)
        return service, exclusion

    def test_hackrf_factory_journal_protocol_before_permit_consume(self):
        from tests.test_r11n_hackrf_native_factory import _NativeFactory, _permit
        from sdr_monitor.services.hackrf_native_factory import HackrfNativeRuntimeFactory, HackrfNativeFactoryError
        native, permit = _NativeFactory(), _permit()
        factory = HackrfNativeRuntimeFactory(lambda: native, analytical_event_capacity=EVENT_CAPACITY)
        with self.assertRaises(HackrfNativeFactoryError):
            factory.create(permit)
        self.assertEqual(native.calls, [])
        protocol(native)
        self.assertIs(factory.create(permit), native.control)  # refused protocol did not consume it
        self.assertEqual(native.calls[0]["analytical_event_capacity"], EVENT_CAPACITY)
        self.assertEqual(native.calls[0]["sample_rate_hz"], 10e6)
        self.assertEqual(native.calls[0]["fft_size"], 4096)
        for capacity in (True, -1, 1, EVENT_CAPACITY + 1):
            with self.assertRaises(ValueError):
                HackrfNativeRuntimeFactory(lambda: native, analytical_event_capacity=capacity)

    def test_hackrf_stop_observer_only_after_confirmed_stop_does_not_own_release(self):
        from tests.test_r11p_hackrf_product_live_orchestration import _NativeFactory, _Control, _permit
        from sdr_monitor.services.hackrf_native_factory import HackrfNativeRuntimeFactory
        from sdr_monitor.services.hackrf_product_live import HackrfProductLiveCoordinator
        for complete in (False, True):
            control = _Control(stop_complete=complete)
            coordinator = HackrfProductLiveCoordinator(HackrfNativeRuntimeFactory(lambda: _NativeFactory(control)))
            self.assertTrue(coordinator.start_after_confirmation(_permit(), user_confirmed=True).started)
            observed = []
            def after_stop(owned):
                self.assertIs(owned, control)
                self.assertIs(coordinator._control, control)
                self.assertEqual(control.stop_calls, [5000])
                observed.append(True)
                raise RuntimeError("observer failure must not change cleanup acknowledgment")
            result = coordinator.stop(5000, after_native_stop=after_stop)
            self.assertEqual(result.stopped, complete)
            self.assertEqual(observed, [True] if complete else [])
            if not complete:
                control.stop_complete = True
                self.assertTrue(coordinator.stop(5000).stopped)
            self.assertIsNone(coordinator._control)

    def test_rtl_actual_worker_same_control_terminal_retention_restart(self):
        from tests.test_app07_rtl_product_route import _Native, _Control
        from sdr_monitor.domain.live import LiveSessionState
        class Native(_Native):
            def create_rtl_runtime_control(n, _runtime, center, rate, *args, **kwargs):
                self.assertEqual(kwargs["analytical_event_capacity"], EVENT_CAPACITY)
                n.create_calls += 1
                n.control = _Control(center, rate)
                journal = Journal(generation=args[6], producer=n.create_calls)
                journal.offer(3)
                def drain(count):
                    if n.control.stops:
                        self.assertEqual(n.control.stops, 1)
                    return journal.read(count)
                n.control.drain_analytical_ready_events = drain
                return n.control
        native = protocol(Native())
        service, exclusion = self.rtl(native)
        try:
            self.assertIs(service.start().state, LiveSessionState.RUNNING)
            self.wait(lambda: service.analytical_journal_snapshot().counters is not None)
            before = service.analytical_journal_snapshot()
            self.assertEqual(before.counters.offered, 3)
            self.assertEqual(before.scope.acquisition_epoch, 17)
            self.assertIsNone(service.stop().error)
            final = service.analytical_journal_snapshot()
            self.assertIs(final.state, JournalState.FINAL)
            self.assertEqual(final.events, before.events)
            self.assertFalse(exclusion.claimed)
            self.assertIsNone(service._control)
            service.start()
            self.wait(lambda: service.analytical_journal_snapshot().counters is not None)
            self.assertNotEqual(service.analytical_journal_snapshot().scope.owner_run_id,
                                before.scope.owner_run_id)
            self.assertEqual(service.analytical_journal_terminal_history(), (final,))
        finally:
            service.stop()

    def test_rtl_factory_failure_does_not_trap_next_start_in_prepared_journal(self):
        from tests.test_app07_rtl_product_route import _Native
        class Native(_Native):
            def create_rtl_runtime_control(n, *_args, **_kwargs):
                n.create_calls += 1
                raise RuntimeError("fake factory before handle")
        native = protocol(Native())
        service, exclusion = self.rtl(native)
        for _ in range(2):
            service.start()
            self.assertTrue(exclusion.claimed)
            self.assertIsNone(service.stop().error)
            self.assertTrue(service.analytical_journal_snapshot().native_stop_confirmed)
            self.assertIs(service.analytical_journal_snapshot().state, JournalState.INCOMPLETE)
            self.assertFalse(exclusion.claimed)
        self.assertEqual(native.create_calls, 2)

    def test_hackrf_actual_worker_final_read_before_same_control_release(self):
        from tests.ui_v2.test_app06_hackrf_common_analyzer import graph, stage
        g = graph()
        protocol(g.native)
        g.hackrf._journal = NativeOwnerJournal(g.native, EVENT_CAPACITY)
        create = g.factory.create
        def factory(permit):
            control = create(permit)
            journal = Journal(generation=permit.plan.request.configuration_generation)
            journal.offer(4)
            def drain(count):
                self.assertIs(g.coordinator._control, control)
                return journal.read(count)
            control.drain_analytical_ready_events = drain
            return control
        g.factory.create = factory
        try:
            g.application.discover()
            g.application.select_device("source-hackrf")
            stage(g)
            self.assertEqual(g.factory.controls, [])
            g.application.start()
            self.wait(lambda: g.hackrf.analytical_journal_snapshot().counters is not None)
            self.assertEqual(g.hackrf.analytical_journal_snapshot().counters.offered, 4)
            self.assertIsNone(g.application.stop().error)
            final = g.hackrf.analytical_journal_snapshot()
            self.assertIs(final.state, JournalState.FINAL)
            self.assertEqual(g.factory.controls[0].stops, [5000])
            self.assertIsNone(g.coordinator._control)
        finally:
            g.application.shutdown()


if __name__ == "__main__":
    unittest.main()

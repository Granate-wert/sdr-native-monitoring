"""Actual owner adapters + bounded scalar evidence. Mock only, no physical RX."""
from dataclasses import FrozenInstanceError, replace
from enum import Enum
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock

from sdr_monitor.domain.analytical_journal import OwnerJournalScope
from sdr_monitor.domain.analytical_ready import ReadyClockMapping
from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch
from sdr_monitor.domain.layer_journal import LayerCreationCounters, LayerJournalState
from sdr_monitor.domain.layer_ready import DensityLayerIdentity
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.hackrf_native_factory import HackrfNativeFactoryError, HackrfNativeRuntimeFactory
from sdr_monitor.services.native_layer_journal import (
    HOST_LAYER_RESERVATION, LAYER_EVENT_CAPACITY, NativeLayerJournal, layer_journal_capacity,
)
from sdr_monitor.services.native_owner_journal import HOST_SCALAR_BUDGET, NativeOwnerJournal, _scalar_bytes
from sdr_monitor.services.native_ready_bridge import NativeReadyBridge
from sdr_monitor.services.layer_ready_admission import LayerReadyAdmissionState, admit_layer_ready
from tests.test_app07_layer_ready_admission import density_frame
from tests.test_app07_native_ready_bridge import Clock, State, native_with_clock


class Kinds(Enum):
    Density = 1
    SweepProgress = 2
    SweepTerminal = 3


def protocol(native=None):
    native = native if native is not None else native_with_clock()
    native.LAYER_CREATION_CONTRACT_VERSION = 1
    native.HACKRF_LAYER_CREATION_CONTRACT_VERSION = 1
    native.ANALYTICAL_READY_CONTRACT_VERSION = 1
    native.LayerReadyKind = Kinds
    native.AnalyticalReadyClock = Clock
    native.AnalyticalReadyClockState = State
    native.HackrfRuntimeDspControl = SimpleNamespace(drain_density_layer_ready_events=Mock(),
        drain_latest_spectrum_frame=Mock())
    native.HackrfSweepRuntimeAnalysisControl = SimpleNamespace(drain_sweep_layer_ready_events=Mock())
    return native


def raw_ref(sequence=1, **changes):
    args = dict(kind=Kinds.Density, producer_instance_id=41, creation_sequence=sequence,
        ready_native_ns=150, clock=Clock.NativeSteady, clock_state=State.Monotonic,
        sweep_epoch=0, line_sequence=0, revision=0, config_generation=7,
        update_sequence=4, source_frame_sequence=20, accumulation_sequence=3)
    args.update(changes)
    return SimpleNamespace(**args)


def batch(refs, *, created=None, pending=0, drained=None, lost=0, first_lost=0, last_lost=0, producer=41):
    created = len(refs) + pending + lost if created is None else created
    drained = created - pending - lost if drained is None else drained
    return SimpleNamespace(creations=refs, summary=SimpleNamespace(producer_instance_id=producer,
        created=created, clock_regressions=0, event_capacity=LAYER_EVENT_CAPACITY,
        events_pending=pending, events_drained=drained, events_lost=lost,
        first_lost_creation_sequence=first_lost, last_lost_creation_sequence=last_lost))


def setup():
    native = protocol()
    bridge = NativeReadyBridge(native, host_clock=Mock(side_effect=(1000, 1010, 1100, 1120)))
    bridge.begin()
    bridge.sample()
    scope = OwnerJournalScope(bridge.clock_scope_id, bridge.host_process_id, "owner-run", "source", "RX2",
        "accumulation", 7, 5)
    journal = NativeLayerJournal(native, LAYER_EVENT_CAPACITY)
    journal.begin(scope)
    frame = replace(density_frame(), native_accumulation_sequence=3)
    identity = DensityLayerIdentity(frame.source_id, frame.config_generation, frame.update_sequence,
        frame.source_frame_sequence, frame.accumulation_id, "RX2", frame.acquisition_epoch, 3)
    return native, bridge, scope, journal, identity


class LayerOwnerBridgeTests(unittest.TestCase):
    def test_original_creation_has_same_owner_clock_and_session_without_restamp(self):
        native, bridge, scope, journal, identity = setup()
        raw = raw_ref()
        reader = Mock(return_value=batch([raw]))
        journal.drain(reader)
        expected = journal.receipt(raw, identity, bridge)
        self.assertEqual((expected.ready_native_ns, expected.producer_instance_id, expected.creation_sequence), (150, 41, 1))
        self.assertEqual((expected.owner_run_id, expected.session_id), (scope.owner_run_id, scope.session_id))
        self.assertIs(expected.mapping, ReadyClockMapping.BOUNDED)
        probes = native.analytical_ready_clock_ns.call_count
        for _ in range(20):
            self.assertEqual(journal.receipt(raw, identity, bridge), expected)
            journal.current()
        self.assertEqual((reader.call_count, native.analytical_ready_clock_ns.call_count), (1, probes))
        self.assertEqual(journal.current().counters.created, 1)
        with self.assertRaises(FrozenInstanceError):
            expected.owner_run_id = "other"

    def test_scope_and_original_ref_mismatch_never_authenticate_measurement(self):
        _, bridge, _, journal, identity = setup()
        journal.drain(lambda _: batch([raw_ref()]))
        for change in (dict(receiver_id="RX1"), dict(source_id="peer"), dict(config_generation=8),
                       dict(acquisition_epoch=6), dict(accumulation_id="different-session"),
                       dict(native_accumulation_sequence=4), dict(update_sequence=5)):
            with self.subTest(change=change):
                self.assertIsNone(journal.receipt(raw_ref(), replace(identity, **change), bridge))
        self.assertIsNone(journal.receipt(raw_ref(ready_native_ns=151), identity, bridge))
        foreign_bridge = NativeReadyBridge(protocol())
        self.assertIsNone(journal.receipt(raw_ref(), identity, foreign_bridge))
        self.assertGreater(journal.current().refused_refs, 0)

    def test_stop_preserves_original_refs_and_fresh_start_rejects_old_producer(self):
        _, bridge, scope, journal, identity = setup()
        journal.finish(lambda _: batch([raw_ref()]))
        snapshot = journal.current()
        self.assertIs(snapshot.state, LayerJournalState.FINAL)
        self.assertTrue(snapshot.native_stop_confirmed)
        receipt = journal.receipt(raw_ref(), identity, bridge)
        journal.finish(Mock(side_effect=AssertionError("duplicate terminal drain")))
        self.assertEqual(journal.receipt(raw_ref(), identity, bridge), receipt)
        journal.begin(replace(scope, owner_run_id="new-run"))
        journal.drain(lambda _: batch([raw_ref(producer_instance_id=42)], producer=42))
        self.assertIsNone(journal.receipt(raw_ref(), identity, bridge))
        self.assertEqual(journal.receipt(raw_ref(producer_instance_id=42), identity, bridge).owner_run_id, "new-run")

    def test_native_loss_and_host_eviction_are_distinct_not_reconstructed(self):
        _, bridge, _, journal, identity = setup()
        journal.drain(lambda _: batch([raw_ref(i) for i in range(1, 33)], created=66, pending=32,
            drained=32, lost=2, first_lost=65, last_lost=66))
        journal.drain(lambda _: batch([raw_ref(i) for i in range(33, 65)], created=66, drained=64,
            lost=2, first_lost=65, last_lost=66))
        snapshot = journal.current()
        self.assertIs(snapshot.state, LayerJournalState.ACTIVE)
        self.assertEqual((snapshot.counters.created, snapshot.counters.events_lost, snapshot.host_events_evicted), (66, 2, 32))
        self.assertEqual(len(snapshot.events), 32)
        self.assertIsNone(journal.receipt(raw_ref(1), identity, bridge))  # finite host eviction
        self.assertIsNone(journal.receipt(raw_ref(65), identity, bridge))  # native evidence loss
        self.assertIsNotNone(journal.receipt(raw_ref(64), identity, bridge))
        self.assertLess(_scalar_bytes(snapshot), HOST_LAYER_RESERVATION)

    def test_competing_drainer_regression_oversize_and_failure_stay_incomplete(self):
        cases = (batch([], created=1, drained=1), batch([raw_ref(i) for i in range(1, 34)]),
                 batch([raw_ref(config_generation=8)]), batch([raw_ref(kind=Kinds.SweepTerminal)]))
        for bad in cases:
            with self.subTest(bad=bad):
                _, bridge, _, journal, identity = setup()
                journal.drain(lambda _: bad)
                self.assertIs(journal.current().state, LayerJournalState.INCOMPLETE)
                self.assertIsNone(journal.receipt(raw_ref(), identity, bridge))
                journal.finish(Mock(side_effect=AssertionError("failed evidence must not re-drain")))
                self.assertTrue(journal.current().native_stop_confirmed)
        _, _, _, journal, _ = setup()
        journal.drain(lambda _: batch([raw_ref()]))
        journal.drain(lambda _: batch([], created=0))
        self.assertIs(journal.current().state, LayerJournalState.INCOMPLETE)

    def test_unsupported_is_unknown_and_shared_host_budget_not_doubled(self):
        journal = NativeLayerJournal(object())
        reader = Mock(side_effect=AssertionError("legacy backend must not be read"))
        journal.drain(reader)
        journal.finish(reader)
        self.assertIs(journal.current().state, LayerJournalState.UNSUPPORTED)
        self.assertIsNone(journal.current().counters)
        owner = NativeOwnerJournal(object(), reserved_host_bytes=HOST_LAYER_RESERVATION)
        self.assertEqual(owner._host_budget + HOST_LAYER_RESERVATION, HOST_SCALAR_BUDGET)
        for bad in (-1, True, HOST_SCALAR_BUDGET):
            with self.assertRaises(ValueError):
                NativeOwnerJournal(object(), reserved_host_bytes=bad)
        for changes in (dict(events_lost=True), dict(events_pending=65), dict(created=2),
                        dict(first_lost_creation_sequence=1), dict(producer_instance_id=0)):
            args = vars(batch([raw_ref()]).summary) | changes
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                LayerCreationCounters(**args)

    def test_live_density_keeps_session_and_refuses_foreign_sidecar_reset(self):
        _, bridge, _, journal, identity = setup()
        journal.drain(lambda _: batch([raw_ref()]))
        frame = replace(density_frame(), receiver_id="RX2", native_accumulation_sequence=3)
        ready = journal.receipt(raw_ref(), identity, bridge)
        frame = replace(frame, layer_ready=ready)
        self.assertEqual((frame.accumulation_id, frame.native_accumulation_sequence), ("accumulation", 3))
        self.assertEqual(frame.timestamp_ns, 999)
        for change in (dict(native_accumulation_sequence=4), dict(source_id="peer"),
                       dict(accumulation_id="different-session"), dict(receiver_id="RX1")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(frame, **change)
        self.assertIs(admit_layer_ready(frame, replace(ready, session_id="foreign-session")).state,
            LayerReadyAdmissionState.REFUSED)

    def test_bound_loss_denominator_and_diagnostic_budget_failures_are_not_zero(self):
        from unittest.mock import patch
        _, bridge, scope, journal, identity = setup()
        with self.assertRaises(ValueError):
            journal.begin(scope)
        journal.finish(lambda _: None)
        with self.assertRaises(ValueError):
            journal.begin(SimpleNamespace(**{name: getattr(scope, name) for name in scope.__slots__}))
        journal.begin(scope)
        with patch("sdr_monitor.services.native_layer_journal._scalar_bytes", return_value=HOST_LAYER_RESERVATION):
            journal.drain(lambda _: batch([raw_ref()]))
        self.assertIs(journal.current().state, LayerJournalState.INCOMPLETE)
        self.assertIsNone(journal.current().counters)
        self.assertEqual(journal.current().drain_failures, 1)
        self.assertIsNone(journal.receipt(raw_ref(), identity, bridge))
        journal.finish(lambda _: None)
        self.assertTrue(journal.current().native_stop_confirmed)

    def test_clock_probe_failure_and_regressed_event_do_not_invent_bounds(self):
        native, bridge, _, journal, identity = setup()
        event = raw_ref(clock_state=State.Regressed, ready_native_ns=-17)
        journal.drain(lambda _: batch([event]))
        ready = journal.receipt(event, identity, bridge)
        self.assertIs(ready.mapping, ReadyClockMapping.PRODUCER_REGRESSED)
        self.assertIsNone(ready.host_bounds)
        native.analytical_ready_clock_ns.side_effect = RuntimeError("probe failure")
        bridge.sample()
        # A distinct monotonic producer case preserves the sticky host failure.
        _, other_bridge, _, other_journal, other_identity = setup()
        other_bridge._native.analytical_ready_clock_ns.side_effect = RuntimeError("probe failure")
        other_bridge.sample()
        other_journal.drain(lambda _: batch([raw_ref()]))
        ready = other_journal.receipt(raw_ref(), other_identity, other_bridge)
        self.assertIs(ready.mapping, ReadyClockMapping.PROBE_FAILED)
        self.assertIsNone(ready.host_bounds)


class HackrfLayerFactoryTests(unittest.TestCase):
    def test_capability_gate_constructor_and_before_permit_consumption(self):
        from tests.test_app06_hackrf_dsp_profile import permit
        from tests.test_app06_hackrf_persistence import density_request
        from tests.test_r11n_hackrf_native_factory import _NativeFactory
        native = protocol(_NativeFactory())
        native.analytical_ready_clock_ns = Mock(return_value=100)
        native.HACKRF_PERSISTENCE_CONTRACT_VERSION = 1
        native.PersistenceConfig = lambda *args: args
        native.PersistenceMode = SimpleNamespace(EXPONENTIAL_DECAY="decay")
        self.assertEqual(layer_journal_capacity(native, hackrf=True), 64)
        issued = permit(density_request())
        factory = HackrfNativeRuntimeFactory(lambda: native, layer_event_capacity=64)
        native.HACKRF_LAYER_CREATION_CONTRACT_VERSION = True
        with self.assertRaises(HackrfNativeFactoryError):
            factory.create(issued)
        self.assertEqual(native.calls, [])
        native.HACKRF_LAYER_CREATION_CONTRACT_VERSION = 1
        factory.create(issued)  # previous refusal did not consume permit
        self.assertEqual(native.calls[-1]["layer_event_capacity"], 64)
        native.analytical_ready_clock_ns.assert_not_called()
        for bad in (True, 1, 65):
            with self.assertRaises(ValueError):
                HackrfNativeRuntimeFactory(layer_event_capacity=bad)

    def test_disabled_persistence_does_not_enable_or_pass_layer_keyword(self):
        from tests.test_app06_hackrf_burst_budget import request
        from tests.test_app06_hackrf_dsp_profile import permit
        from tests.test_r11n_hackrf_native_factory import _NativeFactory
        native = _NativeFactory()
        HackrfNativeRuntimeFactory(lambda: native, layer_event_capacity=64).create(permit(request()))
        self.assertNotIn("layer_event_capacity", native.calls[0])


class HackrfLayerServiceTests(unittest.TestCase):
    def test_optional_drain_failure_keeps_actual_spectrum_density_and_cleanup(self):
        from tests.ui_v2.test_app06_hackrf_common_analyzer import graph
        from tests.ui_v2.test_app06_hackrf_persistence import enable_fake_density, stage_density
        g = graph()
        enable_fake_density(g)
        g.application.discover()
        g.application.select_device("source-hackrf")
        profile = stage_density(g).hackrf_request
        native = protocol(g.native)
        native.analytical_ready_clock_ns = time.monotonic_ns
        service = HackrfAnalyzerService(native, g.live, g.catalog.snapshot, g.preflight, g.coordinator)
        selection = g.application.current_source_selection()
        service.bind_selection(selection)
        service.stage(HackrfConfigurationPatch(profile, selection.revision, service.current_snapshot().generation))
        original_create = g.factory.create
        reads = Mock(side_effect=RuntimeError("optional diagnostic drain failure"))
        def create(permit):
            control = original_create(permit)
            control.drain_density_layer_ready_events = reads
            return control
        g.factory.create = create
        try:
            self.assertIsNone(service.start().error)
            deadline = time.monotonic() + 2
            while service.current_snapshot().persistence is None:
                self.assertIsNone(service.current_snapshot().error)
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.001)
            first = service.current_snapshot().spectrum.sequence
            while service.current_snapshot().spectrum.sequence <= first:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.001)
            self.assertIsNone(service.current_snapshot().error)
            self.assertIsNone(service.current_snapshot().persistence.layer_ready)
            self.assertIs(service.density_layer_journal_snapshot().state, LayerJournalState.INCOMPLETE)
            self.assertEqual(reads.call_count, 1)  # no uncontrolled diagnostic retries
            self.assertIsNone(service.stop().error)
            self.assertTrue(service.density_layer_journal_snapshot().native_stop_confirmed)
            self.assertIsNone(g.live._external_analyzer_owner)
        finally:
            service.stop()
            g.application.shutdown()

    def test_actual_worker_same_control_stop_rearm_and_inert_cached_reads(self):
        from tests.ui_v2.test_app06_hackrf_common_analyzer import graph
        from tests.ui_v2.test_app06_hackrf_persistence import enable_fake_density, stage_density
        g = graph()
        enable_fake_density(g)
        g.application.discover()
        g.application.select_device("source-hackrf")
        profile = stage_density(g).hackrf_request
        native = protocol(g.native)
        native.analytical_ready_clock_ns = Mock(side_effect=range(100, 100_100, 100))
        service = HackrfAnalyzerService(native, g.live, g.catalog.snapshot, g.preflight, g.coordinator)
        service.bind_selection(g.application.current_source_selection())
        service.stage(HackrfConfigurationPatch(profile, service._selection.revision, service.current_snapshot().generation))
        original_create = g.factory.create
        reads = []
        def create(permit):
            control = original_create(permit)
            control.events, control.created, control.drained = [], 0, 0
            original_poll = control.poll_persistence_snapshots
            def poll(count):
                snapshots = original_poll(count)
                for density in snapshots:
                    control.created += 1
                    density.layer_ready = raw_ref(control.created, producer_instance_id=100 + len(g.factory.controls),
                        config_generation=density.config_generation, update_sequence=density.update_sequence,
                        source_frame_sequence=density.source_frame_sequence)
                    control.events.append(density.layer_ready)
                return snapshots
            def drain(count):
                import threading
                reads.append((control, threading.current_thread().name))
                events, control.events = control.events[:count], control.events[count:]
                control.drained += len(events)
                return batch(events, created=control.created, pending=len(control.events), drained=control.drained,
                    producer=100 + len(g.factory.controls))
            control.poll_persistence_snapshots = poll
            control.drain_density_layer_ready_events = drain
            return control
        g.factory.create = create
        def wait():
            deadline = time.monotonic() + 2
            while service.current_snapshot().persistence is None or service.current_snapshot().persistence.layer_ready is None:
                self.assertIsNone(service.current_snapshot().error)
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.001)
        try:
            for _ in range(2):
                self.assertIsNone(service.start().error)
                wait()
                snapshot = service.stop()
                self.assertIsNone(snapshot.error)
                journal = service.density_layer_journal_snapshot()
                self.assertIs(journal.state, LayerJournalState.FINAL)
                self.assertEqual(journal.counters.events_pending, 0)
                self.assertEqual(snapshot.persistence.accumulation_id, snapshot.session_id)
                self.assertEqual(snapshot.persistence.layer_ready.session_id, snapshot.session_id)
                density = snapshot.persistence
                raw = SimpleNamespace(source_id=density.source_id, config_generation=density.config_generation,
                    update_sequence=0, source_frame_sequence=density.source_frame_sequence,
                    timestamp_ns=density.timestamp_ns, power_min_db=density.power_min_db,
                    power_max_db=density.power_max_db, power_bins=density.power_bins,
                    frequency_bins=density.frequency_bins, processed_frames=density.processed_frames,
                    exponential_decay=density.exponential_decay, frequencies_hz=density.frequencies_hz,
                    density=density.density, probability_scale=density.probability_scale,
                    count_scale=density.count_scale, unit="DBFS_BIN", quality_flags=density.native_quality_flags)
                # Existing valid legacy update0 is not rejected for lacking new
                # diagnostic identity. A throwing optional accessor is inert too.
                class LegacyDensity:
                    def __getattr__(self, name):
                        if name == "layer_ready":
                            raise RuntimeError("optional telemetry failure")
                        return getattr(raw, name)
                legacy = service._convert_persistence(LegacyDensity(), snapshot)
                self.assertEqual((legacy.update_sequence, legacy.timestamp_ns), (0, density.timestamp_ns))
                self.assertIsNone(legacy.layer_ready)
                before = len(reads), native.analytical_ready_clock_ns.call_count
                for _ in range(20):
                    service.current_snapshot()
                    service.density_layer_journal_snapshot()
                self.assertEqual((len(reads), native.analytical_ready_clock_ns.call_count), before)
            self.assertEqual(len(g.factory.controls), 2)
            self.assertTrue(all(name in ("sdr-hackrf-analyzer", "MainThread") for _, name in reads))
            self.assertIsNone(g.live._external_analyzer_owner)
        finally:
            service.stop()
            g.application.shutdown()


COMPILED_CODE = r'''
import importlib.util
from dataclasses import replace
from pathlib import Path
import sys
import time
from unittest.mock import Mock
from tests.native_test_dependencies import native_test_dll_directory
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.analytical_journal import OwnerJournalScope
from sdr_monitor.domain.analytical_ready import ReadyClockMapping
from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.live import LiveSnapshot, LiveSessionState
from sdr_monitor.domain.layer_journal import LayerJournalState
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.native_layer_journal import LAYER_EVENT_CAPACITY

path = Path(sys.argv[1]).resolve(strict=True)
with native_test_dll_directory(str(path)):
    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == path
    service = HackrfAnalyzerService(native, Mock(), Mock(), Mock(), Mock())
    bridge = service._ready_bridge
    bridge.begin()
    source, session = "hackrf-r11l-test-fixture", "compiled-session"
    scope = OwnerJournalScope(bridge.clock_scope_id, bridge.host_process_id,
        "compiled-owner", source, None, session, 1, 1)
    service._layer_journal.begin(scope)
    owner = native._make_test_hackrf_runtime_dsp_control(4, layer_event_capacity=LAYER_EVENT_CAPACITY)
    try:
        deadline = time.monotonic() + 3
        while owner.metrics().processing.worker_blocks_processed != 4:
            assert time.monotonic() < deadline, "mock processing deadline"
            time.sleep(.001)
        assert owner.stop(1000).complete()
        frames = owner.poll_persistence_snapshots()
        assert frames
        bridge.sample()
        service._layer_journal.finish(owner.drain_density_layer_ready_events)
        assert service.density_layer_journal_snapshot().state is LayerJournalState.FINAL
        fixture = graph()
        try:
            binding = replace(fixture.provider.value.binding_for_source("source-hackrf"), source_id=source)
            choice = AnalyzerSourceChoice(binding, fixture.provider.runtime, "mock", "mock")
            request = HackrfLiveRequest(100e6, 20e6, 15_000_000, 16, 20,
                fft_size=256, hop_size=256, source_id=source, configuration_generation=1,
                persistence_enabled=True, persistence_mode="exponential-decay", persistence_power_bins=16)
            context = LiveSnapshot(1, 0, LiveSessionState.RUNNING, hackrf_request=request,
                source_choice=choice, selection_revision=1, session_id=session,
                active_source_id=source, active_config_generation=1, acquisition_epoch=1,
                clock_domain="host_steady_ns", hackrf_persistence_available=True)
            raw = frames[-1]
            frame = service._convert_persistence(raw, context)
            assert frame.layer_ready is not None
            assert frame.layer_ready.owner_run_id == scope.owner_run_id
            assert frame.layer_ready.mapping is ReadyClockMapping.BOUNDED
            assert frame.layer_ready.ready_native_ns == raw.layer_ready.ready_native_ns
            assert frame.native_accumulation_sequence == raw.layer_ready.accumulation_sequence
            assert frame.timestamp_ns == raw.timestamp_ns
            assert frame.native_quality_flags == int(raw.quality_flags)
            assert frame.accumulation_id == session
            assert not frame.density.flags.writeable
            before = service.density_layer_journal_snapshot().counters
            for _ in range(20):
                service.current_snapshot()
                service.density_layer_journal_snapshot()
            assert service.density_layer_journal_snapshot().counters == before
        finally:
            fixture.application.shutdown()
    finally:
        assert owner.stop(1000).complete()
'''


class MatchingCompiledLayerServiceTests(unittest.TestCase):
    def test_same_native_owner_original_density_through_actual_product_converter(self):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected:
            self.skipTest("explicit matching native required; no SDK fallback")
        module = Path(selected).resolve(strict=True)
        result = subprocess.run([sys.executable, "-c", COMPILED_CODE, str(module)],
            cwd=Path(__file__).resolve().parents[1], env=dict(os.environ),
            capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

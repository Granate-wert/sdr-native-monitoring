"""Actual product single/paired density bridge; explicit compiled MOCK only."""
from dataclasses import replace
from enum import Enum
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.layer_journal import LayerJournalState
from sdr_monitor.services.native_layer_journal import HOST_LAYER_RESERVATION, NativeLayerJournal
from sdr_monitor.services.native_live import _join_stopped_engine, _native_fixed_band_config, _pluto_density_capacity
from sdr_monitor.services.native_owner_journal import HOST_SCALAR_BUDGET, NativeOwnerJournal
from tests.test_app07_product_layer_bridge import protocol, setup
from tests.test_native_live_discovery import _FakeNative

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "native/sdr_core/out/build/rtl-hf/libiio.dll"


class PlutoDensityAdmissionTests(unittest.TestCase):
    def test_idle_cleanup_only_skips_join_for_exact_native_idle_and_false_stream(self):
        class States(Enum):
            CREATED = 1
            CONFIGURED = 2
            RUNNING = 3
            ERROR = 4
        native = SimpleNamespace(EngineState=States)
        for state in States:
            for streaming in (False, True, 0, None):
                engine = SimpleNamespace(state=Mock(return_value=state), streaming=streaming, join=Mock())
                _join_stopped_engine(engine, native)
                expected = state in (States.CREATED, States.CONFIGURED) and streaming is False
                self.assertEqual(engine.join.call_count, 0 if expected else 1)
        for state in ("CONFIGURED", SimpleNamespace(name="CONFIGURED"), None):
            engine = SimpleNamespace(state=Mock(return_value=state), streaming=False,
                join=Mock(side_effect=RuntimeError("unconfirmed join")))
            with self.assertRaises(RuntimeError):
                _join_stopped_engine(engine, native)
        engine = SimpleNamespace(state=Mock(side_effect=RuntimeError("state read failure")), join=Mock())
        _join_stopped_engine(engine, native)
        engine.join.assert_called_once()

    def test_only_loaded_typed_cpu_auto_owner_with_enabled_density_admits(self):
        native = protocol(_FakeNative())
        native.analytical_ready_clock_ns = Mock(return_value=100)
        profile = LiveConfiguration(100e6, 20e6)
        self.assertEqual(_pluto_density_capacity(native, profile), 0)
        native.PlutoReceiverSelection = SimpleNamespace(RX1=1, RX2=2)
        native.PlutoFixedBandEngine = SimpleNamespace(drain_density_layer_ready_events=Mock())
        native.FixedBandConfig = Mock(side_effect=lambda *args, **kwargs: SimpleNamespace(kwargs=kwargs))
        native.DeviceConfig = Mock(wraps=native.DeviceConfig)
        for backend in (BackendKind.CPU, BackendKind.AUTO):
            selected = replace(profile, backend=backend)
            self.assertEqual(_pluto_density_capacity(native, selected), 64)
            self.assertEqual(_native_fixed_band_config(native, selected, "usb:mock",
                layer_event_capacity=64).kwargs["layer_event_capacity"], 64)
        disabled = replace(profile, persistence_enabled=False, persistence_mode="disabled")
        for selected in (disabled, replace(profile, backend=BackendKind.CUDA)):
            self.assertEqual(_pluto_density_capacity(native, selected), 0)
            before = native.DeviceConfig.call_count
            with self.assertRaises(ValueError):
                _native_fixed_band_config(native, selected, "usb:mock", layer_event_capacity=64)
            self.assertEqual(native.DeviceConfig.call_count, before)
        self.assertNotIn("layer_event_capacity", _native_fixed_band_config(native, disabled, "usb:mock").kwargs)
        native.LAYER_CREATION_CONTRACT_VERSION = True
        self.assertEqual(_pluto_density_capacity(native, profile), 0)
        for invalid in (True, -1, 1, 65):
            with self.assertRaises(ValueError):
                _native_fixed_band_config(native, profile, "usb:mock", layer_event_capacity=invalid)

    def test_prepared_failure_and_rearm_clear_original_scope_with_no_probe(self):
        native, _, scope, journal, _ = setup()
        with self.assertRaises(ValueError):
            journal.prepare(capacity=64)  # actual admitted scope cannot be stolen
        journal.finish(Mock(side_effect=RuntimeError("diagnostic failure")))
        calls = native.analytical_ready_clock_ns.call_count
        journal.prepare(capacity=64)
        self.assertIs(journal.current().state, LayerJournalState.ARMED)
        self.assertIsNone(journal.current().scope)
        before = journal.current()
        with self.assertRaises(ValueError):
            journal.preflight_scope(replace(scope, source_id="x" * HOST_LAYER_RESERVATION))
        self.assertIs(journal.current(), before)
        journal.finish(lambda _: None)  # failed native activation before scope
        self.assertIs(journal.current().state, LayerJournalState.INCOMPLETE)
        self.assertTrue(journal.current().native_stop_confirmed)
        journal.prepare(capacity=0)
        journal.begin(scope)
        journal.finish(Mock(side_effect=AssertionError("disabled must not drain")))
        self.assertIs(journal.current().state, LayerJournalState.UNSUPPORTED)
        self.assertIsNone(journal.current().counters)
        self.assertEqual(native.analytical_ready_clock_ns.call_count, calls)
        NativeLayerJournal(object()).prepare(capacity=0)
        owner = NativeOwnerJournal(object())
        owner.prepare(reserved_host_bytes=HOST_LAYER_RESERVATION)
        self.assertEqual(owner._host_budget + HOST_LAYER_RESERVATION, HOST_SCALAR_BUDGET)
        for invalid in (True, -1, HOST_SCALAR_BUDGET):
            with self.assertRaises(ValueError):
                owner.prepare(reserved_host_bytes=invalid)


CODE = r'''
import ctypes
import importlib.util
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from tests.native_test_dependencies import native_test_dll_directory
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.paired_live import PairedLiveRequest
from sdr_monitor.domain.layer_journal import LayerJournalState
from sdr_monitor.domain.analytical_ready import ReadyClockMapping
from sdr_monitor.services.native_live import NativeLiveSessionService

path, case = Path(sys.argv[1]).resolve(strict=True), sys.argv[2]
with native_test_dll_directory(str(path)):
    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == path
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    actual_factory = native.PlutoFixedBandEngine
    controls, reads = [], []
    fail_start = case.endswith("start-failure")
    class ObservedOwner:
        def __init__(self, *args, **kwargs):
            self.actual = actual_factory(*args, **kwargs)
            self.raw = {}
            self.start_calls = 0
            controls.append(self)
        def __getattr__(self, name):
            return getattr(self.actual, name)
        def start(self):
            global fail_start
            self.start_calls += 1
            if fail_start:
                fail_start = False
                raise RuntimeError("test activation failure after native configure")
            return self.actual.start()
        def join(self):
            try:
                return self.actual.join()
            except BaseException:
                traceback.print_exc()
                raise
        def disconnect(self):
            try:
                return self.actual.disconnect()
            except BaseException:
                traceback.print_exc()
                raise
        def drain_density_layer_ready_events(self, receiver, count):
            reads.append((self, receiver, threading.current_thread().name, self.actual.streaming))
            if case == "journal-failure":
                raise RuntimeError("test optional diagnostic failure")
            return self.actual.drain_density_layer_ready_events(receiver, count)
        def poll_persistence_snapshots(self, count):
            values = self.actual.poll_persistence_snapshots(count)
            if values:
                self.raw["RX1"] = values[-1]
            return values
        def poll_receiver_persistence_snapshots(self, receiver, count):
            values = self.actual.poll_receiver_persistence_snapshots(receiver, count)
            if values:
                self.raw[receiver.name] = values[-1]
            return values
    native.PlutoFixedBandEngine = ObservedOwner
    service = NativeLiveSessionService(native, device_buffer_samples=4096)
    app = LiveSessionApplicationService(service)
    try:
        selected = app.select_manual_uri("usb:mock")
        assert selected.error is None, selected.error
        profile = LiveConfiguration(center_hz=2450e6, sample_rate_hz=61_440_000.,
            analog_bandwidth_hz=56e6, gain_db=43., fft_size=1024, backend=BackendKind.CPU,
            persistence_power_bins=16)
        if case == "disabled":
            profile = replace(profile, persistence_enabled=False, persistence_mode="disabled")
        selected = app.apply_configuration(profile)
        assert selected.error is None and selected.applied is not None
        paired = case.startswith("paired")
        if paired:
            app.stage_paired_rtbw(PairedLiveRequest(selected.device.device_id, str(selected.session_id),
                selected.device.capabilities.receiver_topology, selected.applied.applied,
                "x" * 5000 if case == "paired-scope-refusal" else "native-source-a", "native-source-b"))
        assert hooks.mock_iio_live_buffers() == 0
        previous = None
        for run in range(2):
            started = app.start()
            if case == "paired-scope-refusal" and run == 0:
                assert started.error is not None
                assert "source_id" in started.error
                assert controls[-1].start_calls == 0
                assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
                assert all(j.native_stop_confirmed for j in service.density_layer_journal_snapshots())
                assert app.stop().error is None
                app.clear_paired_rtbw()
                current = app.current_snapshot()
                app.stage_paired_rtbw(PairedLiveRequest(current.device.device_id, str(current.session_id),
                    current.device.capabilities.receiver_topology, current.applied.applied,
                    "native-source-a", "native-source-b"))
                started = app.start()
            if case.endswith("start-failure") and run == 0:
                assert started.error is not None
                assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
                assert all(j.native_stop_confirmed for j in service.density_layer_journal_snapshots())
                assert app.stop().error is None
                started = app.start()
            assert started.error is None, started.error
            assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 1
            assert started.applied.applied.sample_rate_hz == profile.sample_rate_hz
            assert started.applied.applied.fft_size == profile.fft_size
            deadline = time.monotonic() + 5
            latest = None
            while True:
                assert time.monotonic() < deadline, (case, service.density_layer_journal_snapshots())
                assert service.latest_snapshot().error is None, service.latest_snapshot().error
                values = service.poll_paired_frames() if paired else service.poll_frames()
                if values:
                    latest = values[-1]
                    snapshots = (latest.primary, latest.secondary) if paired else (latest,)
                    if case == "disabled":
                        if all(s.spectrum is not None for s in snapshots):
                            break
                    elif all(s.persistence is not None for s in snapshots):
                        if case == "journal-failure" or all(s.persistence.layer_ready is not None for s in snapshots):
                            break
                time.sleep(.002)
            for index, snap in enumerate(snapshots):
                if case == "disabled":
                    assert snap.persistence is None
                    continue
                frame = snap.persistence
                assert frame.accumulation_id == snap.session_id
                assert frame.receiver_id == ("RX1", "RX2")[index]
                assert not frame.density.flags.writeable
                assert frame.native_accumulation_sequence > 0
                journal = service.density_layer_journal_snapshots()[index]
                if case == "journal-failure":
                    assert frame.layer_ready is None
                    assert journal.state is LayerJournalState.INCOMPLETE
                    continue
                ref = frame.layer_ready
                assert ref.mapping is ReadyClockMapping.BOUNDED
                assert ref.session_id == snap.session_id and ref.owner_run_id == journal.scope.owner_run_id
                assert ref.identity.source_id == frame.source_id
                assert ref.identity.native_accumulation_sequence == frame.native_accumulation_sequence
                assert ref.producer_instance_id == journal.counters.producer_instance_id
                assert ref.ready_native_ns in [e.ready_native_ns for e in journal.events]
                if previous is not None:
                    assert ref.owner_run_id != previous[index].scope.owner_run_id
                    assert ref.producer_instance_id != previous[index].counters.producer_instance_id
            first_sequence = int(snapshots[0].spectrum.sequence)
            # Actual valid source continues even when optional journal fails.
            while True:
                values = service.poll_paired_frames() if paired else service.poll_frames()
                if values and int((values[-1].primary if paired else values[-1]).spectrum.sequence) > first_sequence:
                    break
                assert time.monotonic() < deadline
                assert service.latest_snapshot().error is None
                time.sleep(.002)
            assert app.stop().error is None
            final = service.density_layer_journal_snapshots()
            assert all(j.native_stop_confirmed for j in final)
            assert service._poller is service._engine is None
            assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
            if case != "disabled":
                for index, snap in enumerate(snapshots):
                    receiver = ("RX1", "RX2")[index]
                    raw = controls[-1].raw[receiver]
                    converted = service._convert_persistence(raw, snap, receiver_id=receiver)
                    assert converted.timestamp_ns == raw.timestamp_ns
                    assert converted.native_quality_flags == int(raw.quality_flags)
                    assert converted.native_accumulation_sequence == raw.layer_ready.accumulation_sequence
                    assert converted.accumulation_id == snap.session_id
                    if paired and index == 1:
                        wrong = service._convert_persistence(raw, snap, receiver_id="RX1")
                        assert wrong.layer_ready is None  # no borrowing RX1 creation evidence
            if case == "disabled":
                assert all(j.state is LayerJournalState.UNSUPPORTED and j.counters is None for j in final)
                assert not reads
            elif case == "journal-failure":
                assert all(j.state is LayerJournalState.INCOMPLETE for j in final)
                assert len(reads) == run + 1
            else:
                assert all(j.state is LayerJournalState.FINAL and j.counters.events_pending == 0 for j in final)
                assert all(j.scope.session_id == started.session_id for j in final)
                if paired:
                    assert final[0].scope.owner_run_id == final[1].scope.owner_run_id
                    assert final[0].counters.producer_instance_id != final[1].counters.producer_instance_id
                for index, journal in enumerate(final):
                    assert service._owner_journals[index]._host_budget == 786432
            # Explicit converter calls above count adapter receipt attempts;
            # they do not create events. Cached reads start from THAT state.
            before = len(reads), service.density_layer_journal_snapshots()
            assert tuple(j.counters for j in before[1]) == tuple(j.counters for j in final)
            for _ in range(20):
                service.latest_snapshot()
                service.density_layer_journal_snapshots()
            assert (len(reads), service.density_layer_journal_snapshots()) == before
            assert all(r[2] in ("sdr-native-live-poller", "MainThread") for r in reads)
            assert all(not r[3] for r in reads if r[2] == "MainThread")
            previous = final
    except BaseException:
        traceback.print_exc()  # retain first failure even if cleanup also refuses
        raise
    finally:
        app.shutdown()
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
'''


class CompiledPlutoDensityServiceTests(unittest.TestCase):
    def run_case(self, case):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected or not MOCK.is_file():
            self.skipTest("explicit matching native + built mock required; no physical fallback")
        environment = dict(os.environ)
        environment.update(LIBIIO_DLL_PATH=str(MOCK), SDR_MOCK_LIBIIO_CONTEXT_NAME="usb",
            SDR_MOCK_LIBIIO_BACKEND_URI="usb:2.42.5", SDR_MOCK_LIBIIO_REFILL_DELAY_MS="1",
            SDR_MOCK_LIBIIO_TOPOLOGY_DUAL="1" if case.startswith("paired") else "")
        result = subprocess.run([sys.executable, "-c", CODE, str(Path(selected).resolve(strict=True)), case],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=25, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_single_owner_original_density_stop_rearm(self):
        self.run_case("single")

    def test_actual_paired_owner_typed_distinct_creation_streams(self):
        self.run_case("paired")

    def test_disabled_density_has_no_drain_or_fabricated_counter(self):
        self.run_case("disabled")

    def test_optional_journal_failure_keeps_source_advancing_and_releases(self):
        self.run_case("journal-failure")

    def test_failed_activation_does_not_trap_journal_rearm(self):
        self.run_case("start-failure")

    def test_paired_failed_activation_releases_both_prepared_journals(self):
        self.run_case("paired-start-failure")

    def test_paired_scope_refusal_before_start_does_not_trap_prepared_owner(self):
        self.run_case("paired-scope-refusal")


if __name__ == "__main__":
    unittest.main()

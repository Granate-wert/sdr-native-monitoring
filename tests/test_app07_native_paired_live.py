"""Actual Live/application owner with explicitly selected native + mock IIO only."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest

from tests.native_test_dependencies import explicit_native_dependencies

ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")
MOCK = Path(os.environ.get("SDR_APP07_TEST_MOCK_LIBIIO",
    str(ROOT / "native/sdr_core/out/build/windows-msvc-cpu-hackrf/libiio.dll")))


@explicit_native_dependencies
def run_case(path: str, case: str) -> None:
    import ctypes

    from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService
    from sdr_monitor.application.live_session import LiveSessionApplicationService
    from sdr_monitor.domain import BackendKind, LiveConfiguration, RecordingOptions
    from sdr_monitor.domain.live import LiveSessionState
    from sdr_monitor.domain.paired_live import PairedLiveRequest
    from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
    from sdr_monitor.domain.analytical_journal import JournalState
    from sdr_monitor.services.native_live import NativeLiveSessionService

    module_path = Path(path).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
    assert spec is not None and spec.loader is not None
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert native.__file__ is not None and Path(native.__file__).resolve() == module_path
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    service = NativeLiveSessionService(native)

    class UnusedSweep:
        def start(self, request):
            raise AssertionError("RTBW never dispatches Sweep")

        def stop(self):
            raise AssertionError("RTBW never stops an unrelated Sweep")

    controller = AnalyzerSessionApplicationService(service, UnusedSweep(), start_live=service.start_admitted)
    app = LiveSessionApplicationService(service, analyzer=controller)

    def refuses(operation):
        try:
            operation()
        except (RuntimeError, ValueError, TypeError):
            return
        raise AssertionError("operation must refuse before mutation")

    try:
        selected = app.select_manual_uri("usb:mock")
        assert selected.error is None, selected.error
        profile = LiveConfiguration(center_hz=2_450_000_000., sample_rate_hz=61_440_000.,
            analog_bandwidth_hz=56_000_000., gain_db=43., fft_size=4096, backend=BackendKind.CPU)
        selected = app.apply_configuration(profile)
        assert selected.error is None and selected.applied is not None
        device = selected.device
        assert device is not None
        topology = device.capabilities.receiver_topology
        assert topology is not None
        if case == "single-layout":
            assert not topology.supports_selection(ReceiverChainSelection.BOTH)
            writes = hooks.mock_iio_rf_mutation_calls()
            refuses(lambda: PairedLiveRequest(device.device_id, str(selected.session_id), topology,
                selected.applied.applied, "actual:rx1", "actual:rx2"))
            assert hooks.mock_iio_rf_mutation_calls() == writes
            return
        assert topology.supports_selection(ReceiverChainSelection.BOTH)
        request = PairedLiveRequest(device.device_id, str(selected.session_id), topology,
            selected.applied.applied, "actual:rx1", "actual:rx2")
        writes = hooks.mock_iio_rf_mutation_calls()
        if case == "empty-serial":
            assert device.serial is None and device.capability_snapshot is None
            refuses(lambda: app.stage_paired_rtbw(request))
            assert hooks.mock_iio_rf_mutation_calls() == writes
            assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
            return
        assert device.serial == "MOCK" and device.capability_snapshot is not None
        if case == "guards":
            rx = native.PlutoReceiverSelection
            manual = native.GainMode.MANUAL
            def readback(gains):
                return SimpleNamespace(receiver_selection=rx.BOTH, receiver_gains=gains,
                    gain_mode=manual, config_generation=1, center_frequency_hz=profile.center_hz,
                    sample_rate_hz=profile.sample_rate_hz, analog_bandwidth_hz=profile.analog_bandwidth_hz,
                    manual_gain_db=43.)
            first = SimpleNamespace(receiver=rx.RX1, gain_mode=manual, manual_gain_db=43.)
            second = SimpleNamespace(receiver=rx.RX2, gain_mode=manual, manual_gain_db=43.)
            service._validate_paired_applied(readback([first, second]))
            for gains in ([], [first], [second, first], [first, first],
                          [first, SimpleNamespace(receiver=rx.RX2, gain_mode=manual, manual_gain_db=42.)],
                          [first, SimpleNamespace(receiver=rx.RX2, gain_mode=manual, manual_gain_db=float("nan"))]):
                refuses(lambda: service._validate_paired_applied(readback(gains)))
            for stale in (replace(request, device_id="other"), replace(request, session_id="old"),
                          replace(request, configuration=replace(profile, gain_db=40.))):
                refuses(lambda: app.stage_paired_rtbw(stale))
            refuses(lambda: replace(device, serial=None))  # USB/domain guard is earlier than paired admission
            for broken_device in (replace(device, serial=None, usb_connection=None), replace(device, identity_key=None),
                                  replace(device, capability_snapshot=None, calibration_identity=None)):
                broken = replace(selected, device=broken_device)
                refuses(lambda: request.validate_snapshot(broken))
            refuses(lambda: replace(request, secondary_source_id=request.primary_source_id))
        before = service.latest_snapshot()
        assert all(value.scope is None for value in service.analytical_journal_snapshots())
        assert app.stage_paired_rtbw(request) is before
        assert hooks.mock_iio_rf_mutation_calls() == writes
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert service.paired_performance() is None and app.poll_paired_publications() == ()
        refuses(service.poll_frames)
        refuses(lambda: app.apply_configuration(profile))
        refuses(service.acquire_native_sweep_lease)
        options = RecordingOptions(output_path=str(ROOT / "evidence/unused_paired_recording"))
        recording_before = service.native_recording_health()
        refuses(lambda: service.arm_native_recording(options))
        refuses(lambda: service.start_native_recording_now(options))
        assert service.native_recording_health() == recording_before
        assert hooks.mock_iio_rf_mutation_calls() == writes
        started = app.start()
        assert started.error is None and started.state is LiveSessionState.RUNNING, started.error
        assert started.applied is not None and started.acquisition_epoch is not None
        assert started.applied.applied.sample_rate_hz == 61_440_000.
        assert started.applied.applied.analog_bandwidth_hz == 56_000_000.
        assert started.spectrum is None and started.persistence is None  # control ≠ primary alias
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 1
        refuses(lambda: app.stage_paired_rtbw(request))
        refuses(app.clear_paired_rtbw)
        refuses(app.poll_published_snapshots)
        refuses(lambda: service.start_native_recording_now(options))
        assert service.is_running() and hooks.mock_iio_live_buffers() == 1
        seen: set[int] = set()
        deadline = time.monotonic() + 10
        latest = None
        while (len(seen) < 3 or latest is None
               or latest.primary.persistence is None or latest.secondary.persistence is None):
            assert service.latest_snapshot().error is None, service.latest_snapshot().error
            assert time.monotonic() < deadline, "bounded paired application publication deadline"
            publications = app.poll_paired_publications()
            assert len(publications) <= 1
            if publications:
                latest = publications[0]
                a, b = latest.primary, latest.secondary
                assert a.spectrum is not None and b.spectrum is not None
                assert a.receiver_id == a.spectrum.receiver_id == "RX1"
                assert b.receiver_id == b.spectrum.receiver_id == "RX2"
                assert a.active_source_id == a.spectrum.source_id == "actual:rx1"
                assert b.active_source_id == b.spectrum.source_id == "actual:rx2"
                assert a.acquisition_epoch == b.acquisition_epoch == started.acquisition_epoch
                assert a.spectrum.config_generation == b.spectrum.config_generation == started.active_config_generation
                assert a.spectrum.sequence == b.spectrum.sequence
                assert not a.spectrum.values.flags.writeable and not b.spectrum.values.flags.writeable
                assert not (a.spectrum.values == b.spectrum.values).all(), "actual distinct native RX values"
                for snapshot in (a, b):
                    assert snapshot.spectrum is not None
                    density = snapshot.persistence
                    if density is not None:
                        assert density.source_id == snapshot.spectrum.source_id
                        assert density.receiver_id == snapshot.receiver_id
                        assert density.source_frame_sequence <= snapshot.spectrum.sequence
                        assert density.config_generation == snapshot.active_config_generation
                seen.add(int(a.spectrum.sequence))
            time.sleep(.005)
        deadline = time.monotonic() + 2
        while app.paired_performance() is None:
            assert time.monotonic() < deadline
            time.sleep(.005)
        metrics = app.paired_performance()
        assert metrics is not None and latest is not None
        deadline = time.monotonic() + 2
        while any(value.counters is None for value in service.analytical_journal_snapshots()):
            assert time.monotonic() < deadline, "actual paired worker journal admission deadline"
            assert service.latest_snapshot().error is None
            time.sleep(.005)
        journals = service.analytical_journal_snapshots()
        assert len(journals) == 2
        assert all(value.state is JournalState.ACTIVE and value.scope is not None for value in journals)
        assert [value.scope.receiver_id for value in journals] == ["RX1", "RX2"]
        assert [value.scope.source_id for value in journals] == ["actual:rx1", "actual:rx2"]
        assert journals[0].scope.owner_run_id == journals[1].scope.owner_run_id
        assert journals[0].counters.producer_instance_id != journals[1].counters.producer_instance_id
        assert app.analytical_journal_snapshots() == journals
        for publication, journal in zip((latest.primary, latest.secondary), journals, strict=True):
            assert journal.native_presentation is not None and journal.native_presentation.forwarded > 0
            assert journal.native_presentation.accounting_failures == 0
            assert journal.native_owner_handoffs_unclassified >= 0
            assert journal.adapter is not None and journal.adapter.published_packets > 0
            assert journal.adapter.binding_failures == journal.adapter.unqualified_packets == 0
            assert journal.adapter_native_handoffs_unclassified >= 0
            ref = publication.spectrum.detector_ready
            assert ref is not None and ref.owner_run_id == journal.scope.owner_run_id
            assert ref.producer_instance_id == journal.counters.producer_instance_id
            assert ref.source_id == journal.scope.source_id
        assert sum(value.host_scalar_reserved_bytes for value in journals) == 2_097_152
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 1
        if case == "guards":
            for change in (
                    {"first_sample_index": -1}, {"shared_input_gaps_before": True},
                    {"secondary": replace(latest.secondary, receiver_id="RX1")},
                    {"secondary": replace(latest.secondary, active_source_id="other")},
                    {"secondary": replace(latest.secondary, clock_domain="other")},
                    {"secondary": replace(latest.secondary, active_config_generation=999999)}):
                refuses(lambda: replace(latest, **change))
            refuses(lambda: replace(metrics, iq_samples_received=-1))
            refuses(lambda: replace(metrics, paired_processing_ms=float("nan")))
        assert metrics.primary.fft_frames_computed > 0 and metrics.secondary.fft_frames_computed > 0
        assert metrics.primary.source_id == "actual:rx1" and metrics.secondary.source_id == "actual:rx2"
        assert metrics.iq_samples_received > 0 and metrics.paired_processing_ms > 0
        stopped = app.stop()
        assert stopped.error is None and not service.is_running()
        assert app.poll_paired_publications() == () and service.poll_paired_frames() == ()
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert service._poller is service._engine is None
        terminals = service.analytical_journal_snapshots()
        assert all(value.state is JournalState.FINAL and value.native_stop_confirmed for value in terminals)
        assert all(value.counters.outstanding == value.counters.events_pending == 0 for value in terminals)
        assert all(value.events for value in terminals)
        assert all(value.adapter is not None and value.adapter.published_packets > 0 for value in terminals)
        assert all(value.adapter.binding_failures == value.adapter.unqualified_packets == 0 for value in terminals)
        assert service._journal_engine is None
        assert app.paired_performance() is not None
        # Stopped plan is armed only; separate Start makes a fresh actual epoch.
        restarted = app.start()
        assert restarted.error is None and restarted.acquisition_epoch is not None
        assert restarted.acquisition_epoch > started.acquisition_epoch
        deadline = time.monotonic() + 10
        while True:
            values = app.poll_paired_publications()
            if values:
                assert values[0].primary.acquisition_epoch == restarted.acquisition_epoch
                break
            assert service.latest_snapshot().error is None, service.latest_snapshot().error
            assert time.monotonic() < deadline
            time.sleep(.005)
        app.stop()
        fresh = service.analytical_journal_snapshots()
        assert all(value.state is JournalState.FINAL for value in fresh)
        assert fresh[0].scope.owner_run_id != terminals[0].scope.owner_run_id
        assert all(history == (terminal,) for history, terminal in
                   zip(service.analytical_journal_terminal_history(), terminals, strict=True))
        app.clear_paired_rtbw()
        assert service.poll_frames() == [] and service.paired_performance() is None
        app.apply_configuration(profile)
    finally:
        app.shutdown()
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
    assert service._poller is service._engine is None
    print("actual same Live/application paired RTBW owner PASS; mock only; no product/UI/RF proof")


@unittest.skipUnless(MODULE and MOCK.is_file(), "requires explicit matching native + built mock")
class NativePairedLiveTests(unittest.TestCase):
    def _run(self, case: str) -> None:
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK)
        # Current native USB admission observes the SAME context, not caller
        # aliases. Keep one deterministic mock connection across owner opens.
        environment["SDR_MOCK_LIBIIO_CONTEXT_NAME"] = "usb"
        environment["SDR_MOCK_LIBIIO_BACKEND_URI"] = "usb:2.42.5"
        environment["SDR_MOCK_LIBIIO_TOPOLOGY_DUAL"] = "1"
        if case == "single-layout":
            environment.pop("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", None)
        if case == "empty-serial":
            environment["SDR_MOCK_LIBIIO_EMPTY_SERIAL"] = "1"
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        command = "from tests.test_app07_native_paired_live import run_case; import sys; run_case(sys.argv[1],sys.argv[2])"
        result = subprocess.run([sys.executable, "-c", command, MODULE, case], cwd=ROOT,
            env=environment, text=True, capture_output=True, timeout=45, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_same_owner_pair_and_explicit_next_start(self) -> None:
        self._run("delivery")

    def test_stale_identity_profile_recording_and_single_port_guards(self) -> None:
        self._run("guards")

    def test_observed_single_rx_layout_refuses_before_rf(self) -> None:
        self._run("single-layout")

    def test_empty_serial_default_route_guard_is_not_transferred(self) -> None:
        self._run("empty-serial")

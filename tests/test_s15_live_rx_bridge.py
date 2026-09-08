"""Native Pluto RX bridge tests without requiring real hardware.

Exercises ``NativeLiveSessionService`` end to end against a fake native
module: discovery, selection, configuration, engine start, the bounded
latest-wins poller, drop accounting, engine error propagation, cancellation
and the start guards.  The fake native module is the only dependency; no
``esw_dfl`` or ``_sgram_native`` import is allowed on this path.
"""

from __future__ import annotations

import time
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.domain import (
    BackendKind,
    DeviceTransport,
    LiveConfiguration,
    RecordingOptions,
    RecordingState,
    LiveSessionState,
)
from sdr_monitor.services.native_live import NativeLiveSessionService


def _wait_for(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _make_frame(
    sequence: int,
    *,
    fft_size: int = 4096,
    hop_size: int = 2048,
    center_hz: float = 2.4e9,
    sample_rate_hz: float = 2e6,
    unit_name: str = "DBFS_BIN",
    source_id: str = "fake-pluto",
    config_generation: int = 0,
    quality_flags: int = 0,
    dropped_samples_before: int = 0,
    dropped_iq_blocks_before: int = 0,
    dropped_fft_frames_before: int = 0,
) -> SimpleNamespace:
    """Build a fake native spectrum frame with real NumPy arrays."""
    frequencies = np.linspace(-sample_rate_hz / 2.0, sample_rate_hz / 2.0, fft_size, dtype=np.float64)
    values = np.zeros(fft_size, dtype=np.float32)
    return SimpleNamespace(
        source=SimpleNamespace(source_id=source_id),
        frame_sequence=sequence,
        timestamp_ns=1_000_000_000 + sequence,
        config_generation=config_generation,
        quality_flags=quality_flags,
        center_frequency_hz=center_hz,
        sample_rate_hz=sample_rate_hz,
        fft_size=fft_size,
        hop_size=hop_size,
        frequencies_hz=frequencies,
        values=values,
        unit=SimpleNamespace(name=unit_name),
        dropped_samples_before=dropped_samples_before,
        dropped_iq_blocks_before=dropped_iq_blocks_before,
        dropped_fft_frames_before=dropped_fft_frames_before,
    )


class _FakeNativeDevice:
    def __init__(self, capabilities: object) -> None:
        self._capabilities = capabilities
        self.disconnected = False
        self.probe_calls = 0

    def capabilities(self) -> object:
        return self._capabilities

    def probe(self) -> object:
        self.probe_calls += 1
        return SimpleNamespace()

    def disconnect(self) -> None:
        self.disconnected = True


class _FakeEngine:
    """Records the native engine lifecycle and serves scripted frames."""

    def __init__(self, uri: str, timeout_ms: int) -> None:
        self.uri = uri
        self.timeout_ms = timeout_ms
        self.configure_calls = 0
        self.last_config: object | None = None
        self.start_calls = 0
        self.request_stop_calls = 0
        self.join_calls = 0
        self.disconnect_calls = 0
        self.configure_error: Exception | None = None
        self.frames: list[object] = []
        self.frames_exhaust = False
        self.persistence: list[object] = []
        self.latest_drain_calls = 0
        self.latest_drain_enabled = True
        self._states = [LiveSessionState.RUNNING]
        self.metrics_result = SimpleNamespace(
            device=SimpleNamespace(output_blocks_dropped=0, short_reads=0),
            spectrum_queue=SimpleNamespace(dropped=0),
        )
        self.applied = SimpleNamespace(active_backend=SimpleNamespace(name="AUTO"))

    def configure(self, config: object) -> object:
        self.configure_calls += 1
        self.last_config = config
        if self.configure_error is not None:
            raise self.configure_error
        return self.applied

    def start(self) -> None:
        self.start_calls += 1

    def request_stop(self) -> None:
        self.request_stop_calls += 1

    def join(self) -> None:
        self.join_calls += 1

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def poll_spectrum_frames(self, max_items: int) -> list[object]:
        del max_items
        if self.frames_exhaust:
            return []
        return self.frames

    def drain_latest_spectrum_frame(self) -> object:
        self.latest_drain_calls += 1
        if not self.latest_drain_enabled or self.frames_exhaust or not self.frames:
            return SimpleNamespace(frame=None, coalesced_frames=0)
        return SimpleNamespace(
            frame=self.frames[-1],
            coalesced_frames=max(0, len(self.frames) - 1),
        )

    def poll_persistence_snapshots(self, max_items: int) -> list[object]:
        del max_items
        return self.persistence

    def state(self) -> LiveSessionState:
        value = self._states[0]
        if len(self._states) > 1:
            self._states = self._states[1:]
        return value

    def metrics(self) -> object:
        return self.metrics_result


class _DeviceConfig:
    def __init__(self, *args: object) -> None:
        self.args = args


class _DspConfig:
    def __init__(self, *args: object) -> None:
        self.args = args


class _PersistenceConfig:
    def __init__(self, *args: object) -> None:
        self.args = args


class _RecordingConfig:
    def __init__(self, *args: object) -> None:
        self.args = args


class _FixedBandConfig:
    def __init__(self, *args: object) -> None:
        self.args = args


class _GainMode:
    MANUAL = "manual"


class _WindowType:
    HANN = "hann"


class _DetectorType:
    SAMPLE = "sample"


class _SpectrumUnit:
    DBFS_BIN = "dbfs_bin"


class _PrecisionMode:
    ACCURATE_F32_F64_ACCUM = "accurate_f32_f64_accum"


class _CalibrationStatus:
    UNCALIBRATED = "uncalibrated"


class _PersistenceMode:
    DISABLED = "disabled"
    EXPONENTIAL_DECAY = "exponential_decay"
    ROLLING_EXACT = "rolling_exact"


class _ComputeBackendKind:
    AUTO = "auto"


class _OverflowPolicy:
    DROP_NEWEST = "drop_newest"


class _QualityFlag:
    IQ_DROPPED = 1 << 5
    FFT_DROPPED = 1 << 6
    TIMESTAMP_ESTIMATED = 1 << 13
    BACKEND_FALLBACK = 1 << 14
    BACKEND_DISCONTINUITY = 1 << 15


class _FakeNative:
    """Stand-in for the packaged ``_sdr_native`` extension."""

    def __init__(self) -> None:
        numeric_range = SimpleNamespace(minimum=1e6, maximum=30e6, step=1.0)
        self.capabilities = SimpleNamespace(
            model="Analog Devices PlutoSDR",
            sample_rate_ranges_hz=(numeric_range,),
            gain_range_db=SimpleNamespace(minimum=-3.0, maximum=71.0, step=1.0),
        )
        self.created_devices: list[_FakeNativeDevice] = []
        self.engines: list[_FakeEngine] = []
        self.engine_configure_error: Exception | None = None
        self.DeviceConfig = _DeviceConfig
        self.DspConfig = _DspConfig
        self.PersistenceConfig = _PersistenceConfig
        self.RecordingConfig = _RecordingConfig
        self.FixedBandConfig = _FixedBandConfig
        self.GainMode = _GainMode
        self.WindowType = _WindowType
        self.DetectorType = _DetectorType
        self.SpectrumUnit = _SpectrumUnit
        self.PrecisionMode = _PrecisionMode
        self.CalibrationStatus = _CalibrationStatus
        self.PersistenceMode = _PersistenceMode
        self.ComputeBackendKind = _ComputeBackendKind
        self.OverflowPolicy = _OverflowPolicy
        self.QualityFlag = _QualityFlag

    def build_info(self) -> dict[str, object]:
        return {"pluto_compiled": True, "cuda_compiled": True}

    def scan_pluto_contexts(self, filter_value: str) -> tuple[object, ...]:
        assert filter_value == "usb,ip"
        return (SimpleNamespace(uri="ip:pluto.local", description="192.168.2.1"),)

    def probe_pluto_context(self, uri: str, timeout_ms: int) -> object:
        assert uri == "ip:pluto.local"
        assert timeout_ms == 3000
        return SimpleNamespace(model="Analog Devices PlutoSDR", firmware="v0.38")

    def PlutoDevice(self, uri: str, timeout_ms: int) -> _FakeNativeDevice:
        assert uri == "ip:pluto.local"
        assert timeout_ms == 3000
        device = _FakeNativeDevice(self.capabilities)
        self.created_devices.append(device)
        return device

    def PlutoFixedBandEngine(self, uri: str, timeout_ms: int) -> _FakeEngine:
        engine = _FakeEngine(uri, timeout_ms)
        engine.configure_error = self.engine_configure_error
        self.engines.append(engine)
        return engine


class NativeLiveRxBridgeTests(unittest.TestCase):
    def _connected_service(self, native: _FakeNative | None = None) -> tuple[_FakeNative, NativeLiveSessionService]:
        native = native or _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        service.apply_configuration(
            LiveConfiguration(center_hz=2.4e9, sample_rate_hz=2e6, gain_db=18.0)
        )
        return native, service

    def test_discovery_maps_native_context_to_domain_descriptor(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)

        devices = service.discover_devices()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].uri, "ip:pluto.local")
        self.assertEqual(devices[0].transport, DeviceTransport.IP)
        self.assertEqual(devices[0].capabilities.supported_backends, (BackendKind.AUTO, BackendKind.CPU, BackendKind.CUDA))

    def test_full_happy_path_starts_engine(self) -> None:
        native, service = self._connected_service()
        try:
            snapshot = service.start()

            self.assertEqual(snapshot.state, LiveSessionState.RUNNING)
            self.assertEqual(snapshot.sequence, 0)
            self.assertEqual(snapshot.quality.backend, BackendKind.AUTO)
            engine = native.engines[-1]
            self.assertEqual(engine.uri, "ip:pluto.local")
            self.assertEqual(engine.timeout_ms, 3000)
            self.assertEqual(engine.configure_calls, 1)
            self.assertEqual(engine.start_calls, 1)
            self.assertTrue(service.is_running())
        finally:
            service.stop()

    def test_poller_publishes_frames(self) -> None:
        native, service = self._connected_service()
        try:
            frame = _make_frame(sequence=7)
            service.start()
            native.engines[-1].frames = [frame]

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
            snapshot = service.latest_snapshot()
            spectrum = snapshot.spectrum
            self.assertIsNotNone(spectrum)
            self.assertEqual(spectrum.sequence, 7)
            self.assertEqual(spectrum.unit, "DBFS_BIN")
            self.assertEqual(spectrum.values.dtype, np.float32)
            self.assertFalse(spectrum.values.flags.writeable)
            self.assertEqual(spectrum.frequencies_hz.dtype, np.float64)
            self.assertFalse(spectrum.frequencies_hz.flags.writeable)
            self.assertEqual(snapshot.state, LiveSessionState.RUNNING)
            self.assertEqual(snapshot.sequence, 1)
        finally:
            service.stop()

    def test_poller_preserves_native_identity_timestamp_and_loss_metadata(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            native.engines[-1].frames = [
                _make_frame(
                    sequence=7,
                    source_id="pluto:fixture",
                    config_generation=12,
                    quality_flags=(1 << 0) | (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 13),
                    dropped_samples_before=4,
                    dropped_iq_blocks_before=1,
                    dropped_fft_frames_before=2,
                )
            ]

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
            spectrum = service.latest_snapshot().spectrum
            self.assertIsNotNone(spectrum)
            self.assertEqual(spectrum.source_id, "pluto:fixture")
            self.assertEqual(spectrum.config_generation, 12)
            self.assertEqual(spectrum.native_quality_flags,
                             (1 << 0) | (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 13))
            self.assertEqual(spectrum.timestamp_quality.value, "estimated")
            self.assertEqual(
                tuple(reason.value for reason in spectrum.loss_reasons),
                ("source", "acquisition_queue", "dsp"),
            )
        finally:
            service.stop()

    def test_cuda_failover_is_visible_at_the_live_frame_boundary(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            engine = native.engines[-1]
            engine.metrics_result = SimpleNamespace(
                device=SimpleNamespace(output_blocks_dropped=0, short_reads=0),
                spectrum_queue=SimpleNamespace(dropped=0),
                active_backend=SimpleNamespace(name="CPU"),
                backend_fallback_count=1,
                last_backend_error=SimpleNamespace(name="FFT_EXECUTION_FAILED"),
            )
            engine.frames = [
                _make_frame(
                    sequence=7,
                    quality_flags=(1 << 6) | (1 << 14) | (1 << 15),
                )
            ]

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
            snapshot = service.latest_snapshot()
            spectrum = snapshot.spectrum
            self.assertIsNotNone(spectrum)
            self.assertTrue(spectrum.backend_fallback)
            self.assertTrue(spectrum.backend_discontinuity)
            self.assertIn("dsp", tuple(reason.value for reason in spectrum.loss_reasons))
            self.assertEqual(snapshot.quality.backend, BackendKind.CPU)
            self.assertEqual(snapshot.quality.fallback_reason, "backend fallback 1: fft_execution_failed")
            self.assertTrue(snapshot.quality.backend_discontinuity)

            engine.frames = [_make_frame(sequence=8)]
            self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None and service.latest_snapshot().spectrum.sequence == 8))
            recovered = service.latest_snapshot()
            self.assertIsNotNone(recovered.spectrum)
            self.assertFalse(recovered.spectrum.backend_discontinuity)
            self.assertEqual(recovered.quality.backend, BackendKind.CPU)
            self.assertFalse(recovered.quality.backend_discontinuity)
        finally:
            service.stop()

    def test_latest_wins_publishes_last_frame_of_batch(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            native.engines[-1].frames = [_make_frame(sequence=1), _make_frame(sequence=2), _make_frame(sequence=3)]

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
            self.assertEqual(service.latest_snapshot().spectrum.sequence, 3)
            self.assertGreater(native.engines[-1].latest_drain_calls, 0)
        finally:
            service.stop()

    def test_latest_native_drain_reports_exact_coalescing_without_poll_list(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            engine = native.engines[-1]
            engine.poll_spectrum_frames = lambda _max_items: self.fail(
                "fresh native bridge must not materialize a Python frame list"
            )
            engine.frames = [_make_frame(sequence=1), _make_frame(sequence=2), _make_frame(sequence=3)]

            def drain_once() -> object:
                if engine.frames_exhaust:
                    return SimpleNamespace(frame=None, coalesced_frames=0)
                engine.frames_exhaust = True
                return SimpleNamespace(frame=engine.frames[-1], coalesced_frames=2)

            engine.drain_latest_spectrum_frame = drain_once

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
            self.assertEqual(service.latest_snapshot().spectrum.sequence, 3)
            self.assertTrue(_wait_for(
                lambda: service._bridge_native_frames_polled == 3
            ))
            self.assertEqual(service._bridge_frames_coalesced, 2)
            self.assertEqual(service._bridge_frames_published, 1)
        finally:
            service.stop()

    def test_drop_accounting_from_engine_metrics(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            engine = native.engines[-1]
            engine.metrics_result = SimpleNamespace(
                device=SimpleNamespace(output_blocks_dropped=3, short_reads=0),
                spectrum_queue=SimpleNamespace(dropped=2),
            )
            engine.frames = [_make_frame(sequence=1)]

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
            self.assertEqual(service.latest_snapshot().quality.dropped_blocks, 5)
        finally:
            service.stop()

    def test_engine_error_state_terminates_poller(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            engine = native.engines[-1]
            engine.frames = [_make_frame(sequence=1)]
            engine.frames_exhaust = True
            engine._states = [LiveSessionState.RUNNING, LiveSessionState.ERROR]

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().state is LiveSessionState.ERROR))
            snapshot = service.latest_snapshot()
            self.assertIn("error state", snapshot.error)
            poller = service._poller
            if poller is not None:
                poller.join(timeout=1.0)
                self.assertFalse(poller.is_alive())
            self.assertFalse(service.is_running())
        finally:
            service.stop()

    def test_stalled_stream_is_reported_as_error(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            engine = native.engines[-1]
            engine.frames = []
            engine.frames_exhaust = True
            engine._states = [LiveSessionState.RUNNING]

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().state is LiveSessionState.ERROR, timeout=5.0))
            snapshot = service.latest_snapshot()
            self.assertIn("stalled", snapshot.error)
            self.assertFalse(service.is_running())
        finally:
            service.stop()

    def test_start_failure_disconnects_engine(self) -> None:
        native, service = self._connected_service()
        try:
            native.engine_configure_error = RuntimeError("boom")

            failed = service.start()

            self.assertEqual(failed.state, LiveSessionState.ERROR)
            self.assertIn("Pluto RX start failed", failed.error)
            self.assertIn("boom", failed.error)
            engine = native.engines[-1]
            self.assertEqual(engine.disconnect_calls, 1)
            self.assertEqual(engine.start_calls, 0)
        finally:
            service.stop()

    def test_stop_disconnects_engine_and_poller(self) -> None:
        native, service = self._connected_service()
        service.start()
        engine = native.engines[-1]
        poller = service._poller
        self.assertIsNotNone(poller)

        stopped = service.stop()

        self.assertEqual(stopped.state, LiveSessionState.CONNECTED)
        self.assertEqual(engine.request_stop_calls, 1)
        self.assertEqual(engine.join_calls, 1)
        self.assertEqual(engine.disconnect_calls, 1)
        time.sleep(0.05)
        self.assertFalse(poller.is_alive())
        self.assertFalse(service.is_running())

    def test_stop_and_wait_disconnects(self) -> None:
        native, service = self._connected_service()
        service.start()
        engine = native.engines[-1]

        service.stop_and_wait(timeout_s=1.0)

        self.assertEqual(engine.request_stop_calls, 1)
        self.assertEqual(engine.join_calls, 1)
        self.assertEqual(engine.disconnect_calls, 1)
        self.assertEqual(service.latest_snapshot().state, LiveSessionState.CONNECTED)
        self.assertFalse(service.is_running())

    def test_start_without_applied_config_returns_error(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        try:
            device = service.discover_devices()[0]
            service.select_device(device.device_id)

            failed = service.start()

            self.assertEqual(failed.state, LiveSessionState.ERROR)
            self.assertIn("Apply a live configuration before starting", failed.error)
            self.assertEqual(native.engines, [])
        finally:
            service.stop()

    def test_start_twice_returns_already_started(self) -> None:
        native, service = self._connected_service()
        try:
            first = service.start()
            self.assertEqual(first.state, LiveSessionState.RUNNING)

            second = service.start()

            self.assertEqual(second.state, LiveSessionState.ERROR)
            self.assertIn("already started", second.error)
            self.assertEqual(len(native.engines), 1)
        finally:
            service.stop()

    def test_poll_frames_only_while_running(self) -> None:
        native, service = self._connected_service()
        try:
            self.assertEqual(service.poll_frames(), [])
            service.start()
            self.assertEqual(len(service.poll_frames()), 1)
            self.assertEqual(service.poll_frames()[0].state, LiveSessionState.RUNNING)
        finally:
            service.stop()

    def test_persistence_snapshot_is_published(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            engine = native.engines[-1]
            density = np.zeros((256, 4096), dtype=np.float32)
            engine.persistence = [
                SimpleNamespace(
                    update_sequence=42,
                    timestamp_ns=7,
                    source_frame_sequence=41,
                    source_id="density-source",
                    config_generation=93,
                    unit="dBFS/Hz",
                    power_min_db=-140.0,
                    power_max_db=20.0,
                    power_bins=256,
                    frequency_bins=4096,
                    processed_frames=2048,
                    exponential_decay=True,
                    probability_scale=0.25,
                    count_scale=0.5,
                    frequencies_hz=np.linspace(2.399e9, 2.401e9, 4096, dtype=np.float64),
                    density=density,
                )
            ]

            self.assertTrue(_wait_for(lambda: service.latest_snapshot().persistence is not None))
            persistence = service.latest_snapshot().persistence
            self.assertIsNotNone(persistence)
            self.assertEqual(persistence.update_sequence, 42)
            self.assertEqual(persistence.source_id, "density-source")
            self.assertEqual(persistence.config_generation, 93)
            self.assertEqual(persistence.unit, "dBFS/Hz")
            self.assertTrue(persistence.producer_identity_available)
            self.assertEqual(persistence.frequency_bins, 4096)
            self.assertEqual(persistence.power_bins, 256)
            self.assertTrue(persistence.exponential_decay)
            self.assertEqual(persistence.density.shape, (256, 4096))
            self.assertEqual(persistence.density.dtype, np.float32)
            self.assertFalse(persistence.density.flags.writeable)
            self.assertTrue(np.shares_memory(persistence.density, density))
            self.assertEqual(persistence.probability_scale, 0.25)
            self.assertEqual(persistence.count_scale, 0.5)
            self.assertEqual(persistence.frequencies_hz.dtype, np.float64)
        finally:
            service.stop()

    def test_persistence_without_identity_does_not_borrow_latest_spectrum(self) -> None:
        native, service = self._connected_service()
        try:
            service.start()
            engine = native.engines[-1]
            engine.frames = [_make_frame(9, source_id="unrelated-spectrum", config_generation=71)]
            self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
            engine.persistence = [SimpleNamespace(
                update_sequence=10, timestamp_ns=7, source_frame_sequence=3,
                power_min_db=-140.0, power_max_db=20.0,
                power_bins=2, frequency_bins=2, processed_frames=1,
                exponential_decay=False,
                frequencies_hz=np.array([2.399e9, 2.401e9], dtype=np.float64),
                density=np.zeros((2, 2), dtype=np.float32),
            )]
            self.assertTrue(_wait_for(lambda: service.latest_snapshot().persistence is not None))
            persistence = service.latest_snapshot().persistence
            self.assertIsNotNone(persistence)
            self.assertEqual(persistence.source_id, "unknown")
            self.assertEqual(persistence.config_generation, 0)
            self.assertIsNone(persistence.unit)
            self.assertFalse(persistence.producer_identity_available)
        finally:
            service.stop()

    def test_arm_recording_defers_writer_configuration_until_next_live_start(self) -> None:
        native, service = self._connected_service()
        with tempfile.TemporaryDirectory() as temporary:
            output = str(Path(temporary) / "armed")
            armed = service.arm_native_recording(RecordingOptions(output))

            self.assertEqual(armed.state, RecordingState.ARMED)
            self.assertFalse(service.is_running())
            service.start()
            engine = native.engines[-1]
            self.assertEqual(engine.configure_calls, 1)
            self.assertIsInstance(engine.last_config, _FixedBandConfig)
            assert isinstance(engine.last_config, _FixedBandConfig)
            self.assertIsInstance(engine.last_config.args[-1], _RecordingConfig)
            live_health = service.native_recording_health()
            self.assertEqual(live_health.state, RecordingState.RECORDING)
            self.assertEqual(live_health.epoch, 1)
            self.assertEqual(live_health.recording_mode, "rtbw")
            service.stop()
            lifecycle = Path(f"{output}.sdr-lifecycle.jsonl")
            self.assertTrue(lifecycle.exists())
            self.assertIn('"type":"epoch_start"', lifecycle.read_text(encoding="utf-8"))

    def test_start_recording_now_restarts_live_and_records_control_gap(self) -> None:
        native, service = self._connected_service()
        with tempfile.TemporaryDirectory() as temporary:
            output = str(Path(temporary) / "restart")
            service.start()
            first = native.engines[-1]
            health = service.start_native_recording_now(RecordingOptions(output))

            self.assertEqual(len(native.engines), 2)
            self.assertEqual(first.request_stop_calls, 1)
            self.assertEqual(first.join_calls, 1)
            self.assertEqual(first.disconnect_calls, 1)
            self.assertEqual(health.state, RecordingState.RECORDING)
            self.assertEqual(health.epoch, 1)
            self.assertIsNotNone(health.restart_gap_duration_ns)
            service.stop()
            lifecycle = Path(f"{output}.sdr-lifecycle.jsonl")
            text = lifecycle.read_text(encoding="utf-8")
            self.assertIn('"reason":"controlled_live_restart"', text)
            self.assertIn('"gap_scope":"live_control_transaction"', text)

    def test_native_recording_health_reports_recorder_only_loss_and_average_rate(self) -> None:
        native, service = self._connected_service()
        with tempfile.TemporaryDirectory() as temporary:
            service.arm_native_recording(RecordingOptions(str(Path(temporary) / "metrics")))
            service.start()
            native.engines[-1].metrics_result = SimpleNamespace(
                recorder_queue=SimpleNamespace(depth=2, capacity=8),
                recorder_writer_blocks_written=5,
                recorder_writer_samples_written=900,
                recorder_writer_bytes_written=3600,
                recorder_queue_blocks_dropped=1,
                recorder_writer_blocks_unavailable=0,
                recorder_shutdown_blocks_discarded=0,
                recorder_queue_samples_dropped=100,
                recorder_writer_samples_unavailable=0,
                recorder_shutdown_samples_discarded=0,
                spectrum_recorder_frames_dropped=0,
                spectrum_recorder_frames_unavailable=0,
                spectrum_recorder_shutdown_frames_discarded=0,
                spectrum_writer_frames_written=0,
                spectrum_writer_bytes_written=0,
                recorder_writer_failed=False,
                spectrum_writer_failed=False,
            )
            time.sleep(0.01)
            health = service.native_recording_health()

            self.assertEqual(health.queue_depth, 2)
            self.assertEqual(health.recorded_iq_samples, 900)
            self.assertAlmostEqual(health.iq_loss_rate, 0.1)
            self.assertGreater(health.average_iq_sample_rate_hz, 0.0)
            self.assertEqual(health.drop_reasons[0].value, "recorder")
            service.stop()


if __name__ == "__main__":
    unittest.main()

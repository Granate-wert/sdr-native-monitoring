"""Native Pluto discovery adapter tests without requiring real hardware."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from sdr_monitor.domain import BackendKind, DeviceTransport, LiveConfiguration, SweepConfiguration, SweepExecutionMode, LiveSessionState
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.native_sweep import NativeLiveSweepService


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
    """Minimal engine stub so start/stop guard tests never touch hardware."""

    def __init__(self, uri: str, timeout_ms: int) -> None:
        self.uri = uri
        self.timeout_ms = timeout_ms
        self.configure_calls = 0
        self.start_calls = 0
        self.request_stop_calls = 0
        self.join_calls = 0
        self.disconnect_calls = 0
        self.configure_error: Exception | None = None
        self._states = [LiveSessionState.RUNNING]
        self.applied = SimpleNamespace(active_backend=SimpleNamespace(name="AUTO"))

    def configure(self, config: object) -> object:
        self.configure_calls += 1
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
        return []

    def poll_persistence_snapshots(self, max_items: int) -> list[object]:
        del max_items
        return []

    def state(self) -> LiveSessionState:
        return self._states[0]

    def metrics(self) -> object:
        return SimpleNamespace(
            device=SimpleNamespace(output_blocks_dropped=0, short_reads=0),
            spectrum_queue=SimpleNamespace(dropped=0),
        )


class _DeviceConfig:
    def __init__(self, *args: object) -> None:
        self.args = args


class _DspConfig:
    def __init__(self, *args: object) -> None:
        self.args = args


class _PersistenceConfig:
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
    CPU = "cpu"


class _OverflowPolicy:
    DROP_NEWEST = "drop_newest"


class _FakeNative:
    def __init__(self) -> None:
        numeric_range = SimpleNamespace(minimum=1e6, maximum=30e6, step=1.0)
        self.capabilities = SimpleNamespace(
            model="Analog Devices PlutoSDR",
            sample_rate_ranges_hz=(numeric_range,),
            gain_range_db=SimpleNamespace(minimum=-3.0, maximum=71.0, step=1.0),
        )
        self.created: list[_FakeNativeDevice] = []
        self.engines: list[_FakeEngine] = []
        self.engine_configure_error: Exception | None = None
        self.engine_configure_errors: dict[str, Exception] = {}
        self.probe_errors: dict[str, Exception] = {}
        self.DeviceConfig = _DeviceConfig
        self.DspConfig = _DspConfig
        self.PersistenceConfig = _PersistenceConfig
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
        assert timeout_ms == 3000
        error = self.probe_errors.get(uri)
        if error is not None:
            raise error
        device = _FakeNativeDevice(self.capabilities)
        self.created.append(device)
        return device

    def PlutoFixedBandEngine(self, uri: str, timeout_ms: int) -> _FakeEngine:
        engine = _FakeEngine(uri, timeout_ms)
        engine.configure_error = self.engine_configure_errors.get(
            uri,
            self.engine_configure_error,
        )
        self.engines.append(engine)
        return engine


class NativeLiveDiscoveryTests(unittest.TestCase):
    def test_native_sweep_lease_rejects_running_live_without_a_hidden_restart(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        service.apply_configuration(LiveConfiguration(
            sample_rate_hz=2e6,
            analog_bandwidth_hz=2e6,
            gain_db=18.0,
            backend=BackendKind.CPU,
        ))

        lease = service.acquire_native_sweep_lease()

        self.assertEqual(lease.source.context_uri, "ip:pluto.local")
        self.assertEqual(lease.source.live_configuration.backend, BackendKind.CPU)
        blocked = service.start()
        self.assertEqual(blocked.state, LiveSessionState.ERROR)
        self.assertIn("Native Sweep owns", blocked.error)
        self.assertEqual(native.engines, [])
        lease.release()

    def test_native_sweep_plan_is_explicit_and_releases_lease_without_hardware(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        service.apply_configuration(LiveConfiguration(
            sample_rate_hz=2e6,
            analog_bandwidth_hz=2e6,
            backend=BackendKind.CPU,
        ))
        sweep = NativeLiveSweepService(service)

        plan = sweep.plan(SweepConfiguration(
            start_hz=400e6,
            stop_hz=401e6,
            dc_margin_hz=100e3,
            execution_mode=SweepExecutionMode.NATIVE,
        ))

        self.assertGreaterEqual(len(plan.segments), 1)
        self.assertFalse(service._sweep_lease_active)
        self.assertEqual(native.engines, [])

    def test_discovery_maps_native_context_to_domain_descriptor(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)

        devices = service.discover_devices()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].uri, "ip:pluto.local")
        self.assertEqual(devices[0].transport, DeviceTransport.IP)
        self.assertEqual(devices[0].capabilities.gain_range_db, (-3.0, 71.0))
        self.assertIn(20e6, devices[0].capabilities.sample_rates_hz)
        self.assertEqual(devices[0].capabilities.supported_backends, (BackendKind.AUTO, BackendKind.CPU, BackendKind.CUDA))
        self.assertEqual(len(native.created), 1)
        self.assertTrue(native.created[0].disconnected)

    def test_selection_probes_real_context_and_releases_it(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]

        snapshot = service.select_device(device.device_id)
        self.assertEqual(snapshot.state, LiveSessionState.CONNECTED)
        self.assertEqual(snapshot.device.uri, "ip:pluto.local")
        # The probe device context must be released immediately: holding a USB
        # context open would prevent the engine from opening its own context
        # in start() ("iio_create_context_from_uri failed").
        self.assertTrue(native.created[-1].disconnected)
        self.assertGreaterEqual(native.created[-1].probe_calls, 1)

        stopped = service.stop()
        self.assertEqual(stopped.state, LiveSessionState.CONNECTED)

    def test_start_without_applied_config_returns_error(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)

        failed = service.start()

        self.assertEqual(failed.state, LiveSessionState.ERROR)
        self.assertIn("Apply a live configuration before starting", failed.error)
        self.assertEqual(native.engines, [])


    def test_start_falls_back_from_usb_to_ip_without_rediscovery(self) -> None:
        native = _FakeNative()
        native.engine_configure_errors["usb:3.11.5"] = RuntimeError(
            "iio_create_context_from_uri failed"
        )
        service = NativeLiveSessionService(native)
        discovered = service.discover_devices()[0]
        logical = discovered.__class__(
            device_id=discovered.device_id,
            label=discovered.label,
            uri="usb:3.11.5",
            transport=DeviceTransport.USB,
            capabilities=discovered.capabilities,
            alternate_uris=("ip:pluto.local",),
            serial=discovered.serial,
            identity_key=discovered.identity_key,
        )
        service._devices = (logical,)
        selected = service.select_device(logical.device_id)
        self.assertEqual(selected.state, LiveSessionState.CONNECTED)
        self.assertEqual(selected.device.uri, "usb:3.11.5")
        service.apply_configuration(
            LiveConfiguration(
                center_hz=2.4e9,
                sample_rate_hz=2e6,
                gain_db=18.0,
            )
        )

        started = service.start()

        self.assertEqual(started.state, LiveSessionState.RUNNING)
        self.assertEqual(started.device.uri, "ip:pluto.local")
        self.assertEqual([engine.uri for engine in native.engines], [
            "usb:3.11.5",
            "ip:pluto.local",
        ])
        service.stop()


    def test_retry_after_stream_error_releases_old_engine_and_reuses_device(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        service.apply_configuration(
            LiveConfiguration(center_hz=2.4e9, sample_rate_hz=2e6, gain_db=18.0)
        )
        service.start()
        first_engine = native.engines[-1]
        service._publish_error("Pluto RX stream stalled (no frames)")

        restarted = service.start()

        self.assertEqual(restarted.state, LiveSessionState.RUNNING)
        self.assertEqual(len(native.engines), 2)
        self.assertGreaterEqual(first_engine.request_stop_calls, 1)
        self.assertGreaterEqual(first_engine.join_calls, 1)
        self.assertGreaterEqual(first_engine.disconnect_calls, 1)
        service.stop()

    def test_stop_preserves_selected_device_for_restart(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        service.apply_configuration(LiveConfiguration(center_hz=2.4e9, sample_rate_hz=2e6, gain_db=18.0))
        service.start()
        service.stop()

        restarted = service.start()

        self.assertEqual(restarted.state, LiveSessionState.RUNNING)
        self.assertEqual(native.engines[-1].uri, "ip:pluto.local")
        service.stop()


if __name__ == "__main__":
    unittest.main()

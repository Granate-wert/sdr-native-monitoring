"""Native Pluto discovery adapter tests without requiring real hardware."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from sdr_monitor.domain import BackendKind, DeviceTransport, LiveSessionState
from sdr_monitor.services.native_live import NativeLiveSessionService


class _FakeNativeDevice:
    def __init__(self, capabilities: object) -> None:
        self._capabilities = capabilities
        self.disconnected = False

    def capabilities(self) -> object:
        return self._capabilities

    def probe(self) -> object:
        return SimpleNamespace()

    def disconnect(self) -> None:
        self.disconnected = True


class _FakeNative:
    def __init__(self) -> None:
        numeric_range = SimpleNamespace(minimum=1e6, maximum=30e6, step=1.0)
        self.capabilities = SimpleNamespace(
            model="Analog Devices PlutoSDR",
            sample_rate_ranges_hz=(numeric_range,),
            gain_range_db=SimpleNamespace(minimum=-3.0, maximum=71.0, step=1.0),
        )
        self.created: list[_FakeNativeDevice] = []

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
        self.created.append(device)
        return device


class NativeLiveDiscoveryTests(unittest.TestCase):
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

    def test_selection_probes_real_context_and_stop_disconnects_it(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]

        snapshot = service.select_device(device.device_id)
        self.assertEqual(snapshot.state, LiveSessionState.CONNECTED)
        self.assertEqual(snapshot.device.uri, "ip:pluto.local")
        self.assertFalse(native.created[-1].disconnected)

        stopped = service.stop()
        self.assertEqual(stopped.state, LiveSessionState.CONNECTED)
        self.assertTrue(native.created[-1].disconnected)

    def test_start_does_not_claim_native_stream_without_frame_pipeline(self) -> None:
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)

        failed = service.start()

        self.assertEqual(failed.state, LiveSessionState.ERROR)
        self.assertIn("RX streaming", failed.error)


if __name__ == "__main__":
    unittest.main()
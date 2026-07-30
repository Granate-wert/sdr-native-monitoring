from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from esw_dfl.sdr import native_api


ROOT = Path(__file__).resolve().parents[1]
MOCK_LIBIIO = ROOT / "native" / "sdr_core" / "out" / "build" / "windows-msvc-cpu" / "libiio.dll"


@unittest.skipUnless(native_api.native_availability().available, "compiled _sdr_native module is unavailable")
class PlutoNativeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = native_api.require_native()

    def test_runtime_status_is_controlled(self) -> None:
        status = self.native.pluto_runtime_info()
        self.assertIsInstance(status.available, bool)
        self.assertIsInstance(status.error, str)
        if status.available:
            self.assertGreaterEqual(status.major, 0)
            self.assertGreaterEqual(status.minor, 0)
            self.assertIn("usb", list(status.backends))

    def test_invalid_uri_is_rejected_before_context_open(self) -> None:
        with self.assertRaises(ValueError):
            self.native.PlutoDevice("serial:unsupported")

    @unittest.skipUnless(MOCK_LIBIIO.exists(), "mock libiio DLL is built by the native P06 target")
    def test_python_service_against_mock_libiio(self) -> None:
        script = r'''
from esw_dfl.sdr import DeviceConfig, GainMode
from esw_dfl.sdr.pluto import PlutoDeviceService, discover_pluto
items = discover_pluto()
assert len(items) == 1 and items[0].uri == "usb:mock"
with PlutoDeviceService("usb:mock") as device:
    caps = device.capabilities()
    assert caps.tuning_range_hz.minimum == 70_000_000.0
    applied = device.configure(DeviceConfig(
        source_id="python-mock",
        context_uri="usb:mock",
        center_frequency_hz=2_450_000_000.0,
        sample_rate_hz=3_000_000.0,
        analog_bandwidth_hz=1_500_000.0,
        gain_mode=GainMode.MANUAL,
        manual_gain_db=20.0,
        buffer_samples=512,
    ))
    assert applied.sample_layout.significant_bits == 12
    device.start()
    first = device.read_block()
    second = device.read_block()
    assert first.samples.flags.writeable is False
    assert second.first_sample_index == first.first_sample_index + first.sample_count
    metrics = device.metrics()
    assert metrics.blocks_received == 2
    assert metrics.output_pool_exhaustions == 0
    assert metrics.output_blocks_dropped == 0
    device.stop()
'''
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK_LIBIIO)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    @unittest.skipUnless(MOCK_LIBIIO.exists(), "mock libiio DLL is built by the native P06 target")
    def test_constructor_and_configure_release_gil(self) -> None:
        script = r'''
import os
import threading
import time
from esw_dfl.sdr import DeviceConfig, GainMode
from esw_dfl.sdr.pluto import PlutoDeviceService

stop = threading.Event()
progress = [0]
def heartbeat():
    while not stop.is_set():
        progress[0] += 1
        time.sleep(0.001)

worker = threading.Thread(target=heartbeat)
worker.start()
time.sleep(0.02)
try:
    os.environ["SDR_MOCK_LIBIIO_CONSTRUCTOR_DELAY_MS"] = "200"
    before = progress[0]
    device = PlutoDeviceService("usb:mock")
    assert progress[0] - before >= 20, (before, progress[0])
    os.environ["SDR_MOCK_LIBIIO_CONFIG_DELAY_MS"] = "200"
    before = progress[0]
    device.configure(DeviceConfig(
        source_id="python-gil-mock",
        context_uri="usb:mock",
        center_frequency_hz=2_450_000_000.0,
        sample_rate_hz=3_000_000.0,
        analog_bandwidth_hz=1_500_000.0,
        gain_mode=GainMode.MANUAL,
        manual_gain_db=20.0,
        buffer_samples=512,
    ))
    assert progress[0] - before >= 20, (before, progress[0])
    device.disconnect()
finally:
    stop.set()
    worker.join(timeout=2)
'''
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK_LIBIIO)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

@unittest.skipUnless(os.environ.get("SDR_RUN_HARDWARE_TESTS") == "1", "set SDR_RUN_HARDWARE_TESTS=1 for Pluto hardware smoke")
class PlutoHardwareTests(unittest.TestCase):
    def test_discover_configure_stream_disconnect_reconnect(self) -> None:
        native = native_api.require_native()
        contexts = list(native.scan_pluto_contexts())
        self.assertTrue(contexts, "no Pluto context discovered")
        uri = str(contexts[0].uri)
        device = native.PlutoDevice(uri, 3000)
        config = native.DeviceConfig(
            "p06-hardware-test",
            uri,
            2_450_000_000.0,
            3_000_000.0,
            1_500_000.0,
            native.GainMode.MANUAL,
            20.0,
            0,
            4096,
            3,
        )
        applied = device.configure(config)
        self.assertEqual(applied.center_frequency_hz, 2_450_000_000.0)
        self.assertEqual(applied.sample_rate_hz, 3_000_000.0)
        device.start_stream()
        first = device.refill()
        second = device.refill()
        self.assertEqual(second.source_sequence, first.source_sequence + 1)
        self.assertEqual(second.first_sample_index, first.first_sample_index + first.sample_count)
        device.stop_stream()
        device.disconnect()
        self.assertFalse(device.connected)
        reconnected = native.PlutoDevice(uri, 3000)
        self.assertTrue(reconnected.connected)
        reconnected.disconnect()


if __name__ == "__main__":
    unittest.main()
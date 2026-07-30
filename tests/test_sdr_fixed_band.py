from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from esw_dfl.sdr import native_api


ROOT = Path(__file__).resolve().parents[1]
MOCK_LIBIIO = (
    ROOT
    / "native"
    / "sdr_core"
    / "out"
    / "build"
    / "windows-msvc-cpu"
    / "libiio.dll"
)


@unittest.skipUnless(
    native_api.native_availability().available,
    "compiled _sdr_native module is unavailable",
)
@unittest.skipUnless(MOCK_LIBIIO.exists(), "mock libiio DLL is not built")
class FixedBandPipelineTests(unittest.TestCase):
    def run_script(self, script: str, *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK_LIBIIO)
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def test_service_keeps_analytics_independent_of_slow_polling(self) -> None:
        script = r'''
import time
from esw_dfl.sdr import (
    DeviceConfig, DspConfig, EngineState, FixedBandEngineService,
    FixedBandOptions, GainMode, SpectrumUnit,
)

def options(center=2_450_000_000.0):
    return FixedBandOptions(
        device=DeviceConfig(
            source_id="p07-python-mock",
            context_uri="usb:mock",
            center_frequency_hz=center,
            sample_rate_hz=3_000_000.0,
            analog_bandwidth_hz=1_500_000.0,
            gain_mode=GainMode.MANUAL,
            manual_gain_db=20.0,
            buffer_samples=4096,
        ),
        dsp=DspConfig(
            fft_size=1024,
            hop_size=512,
            unit=SpectrumUnit.DBFS_BIN,
            batch_size=4,
        ),
        snapshot_rate_hz=240.0,
        spectrum_queue_capacity=2,
    )

with FixedBandEngineService("usb:mock") as engine:
    applied = engine.configure(options())
    engine.start()
    deadline = time.monotonic() + 3.0
    while engine.metrics().engine.fft_frames_computed < 8 and time.monotonic() < deadline:
        time.sleep(0.001)
    before = engine.metrics().engine.fft_frames_computed
    deadline = time.monotonic() + 1.0
    metrics = engine.metrics()
    while metrics.spectrum_queue.dropped == 0 and time.monotonic() < deadline:
        time.sleep(0.01)  # deliberately no Python polling
        metrics = engine.metrics()
    assert metrics.engine.fft_frames_computed > before
    assert metrics.engine.fft_frames_dropped == 0
    assert metrics.spectrum_queue.depth <= metrics.spectrum_queue.capacity == 2
    assert metrics.spectrum_queue.dropped > 0
    frames = engine.poll_spectrum()
    assert frames and frames[-1].fft_size == 1024
    assert frames[-1].config_generation == applied.config_generation
    reapplied = engine.reconfigure(options(2_451_000_000.0))
    assert engine.state is EngineState.RUNNING
    deadline = time.monotonic() + 3.0
    latest = None
    while time.monotonic() < deadline:
        items = engine.poll_spectrum()
        if items:
            latest = items[-1]
            if latest.config_generation == reapplied.config_generation:
                break
        time.sleep(0.001)
    assert latest is not None
    assert latest.config_generation == reapplied.config_generation
    assert latest.center_frequency_hz == reapplied.center_frequency_hz
    engine.stop()
    assert engine.state is EngineState.STOPPED
    assert engine.metrics().healthy
'''
        completed = self.run_script(script)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_status_queries_do_not_block_stop_and_error_is_preserved(self) -> None:
        script = r'''
import os
import threading
import time
from esw_dfl.sdr import (
    DeviceConfig, DspConfig, EngineState, FixedBandEngineService,
    FixedBandOptions, GainMode, SpectrumUnit,
)

def options(*, event_capacity=64):
    return FixedBandOptions(
        device=DeviceConfig(
            source_id="p07-liveness",
            context_uri="usb:mock",
            center_frequency_hz=2_450_000_000.0,
            sample_rate_hz=3_000_000.0,
            analog_bandwidth_hz=1_500_000.0,
            gain_mode=GainMode.MANUAL,
            manual_gain_db=20.0,
            buffer_samples=4096,
        ),
        dsp=DspConfig(
            fft_size=1024,
            hop_size=512,
            unit=SpectrumUnit.DBFS_BIN,
        ),
        event_queue_capacity=event_capacity,
    )

os.environ["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1500"
with FixedBandEngineService("usb:mock") as engine:
    engine.configure(options())
    engine.start()
    time.sleep(0.05)
    started = time.monotonic()
    assert engine.streaming
    assert time.monotonic() - started < 0.2
    stop_entered = threading.Event()
    def stop_request():
        engine.request_stop()
        stop_entered.set()
    thread = threading.Thread(target=stop_request)
    thread.start()
    assert stop_entered.wait(0.25)
    thread.join()
    engine.join()

os.environ["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
os.environ["SDR_MOCK_LIBIIO_REFILL_FAIL"] = "1"
with FixedBandEngineService("usb:mock") as engine:
    engine.configure(options(event_capacity=1))
    engine.start()
    deadline = time.monotonic() + 3.0
    while engine.state is not EngineState.ERROR and time.monotonic() < deadline:
        time.sleep(0.001)
    metrics = engine.metrics()
    assert metrics.state is EngineState.ERROR
    assert not metrics.healthy
    engine.join()
    assert engine.metrics().has_error
    assert not engine.metrics().healthy
    events = engine.poll_events()
    assert any(
        event.code == "acquisition_failure" and event.severity.name == "CRITICAL"
        for event in events
    )
os.environ.pop("SDR_MOCK_LIBIIO_REFILL_FAIL", None)

with FixedBandEngineService("usb:mock") as engine:
    engine.configure(options())
    engine.disconnect()
    assert not engine.connected
    assert engine.state is EngineState.STOPPED
    try:
        engine.applied_config()
    except Exception:
        pass
    else:
        raise AssertionError("disconnect must invalidate applied configuration")

race_errors = []
engine = FixedBandEngineService("usb:mock")
engine.configure(options())
barrier = threading.Barrier(3)

def start_racer():
    barrier.wait()
    try:
        engine.start()
    except Exception as error:
        race_errors.append(("start", type(error).__name__))

def disconnect_racer():
    barrier.wait()
    try:
        engine.disconnect()
    except Exception as error:
        race_errors.append(("disconnect", type(error).__name__))

start_thread = threading.Thread(target=start_racer)
disconnect_thread = threading.Thread(target=disconnect_racer)
start_thread.start()
disconnect_thread.start()
barrier.wait()
start_thread.join(timeout=5.0)
disconnect_thread.join(timeout=5.0)
assert not start_thread.is_alive()
assert not disconnect_thread.is_alive()
assert not any(role == "disconnect" for role, _ in race_errors)
assert not engine.connected
assert engine.state is EngineState.STOPPED
engine.disconnect()'''
        completed = self.run_script(script, timeout=45.0)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_options_reject_fractional_or_boolean_integer_fields(self) -> None:
        from esw_dfl.sdr import (
            DeviceConfig,
            DspConfig,
            FixedBandOptions,
            GainMode,
            SpectrumUnit,
        )

        device = DeviceConfig(
            source_id="p07-validation",
            context_uri="usb:mock",
            center_frequency_hz=2_450_000_000.0,
            sample_rate_hz=3_000_000.0,
            analog_bandwidth_hz=1_500_000.0,
            gain_mode=GainMode.MANUAL,
            manual_gain_db=20.0,
            buffer_samples=4096,
        )
        dsp = DspConfig(
            fft_size=1024,
            hop_size=512,
            unit=SpectrumUnit.DBFS_BIN,
        )
        for name, value in (
            ("acquisition_queue_capacity", 1.5),
            ("spectrum_queue_capacity", True),
            ("event_queue_capacity", 2**32),
            ("discard_blocks_after_start", 0.5),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                FixedBandOptions(device=device, dsp=dsp, **{name: value})

    def test_headless_cli_devices_and_fixed(self) -> None:
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK_LIBIIO)
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        devices = subprocess.run(
            [sys.executable, "-m", "esw_dfl.sdr.cli", "devices"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(devices.returncode, 0, devices.stdout + devices.stderr)
        payload = json.loads(devices.stdout)
        self.assertEqual(payload["devices"][0]["uri"], "usb:mock")
        description = payload["devices"][0]["description"]
        self.assertNotIn("serial", description.lower())
        self.assertNotIn("TOP-SECRET-P07", description)
        self.assertIn("transport USB", description)
        fixed = subprocess.run(
            [
                sys.executable,
                "-m",
                "esw_dfl.sdr.cli",
                "fixed",
                "--uri",
                "usb:mock",
                "--duration",
                "0.25",
                "--report-interval",
                "0.05",
                "--buffer-samples",
                "4096",
                "--fft",
                "1024",
                "--hop",
                "512",
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
        records = [json.loads(line) for line in fixed.stdout.splitlines()]
        self.assertEqual(records[0]["event"], "configured")
        self.assertEqual(records[0]["backend"], "pluto-libiio/cpu-pocketfft")
        self.assertTrue(any(record["event"] == "status" for record in records))
        self.assertEqual(records[-1]["event"], "stopped")
        self.assertEqual(records[-1]["health"], "OK")
        self.assertGreater(records[-1]["fft_frames_computed"], 0)

        failed = subprocess.run(
            [
                sys.executable,
                "-m",
                "esw_dfl.sdr.cli",
                "fixed",
                "--uri",
                "serial:unsupported",
                "--duration",
                "0.01",
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        error = json.loads(failed.stderr)
        self.assertEqual(error["event"], "error")
        self.assertIn("URI", error["message"])


if __name__ == "__main__":
    unittest.main()

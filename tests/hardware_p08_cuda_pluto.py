"""P08 hardware acceptance: real Pluto RX + CPU/CUDA backend runs.

Opt-in: runs only with SDR_RUN_HARDWARE_TESTS=1. RX-only; never enables TX
and never alters firmware. Evidence is printed as JSONL lines.
"""

from __future__ import annotations

import json
import math
import os
import time
import unittest
from typing import Any

from esw_dfl.sdr import native_api
from esw_dfl.sdr.contracts import CONTRACT_SCHEMA_VERSION

SOAK_SECONDS = int(os.environ.get("SDR_P08_SOAK_SECONDS", "600"))
CENTER_HZ = 2_450_000_000.0
SAMPLE_RATE_HZ = 3_000_000.0
BANDWIDTH_HZ = 1_500_000.0


def _enabled() -> bool:
    return os.environ.get("SDR_RUN_HARDWARE_TESTS") == "1"


def _discover_pluto(native):
    """Return a discovered RX device plus capabilities without exposing serials."""
    try:
        contexts = list(native.scan_pluto_contexts("usb,ip"))
    except Exception:
        return None
    for context in contexts:
        uri = str(context.uri)
        device = None
        try:
            probe = native.probe_pluto_context(uri, 3000)
            device = native.PlutoDevice(uri, 3000)
            capabilities = device.capabilities()
            return {
                "uri": uri,
                "probe": probe,
                "capabilities": capabilities,
            }
        except Exception:
            continue
        finally:
            if device is not None:
                try:
                    device.disconnect()
                except Exception:
                    pass
    return None


def _range_contains(value_range, value: float) -> bool:
    return float(value_range.minimum) <= value <= float(value_range.maximum)


def _supports_any(ranges, value: float) -> bool:
    return any(_range_contains(item, value) for item in ranges)


def _device_config(native, uri: str):
    return native.DeviceConfig(
        "p08-hw",
        uri,
        CENTER_HZ,
        SAMPLE_RATE_HZ,
        BANDWIDTH_HZ,
        native.GainMode.MANUAL,
        40.0,
        0,
        262_144,
        CONTRACT_SCHEMA_VERSION,
    )


def _dsp_config(native, fft_size: int = 4096, hop_size: int = 2048, batch: int = 16):
    return native.DspConfig(
        fft_size,
        hop_size,
        native.WindowType.HANN,
        native.DetectorType.SAMPLE,
        native.SpectrumUnit.DBFS_BIN,
        native.PrecisionMode.ACCURATE_F32_F64_ACCUM,
        batch,
        1,
        8.6,
        native.CalibrationStatus.UNCALIBRATED,
        "",
        CONTRACT_SCHEMA_VERSION,
    )


def _fixed_config(native, backend_kind, uri: str):
    return native.FixedBandConfig(
        _device_config(native, uri),
        _dsp_config(native),
        backend_kind,
        True,
        16,
        native.OverflowPolicy.DROP_NEWEST,
        8,
        64,
        30.0,
        2,
        False,
    )


def _run_engine(native, backend_kind, seconds: float, label: str, uri: str) -> dict:
    engine = native.PlutoFixedBandEngine(uri)
    try:
        engine.configure(_fixed_config(native, backend_kind, uri))
        applied = engine.applied_config()
        if not math.isclose(applied.center_frequency_hz, CENTER_HZ, rel_tol=0.0, abs_tol=1.0):
            raise AssertionError("center-frequency readback differs from request")
        if not math.isclose(applied.sample_rate_hz, SAMPLE_RATE_HZ, rel_tol=0.0, abs_tol=1.0):
            raise AssertionError("sample-rate readback differs from request")
        if not math.isclose(applied.analog_bandwidth_hz, BANDWIDTH_HZ, rel_tol=0.0, abs_tol=1.0):
            raise AssertionError("analog-bandwidth readback differs from request")
        if str(applied.gain_mode) != "GainMode.MANUAL" or not math.isclose(
            applied.manual_gain_db, 40.0, rel_tol=0.0, abs_tol=0.01
        ):
            raise AssertionError("gain readback differs from request")
        if str(applied.sample_layout.output_format) != "SampleFormat.COMPLEX_INT12_IN_INT16_LE":
            raise AssertionError("Pluto did not report canonical Int12 sample format")
        engine.start()
        started = time.monotonic()
        snapshots = 0
        max_spectrum_depth = 0
        try:
            while time.monotonic() - started < seconds:
                time.sleep(0.25)
                frames = engine.poll_spectrum_frames(8)
                snapshots += len(frames)
                metrics = engine.metrics()
                max_spectrum_depth = max(max_spectrum_depth, metrics.spectrum_queue.high_water)
        finally:
            stop_started = time.monotonic()
            engine.stop()
            stop_latency = time.monotonic() - stop_started
        metrics = engine.metrics()
        events = [str(event.code) for event in engine.poll_events(0)]
        return {
            "result": "PASS",
            "label": label,
            "duration_s": round(time.monotonic() - started, 2),
            "state": str(metrics.state),
            "has_error": metrics.has_error,
            "active_backend": str(metrics.active_backend),
            "requested_backend": str(metrics.requested_backend),
            "backend_fallback_count": metrics.backend_fallback_count,
            "fft_frames_computed": metrics.engine.fft_frames_computed,
            "fft_frames_dropped": metrics.engine.fft_frames_dropped,
            "iq_blocks_received": metrics.engine.iq_blocks_received,
            "iq_blocks_dropped": metrics.engine.iq_blocks_dropped,
            "snapshots": snapshots,
            "spectrum_high_water": max_spectrum_depth,
            "spectrum_capacity": metrics.spectrum_queue.capacity,
            "stop_latency_s": round(stop_latency, 3),
            "device_generation": applied.config_generation,
            "readback": {
                "center_frequency_hz": applied.center_frequency_hz,
                "sample_rate_hz": applied.sample_rate_hz,
                "analog_bandwidth_hz": applied.analog_bandwidth_hz,
                "gain_mode": str(applied.gain_mode),
                "manual_gain_db": applied.manual_gain_db,
                "sample_format": str(applied.sample_layout.output_format),
            },
            "events_tail": events[-8:],
        }
    finally:
        try:
            engine.disconnect()
        except Exception:
            pass


@unittest.skipUnless(_enabled(), "hardware tests are opt-in (SDR_RUN_HARDWARE_TESTS=1)")
class HardwareP08AcceptanceTests(unittest.TestCase):
    native: Any
    discovery: dict[str, Any] | None
    uri: str
    capabilities: Any
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = native_api.require_native()
        cls.discovery = _discover_pluto(cls.native)
        if cls.discovery is None:
            print(json.dumps({"result": "NOT_VERIFIED", "reason": "no usable Pluto context discovered"}), flush=True)
            raise unittest.SkipTest("NOT_VERIFIED: no usable Pluto context discovered")
        cls.uri = cls.discovery["uri"]
        cls.capabilities = cls.discovery["capabilities"]
        if not _range_contains(cls.capabilities.tuning_range_hz, CENTER_HZ):
            raise unittest.SkipTest("NOT_VERIFIED: center frequency is outside discovered tuning range")
        if not _supports_any(cls.capabilities.sample_rate_ranges_hz, SAMPLE_RATE_HZ):
            raise unittest.SkipTest("NOT_VERIFIED: sample rate is outside discovered range")
        if not _supports_any(cls.capabilities.analog_bandwidth_ranges_hz, BANDWIDTH_HZ):
            raise unittest.SkipTest("NOT_VERIFIED: analog bandwidth is outside discovered range")
        if not _range_contains(cls.capabilities.gain_range_db, 40.0):
            raise unittest.SkipTest("NOT_VERIFIED: manual gain is outside discovered range")
        if cls.native.GainMode.MANUAL not in cls.capabilities.gain_modes:
            raise unittest.SkipTest("NOT_VERIFIED: manual gain mode is unavailable")

    def test_forced_cpu_baseline(self) -> None:
        result = _run_engine(self.native, self.native.ComputeBackendKind.CPU, 20.0, "forced-cpu", self.uri)
        print(json.dumps(result))
        self.assertFalse(result["has_error"])
        self.assertEqual(result["active_backend"], "ComputeBackendKind.CPU")
        self.assertGreater(result["fft_frames_computed"], 0)

    def test_forced_cuda(self) -> None:
        result = _run_engine(self.native, self.native.ComputeBackendKind.CUDA, 20.0, "forced-cuda", self.uri)
        print(json.dumps(result))
        self.assertFalse(result["has_error"])
        self.assertEqual(result["active_backend"], "ComputeBackendKind.CUDA")
        self.assertGreater(result["fft_frames_computed"], 0)

    def test_auto_long_soak(self) -> None:
        result = _run_engine(self.native, self.native.ComputeBackendKind.AUTO, float(SOAK_SECONDS), "auto-soak", self.uri)
        print(json.dumps(result))
        self.assertFalse(result["has_error"])
        self.assertGreater(result["fft_frames_computed"], 0)
        self.assertLessEqual(result["spectrum_high_water"], result["spectrum_capacity"])
        self.assertLess(result["stop_latency_s"], 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

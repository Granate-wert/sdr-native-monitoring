"""Matching-native/mock-only selected receiver engine binding contract.

No physical RX2/topology, product pane selector or simultaneous owner proof.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")
MOCK = ROOT / "native/sdr_core/out/build/windows-msvc-cpu-hackrf/libiio.dll"


@unittest.skipUnless(MODULE and MOCK.is_file(), "requires explicit staged native and built mock DLL")
class FixedBandReceiverSelectionBindingTests(unittest.TestCase):
    def test_rx2_same_engine_output_readonly_selection_and_legacy_rx1(self) -> None:
        code = r'''
import importlib.util
import pathlib
import sys
import time

path = pathlib.Path(sys.argv[1]).resolve(strict=True)
spec = importlib.util.spec_from_file_location("_sdr_native", path)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
assert pathlib.Path(native.__file__).resolve() == path
rx = native.PlutoReceiverSelection
device = native.DeviceConfig("caller-exact-id", "usb:mock", 2_450_000_000.,
    61_440_000., 56_000_000., native.GainMode.MANUAL, 43., 0, 4096,
    native.CONTRACT_SCHEMA_VERSION)
dsp = native.DspConfig(1024, 512, native.WindowType.HANN, native.DetectorType.SAMPLE,
    native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.REFERENCE_F64, 1, 1,
    8.6, native.CalibrationStatus.UNCALIBRATED, "", native.CONTRACT_SCHEMA_VERSION)
ordinary = native.FixedBandConfig(device, dsp)
assert ordinary.receiver_selection == rx.RX1  # old positional callers unchanged
selected = native.FixedBandConfig(device, dsp, backend=native.ComputeBackendKind.CPU,
    allow_runtime_fallback=False, snapshot_rate_hz=2000., discard_blocks_after_start=0,
    receiver_selection=rx.RX2)
try:
    native.FixedBandConfig(device, dsp, receiver_selection=rx.BOTH)
except native.ConfigurationError:
    pass
else:
    raise AssertionError("single producer cannot publish BOTH as one source")
engine = native.PlutoFixedBandEngine("usb:mock")
try:
    applied = engine.configure(selected)
    assert applied.receiver_selection == rx.RX2
    assert engine.state() == native.EngineState.CONFIGURED and not engine.streaming
    assert engine.config().receiver_selection == engine.metrics().receiver_selection == rx.RX2
    for obj in (selected, engine.metrics()):
        try:
            obj.receiver_selection = rx.RX1
        except AttributeError:
            pass
        else:
            raise AssertionError("receiver selection readback must be immutable")
    engine.start()
    deadline = time.monotonic() + 3
    while engine.metrics().engine.fft_frames_computed < 8:
        assert not engine.metrics().has_error
        assert time.monotonic() < deadline, "native RX2 output deadline"
        time.sleep(.001)
    engine.stop()
    frames = engine.poll_spectrum_frames(0)
    assert frames and not engine.streaming
    for frame in frames:
        assert frame.source.source_id == "caller-exact-id"
        assert frame.source.metadata_json["receiver_selection"] == '"RX2"'
        assert frame.config_generation == applied.config_generation
        assert frame.sample_rate_hz == 61_440_000.
    assert engine.metrics().engine.fft_frames_dropped == 0
finally:
    if engine.state() == native.EngineState.RUNNING:
        engine.stop()
    engine.disconnect()
assert not engine.connected and not engine.streaming
print("same native RX2 engine binding/provenance/readonly/default/refusal PASS; mock only")
'''
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK)
        environment["SDR_MOCK_LIBIIO_TOPOLOGY_DUAL"] = "1"
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", code, MODULE], cwd=ROOT, env=environment,
            text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

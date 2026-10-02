"""Matching-native mock-only paired same-owner reduced boundary."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")
MOCK = ROOT / "native/sdr_core/out/build/windows-msvc-cpu-hackrf/libiio.dll"


@unittest.skipUnless(MODULE and MOCK.is_file(), "requires explicit native and built mock")
class PairedFixedBandBindingTests(unittest.TestCase):
    def test_one_owner_pair_provenance_guards_and_no_raw_python(self) -> None:
        code = r'''
import ctypes
import importlib.util
import os
import pathlib
import sys
import time

path = pathlib.Path(sys.argv[1]).resolve(strict=True)
spec = importlib.util.spec_from_file_location("_sdr_native", path)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
assert pathlib.Path(native.__file__).resolve() == path
hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
rx = native.PlutoReceiverSelection
dsp = native.DspConfig(1024, 512, native.WindowType.HANN, native.DetectorType.SAMPLE,
    native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.REFERENCE_F64, 4, 1,
    8.6, native.CalibrationStatus.UNCALIBRATED, "", native.CONTRACT_SCHEMA_VERSION)

def channel(name, selection, center=2_450_000_000.):
    device = native.DeviceConfig(name, "usb:mock", center, 61_440_000., 56_000_000.,
        native.GainMode.MANUAL, 43., 0, 4096, native.CONTRACT_SCHEMA_VERSION)
    return native.FixedBandConfig(device, dsp, backend=native.ComputeBackendKind.CPU,
        allow_runtime_fallback=False, snapshot_rate_hz=2000., discard_blocks_after_start=0,
        receiver_selection=selection)

config = native.PairedFixedBandConfig(channel("caller:rx1", rx.RX1), channel("caller:rx2", rx.RX2), 1)
try:
    config.output_queue_capacity = 64
except AttributeError:
    pass
else:
    raise AssertionError("paired config readback must be immutable")
try:
    native.PairedFixedBandConfig(config.primary, channel("caller:rx2", rx.RX2, 2_451_000_000.), 1)
except native.ConfigurationError:
    pass
else:
    raise AssertionError("different LO must refuse before RX")
engine = native.PlutoFixedBandEngine("usb:mock")
for name in ("poll_receiver_recorded_iq_blocks", "poll_recorded_iq_blocks", "refill", "push_iq"):
    assert not hasattr(engine, name), "no raw Python data path"
for name in ("set_analytical_consumer", "set_shared_gap_consumer", "push"):
    assert not hasattr(native.DualRxDspPublisher(), name), "native consumers not Python callbacks"
try:
    applied = engine.configure_paired(config)
    assert applied.receiver_selection == rx.BOTH and len(applied.receiver_gains) == 2
    assert hooks.mock_iio_live_contexts() == 1 and hooks.mock_iio_live_buffers() == 0
    assert not engine.streaming and engine.state() == native.EngineState.CONFIGURED
    engine.start()
    deadline = time.monotonic() + 3
    while engine.paired_metrics().dsp.paired_frames_formed < 32:
        assert not engine.paired_metrics().primary.has_error
        assert time.monotonic() < deadline, "paired engine deadline"
        time.sleep(.001)
    assert hooks.mock_iio_live_buffers() == 1
    try:
        engine.poll_spectrum_frames(0)
    except native.ConfigurationError:
        pass
    else:
        raise AssertionError("BOTH cannot alias one Spectrum source")
    engine.stop()
    m = engine.paired_metrics()
    assert m.dsp.primary.fft_frames_dropped == m.dsp.secondary.fft_frames_dropped == 0
    assert m.primary.acquisition_queue_blocks_dropped == m.secondary.acquisition_queue_blocks_dropped
    assert m.dsp.paired_frames_formed == m.dsp.primary.fft_frames_computed == m.dsp.secondary.fft_frames_computed
    assert m.paired_snapshots_emitted > 0 and m.paired_snapshots_superseded > 0
    assert m.paired_processing_ms > 0 and m.dsp.paired_frames_published == 0
    pair = engine.drain_latest_paired_spectrum_frame().frame
    assert pair is not None
    assert pair.primary.source.source_id == "caller:rx1" and pair.secondary.source.source_id == "caller:rx2"
    assert pair.primary.source.metadata_json["receiver_selection"] == '"RX1"'
    assert pair.secondary.source.metadata_json["receiver_selection"] == '"RX2"'
    assert pair.primary.first_sample_index == pair.secondary.first_sample_index
    assert pair.config_generation == applied.config_generation
    assert hooks.mock_iio_live_buffers() == 0
finally:
    if engine.state() in (native.EngineState.RUNNING, native.EngineState.STOPPING, native.EngineState.ERROR):
        engine.request_stop()
        engine.join()
    engine.disconnect()
assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
print("paired same-owner binding/provenance/guards/reduced-only PASS; mock ONLY")
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

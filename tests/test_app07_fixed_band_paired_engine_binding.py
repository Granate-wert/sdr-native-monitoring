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
    def test_unicode_recording_alias_and_replay_reprocess_boundary(self) -> None:
        code = r'''
import ctypes
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
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

def channel(name, selection, base):
    device = native.DeviceConfig(name, "usb:mock", 2_450_000_000., 61_440_000., 56_000_000.,
        native.GainMode.MANUAL, 43., 0, 4096, native.CONTRACT_SCHEMA_VERSION)
    recording = native.RecordingConfig(True, str(base), True, True, 4096, 32, False,
        native.CONTRACT_SCHEMA_VERSION)
    return native.FixedBandConfig(device, dsp, backend=native.ComputeBackendKind.CPU,
        allow_runtime_fallback=False, snapshot_rate_hz=2000., discard_blocks_after_start=0,
        receiver_selection=selection, recording=recording)

with tempfile.TemporaryDirectory(prefix="sdr_pair_unicode_") as root:
    folder = pathlib.Path(root) / "\u0414\u0430\u043d\u043d\u044b\u0435_\u00c4"
    upper, lower = folder / "\u0417\u0410\u041f\u0418\u0421\u042c_\u00c4", folder / "\u0437\u0430\u043f\u0438\u0441\u044c_\u00e4"
    writes = hooks.mock_iio_rf_mutation_calls()
    try:
        native.PairedFixedBandConfig(channel("rx1", rx.RX1, upper),
            channel("rx2", rx.RX2, str(lower) + ".sigmf-meta.part"), 1)
    except native.ConfigurationError:
        pass
    else:
        raise AssertionError("Unicode case alias must refuse at typed paired config boundary")
    assert hooks.mock_iio_rf_mutation_calls() == writes and not folder.exists()
    bases = [folder / "\u041f\u0440\u0438\u0451\u043c_RX1", folder / "\u041f\u0440\u0438\u0451\u043c_RX2"]
    config = native.PairedFixedBandConfig(channel("rx1", rx.RX1, bases[0]), channel("rx2", rx.RX2, bases[1]), 1)
    engine = native.PlutoFixedBandEngine("usb:mock")
    try:
        engine.configure_paired(config)
        engine.start()
        deadline = time.monotonic() + 3
        while True:
            m = engine.paired_metrics()
            assert not m.primary.has_error and not m.secondary.has_error
            if min(m.primary.spectrum_writer_frames_written, m.secondary.spectrum_writer_frames_written,
                   m.primary.recorder_writer_blocks_written, m.secondary.recorder_writer_blocks_written) >= 2:
                break
            assert time.monotonic() < deadline, "Unicode writer deadline"
            time.sleep(.001)
        engine.stop()
    finally:
        if engine.state() in (native.EngineState.RUNNING, native.EngineState.STOPPING, native.EngineState.ERROR):
            engine.request_stop()
            engine.join()
        engine.disconnect()
    for index, base in enumerate(bases, 1):
        info = native.inspect_final_native_recording(str(base))
        scan = native.scan_native_recording_prefix(str(base))
        reader = native.NativeSpectrumRecordingReader(str(base))
        assert info.iq_manifest_final and info.spectrum_manifest_final and scan.spectrum_complete_records > 0
        assert reader.frame_count > 0 and reader.read_frame(0).source_id == f"rx{index}"
        manifest = json.loads(pathlib.Path(str(base) + ".sdr-spectrum.meta").read_text(encoding="utf-8"))
        assert manifest["sdr"]["spectrum_file"] == base.name + ".sdr-spectrum.bin"
        del reader
        output = folder / ("\u041e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0430_" + str(index))
        selection = native.DspBackendSelectionOptions(preference=native.ComputeBackendKind.CPU,
            allow_runtime_fallback=False)
        reprocessor = native.NativeIqRecordingReprocessor(str(base), str(output), dsp, selection)
        for _ in range(256):
            if reprocessor.process(1):
                break
        else:
            raise AssertionError("bounded Unicode reprocess")
        assert reprocessor.progress.state == native.NativeIqReprocessState.COMPLETED
        assert reprocessor.progress.output_uri == str(output)
        del reprocessor
        replay = native.NativeSpectrumRecordingReader(str(output))
        assert replay.frame_count > 0 and replay.read_frame(0).source_id == f"rx{index}"
        del replay
assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
print("Unicode alias/writers/replay/reprocess Python boundary PASS; mock ONLY")
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

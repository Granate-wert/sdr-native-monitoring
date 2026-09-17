"""R10-D1A Python/native boundary tests with the deterministic libiio mock."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


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
NATIVE_MODULE = ROOT / "sdr_monitor" / "_sdr_native.cp313-win_amd64.pyd"


@unittest.skipUnless(NATIVE_MODULE.exists(), "compiled standalone native module is unavailable")
@unittest.skipUnless(MOCK_LIBIIO.exists(), "mock libiio DLL is not built")
class NativeContinuousSweepBoundaryTests(unittest.TestCase):
    def test_python_cleanup_after_native_configure_rejection_does_not_call_invalid_idle_stop(self):
        script = r'''
from types import SimpleNamespace
from unittest.mock import patch
import sdr_monitor._sdr_native as native
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
from sdr_monitor.services.native_sweep import NativeSweepService, NativeSweepSource
from sdr_monitor.domain import BackendKind, LiveConfiguration, SweepConfiguration, SweepExecutionMode, SweepState

display = NativeContinuousSweepDisplayService(native, "usb:mock")
# The wrapper accepts identity, but the actual pybind Configure rejects this
# wrong native type before device I/O and leaves the coordinator CREATED.
config = SimpleNamespace(epoch=1, segments=(SimpleNamespace(
    fixed_band=SimpleNamespace(device=SimpleNamespace(source_id="fake"))),))
try:
    display.start(config)
except TypeError:
    pass
else:
    raise AssertionError("native Configure accepted a forged type")
assert display._coordinator.state() == native.EngineState.CREATED
display.close()
assert display._closed

released = []
source = NativeSweepSource("usb:mock", "fake", LiveConfiguration(backend=BackendKind.CPU))
service = NativeSweepService(native, source, assert_exclusive=lambda: None,
                             release_lease=lambda: released.append(True))
with patch("sdr_monitor.services.native_sweep.build_native_fixed_band_config", return_value=object()):
    result = service.execute(SweepConfiguration(start_hz=100e6, stop_hz=101e6,
        execution_mode=SweepExecutionMode.NATIVE), lambda _: None)
assert result.state is SweepState.ERROR
assert released == [True]
service.close()
assert released == [True]
'''
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK_LIBIIO)
        completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                   env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_completed_lines_are_readonly_and_raw_iq_recording_is_rejected(self) -> None:
        script = r'''
import time
import sdr_monitor._sdr_native as native

device = native.DeviceConfig(
    "r10d-python-mock", "usb:mock", 2_450_000_000.0, 3_000_000.0,
    1_500_000.0, native.GainMode.MANUAL, 20.0, 0, 4096, 5,
)
dsp = native.DspConfig(
    1024, 512, native.WindowType.HANN, native.DetectorType.SAMPLE,
    native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.ACCURATE_F32_F64_ACCUM,
    4, 1, 8.6, native.CalibrationStatus.UNCALIBRATED, "", 5,
)
profile = native.ContinuousSweepLineConfig(
    True, 9, 2_449_500_000.0, 2_450_500_000.0, 1_500_000.0, 2,
)
config = native.FixedBandConfig(
    device, dsp,
    continuous_sweep_line=profile,
    snapshot_rate_hz=60.0,
)
assert config.continuous_sweep_line.enabled
engine = native.PlutoFixedBandEngine("usb:mock")
try:
    applied = engine.configure(config)
    assert applied.sample_rate_hz == 3_000_000.0
    engine.start()
    deadline = time.monotonic() + 3.0
    while engine.metrics().completed_sweep_lines < 8 and time.monotonic() < deadline:
        time.sleep(0.001)
    metrics = engine.metrics()
    assert metrics.completed_sweep_lines >= 8, metrics.completed_sweep_lines
    assert metrics.gapped_sweep_lines == 0
    assert metrics.sweep_line_queue.capacity >= 14
    lines = engine.poll_sweep_line_frames()
    assert lines
    line = lines[-1]
    assert line.state == "complete"
    assert line.epoch == 9
    assert line.frequencies_hz.flags.writeable is False
    assert line.values.flags.writeable is False
    assert line.quality_flags_per_bin.flags.writeable is False
    assert line.source_segment_indices.flags.writeable is False
    assert len(line.frequencies_hz) == len(line.values) >= 2
    from sdr_monitor.services.native_continuous_sweep import _to_domain_line
    from sdr_monitor.domain.sweep_lines import SweepQualitySchema
    converted = _to_domain_line(line)
    assert converted.is_complete
    assert converted.quality_schema is SweepQualitySchema.NATIVE_V5
    assert converted.aggregate_quality_flags & int(native.QualityFlag.UNCALIBRATED)
    assert (converted.quality_flags == line.quality_flags_per_bin).all()
    assert len(line.segment_acquisition) == 1
    assert len(converted.segment_acquisition) == 1
    record = converted.segment_acquisition[0]
    assert record.segment_index == 0
    assert record.config_generation == line.segment_config_generations[0][1]
    assert record.timestamp_ns == line.segment_acquisition[0].timestamp_ns > 0
    assert record.sample_rate_hz == applied.sample_rate_hz
    assert record.fft_size == 1024
    from sdr_monitor.domain.analyzer import bundle_from_sweep, AnalyzerPublicationKind
    bundle = bundle_from_sweep(converted)
    assert bundle.publication_kind is AnalyzerPublicationKind.SWEEP_COMPLETE
    assert bundle.spectrum.segment_acquisition[0] is record
finally:
    try:
        engine.stop()
    except Exception:
        pass
    engine.disconnect()

recording = native.RecordingConfig(True, "r10d-disallowed", True, False, 4096, 1, False, 5)
try:
    native.FixedBandConfig(device, dsp, recording=recording, continuous_sweep_line=profile)
except native.ConfigurationError:
    pass
else:
    raise AssertionError("max-rate Sweep accepted raw I/Q recording")
'''
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK_LIBIIO)
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
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

    def test_multisegment_coordinator_separates_progress_and_terminal_snapshots(self) -> None:
        script = r'''
import time
import sdr_monitor._sdr_native as native

def fixed(center):
    device = native.DeviceConfig(
        "r10d-coordinator-python-mock", "usb:mock", center, 3_000_000.0,
        1_500_000.0, native.GainMode.MANUAL, 20.0, 0, 4096, 5,
    )
    dsp = native.DspConfig(
        1024, 512, native.WindowType.HANN, native.DetectorType.SAMPLE,
        native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.ACCURATE_F32_F64_ACCUM,
        4, 1, 8.6, native.CalibrationStatus.UNCALIBRATED, "", 5,
    )
    return native.FixedBandConfig(device, dsp, snapshot_rate_hz=120.0, discard_blocks_after_start=1)

segments = [
    native.ContinuousSweepSegmentConfig(fixed(2_449_500_000.0), 2_449_000_000.0, 2_450_100_000.0),
    native.ContinuousSweepSegmentConfig(fixed(2_450_500_000.0), 2_449_900_000.0, 2_451_000_000.0),
]
config = native.ContinuousSweepCoordinatorConfig(
    72, 2_449_000_000.0, 2_451_000_000.0, segments,
    output_queue_capacity=4, segment_frame_timeout_ms=1000,
    line_snapshot_rate_hz=2_000.0,
)
coordinator = native.NativeContinuousSweepCoordinator("usb:mock")
try:
    coordinator.configure(config)
    coordinator.start()
    preview = None
    preview_deadline = time.monotonic() + 3.0
    while preview is None and time.monotonic() < preview_deadline:
        preview = coordinator.poll_progress()
        if preview is None:
            time.sleep(0.001)
    assert preview is not None, "no native preview before terminal line"
    assert preview.revision == 1 and preview.epoch == 72
    assert not hasattr(preview, "completed_ns") and not hasattr(preview, "state")
    assert len(preview.acquired_segment_generations) == 1
    assert preview.acquired_segment_generations[0][1] > 0
    assert len(preview.segment_acquisition) == 1
    acquisition = preview.segment_acquisition[0]
    assert acquisition.segment_index == 0
    assert acquisition.config_generation == preview.acquired_segment_generations[0][1]
    assert acquisition.timestamp_ns > 0 and acquisition.sample_rate_hz > 0
    assert acquisition.fft_size > 0 and acquisition.first_sample_index >= 0
    try:
        acquisition.timestamp_ns = 0
        raise AssertionError("native acquisition metadata is mutable")
    except AttributeError:
        pass
    assert list(preview.pending_segment_indices) == [1]
    preview_arrays = [preview.frequencies_hz, preview.values,
                      preview.quality_flags_per_bin, preview.source_segment_indices]
    assert all(not array.flags.writeable for array in preview_arrays)
    saved_values = preview.values.copy()
    from sdr_monitor.services.native_continuous_sweep import _to_domain_progress
    from sdr_monitor.domain.analyzer import bundle_from_sweep
    reduced = _to_domain_progress(preview)
    assert len(reduced.segment_acquisition) == 1
    assert reduced.segment_acquisition[0].timestamp_ns == acquisition.timestamp_ns
    assert reduced.segment_acquisition[0].quality_flags == acquisition.quality_flags
    bundle = bundle_from_sweep(reduced)
    assert bundle.mode == "sweep" and bundle.spectrum.revision == 1
    assert bundle.unit == preview.unit
    assert not hasattr(bundle.spectrum, "is_complete")
    deadline = time.monotonic() + 5.0
    while coordinator.metrics().completed_lines < 2 and time.monotonic() < deadline:
        time.sleep(0.002)
    metrics = coordinator.metrics()
    assert metrics.completed_lines >= 2, metrics.completed_lines
    assert metrics.output_queue.capacity == 4
    lines = coordinator.poll_lines()
    assert lines
    line = next(item for item in lines if item.state == "complete")
    assert line.epoch == 72
    assert len(line.segment_config_generations) == 2
    assert all(generation > 0 for _index, generation in line.segment_config_generations)
    applied = coordinator.applied_segments()
    assert len(applied) == 2
    assert all(item.sample_rate_hz == 3_000_000.0 for item in applied)
    assert all(item.analog_bandwidth_hz == 1_500_000.0 for item in applied)
    assert all(item.config_generation > 0 for item in applied)
    assert line.values.flags.writeable is False
finally:
    try:
        coordinator.stop()
    except Exception:
        pass
    coordinator.disconnect()
import numpy as np
np.testing.assert_array_equal(preview_arrays[1], saved_values)
del preview
np.testing.assert_array_equal(preview_arrays[1], saved_values)

# Exercise the actual owning Python bridge, not only standalone converters.
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
service = NativeContinuousSweepDisplayService(native, "usb:mock")
try:
    service.start(config)
    saw_progress = False
    saw_complete = False
    deadline = time.monotonic() + 5
    while not (saw_progress and saw_complete) and time.monotonic() < deadline:
        snapshot = service.poll_latest()
        if snapshot.progress is not None:
            saw_progress = True
            assert snapshot.progress.epoch == config.epoch
            assert snapshot.progress.source_id == segments[0].fixed_band.device.source_id
            assert snapshot.analyzer_bundle.spectrum is snapshot.progress
            assert snapshot.progress.segment_acquisition
        if snapshot.line is not None and snapshot.line.is_complete:
            saw_complete = True
            assert len(snapshot.line.segment_acquisition) == 2
        time.sleep(0.001)
    assert saw_progress and saw_complete, (saw_progress, saw_complete)
    service.stop()
    final = service.poll_latest()
    assert final.line is not None and not final.line.is_complete
    assert final.line.epoch == config.epoch
    assert final.progress is None
    assert service.poll_latest().line is None
finally:
    service.close()
'''
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK_LIBIIO)
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "50"
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


if __name__ == "__main__":
    unittest.main()

"""Explicit compiled SAME-owner layer journals; mock, not RF/paint proof."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "native/sdr_core/out/build/rtl-hf/libiio.dll"

CODE = r'''
import ctypes
import importlib.util
import os
from pathlib import Path
import sys
import time
from tests.native_test_dependencies import native_test_dll_directory

path = Path(sys.argv[1]).resolve(strict=True)
paired = sys.argv[2] == "paired"
with native_test_dll_directory(str(path)):
    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == path
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    rx = native.PlutoReceiverSelection
    dsp = native.DspConfig(1024, 512, native.WindowType.HANN, native.DetectorType.SAMPLE,
        native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.REFERENCE_F64, 4, 1,
        8.6, native.CalibrationStatus.UNCALIBRATED, "", native.CONTRACT_SCHEMA_VERSION)
    persistence = native.PersistenceConfig(True, native.PersistenceMode.EXPONENTIAL_DECAY,
        8, 1., -140., 0., 16, 30., native.CONTRACT_SCHEMA_VERSION)
    sweep = native.ContinuousSweepLineConfig(True, 37, 2_440_000_000., 2_460_000_000., 36_000_000.)
    def channel(selection, capacity):
        device = native.DeviceConfig("owner:rx1" if selection == rx.RX1 else "owner:rx2", "usb:mock",
            2_450_000_000., 61_440_000., 56_000_000., native.GainMode.MANUAL, 20., 0, 4096,
            native.CONTRACT_SCHEMA_VERSION)
        return native.FixedBandConfig(device, dsp, backend=native.ComputeBackendKind.CPU,
            allow_runtime_fallback=False, discard_blocks_after_start=0, persistence=persistence,
            continuous_sweep_line=sweep, receiver_selection=selection, layer_event_capacity=capacity)
    engine = native.PlutoFixedBandEngine("usb:mock")
    def apply():
        if paired:
            return engine.configure_paired(native.PairedFixedBandConfig(channel(rx.RX1, 1), channel(rx.RX2, 32)))
        return engine.configure(channel(rx.RX1, 1))
    try:
        applied = apply()
        before = engine.drain_density_layer_ready_events(rx.RX1)
        assert before.summary.created == 0 and before.summary.event_capacity == 1
        assert hooks.mock_iio_live_contexts() == 1 and hooks.mock_iio_live_buffers() == 0
        engine.start()
        deadline = time.monotonic() + 3
        while True:
            m = engine.paired_metrics().primary if paired else engine.metrics()
            assert not m.has_error
            if m.completed_sweep_lines >= 3:
                break
            assert time.monotonic() < deadline, "same-owner layer deadline"
            time.sleep(.001)
        assert hooks.mock_iio_live_buffers() == 1
        engine.stop()
        ids = set()
        for receiver in ((rx.RX1, rx.RX2) if paired else (rx.RX1,)):
            density = engine.poll_receiver_persistence_snapshots(receiver)
            lines = engine.poll_receiver_sweep_line_frames(receiver)
            d = engine.drain_density_layer_ready_events(receiver)
            s = engine.drain_sweep_layer_ready_events(receiver)
            assert density and lines and d.summary.created > 0 and s.summary.created >= 3
            assert density[-1].layer_ready.producer_instance_id == d.summary.producer_instance_id
            assert lines[-1].layer_ready.producer_instance_id == s.summary.producer_instance_id
            assert density[-1].layer_ready.config_generation == applied.config_generation
            assert lines[-1].layer_ready.sweep_epoch == 37
            for summary in (d.summary, s.summary):
                assert summary.created == summary.events_drained + summary.events_pending + summary.events_lost
                ids.add(summary.producer_instance_id)
                try:
                    summary.created = 0
                except AttributeError:
                    pass
                else:
                    raise AssertionError("creation summary must be readonly")
            reread = engine.drain_sweep_layer_ready_events(receiver)
            assert not reread.creations and reread.summary.created == s.summary.created
            if receiver == rx.RX1:
                assert s.summary.events_lost > 0
        assert len(ids) == (4 if paired else 2)
        for selection in (rx.BOTH,) if paired else (rx.BOTH, rx.RX2):
            try:
                engine.drain_density_layer_ready_events(selection)
            except native.ConfigurationError:
                pass
            else:
                raise AssertionError("foreign/ambiguous receiver admitted")
        apply()
        rearmed = engine.drain_density_layer_ready_events(rx.RX1)
        assert rearmed.summary.created == 0 and rearmed.summary.producer_instance_id not in ids
        assert not engine.streaming
    finally:
        if engine.state() in (native.EngineState.RUNNING, native.EngineState.STOPPING, native.EngineState.ERROR):
            engine.request_stop()
            engine.join()
        engine.disconnect()
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
'''


class LayerOwnerBindingTests(unittest.TestCase):
    def run_owner(self, paired: bool) -> None:
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected or not MOCK.is_file():
            self.skipTest("requires explicit matching native module and built mock; no fallback")
        module = Path(selected).resolve(strict=True)
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK)
        environment["SDR_MOCK_LIBIIO_TOPOLOGY_DUAL"] = "1" if paired else ""
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", CODE, str(module), "paired" if paired else "single"],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_single_owner(self) -> None:
        self.run_owner(False)

    def test_paired_owner(self) -> None:
        self.run_owner(True)

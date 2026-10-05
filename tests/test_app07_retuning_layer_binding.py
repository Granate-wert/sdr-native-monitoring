"""Explicit matching coordinator creation journals; mock, not hardware/paint."""
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
mode = sys.argv[2]
paired = mode.startswith("paired")
single = mode.endswith("window")
with native_test_dll_directory(str(path)):
    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == path
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    rx = native.PlutoReceiverSelection
    selected = rx.RX2 if mode == "rx2-retune" else rx.RX1
    dsp = native.DspConfig(4096, 2048, native.WindowType.HANN, native.DetectorType.SAMPLE,
        native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.REFERENCE_F64, 1, 1,
        8.6, native.CalibrationStatus.UNCALIBRATED, "", native.CONTRACT_SCHEMA_VERSION)
    def plan(receiver, epoch, capacity):
        segments = []
        for index in range(1 if single else 2):
            center = 2_440_000_000. + index * 30_000_000.
            device = native.DeviceConfig("opaque-left" if receiver == rx.RX1 else "opaque-right", "usb:mock",
                center, 61_440_000., 56_000_000., native.GainMode.MANUAL, 20., 0, 8192,
                native.CONTRACT_SCHEMA_VERSION)
            fixed = native.FixedBandConfig(device, dsp, backend=native.ComputeBackendKind.CPU,
                allow_runtime_fallback=False, discard_blocks_after_start=0,
                snapshot_rate_hz=1., receiver_selection=receiver)
            segments.append(native.ContinuousSweepSegmentConfig(fixed, center - 18_000_000., center + 18_000_000.))
        return native.ContinuousSweepCoordinatorConfig(epoch, 2_422_000_000.,
            2_458_000_000. if single else 2_488_000_000., segments,
            usable_window_hz=36_000_000., analysis_bins_per_usable_window=2048,
            line_snapshot_rate_hz=2000., layer_event_capacity=capacity)
    owner = native.NativeContinuousSweepCoordinator("usb:mock")
    def apply(epoch, capacity):
        a = plan(rx.RX1 if paired else selected, epoch, capacity)
        if paired:
            owner.configure_paired(native.PairedContinuousSweepCoordinatorConfig(
                "exact-shared-resource", a, plan(rx.RX2, epoch, 256)))
        else:
            owner.configure(a)
    def wait_lines():
        deadline = time.monotonic() + 5
        while owner.metrics().completed_lines < 4:
            assert not owner.metrics().has_error, owner.last_error()
            assert time.monotonic() < deadline, "coordinator mock deadline"
            time.sleep(.001)
    try:
        mutations = hooks.mock_iio_rf_mutation_calls()
        apply(41, 1)
        assert hooks.mock_iio_rf_mutation_calls() == mutations
        before = owner.drain_sweep_layer_ready_events(rx.RX1 if paired else selected)
        assert before.summary.created == 0
        owner.start()
        wait_lines()
        owner.stop()
        ids = set()
        for receiver in ((rx.RX1, rx.RX2) if paired else (selected,)):
            events = owner.drain_sweep_layer_ready_events(receiver)
            assert events.summary.created > 4
            assert events.summary.created == events.summary.events_drained + events.summary.events_lost
            assert events.summary.events_pending == 0
            assert all(ref.sweep_epoch == 41 and ref.producer_instance_id == events.summary.producer_instance_id
                for ref in events.creations)
            ids.add(events.summary.producer_instance_id)
            if receiver == (rx.RX1 if paired else selected):
                assert events.summary.events_lost > 0
        assert len(ids) == (2 if paired else 1)
        outputs = owner.poll_paired_lines() if paired else owner.poll_lines()
        assert outputs
        for output in outputs:
            lines = (output.primary, output.secondary) if paired else (output,)
            assert all(line.layer_ready.producer_instance_id in ids for line in lines)
        for invalid in ((rx.BOTH,) if paired else (rx.BOTH, rx.RX1 if selected == rx.RX2 else rx.RX2)):
            try:
                owner.drain_sweep_layer_ready_events(invalid)
            except native.ConfigurationError:
                pass
            else:
                raise AssertionError("ambiguous/foreign RX accepted")
        apply(42, 32)
        rearmed = owner.drain_sweep_layer_ready_events(rx.RX1 if paired else selected)
        assert rearmed.summary.created == 0 and rearmed.summary.producer_instance_id not in ids
        assert owner.state() == native.EngineState.CONFIGURED
        owner.start()
        wait_lines()
        owner.stop()
    finally:
        owner.stop()
        owner.disconnect()
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
'''


class RetuningLayerBindingTests(unittest.TestCase):
    def run_owner(self, mode: str) -> None:
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected or not MOCK.is_file():
            self.skipTest("explicit matching native and built mock required; no physical fallback")
        module = Path(selected).resolve(strict=True)
        environment = dict(os.environ)
        environment.update(LIBIIO_DLL_PATH=str(MOCK), SDR_MOCK_LIBIIO_TOPOLOGY_DUAL="1",
                           SDR_MOCK_LIBIIO_REFILL_DELAY_MS="1")
        result = subprocess.run(
            [sys.executable, "-c", CODE, str(module), mode], cwd=ROOT,
            env=environment, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_single_retuning(self) -> None:
        self.run_owner("single-retune")

    def test_selected_rx2_retuning(self) -> None:
        self.run_owner("rx2-retune")

    def test_paired_retuning(self) -> None:
        self.run_owner("paired-retune")

    def test_single_window(self) -> None:
        self.run_owner("single-window")

    def test_paired_window(self) -> None:
        self.run_owner("paired-window")

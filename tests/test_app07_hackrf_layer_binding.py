"""Matching HackRF owner-layer ABI; mock only, never a physical SDK fallback."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

CODE = r'''
import importlib.util
from pathlib import Path
import sys
import time
from tests.native_test_dependencies import native_test_dll_directory

path = Path(sys.argv[1]).resolve(strict=True)
mode = sys.argv[2]
with native_test_dll_directory(str(path)):
    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == path
    assert native.HACKRF_LAYER_CREATION_CONTRACT_VERSION == 1
    assert hasattr(native.HackrfRuntimeDspControl, "drain_density_layer_ready_events")
    assert hasattr(native.HackrfSweepRuntimeAnalysisControl, "drain_sweep_layer_ready_events")
    if mode == "density":
        owner = native._make_test_hackrf_runtime_dsp_control(4, layer_event_capacity=1)
        try:
            deadline = time.monotonic() + 3
            while owner.metrics().processing.worker_blocks_processed != 4:
                assert time.monotonic() < deadline, "mock processing deadline"
                time.sleep(.001)
            assert owner.stop(1000).complete()
            frames = owner.poll_persistence_snapshots()
            events = owner.drain_density_layer_ready_events()
            assert frames and events.creations
            assert frames[0].layer_ready.producer_instance_id == events.summary.producer_instance_id
            assert frames[0].layer_ready.creation_sequence == events.creations[0].creation_sequence
            assert events.summary.created == events.summary.events_drained + events.summary.events_lost
            assert events.summary.events_pending == 0
            assert not owner.drain_density_layer_ready_events().creations
            try:
                events.creations[0].creation_sequence = 99
            except AttributeError:
                pass
            else:
                raise AssertionError("creation reference is mutable")
        finally:
            assert owner.stop(1000).complete()
    elif mode == "disabled":
        owner = native._make_test_hackrf_runtime_dsp_control(4)
        try:
            for _ in range(2):
                try:
                    owner.drain_density_layer_ready_events()
                except native.ConfigurationError:
                    pass
                else:
                    raise AssertionError("disabled journal invented evidence")
                assert owner.stop(1000).complete()
        finally:
            assert owner.stop(1000).complete()
    elif mode == "sweep-refusal":
        try:
            native.create_hackrf_sweep_runtime_control([1, 2, 3, 4], "opaque-source", 7,
                4096, 100, 300, 16, 20, layer_event_capacity=4097)
        except native.ConfigurationError as error:
            assert "journal capacity" in str(error), str(error)
        else:
            raise AssertionError("invalid journal reached real Sweep SDK")
    elif mode == "density-refusal":
        try:
            native.create_hackrf_runtime_dsp_control(100e6, 20e6, 15_000_000, 16, 20,
                False, False, 4096, 4096, native.WindowType.HANN, native.DetectorType.SAMPLE,
                32, 24, 8, 4, 7, "opaque-source", [1, 2, 3, 4], layer_event_capacity=1)
        except native.ConfigurationError as error:
            assert "density journal" in str(error), str(error)
        else:
            raise AssertionError("disabled density admitted journal before real SDK")
    else:
        raise AssertionError(mode)
'''


class HackrfLayerBindingTests(unittest.TestCase):
    def run_owner(self, mode: str) -> None:
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected:
            self.skipTest("explicit matching native required; no physical fallback")
        module = Path(selected).resolve(strict=True)
        result = subprocess.run(
            [sys.executable, "-c", CODE, str(module), mode], cwd=ROOT,
            env=dict(os.environ), capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_same_stopped_density_owner(self) -> None:
        self.run_owner("density")

    def test_default_disabled(self) -> None:
        self.run_owner("disabled")

    def test_sweep_factory_before_sdk(self) -> None:
        self.run_owner("sweep-refusal")

    def test_density_factory_before_sdk(self) -> None:
        self.run_owner("density-refusal")

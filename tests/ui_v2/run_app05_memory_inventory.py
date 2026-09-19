"""Synthetic normal-composition memory profile; never opens a receiver.

Run as a module from an exact checkout. JSON separates Windows process memory
from exposed backing-array inventory. Neither is a native allocator proof.
"""
import argparse
import ctypes
from ctypes import wintypes
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sdr_monitor.domain.live import LivePersistenceFrame
from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests
from tests.ui_v2.test_app05_prepared_live import measurement


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged",
            "nonpaged", "pagefile", "peak_pagefile", "private_bytes")]


def process_memory():
    if os.name != "nt":
        return {"working_set": None, "private_bytes": None}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    api = ctypes.WinDLL("psapi", use_last_error=True)
    api.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
    api.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"working_set": int(counters.working_set), "private_bytes": int(counters.private_bytes)}


def run(frames=120, bins=65536, power_bins=64):
    app = QApplication.instance() or QApplication([])
    fixture = AnalyzerWorkspaceProductTests("runTest")
    fixture.app = app
    fixture.setUp()
    rows = []
    started = time.monotonic()
    try:
        fixture.select_and_apply()
        scene = fixture.page.visualization.spectrum_scene
        scene.set_persistence_visible(True)
        template = measurement(fixture)
        spectrum = template.spectrum
        frequencies = spectrum.center_frequency_hz + (np.arange(bins) - bins / 2) * spectrum.sample_rate_hz / bins
        for index in range(frames):
            sequence = index + 1
            if index % 20 == 0:
                fixture.shell.select_workspace("calibration" if (index // 20) % 2 else "analyzer")
            frame = replace(spectrum, sequence=sequence, fft_size=bins, hop_size=bins,
                            frequencies_hz=frequencies, values=np.full(bins, -65 + index % 7, dtype=np.float32),
                            acquisition_epoch=1, receiver_id="synthetic-rx", clock_domain="unknown")
            density = LivePersistenceFrame(
                update_sequence=sequence, timestamp_ns=sequence, source_frame_sequence=sequence,
                power_min_db=-120, power_max_db=0, power_bins=power_bins, frequency_bins=bins,
                processed_frames=sequence, exponential_decay=False, frequencies_hz=frequencies,
                density=np.full((power_bins, bins), .25, dtype=np.float32),
                source_id=frame.source_id, config_generation=frame.config_generation, unit=frame.unit,
                producer_identity_available=True, acquisition_epoch=1, receiver_id="synthetic-rx", clock_domain="unknown")
            snapshot = replace(template, sequence=sequence, spectrum=frame, persistence=density)
            fixture.presenter._emit_snapshot(snapshot)
            fixture.wait(lambda: fixture.composition.view_model.state.spectrum is frame
                         and fixture.presenter._preparation_future is None)
            fixture.wait(lambda: fixture.composition.spectrum_projector._future is None)
            sampled = time.perf_counter()
            inventory = fixture.composition.memory_snapshot(fixture.page)
            sample_ms = (time.perf_counter() - sampled) * 1000
            rows.append({"index": index, "seconds": time.monotonic() - started,
                         "page": fixture.shell.active_workspace_id, "sample_ms": sample_ms,
                         **process_memory(), "inventory": asdict(inventory)})
        return {"kind": "synthetic post-domain RTBW, not RF/FPS or long-soak acceptance",
                "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "bins": bins, "power_bins": power_bins, "frames": frames, "samples": rows}
    finally:
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--bins", type=int, default=65536)
    parser.add_argument("--power-bins", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (not 1 <= args.frames <= 10000 or not 2 <= args.bins <= 2_000_000
            or not 1 <= args.power_bins <= 64 or args.bins * args.power_bins > 8_388_608):
        parser.error("bounded synthetic profile dimensions exceeded")
    output = run(args.frames, args.bins, args.power_bins)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    samples = output["samples"]
    print(json.dumps({"output": str(args.output), "frames": len(samples),
                      "private_first": samples[0]["private_bytes"], "private_last": samples[-1]["private_bytes"],
                      "unique_array_peak": max(s["inventory"]["unique_array_bytes"] for s in samples),
                      "inventory_p95_ms": float(np.percentile([s["sample_ms"] for s in samples], 95))}))

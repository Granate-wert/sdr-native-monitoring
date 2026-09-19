"""Synthetic normal-composition memory profile; never opens a receiver.

Run as a module from an exact checkout. JSON separates Windows process memory
from exposed backing-array inventory. Neither is a native allocator proof.
"""
import argparse
from collections import deque
import ctypes
from ctypes import wintypes
from dataclasses import asdict, replace
import gc
import json
import os
from pathlib import Path
import subprocess
import time
import tracemalloc

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


class MemorySamples:
    """Optional bounded evidence retention; still measure every produced frame."""

    def __init__(self, capacity=None):
        if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1):
            raise ValueError("sample capacity must be a positive integer or None")
        self.rows = deque(maxlen=capacity)
        self.total = 0
        self.phases = {}
        self.peak_arrays = self.peak_observed_reserved = self.peak_reservations = None
        self.inventory_sampled = 0
        self.first_private = self.last_private = None
        # Fixed-size scalar history for long-run plateau checks, independent of
        # evidence-row capacity. Never retains frames, inventories or Qt objects.
        self.checkpoints = deque(maxlen=100)

    def append(self, row):
        # Same full inventory serialization work in bounded/unbounded modes.
        # Only retention changes; no pipeline frame/event/sampling decimation.
        self.rows.append(row)
        self.total += 1
        if self.total == 1:
            self.first_private = row["private_bytes"]
        self.last_private = row["private_bytes"]
        scalar = {key: row[key] for key in ("index", "seconds", "private_bytes", "working_set")}
        phase = self.phases.setdefault(row["page"], {"first": scalar, "last": scalar, "count": 0})
        phase["last"] = scalar
        phase["count"] += 1
        if row["index"] % 100 == 0:
            self.checkpoints.append({"page": row["page"], **scalar})
        inventory = row["inventory"]
        if inventory is None:
            return
        self.inventory_sampled += 1
        self.peak_arrays = max(self.peak_arrays or 0, inventory["unique_array_bytes"])
        ledger = inventory.get("allocation_budget") or {}
        self.peak_observed_reserved = max(self.peak_observed_reserved or 0, ledger.get("peak_bytes", 0))
        self.peak_reservations = max(self.peak_reservations or 0, ledger.get("reserved_bytes", 0))

    def summary(self):
        return {"capacity": self.rows.maxlen, "total_sampled": self.total,
                "retained": len(self.rows), "phases": self.phases,
                "inventory_sampled": self.inventory_sampled,
                "private_first": self.first_private, "private_last": self.last_private,
                "unique_array_peak": self.peak_arrays,
                "ledger_peak_observed_reserved": self.peak_observed_reserved,
                "post_ack_reserved_peak": self.peak_reservations,
                "checkpoints": list(self.checkpoints),
                "timing_distribution_scope": "retained samples only"}


def should_sample(index, frames, observation):
    if observation not in ("full", "process-only", "checkpoints"):
        raise ValueError("unknown observation mode")
    return observation != "checkpoints" or index in (0, 20, frames - 1) or index % 100 == 0


def run(frames=120, bins=65536, power_bins=64, *, sample_capacity=None, trace_allocations=False,
        collect_endpoints=False, observation="full"):
    should_sample(0, frames, observation)  # reject before constructing any GUI
    recorder = MemorySamples(sample_capacity)
    app = QApplication.instance() or QApplication([])
    fixture = AnalyzerWorkspaceProductTests("runTest")
    fixture.app = app
    fixture.setUp()
    started = time.monotonic()
    traced_first = {}
    traced_result = None
    collection_endpoints = []
    if trace_allocations:
        tracemalloc.start(8)
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
            if should_sample(index, frames, observation):
                sampled = time.perf_counter()
                inventory = (fixture.composition.memory_snapshot(fixture.page)
                             if observation == "full" else None)
                sample_ms = (time.perf_counter() - sampled) * 1000
                recorder.append({"index": index, "seconds": time.monotonic() - started,
                             "page": fixture.shell.active_workspace_id, "sample_ms": sample_ms,
                             **process_memory(), "inventory": None if inventory is None else asdict(inventory)})
            page = fixture.shell.active_workspace_id
            if collect_endpoints and (index in (0, 20) or index == frames - 1):
                before = process_memory()
                collected = gc.collect()
                collection_endpoints.append({"index": index, "page": page, "collected": collected,
                                             "before": before, "after": process_memory()})
            if trace_allocations:
                if page not in traced_first:
                    traced_first[page] = (index, tracemalloc.take_snapshot())
                if index == frames - 1:
                    first_index, first_trace = traced_first[page]
                    differences = tracemalloc.take_snapshot().compare_to(first_trace, "traceback")
                    traced_result = {
                        "scope": "Python/tracemalloc allocation delta at same page; tracing perturbs memory/timing",
                        "page": page, "first_index": first_index, "last_index": index,
                        "net_bytes": sum(item.size_diff for item in differences),
                        "top": [{"bytes": item.size_diff, "count": item.count_diff,
                                 "traceback": item.traceback.format()} for item in differences[:25]]}
        return {"kind": "synthetic post-domain RTBW, not RF/FPS or long-soak acceptance",
                "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "bins": bins, "power_bins": power_bins, "frames": frames,
                "observation": observation,
                "observation_scope": "Only diagnostic sampling changes; every pipeline frame and visibility transition still runs",
                "sampling": recorder.summary(), "samples": list(recorder.rows),
                "allocation_trace": traced_result,
                "collection_endpoints": collection_endpoints,
                "collection_scope": "Explicit diagnostic endpoint collection only; not normal product behavior"}
    finally:
        fixture.tearDown()
        fixture.doCleanups()
        if trace_allocations:
            tracemalloc.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--bins", type=int, default=65536)
    parser.add_argument("--power-bins", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-capacity", type=int, help="Keep only this many latest evidence rows; still sample every frame")
    parser.add_argument("--trace-allocations", action="store_true", help="Perturbing Python allocation attribution, not a timing/RSS baseline")
    parser.add_argument("--collect-endpoints", action="store_true", help="Diagnostic GC at first visibility phases and last frame only")
    parser.add_argument("--observation", choices=("full", "process-only", "checkpoints"), default="full",
                        help="Causal sampling isolation: full inventory+process, process every frame, or process checkpoints only")
    args = parser.parse_args()
    if (not 1 <= args.frames <= 10000 or not 2 <= args.bins <= 2_000_000
            or not 1 <= args.power_bins <= 64 or args.bins * args.power_bins > 8_388_608):
        parser.error("bounded synthetic profile dimensions exceeded")
    if args.sample_capacity is not None and args.sample_capacity < 1:
        parser.error("sample capacity must be positive")
    output = run(args.frames, args.bins, args.power_bins, sample_capacity=args.sample_capacity,
                 trace_allocations=args.trace_allocations, collect_endpoints=args.collect_endpoints,
                 observation=args.observation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    samples = output["samples"]
    print(json.dumps({"output": str(args.output), "frames": output["frames"],
                      **output["sampling"],
                      "retained_inventory_p95_ms": float(np.percentile([s["sample_ms"] for s in samples], 95))}))

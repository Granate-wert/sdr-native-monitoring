"""Bounded CPU probe for post-map snapshot and overlapped SHA (diagnostic only).

Neither candidate arm implements a freshness witness: rematerialization is
excluded, and this cannot be used as a product correctness gate.
"""

from __future__ import annotations

import gc
import statistics
import time
import weakref
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from sdr_monitor.ui.v2.spectrum import persistence_projection as projection
from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
    DensityValueMode,
    PersistenceDensityView,
    PersistenceRenderMode,
)


def _percentile(samples: list[float], value: float) -> float:
    return float(np.percentile(np.asarray(samples), value))


def _run_arm(view: PersistenceDensityView, *, mode: str, iterations: int) -> dict:
    policy = projection.PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, True)
    original_witness = projection.persistence_input_witness
    retained_snapshot: bytes | None = None
    times: list[float] = []
    image_digests: list[bytes] = []
    history = None

    def no_hash_witness(source: PersistenceDensityView, *, cancelled=None):
        projection.check_cancelled(cancelled)
        return projection.PersistenceInputWitness(weakref.ref(source.density), b"diagnostic")

    try:
        with ThreadPoolExecutor(max_workers=1) as witness_pool:
            if mode != "sha_sequential":
                projection.persistence_input_witness = no_hash_witness
            for index in range(iterations + 2):
                request = projection.PersistenceImageRequest(view, policy, history)
                start = time.perf_counter_ns()
                future = (witness_pool.submit(original_witness, view)
                          if mode == "sha_overlapped" else None)
                result = projection.prepare_persistence_image(request)
                if mode == "snapshot_postmap":
                    retained_snapshot = bytes(memoryview(view.density).cast("B"))
                if future is not None:
                    future.result()
                elapsed_ms = (time.perf_counter_ns() - start) / 1e6
                history = result.as_history(index + 1)
                if index >= 2:
                    times.append(elapsed_ms)
                    image_digests.append(projection.hashlib.sha256(memoryview(result.image).cast("B")).digest())
    finally:
        projection.persistence_input_witness = original_witness
    assert retained_snapshot is None or len(retained_snapshot) == view.density.nbytes
    return {
        "mode": mode,
        "iterations": len(times),
        "p50_ms": statistics.median(times),
        "p95_ms": _percentile(times, 95),
        "max_ms": max(times),
        "image_digests": image_digests,
        "extra_retained_snapshot_bytes": 0 if retained_snapshot is None else len(retained_snapshot),
    }


def main() -> None:
    rng = np.random.default_rng(415)
    density = rng.random((256, 16_384), dtype=np.float32)
    density[density < 0.965] = 0.0
    frequencies = np.linspace(2.4e9, 2.42e9, density.shape[1] + 1)
    levels = np.linspace(-120.0, -20.0, density.shape[0] + 1)
    for array in (density, frequencies, levels):
        array.setflags(write=False)
    view = PersistenceDensityView(object(), density, frequencies, levels,
                                  DensityValueMode.PROBABILITY, "dBm")
    arms = []
    for mode in ("sha_sequential", "snapshot_postmap", "sha_overlapped",
                 "sha_overlapped", "snapshot_postmap", "sha_sequential"):
        gc.collect()
        arms.append(_run_arm(view, mode=mode, iterations=20))
    first = arms[0]["image_digests"]
    assert all(arm["image_digests"] == first for arm in arms)
    for arm in arms:
        del arm["image_digests"]
    print({"scope": "CPU microprobe only; no rematerialization, Qt, RX, lifecycle or budget admission",
           "geometry": [256, 16_384], "density_bytes": density.nbytes, "arms": arms})


if __name__ == "__main__":
    main()

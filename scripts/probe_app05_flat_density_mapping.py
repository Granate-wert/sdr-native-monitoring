"""CPU-only matched probe of exact full-resolution density row batching.

This is a worker cost probe, not a GUI, RX, FFT LPS or release acceptance.
The baseline arm runs the unchanged row loop by disabling only its flat-batch
eligibility decision. Both arms keep SHA-256, history, mapping and native
smoothing identical; every image is compared bit-for-bit.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sdr_monitor.ui.v2.spectrum import persistence_projection as worker  # noqa: E402
from sdr_monitor.ui.v2.spectrum.persistence_contracts import (  # noqa: E402
    DensityValueMode,
    PersistenceDensityView,
    PersistenceRenderMode,
)


def _percentile(values: list[float], percentile: float) -> float:
    return float(np.percentile(np.asarray(values), percentile))


def main() -> None:
    rng = np.random.default_rng(20260925)
    density = rng.random((256, 16_384), dtype=np.float32)
    density[density < .965] = 0.0
    frequencies = np.linspace(2.4e9, 2.42e9, density.shape[1] + 1)
    levels = np.linspace(-120.0, -20.0, density.shape[0] + 1)
    for array in (density, frequencies, levels):
        array.setflags(write=False)
    view = PersistenceDensityView(object(), density, frequencies, levels,
                                  DensityValueMode.PROBABILITY, "dBm")
    policy = worker.PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, True)
    accepted = worker.prepare_persistence_image(worker.PersistenceImageRequest(view, policy)).as_history(1)
    request = worker.PersistenceImageRequest(view, policy, accepted)
    measurements: dict[str, list[float]] = {"rows": [], "flat": []}
    for index in range(44):
        order = ("rows", "flat") if index % 2 == 0 else ("flat", "rows")
        results = {}
        for arm in order:
            if arm == "rows":
                with patch.object(worker, "_flat_mapping_eligible", new=lambda _: False):
                    start = time.perf_counter_ns()
                    result = worker.prepare_persistence_image(request)
                    duration_ms = (time.perf_counter_ns() - start) / 1_000_000.0
            else:
                start = time.perf_counter_ns()
                result = worker.prepare_persistence_image(request)
                duration_ms = (time.perf_counter_ns() - start) / 1_000_000.0
            results[arm] = result
            if index >= 4:
                measurements[arm].append(duration_ms)
        if not np.array_equal(results["rows"].image.view(np.uint32),
                              results["flat"].image.view(np.uint32)):
            raise AssertionError("flat mapping changed a prepared image")
    print({
        "scope": "CPU worker only; 40 counterbalanced pairs, no Qt/RX/FFT or product gate",
        "density_shape": list(density.shape),
        "density_bytes": int(density.nbytes),
        "reserve_bytes": worker.persistence_image_reserve(request),
        "arms": {arm: {"p50_ms": statistics.median(values),
                       "p95_ms": _percentile(values, 95),
                       "max_ms": max(values)} for arm, values in measurements.items()},
        "bit_exact_pairs": 44,
    })


if __name__ == "__main__":
    main()

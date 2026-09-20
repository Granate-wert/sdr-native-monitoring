"""Isolated converter stage wall times; no concurrent profiler or RF/UI claim.

Three non-overlapping wrapped scopes: multiply, physical edges, final constructor.
Residual is converter prechecks/range validation plus small Python overhead, not
an exact individual operation. Each call is paired before summary percentiles.
Tracemalloc uses a separate call and includes newly owned conversion output.
"""
import argparse
from contextlib import ExitStack
from dataclasses import replace
from functools import wraps
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
import tracemalloc
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not sys.flags.isolated:
        parser.error("Python -I required")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    import numpy as np
    from sdr_monitor.domain.live import LiveSpectrumFrame
    from sdr_monitor.ui.v2.state import analyzer_layers as layers
    from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
    frame = LiveSpectrumFrame(sequence=1, timestamp_ns=1, source_id="synthetic", config_generation=1,
        center_frequency_hz=100e6, sample_rate_hz=61.44e6, fft_size=65536, hop_size=65536,
        frequencies_hz=100e6 + (np.arange(65536) - 32768) * (61.44e6 / 65536),
        values=np.full(65536, -80, np.float32), unit="dBFS/bin")
    sparse = synthetic_persistence(frame, 64, 1, 4)
    # Numerical stress vector only, not a claimed physically accumulated histogram.
    dense = np.random.default_rng(7).random((64, 65536), dtype=np.float32) * 4
    dense.setflags(write=False)
    cases = {}
    for name, raw in (("sparse", sparse), ("dense", replace(sparse, density=dense))):
        stages = {}

        def wrap(original, label):
            @wraps(original)
            def call(*args, **kwargs):
                begin = perf_counter()
                result = original(*args, **kwargs)
                stages[label] = stages.get(label, 0.0) + (perf_counter() - begin) * 1000
                return result
            return call

        rows = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(layers.np, "multiply", wrap(np.multiply, "multiply")))
            stack.enter_context(patch.object(layers, "_regular_edges", wrap(layers._regular_edges, "edges")))
            stack.enter_context(patch.object(layers.PersistenceDensityFrame, "__post_init__",
                wrap(layers.PersistenceDensityFrame.__post_init__, "constructor")))
            for index in range(22):
                stages.clear()
                begin = perf_counter()
                output = layers.persistence_density_from_native(raw)
                elapsed = (perf_counter() - begin) * 1000
                assert output is not None
                if index >= 2:
                    rows.append(dict(total=elapsed, residual=elapsed - sum(stages.values()), **stages))
                del output
        tracemalloc.start()
        try:
            output = layers.persistence_density_from_native(raw)
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert output is not None
        cases[name] = dict(samples_ms=rows,
            summary_ms={key: dict(p50=float(np.median([row[key] for row in rows])),
                p95=float(np.percentile([row[key] for row in rows], 95))) for key in rows[0]},
            traced_retained_bytes=current, traced_peak_bytes=peak,
            output_hashes={key: hashlib.sha256(getattr(output, key).tobytes()).hexdigest()
                           for key in ("density", "frequency_edges_hz", "level_edges")})
    outside = [n for n, m in tuple(sys.modules.items()) if n.startswith(("sdr_monitor", "scripts"))
               and getattr(m, "__file__", None) and not Path(m.__file__).resolve().is_relative_to(root)]
    if outside:
        raise AssertionError(outside)
    report = dict(scope=__doc__, cases=cases, outside=outside,
        checkout_head=subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python=sys.version.split()[0], numpy=np.__version__)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({name: case["summary_ms"] for name, case in cases.items()}))


if __name__ == "__main__":
    main()

"""Isolated sparse/dense display-transfer wall time and transient traced bytes.

Synthetic fixed 64x65536 matrix, not UI age, acquisition, native or RSS evidence.
The output is preallocated, so traced peak excludes retained source/output arrays.
Run sequentially against explicit clean checkouts; never during a UI benchmark.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
import tracemalloc


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
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, _map_density_values_into
    result = {}
    for kind in ("sparse", "dense"):
        density = (np.zeros((64, 65536), np.float32) if kind == "sparse" else
                   np.random.default_rng(7).random((64, 65536), dtype=np.float32))
        if kind == "sparse":
            density[24] = 1
        density.setflags(write=False)
        output = np.empty(density.shape, np.float32)

        def mapping():
            _map_density_values_into(density, value_mode=DensityValueMode.PROBABILITY,
                logarithmic=True, count_maximum=1, out=output)

        mapping()
        mapping()
        times = []
        for _ in range(20):
            begin = perf_counter()
            mapping()
            times.append((perf_counter() - begin) * 1000)
        tracemalloc.start()
        try:
            mapping()
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        result[kind] = dict(samples_ms=times, p50_ms=float(np.median(times)),
                            p95_ms=float(np.percentile(times, 95)),
                            traced_retained_bytes=current, traced_peak_bytes=peak,
                            output_sha256=hashlib.sha256(output.tobytes()).hexdigest())
    outside = [n for n, m in tuple(sys.modules.items()) if n.startswith("sdr_monitor")
               and getattr(m, "__file__", None) and not Path(m.__file__).resolve().is_relative_to(root)]
    if outside:
        raise AssertionError(outside)
    report = dict(scope=__doc__, cases=result, outside=outside,
        checkout_head=subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python=sys.version.split()[0], numpy=np.__version__)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()

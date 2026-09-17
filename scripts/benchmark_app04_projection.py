"""Synthetic presentation-only timings in an explicitly selected source checkout.

No SDR, EXE, paint FPS or total input-latency claim. Run isolated (-I) in fresh
processes for baseline and candidate; sources are hash-recorded, not guessed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter_ns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", default=12, type=int)
    args = parser.parse_args()
    if not sys.flags.isolated or not 3 <= args.repeats <= 100:
        parser.error("use Python -I and 3..100 repeats")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    import numpy as np
    from sdr_monitor.ui.v2.spectrum.contracts import SpectrumFrameView
    from sdr_monitor.ui.v2.spectrum.envelope import peak_preserving_envelope
    from sdr_monitor.ui.v2.spectrum.sweep_coverage import SweepCoverageState
    from tests.ui_v2.test_app04_sweep_coverage import line, snapshot

    paths = ["sdr_monitor/ui/v2/spectrum/" + name for name in
             ("envelope.py", "envelope_batch.py", "sweep_coverage.py")]
    sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
               for name in paths if (root / name).is_file()}
    results = []
    for count in (65536, 262144, 2000000):
        for pattern in ("dense", "half", "alternating", "missing"):
            values = np.full(count, -70., dtype=np.float32)
            if pattern == "half":
                values[:count // 2] = np.nan
            elif pattern == "alternating":
                values[::2] = np.nan
            elif pattern == "missing":
                values[:] = np.nan
            state = SweepCoverageState()
            state.accept(snapshot(line(1, np.full(count, -80.))))
            current = line(2, values)
            state.accept(snapshot(current))
            view = SpectrumFrameView(current, current.frequencies_hz, current.values_db, current.unit)
            for name, operation in (
                ("coverage", lambda: state.project(0, 3e9, 1920)),
                ("current_envelope", lambda: peak_preserving_envelope(view, 1920)),
            ):
                operation()
                operation()
                durations = []
                for _ in range(args.repeats):
                    start = perf_counter_ns()
                    operation()
                    durations.append((perf_counter_ns() - start) / 1e6)
                results.append(dict(operation=name, bins=count, pattern=pattern,
                                    p50_ms=statistics.median(durations),
                                    p95_ms=float(np.percentile(durations, 95)), samples_ms=durations))
    outside = [name for name, module in list(sys.modules.items())
               if name.startswith(("sdr_monitor", "tests.ui_v2")) and getattr(module, "__file__", None)
               and not Path(module.__file__).resolve().is_relative_to(root)]
    if outside:
        raise RuntimeError(f"imports outside selected checkout: {outside}")
    report = dict(scope="synthetic projection only; not RF, EXE, paint or total UI latency",
                  python=sys.version.split()[0], numpy=np.__version__, width=1920,
                  warmups=2, repeats=args.repeats, source_sha256=sources,
                  product_imports_outside_checkout=outside, results=results)
    # Exclusive evidence publication; never overwrite a previous measurement.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    for result in results:
        print(f"{result['operation']:16} {result['bins']:7} {result['pattern']:11} "
              f"p50={result['p50_ms']:.3f} p95={result['p95_ms']:.3f} ms")


if __name__ == "__main__":
    main()

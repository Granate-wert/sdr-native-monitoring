"""Full domain-bundle microbenchmark, not UI/RF throughput acceptance."""
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
    from sdr_monitor.domain.analyzer import bundle_from_live
    from sdr_monitor.domain.live import LiveSnapshot, LiveSessionState, LiveSpectrumFrame
    cases = {}
    for n in (65536, 262144):
        frame = LiveSpectrumFrame(sequence=1, timestamp_ns=1, center_frequency_hz=2.4e9,
            sample_rate_hz=61.44e6, fft_size=n, hop_size=n,
            frequencies_hz=2.4e9 + (np.arange(n) - n // 2) * (61.44e6 / n),
            values=np.full(n, -70, np.float32), unit="dBFS/bin")
        source = LiveSnapshot(generation=0, sequence=1, state=LiveSessionState.RUNNING, spectrum=frame)
        before = hashlib.sha256(frame.frequencies_hz.tobytes()).hexdigest()
        for _ in range(10):
            bundle_from_live(source)
        samples = []
        for _ in range(100):
            began = perf_counter()
            result = bundle_from_live(source)
            samples.append((perf_counter() - began) * 1000)
            if result.spectrum is not frame:
                raise AssertionError("changed source")
        tracemalloc.start()
        bundle_from_live(source)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if hashlib.sha256(frame.frequencies_hz.tobytes()).hexdigest() != before:
            raise AssertionError("mutated grid")
        cases[str(n)] = dict(samples_ms=samples, p50_ms=float(np.median(samples)),
            p95_ms=float(np.percentile(samples, 95)), traced_current_bytes=current,
            traced_peak_bytes=peak, source_sha256=before)
    report = dict(scope=__doc__, cases=cases,
        checkout_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({n: {k: v for k, v in row.items() if k != "samples_ms"} for n, row in cases.items()}))


if __name__ == "__main__":
    main()

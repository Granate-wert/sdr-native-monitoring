"""Isolated full worker image preparation, not UI/RF throughput acceptance.

Fixed validated inputs; output allocation included, input validation excluded.
Sparse and dense inputs in Direct/Visual prevent sparse-only speed claims.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-module", type=Path,
                        help="isolated candidate extension; never activates the package artifact")
    parser.add_argument("--history-scenario", choices=("same", "changed"), default="same")
    args = parser.parse_args()
    if not sys.flags.isolated:
        parser.error("Python -I required")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    candidate_path = None
    if args.native_module is not None:
        candidate_path = args.native_module.resolve(strict=True)
        if not candidate_path.is_relative_to(root) or candidate_path.suffix.lower() != ".pyd":
            parser.error("candidate extension must be a .pyd inside the checkout")
        # Keep the DLL search handle alive through the run. This affects only
        # this fresh process, never sdr_monitor's active extension on disk.
        dll_search = os.add_dll_directory(str(candidate_path.parent.parent))
        import sdr_monitor
        identity = "sdr_monitor._sdr_native"
        spec = importlib.util.spec_from_file_location(identity, candidate_path)
        if spec is None or spec.loader is None:
            parser.error("candidate native module could not be loaded")
        native = importlib.util.module_from_spec(spec)
        sys.modules[identity] = native
        spec.loader.exec_module(native)
        setattr(sdr_monitor, "_sdr_native", native)
        assert dll_search is not None
    import numpy as np
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
        DensityValueMode, PersistenceDensityFrame, PersistenceRenderMode, adapt_persistence_density,
    )
    from sdr_monitor.ui.v2.spectrum.persistence_projection import (
        PersistenceImagePolicy, PersistenceImageRequest, prepare_persistence_image,
    )
    cases = {}
    for layout in ("C", "F"):
        for kind in ("sparse", "dense"):
            values = (np.zeros((64, 65536), np.float32) if kind == "sparse" else
                      np.random.default_rng(7).random((64, 65536), dtype=np.float32))
            if kind == "sparse":
                values[24] = 1
            values = np.array(values, order=layout, copy=True)
            frequencies, levels = np.arange(65537, dtype=float), np.arange(65, dtype=float)
            for value in (values, frequencies, levels):
                value.setflags(write=False)
            view = adapt_persistence_density(PersistenceDensityFrame(
                values, frequencies, levels, DensityValueMode.PROBABILITY, "dBm"))
            history_view = view
            if args.history_scenario == "changed":
                if kind == "sparse":
                    prior_values = np.zeros_like(values)
                    prior_values[25] = 1
                else:
                    prior_values = 1.0 - values
                prior_values = np.array(prior_values, order=layout, copy=True)
                prior_values.setflags(write=False)
                history_view = adapt_persistence_density(PersistenceDensityFrame(
                    prior_values, frequencies, levels, DensityValueMode.PROBABILITY, "dBm"))
            for mode in PersistenceRenderMode:
                policy = PersistenceImagePolicy(0, mode, True)
                history = prepare_persistence_image(PersistenceImageRequest(history_view, policy)).as_history(1)
                request = PersistenceImageRequest(view, policy, history)
                prepare_persistence_image(request)
                times = []
                for _ in range(12):
                    began = perf_counter()
                    result = prepare_persistence_image(request)
                    times.append((perf_counter() - began) * 1000)
                cases[f"{layout}-{kind}-{mode.value}"] = dict(samples_ms=times,
                    p50_ms=float(np.median(times)), p95_ms=float(np.percentile(times, 95)),
                    output_sha256=hashlib.sha256(result.image.tobytes()).hexdigest())
    outside = [n for n, m in tuple(sys.modules.items()) if n.startswith("sdr_monitor")
               and (module_file := getattr(m, "__file__", None))
               and not Path(module_file).resolve().is_relative_to(root)]
    if outside:
        raise AssertionError(outside)
    report = dict(scope=__doc__, cases=cases, outside=outside,
        history_scenario=args.history_scenario,
        native_module=(None if candidate_path is None else str(candidate_path)),
        native_sha256=(None if candidate_path is None else hashlib.sha256(candidate_path.read_bytes()).hexdigest()),
        checkout_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python=sys.version.split()[0], numpy=np.__version__)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({name: row["p50_ms"] for name, row in cases.items()}))


if __name__ == "__main__":
    main()

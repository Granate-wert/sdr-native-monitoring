"""Bounded inclusive wall timings within the existing Sweep preparation worker.

Synthetic/offscreen diagnostics only, not latency acceptance or RF evidence.
Nested stages overlap; percentiles must not be added. No frames are retained.
Run Python -I with the normal benchmark_app04_poll_overload CLI arguments.
"""
from collections import defaultdict, deque
from contextlib import ExitStack
from functools import wraps
import hashlib
import json
from pathlib import Path
import sys
from threading import local
from time import perf_counter
from unittest.mock import patch


def main():
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
    root = Path(sys.argv[sys.argv.index("--checkout") + 1]).resolve(strict=True)
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    sys.path.insert(0, str(root))
    import numpy as np
    from scripts import benchmark_app04_poll_overload as observer
    from sdr_monitor.domain.sweep_progress import SweepProgressFrame
    from sdr_monitor.ui.v2.state import prepared_sweep, analyzer_layers
    from sdr_monitor.ui.v2.spectrum import contracts, grid_baseline, allocation_budget

    stages = defaultdict(lambda: deque(maxlen=4096))
    context = local()

    def wrapper(name, *, entry=False):
        def decorate(original):
            @wraps(original)
            def invoke(*args, **kwargs):
                active = getattr(context, "active", False)
                if not entry and not active:
                    return original(*args, **kwargs)
                context.active = True
                label = name
                if name == "waterfall_row":
                    label += "_preview" if isinstance(args[0], SweepProgressFrame) else "_terminal"
                began = perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    stages[label].append((perf_counter() - began) * 1000)
                    context.active = active
            return invoke
        return decorate

    with ExitStack() as stack:
        for owner, method, name, entry in (
            (prepared_sweep.SweepSnapshotPreparer, "__call__", "preparation", True),
            (contracts.PreparedSpectrumFrame, "__init__", "prepared_spectrum", False),
            (contracts, "_validate_frequency_grid", "spectrum_grid_validation", False),
            (contracts, "finite_value_extent", "finite_extent", False),
            (grid_baseline.MeasurementGridCache, "prepare", "grid_prepare", False),
            (grid_baseline.MeasurementGridCache, "regular_spacing", "grid_regular_spacing", False),
            (prepared_sweep, "waterfall_line_from_sweep", "waterfall_row", False),
            (analyzer_layers, "_regular_spacing", "regular_validation", False),
            (analyzer_layers, "_reduce_waterfall_columns", "waterfall_reduction", False),
            (allocation_budget.PresentationAllocationBudget, "observe", "ledger_observe", False),
            (allocation_budget.PresentationAllocationBudget, "reserve", "ledger_reserve", False),
            (allocation_budget.AllocationReservation, "commit", "ledger_commit", False),
        ):
            stack.enter_context(patch.object(owner, method, wrapper(name, entry=entry)(getattr(owner, method))))
        observer.main()
    report = dict(scope=__doc__, capacity_per_stage=4096,
        profiler_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        stages={name: dict(count=len(values), **dict(zip(("p50", "p95", "p99", "max"), map(float,
            (*np.percentile(values, [50, 95, 99]), max(values)))))) for name, values in stages.items()})
    with output.with_suffix(".preparation.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()

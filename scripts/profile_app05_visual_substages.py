"""Opt-in scalar timings inside the normal UI V2 persistence worker.

This observer wraps, rather than changes, the product implementation. Timings
include Python wrappers and must not be used as an uninstrumented speed baseline.
No ndarray, request, prepared image, widget or worker is retained in records.
"""

import argparse
from collections import defaultdict, deque
from contextlib import ExitStack, contextmanager
from functools import wraps
import hashlib
import json
from pathlib import Path
import sys
from threading import Lock, local
from time import perf_counter_ns
from unittest.mock import patch


_TIMED_FIELDS = ("total_ms", "witness_ms", "mapping_ms", "native_smoothing_ms", "other_ms")


class SubstageRecorder:
    """A bounded scalar-only witness for completed or failed worker calls."""

    def __init__(self, capacity: int = 8192) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 8192:
            raise ValueError("profile capacity must be in 1..8192")
        self.capacity = capacity
        self._records: deque[dict] = deque(maxlen=capacity)
        self._local = local()
        self._lock = Lock()
        self.total = 0
        self.dropped = 0

    def _observe(self, sample: dict) -> None:
        with self._lock:
            self.total += 1
            if len(self._records) == self.capacity:
                self.dropped += 1
            self._records.append(sample)

    @contextmanager
    def instrument(self, density_module, projection_module):
        """Patch only the selected process, preserving the normal worker owner."""
        original_prepare = projection_module.prepare_persistence_image
        original_witness = density_module.persistence_input_witness
        original_mapping = density_module.map_density_row_for_display
        original_kernel = density_module._visual_smoothing_kernel

        def timed_call(field: str, count_field: str, function, *args, **kwargs):
            sample = getattr(self._local, "sample", None)
            if sample is None:
                return function(*args, **kwargs)
            began = perf_counter_ns()
            try:
                return function(*args, **kwargs)
            finally:
                sample[field] += (perf_counter_ns() - began) / 1_000_000
                sample[count_field] += 1

        @wraps(original_witness)
        def witnessed(*args, **kwargs):
            return timed_call("witness_ms", "witness_calls", original_witness, *args, **kwargs)

        @wraps(original_mapping)
        def mapped(*args, **kwargs):
            return timed_call("mapping_ms", "mapping_calls", original_mapping, *args, **kwargs)

        @wraps(original_kernel)
        def kernel():
            native = original_kernel()
            if native is None:
                return None

            @wraps(native)
            def smoothed(*args, **kwargs):
                return timed_call("native_smoothing_ms", "native_smoothing_calls",
                                  native, *args, **kwargs)

            return smoothed

        @wraps(original_prepare)
        def prepared(request, *args, **kwargs):
            previous = getattr(self._local, "sample", None)
            sample = dict(mode=request.policy.mode.value,
                          shape=list(request.view.density.shape),
                          dtype=request.view.density.dtype.str,
                          has_history=request.history is not None,
                          rematerialize=request.rematerialize,
                          completed=False, total_ms=0.0, witness_ms=0.0,
                          mapping_ms=0.0, native_smoothing_ms=0.0,
                          other_ms=0.0, witness_calls=0, mapping_calls=0,
                          native_smoothing_calls=0)
            self._local.sample = sample
            began = perf_counter_ns()
            try:
                result = original_prepare(request, *args, **kwargs)
                sample["completed"] = True
                return result
            finally:
                sample["total_ms"] = (perf_counter_ns() - began) / 1_000_000
                sample["other_ms"] = (sample["total_ms"] - sample["witness_ms"]
                                      - sample["mapping_ms"] - sample["native_smoothing_ms"])
                self._local.sample = previous
                self._observe(sample)

        with ExitStack() as stack:
            stack.enter_context(patch.object(density_module, "persistence_input_witness", witnessed))
            stack.enter_context(patch.object(density_module, "map_density_row_for_display", mapped))
            stack.enter_context(patch.object(density_module, "_visual_smoothing_kernel", kernel))
            stack.enter_context(patch.object(projection_module, "prepare_persistence_image", prepared))
            yield

    def report(self) -> dict:
        import numpy as np

        with self._lock:
            records, total, dropped = list(self._records), self.total, self.dropped
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in records:
            groups[row["mode"]].append(row)

        def distribution(values: list[float]) -> dict | None:
            if not values or dropped:
                return None
            p50, p95, p99 = np.percentile(values, [50, 95, 99])
            return dict(count=len(values), p50=float(p50), p95=float(p95),
                        p99=float(p99), max=float(max(values)))

        return dict(scope=__doc__, capacity=self.capacity, total=total,
                    retained=len(records), dropped=dropped,
                    complete=total > 0 and dropped == 0 and all(r["completed"] for r in records),
                    stages={mode: dict(count=len(rows),
                        completed=sum(row["completed"] for row in rows),
                        with_history=sum(row["has_history"] for row in rows),
                        rematerialized=sum(row["rematerialize"] for row in rows),
                        calls={name: sum(row[name] for row in rows) for name in
                               ("witness_calls", "mapping_calls", "native_smoothing_calls")},
                        distributions={name: distribution([row[name] for row in rows])
                                       for name in _TIMED_FIELDS})
                            for mode, rows in groups.items()},
                    records=records)


def main(argv: list[str] | None = None) -> int:
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
    own = argparse.ArgumentParser(add_help=False)
    own.add_argument("--substage-capacity", type=int, default=8192)
    options, remaining = own.parse_known_args(argv)
    if "--checkout" not in remaining:
        raise SystemExit("--checkout required")
    root = Path(remaining[remaining.index("--checkout") + 1]).resolve(strict=True)
    sys.path.insert(0, str(root))
    from scripts import benchmark_app05_rtbw_observation as runner
    from sdr_monitor.ui.v2.spectrum import persistence_projection, projection

    recorder = SubstageRecorder(options.substage_capacity)
    with recorder.instrument(persistence_projection, projection):
        result, output, _, _ = runner.main(remaining)
    profile = recorder.report()
    profile["profiler_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["persistence_substage_profile"] = profile
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({k: v for k, v in profile.items() if k != "records"}))
    return int(not profile["complete"] or runner.persistence_catchup_exit_code(result))


if __name__ == "__main__":
    raise SystemExit(main())

"""Opt-in scalar timings and required-slot overlap in normal UI V2.

This observer wraps, rather than changes, the product implementation. Timings
include Python wrappers and must not be used as an uninstrumented speed baseline.
No ndarray, request, prepared image, widget or worker is retained in records.
Required offers observed during optional execution are a lower bound on
worker contention, not a causal allocation of first-paint latency. The
offer-to-optional-end span does not assert that a latest slot stayed occupied.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict, deque
from concurrent.futures import CancelledError
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path
from threading import Lock, local
from time import perf_counter_ns
from typing import Callable
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
        self._active_optional: dict | None = None
        self.concurrent_optional = 0
        self.total = 0
        self.dropped = 0

    def _observe(self, sample: dict) -> None:
        with self._lock:
            self.total += 1
            if len(self._records) == self.capacity:
                self.dropped += 1
            self._records.append(sample)

    @contextmanager
    def instrument(self, density_module, projection_module, *, live_presenter_class=None,
                   record_when: Callable[[], bool] | None = None):
        """Patch only the selected process, preserving the normal worker owner."""
        if live_presenter_class is None:
            from sdr_monitor.ui.presenters.live_presenter import LivePresenter

            live_presenter_class = LivePresenter
        original_prepare = projection_module.prepare_persistence_image
        original_witness = density_module.persistence_input_witness
        original_mapping = density_module.map_density_row_for_display
        original_kernel = density_module._visual_smoothing_kernel
        original_offer = projection_module.SpectrumProjector.offer
        original_live_offer = live_presenter_class._offer_preparation

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
            if record_when is not None and not record_when():
                return original_prepare(request, *args, **kwargs)
            previous = getattr(self._local, "sample", None)
            sample = dict(mode=request.policy.mode.value,
                          shape=list(request.view.density.shape),
                          dtype=request.view.density.dtype.str,
                          has_history=request.history is not None,
                          rematerialize=request.rematerialize,
                          outcome="error", total_ms=0.0, witness_ms=0.0,
                          mapping_ms=0.0, native_smoothing_ms=0.0,
                          other_ms=0.0, witness_calls=0, mapping_calls=0,
                          native_smoothing_calls=0, required_pending_offers=0,
                          required_offer_to_optional_end_ms=0.0,
                          live_pending_offers=0, live_offer_to_optional_end_ms=0.0,
                          _first_required_offer_ns=None, _first_live_offer_ns=None)
            self._local.sample = sample
            began = perf_counter_ns()
            with self._lock:
                if self._active_optional is None:
                    self._active_optional = sample
                else:
                    self.concurrent_optional += 1
            try:
                result = original_prepare(request, *args, **kwargs)
                sample["outcome"] = "completed"
                return result
            except CancelledError:
                sample["outcome"] = "cancelled"
                raise
            finally:
                ended = perf_counter_ns()
                sample["total_ms"] = (ended - began) / 1_000_000
                sample["other_ms"] = (sample["total_ms"] - sample["witness_ms"]
                                      - sample["mapping_ms"] - sample["native_smoothing_ms"])
                with self._lock:
                    first = sample.pop("_first_required_offer_ns")
                    if first is not None:
                        sample["required_offer_to_optional_end_ms"] = (ended - first) / 1_000_000
                    first = sample.pop("_first_live_offer_ns")
                    if first is not None:
                        sample["live_offer_to_optional_end_ms"] = (ended - first) / 1_000_000
                    if self._active_optional is sample:
                        self._active_optional = None
                self._local.sample = previous
                self._observe(sample)

        @wraps(original_offer)
        def offered(projector, request):
            result = original_offer(projector, request)
            # This is a lower-bound overlap witness: the GUI has admitted a
            # required request to the latest slot while optional density is
            # still executing on the shared worker. Never retain the request.
            if request.required_work and projector._pending is request:
                with self._lock:
                    sample = self._active_optional
                    if sample is not None:
                        sample["required_pending_offers"] += 1
                        if sample["_first_required_offer_ns"] is None:
                            sample["_first_required_offer_ns"] = perf_counter_ns()
            return result

        @wraps(original_live_offer)
        def live_offered(presenter, snapshot, revision, *, render=True):
            result = original_live_offer(presenter, snapshot, revision, render=render)
            # Presenter backpressure can keep a newer required frame upstream
            # of SpectrumProjector. Count only an admitted render latest-slot.
            if (render and presenter._projection_in_flight
                    and presenter._pending_preparation is not None
                    and presenter._pending_commands == 0
                    and presenter._preparation_future is None):
                with self._lock:
                    sample = self._active_optional
                    if sample is not None:
                        sample["live_pending_offers"] += 1
                        if sample["_first_live_offer_ns"] is None:
                            sample["_first_live_offer_ns"] = perf_counter_ns()
            return result

        with ExitStack() as stack:
            stack.enter_context(patch.object(density_module, "persistence_input_witness", witnessed))
            stack.enter_context(patch.object(density_module, "map_density_row_for_display", mapped))
            stack.enter_context(patch.object(density_module, "_visual_smoothing_kernel", kernel))
            stack.enter_context(patch.object(projection_module, "prepare_persistence_image", prepared))
            stack.enter_context(patch.object(projection_module.SpectrumProjector, "offer", offered))
            stack.enter_context(patch.object(live_presenter_class, "_offer_preparation", live_offered))
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

        completed = [row for row in records if row["outcome"] == "completed"]
        errors = sum(row["outcome"] == "error" for row in records)
        return dict(scope=__doc__, capacity=self.capacity, total=total,
                    retained=len(records), dropped=dropped,
                    completed=len(completed),
                    cancelled=sum(row["outcome"] == "cancelled" for row in records),
                    errors=errors,
                    concurrent_optional=self.concurrent_optional,
                    complete=bool(completed) and dropped == 0 and errors == 0
                    and self.concurrent_optional == 0,
                    stages={mode: dict(count=len(rows),
                        completed=sum(row["outcome"] == "completed" for row in rows),
                        cancelled=sum(row["outcome"] == "cancelled" for row in rows),
                        errors=sum(row["outcome"] == "error" for row in rows),
                        with_history=sum(row["has_history"] for row in rows),
                        rematerialized=sum(row["rematerialize"] for row in rows),
                        jobs_with_required_pending=sum(row["required_pending_offers"] > 0 for row in rows),
                        required_pending_offers=sum(row["required_pending_offers"] for row in rows),
                        required_offer_to_optional_end_ms=distribution([
                            row["required_offer_to_optional_end_ms"] for row in rows
                            if row["required_pending_offers"] > 0 and row["outcome"] == "completed"]),
                        jobs_with_live_pending=sum(row["live_pending_offers"] > 0 for row in rows),
                        live_pending_offers=sum(row["live_pending_offers"] for row in rows),
                        live_offer_to_optional_end_ms=distribution([
                            row["live_offer_to_optional_end_ms"] for row in rows
                            if row["live_pending_offers"] > 0 and row["outcome"] == "completed"]),
                        calls={name: sum(row[name] for row in rows) for name in
                               ("witness_calls", "mapping_calls", "native_smoothing_calls")},
                        distributions={name: distribution([row[name] for row in rows
                                                           if row["outcome"] == "completed"])
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

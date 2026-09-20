"""Instrument existing RTBW observer; paired first-spectrum-paint stage ages.

Synthetic/offscreen ONLY. Instrumentation perturbs timing. Requests identified
by scalar id plus publication key; no frames/arrays/widgets retained. Missing
or reordered stages are reported, not fabricated or filled from another frame.
Use steady profile without viewport/page churn for queue attribution.
"""
from collections import Counter, OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from time import perf_counter, thread_time
from unittest.mock import patch


def key(frame):
    frame = getattr(frame, "spectrum", frame)
    if frame is None or not hasattr(frame, "timestamp_ns"):
        return None
    return (int(frame.timestamp_ns), "rtbw", int(frame.config_generation))


def instrumented_call(original, before=None, after=None, cpu_samples=None):
    """Keep bound-method metadata so submit hooks identify the real operation."""
    @wraps(original)
    def invoke(*args, **kwargs):
        if before:
            before(*args, **kwargs)
        began = thread_time() if cpu_samples is not None else 0
        value = original(*args, **kwargs)
        if cpu_samples is not None:
            cpu_samples.append((thread_time() - began) * 1000)
        if after:
            after(value, *args, **kwargs)
        return value
    return invoke


class StageRecords:
    def __init__(self, capacity=4096):
        self.capacity = capacity
        self.frames = OrderedDict()
        self.requests = OrderedDict()
        self.rows = deque(maxlen=capacity)
        self.missing = self.reordered = 0
        self.projection_events = Counter()
        self.last_offer = None
        self.lock = threading.RLock()

    def mark(self, identity, stage, when=None):
        if identity is None:
            return
        with self.lock:
            row = self.frames.setdefault(identity, {})
            row.setdefault(stage, perf_counter() if when is None else when)
            while len(self.frames) > self.capacity:
                self.frames.popitem(last=False)

    def request(self, request, stage):
        identity = key(request.traces[0][1].source_frame) if request.traces else None
        if identity is None:
            return
        with self.lock:
            self.projection_events[stage] += 1
            if stage == "projection_offer":
                signature = (identity, request.generation, request.viewport)
                previous = self.last_offer
                if previous is not None:
                    self.projection_events["same_source" if previous[0] == identity else "new_source"] += 1
                    if previous == signature:
                        self.projection_events["same_source_generation_viewport"] += 1
                    if previous[0] == identity and previous[2] != request.viewport:
                        self.projection_events["same_source_new_viewport"] += 1
                self.last_offer = signature
            row = self.requests.setdefault((id(request), identity), {})
            row.setdefault(stage, perf_counter())
            while len(self.requests) > self.capacity:
                self.requests.popitem(last=False)

    def accepted(self, request):
        identity = key(request.traces[0][1].source_frame) if request.traces else None
        with self.lock:
            if identity in self.frames:
                self.frames[identity].update(self.requests.get((id(request), identity), {}))
                self.frames[identity]["applied"] = perf_counter()

    def painted(self, identity, when):
        with self.lock:
            row = self.frames.get(identity, {})
            names = ("publish", "offer", "coalesced", "prepare_begin", "prepare_end",
                     "delivered", "projection_offer", "projection_begin", "projection_end", "applied")
            if not all(name in row for name in names):
                self.missing += 1
                return
            stamps = [row[name] for name in names] + [when]
            if any(b < a for a, b in zip(stamps, stamps[1:])):
                self.reordered += 1
                return
            self.rows.append({"token": identity[0], "total": (when - stamps[0]) * 1000,
                **{name: (b - a) * 1000 for name, a, b in zip(names[1:] + ("paint",), stamps, stamps[1:])}})


def main():
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
    root = Path(sys.argv[sys.argv.index("--checkout") + 1]).resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import numpy as np
    from scripts import benchmark_app05_rtbw_observation as observer
    from scripts.benchmark_app04_poll_overload import PaintAgeTracker
    from sdr_monitor.ui.presenters.live_presenter import LivePresenter
    from sdr_monitor.ui.display_scheduler import DisplayScheduler
    from sdr_monitor.ui.v2.spectrum import projection
    from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
    records = StageRecords()
    cpu = {name: deque(maxlen=4096) for name in ("prepare", "project", "accept")}

    def wrap(before=None, after=None, cpu_name=None):
        def factory(original):
            return instrumented_call(original, before, after,
                                     cpu[cpu_name] if cpu_name else None)
        return factory

    def first_paint(original):
        def painted(tracker, pane, identity, when):
            before = tracker.counts["spectrum"]
            original(tracker, pane, identity, when)
            if pane == "spectrum" and tracker.counts[pane] > before:
                records.painted(identity, when)
        return painted

    def accept_done(value, scene, result):
        with records.lock:
            records.projection_events["accept_callback"] += 1
            records.projection_events["accept_current" if scene._projection_current(result.request)
                                      else "accept_obsolete_geometry"] += 1
        if result.request.traces and scene.displayed_frame is result.request.traces[0][1].source_frame:
            records.accepted(result.request)

    def dispatching(port):
        if (not port._closed and not port._suspended and port._future is None
                and port._pending is not None):
            records.request(port._pending, "projection_dispatch")

    def offering(port, request):
        records.request(request, "projection_offer")
        active = port._active
        if (active is not None and port._future is not None and not port._future.running()
                and not port._future.done() and request.traces and active.traces
                and key(request.traces[0][1].source_frame) != key(active.traces[0][1].source_frame)):
            with records.lock:
                records.projection_events["new_source_while_previous_queued"] += 1

    def preparation_submitted(executor, operation, *args, **kwargs):
        # Observe the actual argument after any latest-slot replacement, not
        # the old pending candidate at entry to _dispatch_preparation.
        if (isinstance(getattr(operation, "__self__", None), LivePresenter)
                and getattr(operation, "__name__", None) == "_prepare" and args):
            records.mark(key(args[0]), "prepare_dispatch")

    def projection_callback(port, future):
        if future is port._future and port._active is not None:
            records.request(port._active, "projection_callback")

    def replacement_taken(snapshot, scheduler, admitted):
        if snapshot is not None:
            # Fresh publication reuses an already granted cadence slot, not
            # a new timer tick. Record its actual admission/dispatch boundary.
            records.mark(key(snapshot), "coalesced")

    with ExitStack() as stack:
        def instrument(owner, name, wrapper):
            stack.enter_context(patch.object(owner, name, wrapper(getattr(owner, name))))
        instrument(PaintAgeTracker, "publish", wrap(before=lambda _, identity, when: records.mark(identity, "publish", when)))
        instrument(PaintAgeTracker, "painted", first_paint)
        instrument(LivePresenter, "offer_snapshot_for_render", wrap(before=lambda _, snapshot: records.mark(key(snapshot), "offer")))
        instrument(LivePresenter, "_emit_render", wrap(before=lambda _, snapshot: records.mark(key(snapshot), "coalesced")))
        instrument(ThreadPoolExecutor, "submit", wrap(before=preparation_submitted))
        if hasattr(DisplayScheduler, "take_pending_replacement"):
            instrument(DisplayScheduler, "take_pending_replacement", wrap(after=replacement_taken))
        instrument(LivePresenter, "_prepare", wrap(
            before=lambda _, snapshot, *args, **kwargs: records.mark(key(snapshot), "prepare_begin"),
            after=lambda value, _, snapshot, *args, **kwargs: records.mark(key(snapshot), "prepare_end"), cpu_name="prepare"))
        instrument(LivePresenter, "_deliver_prepared", wrap(
            before=lambda _, delivery: records.mark(key(delivery.snapshot), "delivered")))
        instrument(projection.SpectrumProjector, "offer", wrap(before=offering))
        instrument(projection.SpectrumProjector, "_dispatch", wrap(before=dispatching))
        instrument(projection.SpectrumProjector, "_finish", wrap(before=projection_callback))
        instrument(projection, "project_spectrum", wrap(
            before=lambda request, **kwargs: records.request(request, "projection_begin"),
            after=lambda result, request, **kwargs: records.request(request, "projection_end"), cpu_name="project"))
        instrument(SpectrumScene, "_accept_projection", wrap(after=accept_done, cpu_name="accept"))
        report, output, _, _ = observer.main()
    rows = [row for row in records.rows if row["token"] > 100]
    report["stage_profile"] = dict(scope=__doc__, retained=len(rows), capacity=records.capacity,
        profiler_sha256=hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
        queue_attribution_scope="Exact scalar identity with both endpoints, including unpainted accepted frames; first 100 tokens excluded; not pooled first-paint ages",
        queue_attribution_ms={name: dict(count=len(values), **dict(zip(("p50", "p95", "p99", "max"), map(float,
            (*np.percentile(values, [50, 95, 99]), max(values))))))
            for name, start, stop in (("coalesced_to_dispatch", "coalesced", "prepare_dispatch"),
                                      ("dispatch_to_prepare_begin", "prepare_dispatch", "prepare_begin"),
                                      ("project_end_to_gui_callback", "projection_end", "projection_callback"),
                                      ("gui_callback_to_applied", "projection_callback", "applied"))
            if (values := [(entry[stop] - entry[start]) * 1000 for identity, entry in records.frames.items()
                           if identity[0] > 100 and start in entry and stop in entry and entry[stop] >= entry[start]])},
        projection_events=dict(records.projection_events),
        projection_wait_ms={name: dict(zip(("p50", "p95", "p99", "max"), map(float,
            (*np.percentile(values, [50, 95, 99]), max(values)))))
            for name, start, stop in (("offer_to_dispatch", "projection_offer", "projection_dispatch"),
                                      ("dispatch_to_begin", "projection_dispatch", "projection_begin"))
            if (values := [(entry[stop] - entry[start]) * 1000 for entry in records.requests.values()
                           if start in entry and stop in entry and entry[stop] >= entry[start]])},
        missing=records.missing, reordered=records.reordered,
        cpu_scope="Windows thread CPU clock is quantized (observed15.625ms); aggregated retained-call totals only, NOT per-call CPU latency percentiles",
        cpu_ms={name: dict(calls=len(data), total=sum(data), mean=sum(data) / len(data))
                for name, data in cpu.items() if data},
        durations_ms={name: dict(zip(("p50", "p95", "p99", "max"), map(float,
            (*np.percentile([row[name] for row in rows], [50, 95, 99]), max(row[name] for row in rows)))))
            for name in rows[0] if name != "token"} if rows else {})
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report["stage_profile"]))


if __name__ == "__main__":
    main()

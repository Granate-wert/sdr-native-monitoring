"""Instrument existing RTBW observer; paired first-spectrum-paint stage ages.

Synthetic/offscreen ONLY. Instrumentation perturbs timing. Requests identified
by scalar id plus publication key; no frames/arrays/widgets retained. Missing
or reordered stages are reported, not fabricated or filled from another frame.
Request-local delivery stamps distinguish re-projection after viewport/show.
Navigation context is scalar timing, not proof of causation.
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
    def __init__(self, capacity=4096, *, source_stages=None):
        self.capacity = capacity
        self.source_stages = source_stages or ("publish", "offer", "coalesced", "prepare_dispatch",
                                              "prepare_begin", "prepare_end", "delivered")
        self.frames = OrderedDict()
        self.requests = OrderedDict()
        self.accepted_requests = OrderedDict()
        self.prepared_deliveries = OrderedDict()
        self.deliveries = OrderedDict()
        self.rows = deque(maxlen=capacity)
        self.details = deque(maxlen=capacity)
        self.missing = self.reordered = 0
        self.missing_stages = Counter()
        self.projection_events = Counter()
        self.last_offer = None
        self.last_visibility = None
        self.last_show = None
        self.last_viewport = None
        self.viewport_changed_at = None
        self.lock = threading.RLock()
        self.flow = OrderedDict()
        self.flow_evictions = 0

    def flow_mark(self, identity, stage):
        if identity is None:
            return
        with self.lock:
            self.flow.setdefault(identity, set()).add(stage)
            self.flow.move_to_end(identity)
            while len(self.flow) > self.capacity:
                self.flow.popitem(last=False)
                self.flow_evictions += 1

    def mark(self, identity, stage, when=None):
        if identity is None:
            return
        with self.lock:
            self.flow_mark(identity, stage)
            row = self.frames.setdefault(identity, {})
            if stage == "coalesced":
                # Same source may be prepared again after show. Never attach
                # its earlier preparation to a later projection request.
                row = self.frames[identity] = {name: row[name] for name in
                    ("publish", "offer") if name in row}
            row.setdefault(stage, perf_counter() if when is None else when)
            while len(self.frames) > self.capacity:
                self.frames.popitem(last=False)

    def request(self, request, stage, when=None):
        identity = key(request.traces[0][1].source_frame) if request.traces else None
        if identity is None:
            return
        with self.lock:
            self.projection_events[stage] += 1
            required = getattr(request, "required_work", True)
            kind = ("density_only" if not required else
                    "required_density" if getattr(request, "persistence", None) is not None else "required_only")
            self.projection_events[f"{stage}:{kind}"] += 1
            if required:
                self.flow_mark(identity, stage)
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
            request_key = (id(request), identity, request.generation, request.viewport)
            if stage == "projection_offer":
                self.requests[request_key] = {}  # id reuse / explicit retry is a new offer
            row = self.requests.setdefault(request_key, {})
            if stage == "projection_offer":
                # Copy scalar delivery stamps at offer, not at eventual ack:
                # a later preparation of this source can already be in flight.
                row["source_stages"] = {name: value for name, value in
                    self.deliveries.get(identity, self.frames.get(identity, {})).items() if name in
                    self.source_stages}
                row["geometry"] = (request.generation, request.viewport)
            row.setdefault(stage, perf_counter() if when is None else when)
            while len(self.requests) > self.capacity:
                self.requests.popitem(last=False)

    def preparation_started(self, identity):
        with self.lock:
            row = {name: value for name, value in self.frames.get(identity, {}).items()
                   if name in ("publish", "offer", "coalesced", "prepare_dispatch")}
            row["prepare_begin"] = perf_counter()
            self.mark(identity, "prepare_begin", row["prepare_begin"])
            return row

    def preparation_finished(self, delivery, row):
        identity = key(delivery.snapshot)
        with self.lock:
            row["prepare_end"] = perf_counter()
            self.mark(identity, "prepare_end", row["prepare_end"])
            # Control ack may replace the outer delivery/revision; its immutable
            # prepared value is unchanged. Capture only its scalar id, not owner.
            self.prepared_deliveries[(id(delivery.value), identity)] = row
            while len(self.prepared_deliveries) > self.capacity:
                self.prepared_deliveries.popitem(last=False)

    def delivered(self, delivery):
        identity = key(delivery.snapshot)
        with self.lock:
            row = dict(self.prepared_deliveries.get((id(delivery.value), identity), {}))
            row["delivered"] = perf_counter()
            self.mark(identity, "delivered", row["delivered"])
            self.deliveries[identity] = row
            self.deliveries.move_to_end(identity)
            while len(self.deliveries) > self.capacity:
                self.deliveries.popitem(last=False)

    def accepted(self, request, *, required_only=False):
        if not getattr(request, "required_work", True):
            return  # An image upload cannot replace the spectrum witness.
        identity = key(request.traces[0][1].source_frame) if request.traces else None
        with self.lock:
            self.flow_mark(identity, "applied")
            if identity in self.frames:
                request_row = self.requests.get(
                    (id(request), identity, request.generation, request.viewport), {})
                # Missing stages must not survive from an earlier accepted
                # viewport of the same source and look like a complete witness.
                row = dict(request_row.get("source_stages", {}))
                row.update({name: value for name, value in request_row.items()
                            if name != "source_stages"})
                # A staged spectrum can paint before optional work completes.
                # Use its exact required-ready boundary, never final completion
                # from a later callback or another request.
                if required_only:
                    row.pop("projection_end", None)
                    row.pop("projection_callback", None)
                    if "required_ready" in row:
                        row["projection_end"] = row["required_ready"]
                    if "required_callback" in row:
                        row["projection_callback"] = row["required_callback"]
                row["completion_kind"] = "required-stage" if required_only else "whole-result"
                row["applied"] = perf_counter()
                self.accepted_requests[identity] = row
                self.accepted_requests.move_to_end(identity)
                while len(self.accepted_requests) > self.capacity:
                    self.accepted_requests.popitem(last=False)

    def visibility(self, active, when=None):
        when = perf_counter() if when is None else when
        with self.lock:
            if self.last_visibility != bool(active):
                self.last_visibility = bool(active)
                if active:
                    self.last_show = when

    def viewport(self, viewport, when=None):
        when = perf_counter() if when is None else when
        with self.lock:
            if self.last_viewport != viewport:
                self.last_viewport = viewport
                self.viewport_changed_at = when

    def painted(self, identity, when):
        with self.lock:
            self.flow_mark(identity, "painted")
            row = self.accepted_requests.get(identity, self.frames.get(identity, {}))
            names = ("publish", "offer", "coalesced", "prepare_begin", "prepare_end",
                     "delivered", "projection_offer", "projection_begin", "projection_end", "applied")
            if not all(name in row for name in names):
                self.missing += 1
                self.missing_stages.update(name for name in names if name not in row)
                return
            stamps = [row[name] for name in names] + [when]
            if any(b < a for a, b in zip(stamps, stamps[1:])):
                self.reordered += 1
                return
            durations = {"token": identity[0], "total": (when - stamps[0]) * 1000,
                **{name: (b - a) * 1000 for name, a, b in zip(names[1:] + ("paint",), stamps, stamps[1:])}}
            self.rows.append(durations)
            self.details.append(dict(identity=identity, durations=durations.copy(),
                completion_kind=row.get("completion_kind", "whole-result"),
                geometry=row.get("geometry"), visible=self.last_visibility,
                since_show_ms=None if self.last_show is None else (when - self.last_show) * 1000,
                since_viewport_ms=None if self.viewport_changed_at is None else
                    (when - self.viewport_changed_at) * 1000))


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

    def accept_projection(original):
        @wraps(original)
        def invoke(scene, result, **kwargs):
            current = scene._projection_current(result.request)
            required_only = kwargs.get("required_only", False)
            optional_only = (not getattr(result.request, "required_work", True) or
                             (not required_only and
                              getattr(scene, "_early_projection_request", None) is result.request))
            began = thread_time()
            value = original(scene, result, **kwargs)
            cpu["accept"].append((thread_time() - began) * 1000)
            with records.lock:
                records.projection_events["accept_callback"] += 1
                records.projection_events["accept_current" if current else "accept_obsolete_geometry"] += 1
            # An obsolete viewport can reference the already displayed source.
            # Source equality alone is not evidence that this request applied.
                records.projection_events["optional_only_callback" if optional_only else "required_admission_callback"] += 1
            if (current and not optional_only and result.request.traces
                    and scene.displayed_frame is result.request.traces[0][1].source_frame):
                records.accepted(result.request, required_only=required_only)
            return value
        return invoke

    def dispatching(original):
        @wraps(original)
        def invoke(port):
            before, when = port._future, perf_counter()
            value = original(port)
            if before is None and port._future is not None and port._active is not None:
                records.request(port._active, "projection_dispatch", when)
            return value
        return invoke

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

    def required_callback(port, serial):
        if serial == port._active_serial and port._active is not None:
            records.request(port._active, "required_callback")

    def projecting(original):
        @wraps(original)
        def invoke(request, **kwargs):
            records.request(request, "projection_begin")
            ready = kwargs.get("spectrum_ready")
            if ready is not None:
                def required_ready(result):
                    records.request(request, "required_ready")
                    return ready(result)
                kwargs["spectrum_ready"] = required_ready
            began = thread_time()
            result = original(request, **kwargs)
            cpu["project"].append((thread_time() - began) * 1000)
            records.request(request, "projection_end")
            return result
        return invoke

    def replacement_taken(snapshot, scheduler, admitted):
        if snapshot is not None:
            # Fresh publication reuses an already granted cadence slot, not
            # a new timer tick. Record its actual admission/dispatch boundary.
            records.mark(key(snapshot), "coalesced")

    def preparing(original):
        @wraps(original)
        def invoke(presenter, snapshot, *args, **kwargs):
            row = records.preparation_started(key(snapshot))
            began = thread_time()
            delivery = original(presenter, snapshot, *args, **kwargs)
            cpu["prepare"].append((thread_time() - began) * 1000)
            records.preparation_finished(delivery, row)
            return delivery
        return invoke

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
        instrument(LivePresenter, "_prepare", preparing)
        instrument(LivePresenter, "_deliver_prepared", wrap(
            before=lambda _, delivery: records.delivered(delivery)))
        instrument(projection.SpectrumProjector, "offer", wrap(before=offering))
        instrument(projection.SpectrumProjector, "_dispatch", dispatching)
        instrument(projection.SpectrumProjector, "_finish", wrap(before=projection_callback))
        if hasattr(projection.SpectrumProjector, "_accept_required"):
            instrument(projection.SpectrumProjector, "_accept_required", wrap(before=required_callback))
        instrument(projection, "project_spectrum", projecting)
        instrument(SpectrumScene, "_accept_projection", accept_projection)
        instrument(SpectrumScene, "set_presentation_active", wrap(
            before=lambda scene, active: records.visibility(active)))
        instrument(SpectrumScene, "_request_projection", wrap(
            before=lambda scene, *_: records.viewport(scene._viewport())))
        report, output, _, _ = observer.main()
    rows = [row for row in records.rows if row["token"] > 100]
    report["stage_profile"] = dict(scope=__doc__, retained=len(rows), capacity=records.capacity,
        flow_scope="Distinct exact source identities after token100 in bounded scalar history. Required projections only; density-only callbacks cannot imply spectrum admission. Evicted sources are not classified. Not RF loss.",
        flow_evictions=records.flow_evictions,
        flow_counts=dict(Counter(stage for identity, stages in records.flow.items()
                                 if identity[0] > 100 for stage in stages)),
        flow_gaps=dict(Counter(label for identity, stages in records.flow.items() if identity[0] > 100
            for label, first, second in (("prepared_not_applied", "prepare_end", "applied"),
                                         ("applied_not_painted", "applied", "painted"),
                                         ("delivered_not_projected", "delivered", "projection_begin"))
            if first in stages and second not in stages)),
        projection_end_scope="Required-ready for early spectrum admission; whole-result end otherwise. Optional final callback never overwrites an early admission. Not density completion latency.",
        profiler_sha256=hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
        queue_attribution_scope="Exact scalar identity with both endpoints, including unpainted accepted frames; first 100 tokens excluded; not pooled first-paint ages",
        queue_attribution_ms={name: dict(count=len(values), **dict(zip(("p50", "p95", "p99", "max"), map(float,
            (*np.percentile(values, [50, 95, 99]), max(values))))))
            for name, start, stop in (("coalesced_to_dispatch", "coalesced", "prepare_dispatch"),
                                      ("dispatch_to_prepare_begin", "prepare_dispatch", "prepare_begin"),
                                      ("project_end_to_gui_callback", "projection_end", "projection_callback"),
                                      ("gui_callback_to_applied", "projection_callback", "applied"))
            if (values := [(entry[stop] - entry[start]) * 1000 for identity, entry in
                           (records.accepted_requests if start.startswith("projection") or start == "projection_callback"
                            else records.frames).items()
                           if identity[0] > 100 and start in entry and stop in entry and entry[stop] >= entry[start]])},
        projection_events=dict(records.projection_events),
        projection_wait_ms={name: dict(zip(("p50", "p95", "p99", "max"), map(float,
            (*np.percentile(values, [50, 95, 99]), max(values)))))
            for name, start, stop in (("offer_to_dispatch", "projection_offer", "projection_dispatch"),
                                      ("dispatch_to_begin", "projection_dispatch", "projection_begin"))
            if (values := [(entry[stop] - entry[start]) * 1000 for entry in records.requests.values()
                           if start in entry and stop in entry and entry[stop] >= entry[start]])},
        missing=records.missing, missing_stages=dict(records.missing_stages), reordered=records.reordered,
        paired_slow_frames=sorted(records.details, key=lambda row: row["durations"]["total"], reverse=True)[:40],
        navigation_scope="Latest visibility and viewport at paint; source/delivery stages copied per exact request at offer; bounded scalar witnesses only",
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

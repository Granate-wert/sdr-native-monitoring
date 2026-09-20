"""Exact-source paired Sweep stages, bounded slow-frame rows, normal Qt observer.

Synthetic/offscreen attribution only. Missing/reordered identities are excluded,
never borrowed from another frame. Inclusive function percentiles are not added.
Run Python -I with benchmark_app04_poll_overload arguments; sidecar .stages.json.
"""
from contextlib import ExitStack
from collections import deque
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from threading import Event
from unittest.mock import patch

STAGES = ("publish", "selected", "domain_end", "prepare_begin", "prepare_end",
          "delivered", "delivery_end", "projection_offer", "projection_begin", "projection_end", "applied")


def mark_selected(records, identity):
    """A new conversion of the same source must not inherit old stage stamps."""
    with records.lock:
        records.frames[identity] = {name: stamp for name, stamp in records.frames.get(identity, {}).items()
                                    if name == "publish"}
        records.mark(identity, "selected")


def paired_paint(records, identity, when):
    with records.lock:
        row = records.accepted_requests.get(identity, {})
        if not all(name in row for name in STAGES):
            records.missing += 1
            records.missing_stages.update(name for name in STAGES if name not in row)
            return
        stamps = [row[name] for name in STAGES] + [when]
        if any(b < a for a, b in zip(stamps, stamps[1:])):
            records.reordered += 1
            return
        records.rows.append(dict(identity=identity, total=(when - stamps[0]) * 1000,
            visible=records.last_visibility,
            since_show_ms=None if records.last_show is None else (when - records.last_show) * 1000,
            geometry=row.get("geometry"),
            projection_window=(row["projection_begin"], row["projection_end"]),
            **{name: (b - a) * 1000 for name, a, b in zip(STAGES[1:] + ("paint",), stamps, stamps[1:])}))


def main():
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
    gate_poll = "--diagnostic-gate-poll" in sys.argv
    if gate_poll:
        sys.argv.remove("--diagnostic-gate-poll")
    root = Path(sys.argv[sys.argv.index("--checkout") + 1]).resolve(strict=True)
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    sys.path.insert(0, str(root))
    import numpy as np
    from scripts import benchmark_app04_poll_overload as observer
    from scripts import profile_app05_rtbw_stages as shared
    from sdr_monitor.services import native_continuous_sweep as service
    from sdr_monitor.ui.v2.state.prepared_sweep import SweepSnapshotPreparer
    from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
    from sdr_monitor.ui.v2.spectrum import projection
    from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene

    def key(frame):
        return observer.sweep_key(getattr(frame, "spectrum", frame))

    records = shared.StageRecords(source_stages=STAGES[:7])
    polls = deque(maxlen=4096)
    projecting = Event()
    gated_ticks = 0

    def gate_projection(original):
        def invoke(*args, **kwargs):
            projecting.set()
            try:
                return original(*args, **kwargs)
            finally:
                projecting.clear()
        return invoke

    def gate_polling(original):
        def invoke(*args, **kwargs):
            nonlocal gated_ticks
            if projecting.is_set():
                gated_ticks += 1
                return None
            return original(*args, **kwargs)
        return invoke

    def poll_interval(original):
        def invoke(*args, **kwargs):
            began = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                polls.append((began, perf_counter()))
        return invoke

    def wrap(before=None, after=None):
        return lambda original: shared.instrumented_call(original, before=before, after=after)

    def first_paint(original):
        def invoke(tracker, pane, identity, when):
            count = tracker.counts["spectrum"]
            original(tracker, pane, identity, when)
            if pane == "spectrum" and tracker.counts[pane] > count:
                paired_paint(records, identity, when)
        return invoke

    def accepted(value, scene, result):
        if result.request.traces and scene.displayed_frame is result.request.traces[0][1].source_frame:
            records.accepted(result.request)

    def preparation(original):
        def invoke(owner, snapshot, bundle):
            identity = key(bundle)
            with records.lock:
                row = {name: stamp for name, stamp in records.frames.get(identity, {}).items()
                       if name in STAGES[:3]}
            row["prepare_begin"] = perf_counter()
            value = original(owner, snapshot, bundle)
            row["prepare_end"] = perf_counter()
            with records.lock:
                records.prepared_deliveries[(id(value), key(value.analyzer_bundle))] = row
                while len(records.prepared_deliveries) > records.capacity:
                    records.prepared_deliveries.popitem(last=False)
            return value
        return invoke

    def delivery(original):
        def invoke(owner, publication):
            identity = key(publication.bundle)
            with records.lock:
                row = dict(records.prepared_deliveries.get((id(publication.presentation), identity), {}))
                row["delivered"] = perf_counter()
            value = original(owner, publication)
            row["delivery_end"] = perf_counter()
            with records.lock:
                records.deliveries[identity] = row
                records.deliveries.move_to_end(identity)
                while len(records.deliveries) > records.capacity:
                    records.deliveries.popitem(last=False)
            return value
        return invoke

    with ExitStack() as stack:
        stack.enter_context(patch.object(shared, "key", key))

        def hook(owner, name, wrapper):
            stack.enter_context(patch.object(owner, name, wrapper(getattr(owner, name))))

        hook(observer.PaintAgeTracker, "publish", wrap(before=lambda _, identity, when: records.mark(identity, "publish", when)))
        hook(ContinuousSweepPresenter, "_poll_and_prepare", poll_interval)
        hook(observer.PaintAgeTracker, "painted", first_paint)
        hook(service, "_to_domain_progress", wrap(
            before=lambda n, **kw: mark_selected(records, (n.line_sequence, "partial", n.revision)),
            after=lambda result, *a, **kw: records.mark(key(result), "domain_end")))
        hook(service, "_to_domain_line", wrap(
            before=lambda n, **kw: mark_selected(records, (n.line_sequence, n.state, 0)),
            after=lambda result, *a, **kw: records.mark(key(result), "domain_end")))
        hook(SweepSnapshotPreparer, "__call__", preparation)
        hook(ContinuousSweepPresenter, "_emit_snapshot", delivery)
        hook(projection.SpectrumProjector, "offer", wrap(before=lambda _, request: records.request(request, "projection_offer")))
        hook(projection, "project_spectrum", wrap(
            before=lambda request, **kw: records.request(request, "projection_begin"),
            after=lambda value, request, **kw: records.request(request, "projection_end")))
        hook(SpectrumScene, "_accept_projection", wrap(after=accepted))
        hook(SpectrumScene, "set_presentation_active", wrap(
            before=lambda scene, active: records.visibility(active)))
        if gate_poll:
            # Diagnostic scheduling experiment only; not a product feature.
            hook(projection, "project_spectrum", gate_projection)
            hook(ContinuousSweepPresenter, "_poll", gate_polling)
        observer.main()
    rows = list(records.rows)
    overlap = {}
    for row in rows:
        # Carry these scalar endpoints at paint: high-rate publications may
        # evict the original frame record before the run's final report.
        left, right = row.pop("projection_window")
        overlap[row["identity"]] = sum(max(0., min(right, end) - max(left, begin))
                                       for begin, end in polls) * 1000
    names = ("total",) + STAGES[1:] + ("paint",)
    groups = {"all": rows, "over_50ms": [row for row in rows if row["total"] > 50],
              "at_most_50ms": [row for row in rows if row["total"] <= 50],
              "resume_250ms": [row for row in rows if row["since_show_ms"] is not None
                               and row["since_show_ms"] < 250],
              "steady_visible": [row for row in rows if row["visible"]
                                 and row["since_show_ms"] is not None and row["since_show_ms"] >= 250]}
    report = dict(scope=__doc__, capacity=records.capacity, retained=len(rows),
        missing=records.missing, reordered=records.reordered,
        missing_stages=dict(records.missing_stages),
        navigation_scope="Visibility at first paint, resume first250ms after show, not causal proof; exact accepted request carries copied preparation/delivery stamps",
        diagnostic_gate_poll=gate_poll, gated_ticks=gated_ticks,
        overlap_scope="Inclusive overlap with existing Sweep poll+prepare worker; not an additive stage or causal proof",
        projection_poll_overlap={group: dict(count=len(values),
            overlapping=sum(overlap.get(row["identity"], 0.) > 0 for row in values),
            overlap_ms_p50=float(np.median([overlap[row["identity"]] for row in values if row["identity"] in overlap])))
            for group, values in groups.items() if values and any(row["identity"] in overlap for row in values)},
        profiler_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        groups={group: dict(count=len(values), stages={name: dict(zip(("p50", "p95", "max"), map(float,
            (*np.percentile([row[name] for row in values], [50, 95]), max(row[name] for row in values)))))
            for name in names}) for group, values in groups.items() if values},
        slowest=[dict(row, projection_poll_overlap_ms=overlap.get(row["identity"]))
                 for row in sorted(rows, key=lambda row: row["total"], reverse=True)[:40]])
    with output.with_suffix(".stages.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(dict(retained=len(rows), missing=records.missing, reordered=records.reordered,
                         overlap=report["projection_poll_overlap"], groups=report["groups"], slowest=report["slowest"][:5])))


if __name__ == "__main__":
    main()

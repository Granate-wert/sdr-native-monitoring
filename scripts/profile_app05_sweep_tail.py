"""Exact-source paired Sweep stages, bounded slow-frame rows, normal Qt observer.

Synthetic/offscreen attribution only. Missing/reordered identities are excluded,
never borrowed from another frame. Inclusive function percentiles are not added.
Run Python -I with benchmark_app04_poll_overload arguments; sidecar .stages.json.
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

STAGES = ("publish", "selected", "domain_end", "prepare_begin", "prepare_end",
          "delivered", "delivery_end", "projection_offer", "projection_begin", "projection_end", "applied")


def paired_paint(records, identity, when):
    with records.lock:
        row = records.frames.get(identity, {})
        if not all(name in row for name in STAGES):
            records.missing += 1
            return
        stamps = [row[name] for name in STAGES] + [when]
        if any(b < a for a, b in zip(stamps, stamps[1:])):
            records.reordered += 1
            return
        records.rows.append(dict(identity=identity, total=(when - stamps[0]) * 1000,
            **{name: (b - a) * 1000 for name, a, b in zip(STAGES[1:] + ("paint",), stamps, stamps[1:])}))


def main():
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
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

    records = shared.StageRecords()

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

    with ExitStack() as stack:
        stack.enter_context(patch.object(shared, "key", key))

        def hook(owner, name, wrapper):
            stack.enter_context(patch.object(owner, name, wrapper(getattr(owner, name))))

        hook(observer.PaintAgeTracker, "publish", wrap(before=lambda _, identity, when: records.mark(identity, "publish", when)))
        hook(observer.PaintAgeTracker, "painted", first_paint)
        hook(service, "_to_domain_progress", wrap(
            before=lambda n, **kw: records.mark((n.line_sequence, "partial", n.revision), "selected"),
            after=lambda result, *a, **kw: records.mark(key(result), "domain_end")))
        hook(service, "_to_domain_line", wrap(
            before=lambda n, **kw: records.mark((n.line_sequence, n.state, 0), "selected"),
            after=lambda result, *a, **kw: records.mark(key(result), "domain_end")))
        hook(SweepSnapshotPreparer, "__call__", wrap(
            before=lambda _, snapshot, bundle: records.mark(key(bundle), "prepare_begin"),
            after=lambda value, *a, **kw: records.mark(key(value.analyzer_bundle), "prepare_end")))
        hook(ContinuousSweepPresenter, "_emit_snapshot", wrap(
            before=lambda _, publication: records.mark(key(publication.bundle), "delivered"),
            after=lambda value, _, publication: records.mark(key(publication.bundle), "delivery_end")))
        hook(projection.SpectrumProjector, "offer", wrap(before=lambda _, request: records.request(request, "projection_offer")))
        hook(projection, "project_spectrum", wrap(
            before=lambda request, **kw: records.request(request, "projection_begin"),
            after=lambda value, request, **kw: records.request(request, "projection_end")))
        hook(SpectrumScene, "_accept_projection", wrap(after=accepted))
        observer.main()
    rows = list(records.rows)
    names = ("total",) + STAGES[1:] + ("paint",)
    groups = {"all": rows, "over_50ms": [row for row in rows if row["total"] > 50],
              "at_most_50ms": [row for row in rows if row["total"] <= 50]}
    report = dict(scope=__doc__, capacity=records.capacity, retained=len(rows),
        missing=records.missing, reordered=records.reordered,
        profiler_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        groups={group: dict(count=len(values), stages={name: dict(zip(("p50", "p95", "max"), map(float,
            (*np.percentile([row[name] for row in values], [50, 95]), max(row[name] for row in values)))))
            for name in names}) for group, values in groups.items() if values},
        slowest=sorted(rows, key=lambda row: row["total"], reverse=True)[:40])
    with output.with_suffix(".stages.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(dict(retained=len(rows), missing=records.missing, reordered=records.reordered,
                         groups=report["groups"], slowest=report["slowest"][:5])))


if __name__ == "__main__":
    main()

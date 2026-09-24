"""Opt-in physical Pluto RTBW -> visible product UI V2 timing observer.

RX only. This uses the normal V2 shell, source drawer and Start/Stop controls.
The observer subclasses the two pyqtgraph viewport widgets and patches scalar
stage methods; default behavior never changes the backend, FFT, scheduler or
renderer. Explicit diagnostic display flags can change only V2 presentation.
Native timestamps are host-wall estimates, not hardware capture time.
Qt paint-return is not DWM presentation or monitor scanout. Run on Windows with
an explicit URI and a unique output path; no device is opened at import time.
"""

from __future__ import annotations

import argparse
from collections import Counter, OrderedDict, deque
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import weakref
from time import perf_counter_ns, time_ns
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NATIVE_COUNTER_FIELDS = (
    "fft_frames_computed", "fft_frames_dropped", "iq_samples_dropped", "iq_blocks_dropped",
    "source_samples_dropped", "source_blocks_dropped",
    "acquisition_queue_samples_dropped", "acquisition_queue_blocks_dropped",
    "snapshots_emitted", "snapshots_superseded", "persistence_updates",
    "persistence_snapshots_superseded",
)


def counter_deltas(before: object, after: object, fields: tuple[str, ...]) -> dict[str, int]:
    """Subtract cumulative counters across the measured interval only."""
    return {field: int(getattr(after, field)) - int(getattr(before, field)) for field in fields}


def projector_counters(projector: object | None) -> dict[str, int] | None:
    if projector is None:
        return None
    result = {field: int(getattr(projector, field)) for field in ("completed", "cancelled", "superseded")}
    optional = getattr(projector, "persistence_projector", None)
    if optional is not None:
        result.update({f"optional_{field}": int(getattr(optional, field))
                       for field in ("completed", "cancelled", "superseded")})
    return result


def required_projection_acceptance(scene: object, request: object, *, required_only: bool) -> bool:
    """Predict the successful required branch, including same-source reprojection."""
    return bool(getattr(request, "required_work") and request.owner is scene._projection_owner
                and scene._projection_current(request)
                and (required_only or scene._early_projection_request is not request))


class DensitySequenceWitness:
    """Bounded weak identity bridge from a prepared density to its native update."""

    def __init__(self, capacity: int = 512) -> None:
        self._sources: OrderedDict[int, tuple[weakref.ReferenceType, int]] = OrderedDict()
        self.capacity = capacity

    def observe(self, state: object) -> None:
        raw = getattr(getattr(state, "bundle", None), "persistence", None)
        prepared = getattr(getattr(state, "live", None), "persistence_frame", None)
        density = getattr(prepared, "density", None)
        if raw is None or density is None:
            return
        key = id(density)
        self._sources[key] = (weakref.ref(density), int(raw.update_sequence))
        self._sources.move_to_end(key)
        while len(self._sources) > self.capacity:
            self._sources.popitem(last=False)

    def sequence(self, density: object | None) -> int | None:
        if density is None:
            return None
        pair = self._sources.get(id(density))
        return pair[1] if pair is not None and pair[0]() is density else None


def hide_show_checks(trace: dict[str, Any]) -> dict[str, bool]:
    """Fail closed on missing or stale scalar witnesses; no DWM/scanout claim."""
    before = trace.get("before_hide") or {}
    hidden = trace.get("after_hide") or {}
    before_show = trace.get("before_show") or {}
    after_show = trace.get("after_show") or {}
    at_end = trace.get("at_end") or {}
    after_stop = trace.get("after_stop") or {}
    target = trace.get("show_target_sequence")
    return {
        "live_continued": bool(before.get("running") and hidden.get("running")
                               and before_show.get("running") and after_show.get("running")
                               and at_end.get("running")),
        "epoch_unchanged": bool(before.get("epoch") is not None
                                and all(row.get("epoch") == before["epoch"]
                                        for row in (hidden, before_show, after_show, at_end))),
        "native_advanced_hidden": bool(before.get("native_sequence") is not None
                                        and before_show.get("native_sequence") is not None
                                        and before_show["native_sequence"] > before["native_sequence"]),
        "density_advanced_hidden": bool(before.get("latest_sequence") is not None
                                         and before_show.get("latest_sequence") is not None
                                         and before_show["latest_sequence"] > before["latest_sequence"]),
        "navigation_effective": bool(before.get("workspace") == "analyzer"
                                     and hidden.get("workspace") == "calibration"
                                     and hidden.get("analyzer_visible") is False
                                     and after_show.get("workspace") == "analyzer"
                                     and after_show.get("analyzer_visible") is True),
        "hide_cleared_image": bool(hidden.get("presentation_active") is False
                                   and hidden.get("image_visible") is False
                                   and hidden.get("image_present") is False
                                   and hidden.get("uploaded_sequence") is None),
        "no_hidden_upload": bool(hidden.get("image_uploads") is not None
                                  and before_show.get("image_uploads") == hidden["image_uploads"]
                                  and trace.get("hidden_uploads") == 0),
        "show_target_known": target is not None,
        "show_restored_presentation": bool(after_show.get("presentation_active") is True
                                           and at_end.get("presentation_active") is True),
        "fresh_visible_upload": bool(trace.get("first_fresh_visible_upload_ns") is not None),
        "no_stale_show_upload": bool(target is not None and trace.get("shown_stale_uploads") == 0
                                     and trace.get("shown_unmapped_uploads") == 0),
        "fresh_qt_paint_within_1s": bool(trace.get("fresh_paint_ms") is not None
                                         and 0 <= trace["fresh_paint_ms"] <= 1000),
        "new_spectrum_qt_paint_within_1s": bool(trace.get("new_spectrum_paint_ms") is not None
                                               and 0 <= trace["new_spectrum_paint_ms"] <= 1000),
        "final_image_visible": bool(at_end.get("image_visible") is True
                                    and at_end.get("uploaded_sequence") is not None
                                    and target is not None
                                    and at_end["uploaded_sequence"] >= target),
        "upload_observation_accounted": bool(trace.get("uploads_total") ==
                                             len(trace.get("uploads") or [])
                                             + trace.get("uploads_not_retained", -1)),
        "stop_completed": bool(after_stop.get("running") is False
                               and after_stop.get("workspace") == "analyzer"),
        "stop_retained_last_frame": bool(at_end.get("displayed_frame_key") is not None
                                         and after_stop.get("displayed_frame_key") is not None
                                         and after_stop["displayed_frame_key"][:2]
                                             == at_end["displayed_frame_key"][:2]
                                         and after_stop["displayed_frame_key"][2]
                                             >= at_end["displayed_frame_key"][2]),
        "stop_retained_density": bool(at_end.get("uploaded_sequence") is not None
                                      and after_stop.get("uploaded_sequence") is not None
                                      and after_stop["uploaded_sequence"] >= at_end["uploaded_sequence"]
                                      and after_stop.get("image_visible") is True),
    }


def paint_intersects_item(widget: object, event: object, item: object) -> bool:
    """Observe an actual dirty-region intersection, not any viewport paint."""
    try:
        if not item.isVisible():
            return False
        mapped = widget.mapFromScene(item.sceneBoundingRect()).boundingRect()
        return not mapped.isEmpty() and event.rect().intersects(mapped)
    except (AttributeError, RuntimeError, TypeError):
        return False


def distribution(values: deque[float], dropped: int) -> dict[str, float] | None:
    """Never report a bounded tail as a whole-run percentile."""
    if dropped or not values:
        return None
    import numpy as np

    p50, p95, p99 = np.percentile(tuple(values), (50, 95, 99))
    return dict(p50=float(p50), p95=float(p95), p99=float(p99), max=float(max(values)))


class ScalarSeries:
    def __init__(self, capacity: int = 8192) -> None:
        self.values: deque[float] = deque(maxlen=capacity)
        self.count = 0

    def add(self, value: float) -> None:
        self.count += 1
        self.values.append(float(value))

    def report(self, *, unit: str = "ms") -> dict[str, object]:
        dropped = self.count - len(self.values)
        return dict(count=self.count, retained=len(self.values), dropped=dropped,
                    **{unit: distribution(self.values, dropped)})


class WorkerSeries:
    """One bounded scalar timing series safe to report after worker shutdown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples = ScalarSeries()

    def add(self, value: float) -> None:
        with self._lock:
            self._samples.add(value)

    def report(self) -> dict[str, object]:
        with self._lock:
            return self._samples.report()


class FrameTimeline:
    """Bounded scalar stage correlations; ambiguous source keys are excluded."""

    EDGES = (
        ("offer_return", "scheduler_emit"),
        ("scheduler_emit", "prepare_begin"),
        ("prepare_begin", "prepare_end"),
        ("prepare_end", "model_snapshot"),
        ("model_bundle", "required_ready"),
        ("required_ready", "scene_accept"),
        ("scene_accept", "paint_return"),
        ("model_bundle", "paint_return"),
    )

    def __init__(self, capacity: int = 8192) -> None:
        if capacity < 1:
            raise ValueError("timeline capacity must be positive")
        self._lock = threading.Lock()
        self._capacity = capacity
        self._frames: OrderedDict[tuple[int, int, int], dict[str, tuple[int, int]]] = OrderedDict()
        self._ambiguous: dict[tuple[int, int, int], set[str]] = {}
        self.evicted = 0
        self.first_paints = 0
        self.duplicate_stages: Counter[str] = Counter()

    def _stages_locked(self, key: tuple[int, int, int]) -> dict[str, tuple[int, int]]:
        stages = self._frames.get(key)
        if stages is None:
            stages = {}
            self._frames[key] = stages
            if len(self._frames) > self._capacity:
                old_key, _ = self._frames.popitem(last=False)
                self._ambiguous.pop(old_key, None)
                self.evicted += 1
        return stages

    def mark(self, key: tuple[int, int, int] | None, stage: str, when_ns: int | None = None,
             *, instance: int | None) -> None:
        if key is None:
            return
        with self._lock:
            stages = self._stages_locked(key)
            if stage in stages:
                self.duplicate_stages[stage] += 1
                self._ambiguous.setdefault(key, set()).add(stage)
            elif instance is not None:
                stages[stage] = (perf_counter_ns() if when_ns is None else when_ns, instance)

    def paint(self, key: tuple[int, int, int] | None, when_ns: int, *, instance: int | None) -> None:
        if key is None:
            return
        with self._lock:
            stages = self._stages_locked(key)
            if "paint_return" in stages:
                return  # A repeat paint is not another first-paint latency sample.
            if instance is None:
                return
            stages["paint_return"] = (when_ns, instance)
            self.first_paints += 1

    def report(self) -> dict[str, object]:
        with self._lock:
            edges = {f"{start}_to_{end}": ScalarSeries(self._capacity)
                     for start, end in self.EDGES}
            missing: Counter[str] = Counter()
            identity_mismatch: Counter[str] = Counter()
            out_of_order: Counter[str] = Counter()
            ambiguous_edges: Counter[str] = Counter()
            ambiguous_paints = 0
            for key, stages in self._frames.items():
                if "paint_return" not in stages:
                    continue
                if key in self._ambiguous:
                    ambiguous_paints += 1
                duplicate_stages = self._ambiguous.get(key, set())
                for start, end in self.EDGES:
                    name = f"{start}_to_{end}"
                    if start in duplicate_stages or end in duplicate_stages:
                        ambiguous_edges[name] += 1
                        continue
                    if start not in stages or end not in stages:
                        missing[name] += 1
                        continue
                    start_ns, start_instance = stages[start]
                    end_ns, end_instance = stages[end]
                    if start_instance != end_instance:
                        identity_mismatch[name] += 1
                    elif end_ns < start_ns:
                        out_of_order[name] += 1
                    else:
                        edges[name].add((end_ns - start_ns) / 1e6)
            edge_reports = {name: series.report() for name, series in edges.items()}
            if self.evicted:
                # An evicted early stage biases whole-run latency distributions.
                for series in edge_reports.values():
                    series["ms"] = None
            return dict(first_paints=self.first_paints, retained_keys=len(self._frames),
                        evicted_keys=self.evicted, ambiguous_paints=ambiguous_paints,
                        duplicate_stages=dict(self.duplicate_stages), ambiguous_edges=dict(ambiguous_edges),
                        missing=dict(missing),
                        identity_mismatch=dict(identity_mismatch), out_of_order=dict(out_of_order),
                        edges_ms=edge_reports,
                        scope="Each edge uses equal scalar object identity and non-duplicate endpoint stages; "
                              "offer_return is after LivePresenter._poll_frames offered its snapshot, not "
                              "RX publication or poll entry. Edges have different eligible key sets and their "
                              "percentiles are conditional, not additive "
                              "or a full causal pipeline.")


class PaintObserver:
    """Bounded GUI-only sequence/time witness; no frame or ndarray retention."""

    def __init__(self) -> None:
        self.workspace = None
        self.measuring = False
        self.model_events = 0
        self.model_first: OrderedDict[tuple[int, int, int], int] = OrderedDict()
        self.model_keys_evicted = 0
        self.unique_paints = 0
        self.repeat_paints = 0
        self.changed_during_paint = 0
        self.unmapped_paints = 0
        self.wall_clock_invalid = 0
        self.first_key = self.last_key = None
        self.last_unique_paint_ns: int | None = None
        self.last_spectrum_paint_ns: int | None = None
        self.paint_duration = ScalarSeries()
        self.model_to_paint = ScalarSeries()
        self.estimated_source_to_paint = ScalarSeries()
        self.unique_paint_gap = ScalarSeries()
        self.waterfall_paint_duration = ScalarSeries()
        self.gui_render_handler = ScalarSeries()
        self.worker_preparation = WorkerSeries()
        self.worker_projection_required = WorkerSeries()
        self.worker_projection_optional_only = WorkerSeries()
        self.worker_projection_total = WorkerSeries()
        self.worker_density_image = WorkerSeries()
        self.timeline = FrameTimeline()
        self.required_curve_admissions = 0
        self.scene_source_changes = 0
        self.density_uploads = 0
        self.waterfall_rows_admitted = 0
        self.spectrum_x_range_changes = 0
        self._last_paint_curve_admissions = 0
        self._last_paint_density_uploads = 0
        self._last_paint_model_events = 0
        self._last_paint_waterfall_rows = 0
        self._last_paint_x_range_changes = 0
        self.repeat_causes: Counter[str] = Counter()
        self.repeat_precursors: Counter[str] = Counter()
        self.repeat_gaps = {name: ScalarSeries() for name in (
            "curve_only", "density_only", "curve_and_density", "no_required_or_density_admission")}
        self.repeat_regions = {name: ScalarSeries() for name in self.repeat_gaps}
        self.spectrum_paint_bounding_rect_fraction = ScalarSeries()
        self.repeat_paint_bounding_rect_fraction = ScalarSeries()
        self.timestamp_qualities: Counter[str] = Counter()
        self.loss_reasons_seen: set[str] = set()

    @staticmethod
    def key(bundle: object) -> tuple[int, int, int] | None:
        from sdr_monitor.domain.live import LiveSpectrumFrame

        spectrum = getattr(bundle, "spectrum", None)
        if not isinstance(spectrum, LiveSpectrumFrame):
            return None
        return (int(spectrum.acquisition_epoch or 0),
                int(spectrum.config_generation), int(spectrum.sequence))

    def model_enter(self, workspace: object, state: object) -> None:
        if not self.measuring or workspace is not self.workspace:
            return
        key = self.key(getattr(state, "bundle", None))
        if key is None:
            return
        self.model_events += 1
        now = perf_counter_ns()
        self.timeline.mark(key, "model_snapshot", now,
                           instance=id(getattr(getattr(state, "live", None), "snapshot", None)))
        self.timeline.mark(key, "model_bundle", now, instance=id(state.bundle))
        if key not in self.model_first:
            self.model_first[key] = perf_counter_ns()
            if len(self.model_first) > 8192:
                self.model_first.popitem(last=False)
                self.model_keys_evicted += 1

    def painted(self, widget: object, begin_ns: int, end_ns: int,
                before: object, after: object, bounding_rect_fraction: float) -> None:
        if not self.measuring or self.workspace is None:
            return
        pane = self.workspace.visualization.waterfall_pane
        if widget is pane._graphics:
            self.waterfall_paint_duration.add((end_ns - begin_ns) / 1e6)
            return
        scene = self.workspace.visualization.spectrum_scene
        if widget is not scene._graphics:
            return
        self.paint_duration.add((end_ns - begin_ns) / 1e6)
        self.spectrum_paint_bounding_rect_fraction.add(bounding_rect_fraction)
        if before is not after:
            self.changed_during_paint += 1
            return
        key = self.key(after)
        if key is None:
            self.unmapped_paints += 1
            return
        if key == self.last_key:
            self.repeat_paints += 1
            self.repeat_paint_bounding_rect_fraction.add(bounding_rect_fraction)
            curve = self.required_curve_admissions != self._last_paint_curve_admissions
            density = self.density_uploads != self._last_paint_density_uploads
            category = ("curve_and_density" if curve and density else "curve_only" if curve else
                        "density_only" if density else "no_required_or_density_admission")
            self.repeat_causes[category] += 1
            self.repeat_regions[category].add(bounding_rect_fraction)
            if self.last_spectrum_paint_ns is not None:
                self.repeat_gaps[category].add((end_ns - self.last_spectrum_paint_ns) / 1e6)
            self.last_spectrum_paint_ns = end_ns
            if category == "no_required_or_density_admission":
                precursors = []
                if self.model_events != self._last_paint_model_events:
                    precursors.append("model")
                if self.waterfall_rows_admitted != self._last_paint_waterfall_rows:
                    precursors.append("waterfall")
                if self.spectrum_x_range_changes != self._last_paint_x_range_changes:
                    precursors.append("x_range")
                self.repeat_precursors["+".join(precursors) or "none"] += 1
            self._last_paint_curve_admissions = self.required_curve_admissions
            self._last_paint_density_uploads = self.density_uploads
            self._last_paint_model_events = self.model_events
            self._last_paint_waterfall_rows = self.waterfall_rows_admitted
            self._last_paint_x_range_changes = self.spectrum_x_range_changes
            return
        self.unique_paints += 1
        if self.first_key is None:
            self.first_key = key
        self.last_key = key
        self.last_spectrum_paint_ns = end_ns
        self._last_paint_curve_admissions = self.required_curve_admissions
        self._last_paint_density_uploads = self.density_uploads
        self._last_paint_model_events = self.model_events
        self._last_paint_waterfall_rows = self.waterfall_rows_admitted
        self._last_paint_x_range_changes = self.spectrum_x_range_changes
        self.timeline.paint(key, end_ns, instance=id(after))
        if self.last_unique_paint_ns is not None:
            self.unique_paint_gap.add((end_ns - self.last_unique_paint_ns) / 1e6)
        self.last_unique_paint_ns = end_ns
        model_ns = self.model_first.pop(key, None)
        if model_ns is None:
            self.unmapped_paints += 1
        else:
            self.model_to_paint.add((end_ns - model_ns) / 1e6)
        spectrum = after.spectrum
        quality = getattr(spectrum, "timestamp_quality", None)
        self.timestamp_qualities[str(getattr(quality, "value", quality))] += 1
        self.loss_reasons_seen.update(str(getattr(reason, "value", reason))
                                      for reason in getattr(spectrum, "loss_reasons", ()))
        estimated_age_ms = (time_ns() - int(spectrum.timestamp_ns)) / 1e6
        if -1.0 <= estimated_age_ms <= 60_000.0:
            self.estimated_source_to_paint.add(estimated_age_ms)
        else:
            self.wall_clock_invalid += 1

    def report(self) -> dict[str, object]:
        return dict(model_events=self.model_events, unique_spectrum_paints=self.unique_paints,
                    repeat_spectrum_paints=self.repeat_paints,
                    changed_during_paint=self.changed_during_paint,
                    unmapped_paints=self.unmapped_paints,
                    model_keys_evicted=self.model_keys_evicted,
                    wall_clock_invalid=self.wall_clock_invalid,
                    first_key=self.first_key, last_key=self.last_key,
                    spectrum_paint_return_ms=self.paint_duration.report(),
                    waterfall_paint_return_ms=self.waterfall_paint_duration.report(),
                    gui_model_to_first_paint_return_ms=self.model_to_paint.report(),
                    estimated_source_timestamp_to_first_paint_return_ms=self.estimated_source_to_paint.report(),
                    unique_spectrum_paint_gap_ms=self.unique_paint_gap.report(),
                    gui_render_handler_ms=self.gui_render_handler.report(),
                    worker_preparation_ms=self.worker_preparation.report(),
                    worker_projection_required_ms=self.worker_projection_required.report(),
                    worker_projection_optional_only_ms=self.worker_projection_optional_only.report(),
                    worker_projection_total_ms=self.worker_projection_total.report(),
                    worker_density_image_ms=self.worker_density_image.report(),
                    frame_timeline=self.timeline.report(),
                    required_curve_admissions=self.required_curve_admissions,
                    scene_source_changes=self.scene_source_changes,
                    density_uploads=self.density_uploads,
                    waterfall_rows_admitted=self.waterfall_rows_admitted,
                    spectrum_x_range_changes=self.spectrum_x_range_changes,
                    repeated_paints_by_tracked_change=dict(self.repeat_causes),
                    no_required_or_density_repeat_precursors=dict(self.repeat_precursors),
                    repeat_paint_gap_ms={name: series.report() for name, series in self.repeat_gaps.items()},
                    repeat_paint_bounding_rect_by_change={name: series.report(unit="fraction")
                                                          for name, series in self.repeat_regions.items()},
                    spectrum_paint_bounding_rect_fraction=(
                        self.spectrum_paint_bounding_rect_fraction.report(unit="fraction")),
                    repeat_paint_bounding_rect_fraction=(
                        self.repeat_paint_bounding_rect_fraction.report(unit="fraction")),
                    timestamp_qualities=dict(self.timestamp_qualities),
                    loss_reasons_seen=sorted(self.loss_reasons_seen))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--uri", required=True, help="explicit Pluto usb: or ip: URI")
    result.add_argument("--output", type=Path, required=True, help="new JSON path; never overwritten")
    result.add_argument("--duration", type=float, default=10.0)
    result.add_argument("--warmup", type=float, default=2.0)
    result.add_argument("--center-mhz", type=float, default=2400.0)
    result.add_argument("--sample-rate-msps", type=float, default=3.0)
    result.add_argument("--fft", type=int, default=4096)
    result.add_argument("--buffer-samples", type=int, default=262144,
                        help="native RX refill geometry; 262144 is the product default")
    result.add_argument("--hide-persistence", action="store_true",
                        help="normal V2 display toggle; native histogram still runs")
    result.add_argument("--lock-vertical-range", action="store_true",
                        help="diagnostic: freeze Auto Y after warmup, without changing RX")
    result.add_argument("--split-persistence", action="store_true",
                        help="opt-in existing second worker seam; not product wiring")
    result.add_argument("--visual-substages", action="store_true",
                        help="opt-in scalar Visual worker substage timings (perturbs timing)")
    result.add_argument("--hide-show", action="store_true",
                        help="one timed Live -> calibration -> analyzer -> Stop lifecycle gate")
    result.add_argument("--teardown-timing", action="store_true",
                        help="diagnostic-only flushed stderr markers around Stop, close and process return")
    result.add_argument("--render-mode", choices=("direct", "visual"), default="direct")
    result.add_argument("--display-fps", type=int, choices=(15, 30, 60, 120, 144, 240), default=120)
    result.add_argument("--window-width", type=int, default=1400)
    result.add_argument("--window-height", type=int, default=850)
    return result


def main() -> int:
    args = parser().parse_args()
    if os.name != "nt" or not args.uri.startswith(("usb:", "ip:")):
        raise SystemExit("physical UI observer requires Windows and an explicit usb:/ip: URI")
    if not 2 <= args.duration <= 120 or not 0 <= args.warmup <= 30:
        raise SystemExit("duration must be 2..120 s and warmup 0..30 s")
    if args.sample_rate_msps <= 0 or args.fft < 256 or args.fft & (args.fft - 1):
        raise SystemExit("positive Fs and a power-of-two FFT >=256 are required")
    if args.buffer_samples < 4096 or args.buffer_samples > 262144 or args.buffer_samples & (args.buffer_samples - 1):
        raise SystemExit("buffer-samples must be a power of two in 4096..262144")
    if args.split_persistence and args.visual_substages:
        raise SystemExit("Visual substage contention profile requires the combined product worker")
    if args.visual_substages and args.render_mode != "visual":
        raise SystemExit("Visual substage profile requires --render-mode visual")
    if args.hide_show and (args.duration < 6 or args.render_mode != "visual"
                           or args.hide_persistence or args.split_persistence or args.visual_substages):
        raise SystemExit("Hide/Show requires >=6 s Visual with persistence visible and no split/profile")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("output already exists; choose a new evidence path")
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "windows"
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        source_commit = None
    else:
        source_commit = revision.stdout.strip() if revision.returncode == 0 else None
    script_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    scene_sha256 = hashlib.sha256((ROOT / "sdr_monitor/ui/v2/spectrum/scene.py").read_bytes()).hexdigest()
    projection_sha256 = hashlib.sha256((ROOT / "sdr_monitor/ui/v2/spectrum/persistence_projection.py")
                                        .read_bytes()).hexdigest()
    try:
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                                capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        tracked_dirty_paths = None
    else:
        tracked_dirty_paths = ([line[3:] for line in status.stdout.splitlines()]
                               if status.returncode == 0 else None)

    import pyqtgraph as pg
    import PySide6
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from sdr_monitor.domain.live import BackendKind
    from sdr_monitor.services import build_default_sdr_services
    from sdr_monitor.services.native_live import NativeLiveSessionService
    from sdr_monitor.services.native_recording import NativeLiveRecordingService
    from sdr_monitor.services.native_sweep import NativeLiveSweepService
    from sdr_monitor.ui.presenters.live_presenter import LivePresenter
    from sdr_monitor.ui.v2.workspaces.analyzer import AnalyzerWorkspaceV2
    from sdr_monitor.ui.v2.state.prepared_live import LiveSnapshotPreparer
    from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
    from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
    from sdr_monitor.ui.v2.spectrum.persistence_overlay import PersistenceOverlay
    from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
    import sdr_monitor.ui.v2.spectrum.projection as projection_module
    import sdr_monitor.ui.v2.spectrum.persistence_projection as persistence_projection_module
    import sdr_monitor.ui.v2.spectrum.persistence_projector as persistence_projector_module
    import sdr_monitor.ui.v2.product_live as product_live_module
    from sdr_monitor.ui.v2_composition import build_v2_shell

    try:
        import sdr_monitor._sdr_native as native_module
        native_path = Path(native_module.__file__).resolve()
        native_binary = dict(path=str(native_path), sha256=hashlib.sha256(native_path.read_bytes()).hexdigest())
    except (ImportError, OSError, TypeError):
        native_binary = None

    observer = PaintObserver()
    density_witness = DensitySequenceWitness()
    lifecycle: dict[str, Any] = dict(phase="idle", uploads=[], uploads_total=0,
                                     uploads_not_retained=0, hidden_uploads=0,
                                     shown_stale_uploads=0, shown_unmapped_uploads=0)
    original_render = AnalyzerWorkspaceV2._render
    original_prepare = LiveSnapshotPreparer.prepare_cancellable
    original_project = projection_module.project_spectrum
    original_density_image = projection_module.prepare_persistence_image
    original_poll = LivePresenter._poll_frames
    original_emit = LivePresenter._emit_render
    original_accept = SpectrumScene._accept_projection
    original_image_accept = PersistenceOverlay.accept_worker_image
    original_waterfall_set_line = WaterfallPane.set_line

    def observed_poll(presenter):
        previous = presenter._last_poll_key
        result = original_poll(presenter)
        if observer.measuring and presenter._last_poll_key != previous:
            snapshot = presenter._last_polled
            observer.timeline.mark(observer.key(snapshot), "offer_return", instance=id(snapshot))
        return result

    def observed_emit(presenter, snapshot):
        if observer.measuring:
            observer.timeline.mark(observer.key(snapshot), "scheduler_emit", instance=id(snapshot))
        return original_emit(presenter, snapshot)

    def observed_accept(scene, result, *, required_only=False):
        before = scene.displayed_frame
        request = result.request
        required_admission = required_projection_acceptance(scene, request, required_only=required_only)
        accepted = original_accept(scene, result, required_only=required_only)
        if (observer.measuring and observer.workspace is not None
                and scene is observer.workspace.visualization.spectrum_scene):
            if required_admission:
                observer.required_curve_admissions += 1
                frame = scene.displayed_frame
                observer.timeline.mark(observer.key(frame), "scene_accept", instance=id(frame))
            if scene.displayed_frame is not before:
                observer.scene_source_changes += 1
        return accepted

    def observed_image_accept(overlay, request, result, error):
        before = overlay.metrics.image_uploads
        accepted = original_image_accept(overlay, request, result, error)
        if (observer.measuring and observer.workspace is not None
                and overlay is observer.workspace.visualization.spectrum_scene._persistence):
            admitted = overlay.metrics.image_uploads - before
            observer.density_uploads += admitted
            if args.hide_show and admitted:
                sequence = density_witness.sequence(overlay._uploaded_density)
                upload_phase = lifecycle["phase"]
                lifecycle["uploads_total"] += 1
                if upload_phase == "hidden":
                    lifecycle["hidden_uploads"] += admitted
                elif upload_phase == "shown":
                    target = lifecycle.get("show_target_sequence")
                    if sequence is None or target is None:
                        lifecycle["shown_unmapped_uploads"] += admitted
                    elif sequence < target:
                        lifecycle["shown_stale_uploads"] += admitted
                    elif overlay.image_item.isVisible():
                        lifecycle.setdefault("first_fresh_visible_upload_ns", perf_counter_ns())
                events = lifecycle["uploads"]
                if len(events) < 128:
                    events.append(dict(phase=upload_phase, when_ns=perf_counter_ns(),
                                       sequence=sequence,
                                       visible=overlay.image_item.isVisible()))
                else:
                    lifecycle["uploads_not_retained"] += 1
        return accepted

    def observed_waterfall_set_line(pane, frame):
        before = pane.metrics.rows_admitted
        result = original_waterfall_set_line(pane, frame)
        if (observer.measuring and observer.workspace is not None
                and pane is observer.workspace.visualization.waterfall_pane):
            observer.waterfall_rows_admitted += pane.metrics.rows_admitted - before
        return result

    def observed_render(workspace, state):
        began = perf_counter_ns()
        observer.model_enter(workspace, state)
        if args.hide_show and observer.measuring and workspace is observer.workspace:
            density_witness.observe(state)
        try:
            return original_render(workspace, state)
        finally:
            if observer.measuring and workspace is observer.workspace:
                observer.gui_render_handler.add((perf_counter_ns() - began) / 1e6)

    def observed_prepare(preparer, snapshot, *, cancelled=None):
        began = perf_counter_ns()
        key = observer.key(snapshot) if observer.measuring else None
        observer.timeline.mark(key, "prepare_begin", began, instance=id(snapshot))
        try:
            return original_prepare(preparer, snapshot, cancelled=cancelled)
        finally:
            if observer.measuring:
                ended = perf_counter_ns()
                observer.worker_preparation.add((ended - began) / 1e6)
                observer.timeline.mark(key, "prepare_end", ended, instance=id(snapshot))

    def observed_project(*args, **kwargs):
        began = perf_counter_ns()
        ready = kwargs.get("spectrum_ready")
        required_work = bool(getattr(args[0], "required_work", False))
        current_bundle = next((view.source_frame for kind, view in getattr(args[0], "traces", ())
                               if getattr(kind, "value", None) == "current"), None)
        key = observer.key(current_bundle) if observer.measuring else None
        emitted_required = False
        if ready is not None:
            def required_ready(result):
                nonlocal emitted_required
                emitted_required = True
                if observer.measuring:
                    ended = perf_counter_ns()
                    observer.worker_projection_required.add((ended - began) / 1e6)
                    observer.timeline.mark(key, "required_ready", ended,
                                           instance=None if current_bundle is None else id(current_bundle))
                return ready(result)

            kwargs["spectrum_ready"] = required_ready
        try:
            return original_project(*args, **kwargs)
        finally:
            if observer.measuring:
                elapsed = (perf_counter_ns() - began) / 1e6
                observer.worker_projection_total.add(elapsed)
                if not required_work:
                    observer.worker_projection_optional_only.add(elapsed)
                elif not emitted_required:
                    observer.worker_projection_required.add(elapsed)
                    observer.timeline.mark(key, "required_ready",
                                           instance=None if current_bundle is None else id(current_bundle))

    def observed_density_image(*args, **kwargs):
        began = perf_counter_ns()
        try:
            return original_density_image(*args, **kwargs)
        finally:
            if observer.measuring:
                observer.worker_density_image.add((perf_counter_ns() - began) / 1e6)

    class MeasuredGraphics(pg.GraphicsLayoutWidget):
        def paintEvent(self, event):
            workspace = observer.workspace
            scene = None if workspace is None else workspace.visualization.spectrum_scene
            before = None if scene is None or self is not scene._graphics else scene.displayed_frame
            began = perf_counter_ns()
            super().paintEvent(event)
            ended = perf_counter_ns()
            after = None if scene is None or self is not scene._graphics else scene.displayed_frame
            area = max(1, self.width() * self.height())
            rect = event.rect()
            region_fraction = min(1.0, max(0.0, rect.width() * rect.height() / area))
            observer.painted(self, began, ended, before, after, region_fraction)
            if (args.hide_show and observer.measuring and scene is not None and self is scene._graphics
                    and lifecycle["phase"] == "shown"):
                target = lifecycle.get("show_target_sequence")
                overlay = scene._persistence
                uploaded = density_witness.sequence(overlay._uploaded_density)
                if (target is not None and uploaded is not None and uploaded >= target
                        and paint_intersects_item(self, event, overlay.image_item)):
                    lifecycle.setdefault("first_fresh_paint_ns", ended)
                before_hide_key = lifecycle.get("before_hide_frame_key")
                painted_key = observer.key(after)
                if (before_hide_key is not None and painted_key is not None
                        and painted_key[:2] == before_hide_key[:2]
                        and painted_key[2] > before_hide_key[2]
                        and paint_intersects_item(self, event, scene._curves[TraceKind.CURRENT].curve)):
                    lifecycle.setdefault("first_new_spectrum_paint_ns", ended)
                    lifecycle.setdefault("first_new_spectrum_key", painted_key)

    original_compose = product_live_module.compose_v2_live_product

    def observed_compose(presenter, **kwargs):
        kwargs["persistence_submit"] = presenter.submit_persistence_task
        return original_compose(presenter, **kwargs)

    report: dict[str, object] = {}
    visual_recorder = None
    visual_profiler_sha256 = None
    if args.visual_substages:
        from scripts import profile_app05_visual_substages as visual_profile_module

        visual_recorder = visual_profile_module.SubstageRecorder()
        visual_profiler_sha256 = hashlib.sha256(Path(visual_profile_module.__file__).read_bytes()).hexdigest()

    @contextmanager
    def visual_profile():
        nonlocal original_density_image
        assert visual_recorder is not None
        with visual_recorder.instrument(persistence_projection_module, projection_module,
                                        live_presenter_class=LivePresenter,
                                        record_when=lambda: observer.measuring):
            # The physical observer wraps the profiler, not its pre-patch target.
            original_density_image = projection_module.prepare_persistence_image
            yield

    visual_patch = nullcontext() if visual_recorder is None else visual_profile()
    split_patch = (patch.object(product_live_module, "compose_v2_live_product", observed_compose)
                   if args.split_persistence else nullcontext())
    with visual_patch, patch.object(pg, "GraphicsLayoutWidget", MeasuredGraphics), patch.object(
            LivePresenter, "_poll_frames", observed_poll), patch.object(
            LivePresenter, "_emit_render", observed_emit), patch.object(
            SpectrumScene, "_accept_projection", observed_accept), patch.object(
            PersistenceOverlay, "accept_worker_image", observed_image_accept), patch.object(
            WaterfallPane, "set_line", observed_waterfall_set_line), patch.object(
            AnalyzerWorkspaceV2, "_render", observed_render), patch.object(
            LiveSnapshotPreparer, "prepare_cancellable", observed_prepare), patch.object(
            projection_module, "project_spectrum", observed_project), patch.object(
            projection_module, "prepare_persistence_image", observed_density_image), patch.object(
            persistence_projector_module, "prepare_persistence_image", observed_density_image), split_patch:
        app = QApplication.instance() or QApplication([])
        # AppShellV2 persists geometry/theme/navigation on close. Never write
        # these benchmark window values into the user's product QSettings.
        settings_namespace = dict(organization="SDR Native Monitoring Bench",
                                  domain="local.sdr-native-monitoring-bench",
                                  application="APP05 Physical UI Observer")
        app.setOrganizationName(settings_namespace["organization"])
        app.setOrganizationDomain(settings_namespace["domain"])
        app.setApplicationName(settings_namespace["application"])
        services = build_default_sdr_services()
        if not isinstance(services.live_sdr, NativeLiveSessionService):
            raise RuntimeError("native Pluto service unavailable; no RX was attempted")
        if args.buffer_samples != 262144:
            # Explicit benchmark experiment only. All three receiver-facing
            # ports must retain the SAME replacement live owner; product
            # build_v2_shell() still uses its unchanged 262144-sample default.
            live = NativeLiveSessionService(
                services.live_sdr._native, device_buffer_samples=args.buffer_samples)
            services = replace(services, live_sdr=live,
                               sweep=NativeLiveSweepService(live),
                               recording=NativeLiveRecordingService(live))
        shell = build_v2_shell(services)
        shell.resize(args.window_width, args.window_height)
        shell.show()
        workspace = shell._workspace_pages["analyzer"]
        observer.workspace = workspace
        workspace.visualization.spectrum_scene.view_box.sigXRangeChanged.connect(
            lambda *_: setattr(observer, "spectrum_x_range_changes",
                               observer.spectrum_x_range_changes + int(observer.measuring)))
        composition = shell._context.close_ports[0].shutdown.__self__
        composition._presenter.set_display_fps(args.display_fps)
        workspace.visualization.spectrum_scene.set_persistence_render_mode(
            PersistenceRenderMode(args.render_mode))
        if args.hide_persistence:
            workspace.visualization.spectrum_scene.set_persistence_visible(False)
        phase = "select"
        deadline_ns = perf_counter_ns() + 15_000_000_000
        measure_end_ns = None
        failure = None
        native_before_stop = None
        native_at_start = None
        display_before_stop = None
        persistence_before_stop = None
        persistence_at_start = None
        projector_before_stop = None
        projector_at_start = None
        initial_sequence = final_sequence = None
        applied = None
        measurement_started_ns = measurement_finished_ns = None
        teardown_stage_events: list[dict[str, object]] = []

        def mark_teardown(stage: str) -> None:
            if not args.teardown_timing:
                return
            when_ns = perf_counter_ns()
            teardown_stage_events.append(dict(stage=stage, when_ns=when_ns))
            print(f"APP05_STAGE {when_ns} {stage}", file=sys.stderr, flush=True)

        def lifecycle_snapshot() -> dict[str, object]:
            scene = workspace.visualization.spectrum_scene
            overlay = scene._persistence
            snapshot = services.live_sdr.latest_snapshot()
            latest = overlay.latest_view
            return dict(when_ns=perf_counter_ns(), workspace=shell.active_workspace_id,
                        analyzer_visible=workspace.isVisible(),
                        presentation_active=overlay._presentation_active,
                        image_visible=overlay.image_item.isVisible(),
                        image_present=overlay.image_item.image is not None,
                        image_uploads=overlay.metrics.image_uploads,
                        latest_sequence=density_witness.sequence(None if latest is None else latest.density),
                        uploaded_sequence=density_witness.sequence(overlay._uploaded_density),
                        displayed_frame_key=observer.key(scene.displayed_frame),
                        native_sequence=int(snapshot.sequence),
                        epoch=snapshot.acquisition_epoch, running=services.live_sdr.is_running())

        def fail(message: str) -> None:
            nonlocal failure, phase, deadline_ns
            failure = message
            phase = "stop"
            deadline_ns = perf_counter_ns() + 15_000_000_000

        def tick() -> None:
            nonlocal phase, deadline_ns, measure_end_ns, applied, native_before_stop, failure
            nonlocal display_before_stop, persistence_before_stop, projector_before_stop
            nonlocal native_at_start, persistence_at_start, projector_at_start
            nonlocal initial_sequence, final_sequence
            nonlocal measurement_started_ns, measurement_finished_ns
            now = perf_counter_ns()
            state = workspace.model.state
            if now > deadline_ns and phase not in ("measure", "done"):
                if phase in ("stop", "wait_stopped", "wait_closed"):
                    failure = failure or f"cleanup phase {phase} exceeded its 15-s deadline"
                    timer.stop()
                    app.quit()
                    return
                fail(f"phase {phase} exceeded its 15-s deadline")
            try:
                if phase == "select":
                    workspace.drawer._uri.setText(args.uri)
                    workspace.drawer._use_uri.click()
                    phase = "wait_selected"
                elif phase == "wait_selected":
                    device = getattr(state.live.snapshot, "device", None)
                    if device is not None and not state.live.busy:
                        workspace.drawer._center.setValue(args.center_mhz)
                        workspace.drawer._sample_rate.setValue(args.sample_rate_msps)
                        workspace.drawer._fft.setValue(args.fft)
                        cpu_index = workspace.drawer._backend.findData(BackendKind.CPU.value)
                        if cpu_index < 0:
                            fail("selected device did not publish CPU backend")
                        else:
                            workspace.drawer._backend.setCurrentIndex(cpu_index)
                            if not workspace.drawer._apply.isEnabled():
                                fail("V2 drawer refused the requested configuration")
                            else:
                                workspace.drawer._apply.click()
                                phase = "wait_applied"
                elif phase == "wait_applied":
                    applied = getattr(state.live.snapshot, "applied", None)
                    if applied is not None and not state.live.busy and not state.configuration_pending:
                        if not workspace.primary.isEnabled():
                            fail("V2 Start disabled after applied configuration")
                        else:
                            workspace.primary.click()
                            phase = "wait_running"
                elif phase == "wait_running":
                    if state.live.primary_action.value == "stop" and state.bundle is not None:
                        deadline_ns = now + int((args.warmup + args.duration + 5) * 1e9)
                        measure_end_ns = now + int((args.warmup + args.duration) * 1e9)
                        phase = "warmup"
                elif phase == "warmup":
                    if measure_end_ns is not None and now >= measure_end_ns - int(args.duration * 1e9):
                        if args.lock_vertical_range:
                            workspace.visualization.spectrum_scene.set_vertical_lock(True)
                        snapshot = services.live_sdr.latest_snapshot()
                        native_at_start = snapshot.performance
                        initial_sequence = int(snapshot.sequence)
                        persistence_at_start = workspace.visualization.spectrum_scene.persistence_metrics
                        projector_at_start = projector_counters(composition.spectrum_projector)
                        composition._presenter.reset_display_metrics()
                        measurement_started_ns = now
                        observer.measuring = True
                        phase = "measure"
                elif phase == "measure":
                    if not services.live_sdr.is_running():
                        observer.measuring = False
                        fail("physical RX left RUNNING during measurement")
                        return
                    if args.hide_show and measurement_started_ns is not None:
                        if lifecycle["phase"] == "idle" and now >= measurement_started_ns + 2_000_000_000:
                            if "calibration" not in shell._definitions:
                                fail("calibration workspace unavailable; Hide/Show not exercised")
                                return
                            lifecycle["before_hide"] = lifecycle_snapshot()
                            lifecycle["before_hide_frame_key"] = lifecycle["before_hide"]["displayed_frame_key"]
                            lifecycle["phase"] = "hiding"
                            shell.select_workspace("calibration")
                            lifecycle["after_hide"] = lifecycle_snapshot()
                            lifecycle["phase"] = "hidden"
                        elif (lifecycle["phase"] == "hidden" and
                              now >= lifecycle["after_hide"]["when_ns"] + 1_000_000_000):
                            lifecycle["before_show"] = lifecycle_snapshot()
                            lifecycle["show_target_sequence"] = lifecycle["before_show"]["latest_sequence"]
                            lifecycle["show_intent_ns"] = perf_counter_ns()
                            lifecycle["phase"] = "shown"
                            shell.select_workspace("analyzer")
                            lifecycle["after_show"] = lifecycle_snapshot()
                    if measure_end_ns is not None and now >= measure_end_ns:
                        observer.measuring = False
                        measurement_finished_ns = now
                        if args.hide_show:
                            lifecycle["at_end"] = lifecycle_snapshot()
                        snapshot = services.live_sdr.latest_snapshot()
                        native_before_stop = snapshot.performance
                        final_sequence = int(snapshot.sequence)
                        applied = snapshot.applied
                        display_before_stop = composition._presenter.display_metrics
                        scene = workspace.visualization.spectrum_scene
                        persistence_before_stop = scene.persistence_metrics
                        projector_before_stop = projector_counters(composition.spectrum_projector)
                        if initial_sequence is None or final_sequence <= initial_sequence:
                            failure = "native spectrum sequence did not advance during measurement"
                        phase = "stop"
                        deadline_ns = now + 15_000_000_000
                elif phase == "stop":
                    if state.live.primary_action.value == "stop" and not state.live.busy:
                        mark_teardown("stop_click_before")
                        workspace.primary.click()
                        mark_teardown("stop_click_after")
                        phase = "wait_stopped"
                    elif state.live.primary_action.value != "stop" and not state.live.busy:
                        phase = "wait_stopped"
                elif phase == "wait_stopped":
                    if state.live.primary_action.value != "stop" and not state.live.busy and not state.stopping:
                        mark_teardown("stop_acknowledged")
                        if args.hide_show:
                            lifecycle["after_stop"] = lifecycle_snapshot()
                        mark_teardown("shell_close_before")
                        shell.close()
                        mark_teardown("shell_close_after")
                        phase = "wait_closed"
                elif phase == "wait_closed":
                    if shell._is_closed:
                        mark_teardown("shell_closed_acknowledged")
                        phase = "done"
                        timer.stop()
                        app.quit()
            except Exception as error:
                if phase in ("stop", "wait_stopped", "wait_closed"):
                    timer.stop()
                    app.quit()
                    fail(f"cleanup failed: {error}")
                else:
                    fail(f"{type(error).__name__}: {error}")

        timer = QTimer()
        timer.setInterval(25)
        timer.timeout.connect(tick)
        timer.start()
        app.exec()
        timer.stop()
        mark_teardown("qt_event_loop_returned")
        # Last-window-closed can exit the Qt loop before the next timer tick.
        if shell._is_closed and phase == "wait_closed":
            mark_teardown("shell_closed_after_loop")
            phase = "done"
        if phase != "done":
            try:
                services.live_sdr.stop_and_wait(5.0)
            except Exception as error:
                failure = (failure or "incomplete UI close") + f"; emergency Stop failed: {error}"
        screen = shell.screen()
        window = dict(logical_width=shell.width(), logical_height=shell.height(),
                      dpr=shell.devicePixelRatioF(),
                      screen_name=None if screen is None else screen.name())
        budget = composition.allocation_budget.snapshot()
        workers = [thread.name for thread in threading.enumerate()
                   if thread is not threading.main_thread()]
        measured_elapsed_s = (None if measurement_started_ns is None or measurement_finished_ns is None
                              else (measurement_finished_ns - measurement_started_ns) / 1e9)
        native_interval = (None if native_at_start is None or native_before_stop is None else
                           counter_deltas(native_at_start, native_before_stop, NATIVE_COUNTER_FIELDS))
        if native_interval is not None and measured_elapsed_s is not None and measured_elapsed_s > 0:
            native_interval["computed_fft_per_s"] = native_interval["fft_frames_computed"] / measured_elapsed_s
        persistence_interval = None
        if persistence_at_start is not None and persistence_before_stop is not None:
            metric_names = tuple(key for key in asdict(persistence_before_stop)
                                 if key != "retained_extra_image_buffers")
            persistence_interval = counter_deltas(persistence_at_start, persistence_before_stop, metric_names)
            persistence_interval["retained_extra_image_buffers_at_end"] = (
                persistence_before_stop.retained_extra_image_buffers)
        projector_interval = None
        if projector_at_start is not None and projector_before_stop is not None:
            projector_interval = {key: value - projector_at_start[key]
                                  for key, value in projector_before_stop.items()}
        measurement_valid = (measured_elapsed_s is not None and native_interval is not None
                             and native_interval["fft_frames_computed"] > 0
                             and all(value >= 0 for key, value in native_interval.items()
                                     if key != "computed_fft_per_s")
                             and initial_sequence is not None and final_sequence is not None
                             and final_sequence > initial_sequence)
        visual_profile_result = None if visual_recorder is None else visual_recorder.report()
        if visual_profile_result is not None:
            visual_profile_result["profiler_sha256"] = visual_profiler_sha256
            visual_profile_result["physical_run_scope"] = (
                "Only optional-worker calls that began while observer.measuring was true "
                "are recorded. A call may finish just after the measured interval; "
                "instruments perturb timing. These are neither an uninstrumented "
                "performance baseline nor DWM/scanout observations."
            )
        visual_profile_complete = (None if visual_profile_result is None
                                   else bool(visual_profile_result["complete"]))
        if visual_profile_complete is False:
            failure = ((failure + "; ") if failure else "") + "Visual substage profile incomplete"
        hide_show_result = None
        if args.hide_show:
            shown_ns = lifecycle.get("show_intent_ns")
            for source, result_key in (("first_fresh_paint_ns", "fresh_paint_ms"),
                                       ("first_new_spectrum_paint_ns", "new_spectrum_paint_ms")):
                painted_ns = lifecycle.get(source)
                lifecycle[result_key] = (None if shown_ns is None or painted_ns is None
                                         else (painted_ns - shown_ns) / 1e6)
            checks = hide_show_checks(lifecycle)
            hide_show_result = dict(passed=all(checks.values()), checks=checks, trace=lifecycle,
                                    scope="One physical Live navigation cycle. Fresh means a mapped native "
                                          "persistence update and a newer displayed spectrum source at Qt "
                                          "paint-return whose dirty region intersects the corresponding "
                                          "GraphicsItem bounds. It does not prove exact changed pixels, "
                                          "DWM/scanout, a 50-ms goal, moving-latest freshness or repeated stability.")
            if not hide_show_result["passed"]:
                failed_checks = ", ".join(name for name, accepted in checks.items() if not accepted)
                failure = ((failure + "; ") if failure else "") + f"Hide/Show gate: {failed_checks}"
        report = dict(schema="app05-physical-visible-v2-v7", result="pass" if phase == "done" and
                      failure is None and measurement_valid and observer.unique_paints >= 2
                      and budget.reserved_bytes == 0 and not workers else "fail",
                      failure=failure, phase=phase, source="physical-pluto-rx", uri=args.uri,
                      source_commit=source_commit, script_sha256=script_sha256,
                      scene_sha256=scene_sha256, tracked_dirty_paths=tracked_dirty_paths,
                      persistence_projection_sha256=projection_sha256,
                      runtime=dict(python=sys.version, executable=sys.executable,
                                   pyqtgraph=pg.__version__, pyside=PySide6.__version__,
                                   native_binary=native_binary),
                      settings_namespace=settings_namespace,
                      requested=dict(center_mhz=args.center_mhz, sample_rate_msps=args.sample_rate_msps,
                                     fft=args.fft, buffer_samples=args.buffer_samples,
                                     persistence_visible=not args.hide_persistence,
                                     vertical_range_locked=args.lock_vertical_range,
                                     split_persistence=args.split_persistence,
                                     visual_substages=args.visual_substages,
                                     hide_show=args.hide_show,
                                     render_mode=args.render_mode, display_fps=args.display_fps,
                                     backend="cpu", warmup_s=args.warmup,
                                     measurement_s=args.duration),
                      measured_elapsed_s=measured_elapsed_s,
                      native_sequence_interval=None if initial_sequence is None or final_sequence is None
                          else dict(first=initial_sequence, last=final_sequence),
                      applied=None if applied is None else dict(
                          center_hz=applied.applied.center_hz,
                          sample_rate_hz=applied.applied.sample_rate_hz,
                          analog_bandwidth_hz=applied.applied.analog_bandwidth_hz,
                          fft_size=applied.applied.fft_size,
                          backend=applied.applied.backend.value,
                          readback_fields=applied.readback_fields),
                      window=window,
                      native_performance=None if native_before_stop is None else dict(
                          analytical_fft_rate_hz=native_before_stop.analytical_fft_rate_hz,
                          spectrum_snapshot_rate_hz=native_before_stop.spectrum_snapshot_rate_hz,
                          iq_sample_rate_hz=native_before_stop.iq_sample_rate_hz,
                          fft_frames_computed=native_before_stop.fft_frames_computed,
                          fft_frames_dropped=native_before_stop.fft_frames_dropped,
                          iq_samples_dropped=native_before_stop.iq_samples_dropped,
                          iq_blocks_dropped=native_before_stop.iq_blocks_dropped,
                          source_samples_dropped=native_before_stop.source_samples_dropped,
                          source_blocks_dropped=native_before_stop.source_blocks_dropped,
                          acquisition_queue_samples_dropped=native_before_stop.acquisition_queue_samples_dropped,
                          acquisition_queue_blocks_dropped=native_before_stop.acquisition_queue_blocks_dropped,
                          snapshots_emitted=native_before_stop.snapshots_emitted,
                          snapshots_superseded=native_before_stop.snapshots_superseded,
                          persistence_updates=native_before_stop.persistence_updates,
                          persistence_snapshots_superseded=native_before_stop.persistence_snapshots_superseded,
                          end_to_end_latency_ms=native_before_stop.end_to_end_latency_ms),
                      native_measurement_deltas=native_interval,
                      display_scheduler=None if display_before_stop is None else asdict(display_before_stop),
                      persistence_overlay_measurement_deltas=persistence_interval,
                      projector_measurement_deltas=projector_interval,
                      observer=observer.report(),
                      hide_show=hide_show_result,
                      teardown_stage_events=teardown_stage_events if args.teardown_timing else None,
                      teardown_stage_events_scope=(
                          "JSON includes markers through report_write_before; "
                          "main_returning is stderr-only. Flushed GUI-thread stderr "
                          "markers perturb timing and are not a Stop/close latency benchmark"
                          if args.teardown_timing else None),
                      persistence_substage_profile=visual_profile_result,
                      persistence_substage_profile_complete=visual_profile_complete,
                      allocation_budget=asdict(budget), workers_after_close=workers,
                      scope="Successful run means bounded RX/UI lifecycle evidence, not performance acceptance. "
                            "Visible Qt paint-return only; Pluto timestamp estimates sample start from refill "
                            "completion and nominal block duration. No hardware capture, DWM/scanout/FPS claim. "
                            "Observer patches viewport paint and scalar stage methods; timings are perturbed. "
                            "Repeated paint categories name tracked admissions, not proven Qt invalidation causes. "
                            "Qt event.rect() is only a bounding rectangle, not painted-pixel/scanout area. "
                            "Commit and hashes do not bind external DLLs, device firmware or desktop compositor.")
    mark_teardown("report_write_before")
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(dict(result=report["result"], output=str(output),
                          observer=report["observer"], native_performance=report["native_performance"]),
                     ensure_ascii=False))
    mark_teardown("main_returning")
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

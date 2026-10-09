"""Opt-in test/HIL observer, not product acquisition or a performance SLA.

Read cached scalar receipts on a separate thread. Never call Qt, SDK, Start,
Stop, drain, repaint or serialize during measurement. Caller owns these
callbacks and MUST ensure they are cached-only. Defaults: 15s warmup, 60s
steady, 1s observation spacing; at most 64 observations, no rolling eviction.
Counter rates describe cached-read envelopes, not hardware capture timestamps.
Paint audit retains conditional original receipts, not all paints or DWM.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil, isfinite
from threading import Event, Thread
from time import perf_counter_ns

from sdr_monitor.services.pane_paint_diagnostics import PanePaintDiagnosticObservation
from sdr_monitor.services.pane_paint_window_audit import MAX_WINDOW_OBSERVATIONS, audit_pane_paint_window


# Explicit current cached-service mappings. A LivePerformance default zero for
# a field that this family never populates is not an observed zero.
_COMMON = ("fft_frames_computed", "fft_frames_dropped", "iq_samples_dropped",
           "acquisition_queue_samples_dropped", "acquisition_queue_blocks_dropped",
           "snapshots_superseded", "bridge_native_frames_polled", "bridge_frames_coalesced",
           "bridge_frames_published", "iq_blocks_received")
_FAMILY_COUNTERS = {
    "ad936x": _COMMON + ("iq_blocks_dropped", "source_samples_dropped", "source_blocks_dropped",
        "snapshots_emitted", "persistence_updates", "persistence_snapshots_superseded",
        "source_sequence_discontinuities", "source_sample_index_discontinuities",
        "source_timestamp_regressions", "source_estimated_timestamp_blocks", "source_refill_calls",
        "source_inter_refill_gap_count"),
    "hackrf": _COMMON + ("snapshots_emitted", "persistence_updates", "persistence_snapshots_superseded",
        "source_sequence_discontinuities", "source_sample_index_discontinuities",
        "source_timestamp_regressions", "source_estimated_timestamp_blocks"),
    "rtl_sdr": _COMMON + ("iq_blocks_dropped",),
}
_COUNTER_SCHEMA = tuple(sorted({name for fields in _FAMILY_COUNTERS.values() for name in fields}
                              | {"iq_samples_received"}))


def cached_live_reading(resource_id: str, family: str, snapshot, *, anchor: tuple[str, ...]) -> CounterReading:
    """Convert one already-cached LiveSnapshot; no SDK, query, or rolling rate.

Caller owns exact profile/identity binding. These mappings require the current
qualified native/services contract. Cache refresh cut timestamps and total
admitted samples are not exposed here: unknown, not reconstructed from Fs.
"""
    if family not in _FAMILY_COUNTERS:
        raise ValueError("unsupported cached family counter authority")
    if snapshot.state.value != "running" or snapshot.error is not None or snapshot.spectrum is None:
        raise ValueError("steady window requires the same healthy RUNNING cached source")
    perf = snapshot.performance
    interval = perf.rate_observation_interval_s
    available = type(interval) in (int, float) and isfinite(interval) and interval > 0
    supported = _FAMILY_COUNTERS[family]
    counters = tuple((name, getattr(perf, name) if available and name in supported else None)
                     for name in _COUNTER_SCHEMA)
    gauges = tuple((name, getattr(perf, name) if available else None)
                   for name in ("acquisition_queue_depth", "spectrum_queue_depth"))
    return CounterReading(resource_id, anchor, counters, gauges)


@dataclass(frozen=True, slots=True)
class CounterReading:
    resource_id: str
    # Must contain exact session/epoch/profile/runtime authority, not a label.
    anchor: tuple[str, ...]
    counters: tuple[tuple[str, int | None], ...]
    gauges: tuple[tuple[str, int | float | None], ...] = ()

    def __post_init__(self):
        if (type(self.resource_id) is not str or not 1 <= len(self.resource_id) <= 128
                or type(self.anchor) is not tuple or not 1 <= len(self.anchor) <= 16
                or any(type(value) is not str or not 1 <= len(value) <= 4096 for value in self.anchor)):
            raise ValueError("exact resource and immutable nonempty anchor required")
        for fields, cumulative in ((self.counters, True), (self.gauges, False)):
            if type(fields) is not tuple or len(fields) > 64:
                raise ValueError("bounded immutable scalar fields required")
            names = []
            for name, value in fields:
                if type(name) is not str or not name or len(name) > 128:
                    raise ValueError("scalar field requires a bounded name")
                names.append(name)
                if value is None:
                    continue  # Unsupported/unobserved, never zero.
                if cumulative and (type(value) is not int or not 0 <= value < 1 << 63):
                    raise ValueError("cumulative counter must be a nonnegative signed-range integer")
                if not cumulative and (type(value) not in (int, float) or not isfinite(value)):
                    raise ValueError("gauge must be finite or explicitly unknown")
            if len(set(names)) != len(names):
                raise ValueError("duplicate scalar field")


@dataclass(frozen=True, slots=True)
class Observation:
    before_ns: int
    after_ns: int
    resources: tuple[CounterReading, ...]
    paint: PanePaintDiagnosticObservation | None = None

    def __post_init__(self):
        if (type(self.before_ns) is not int or type(self.after_ns) is not int
                or not 0 <= self.before_ns <= self.after_ns < 1 << 63
                or type(self.resources) is not tuple or not 1 <= len(self.resources) <= 12
                or any(type(value) is not CounterReading for value in self.resources)
                or len({value.resource_id for value in self.resources}) != len(self.resources)):
            raise ValueError("bounded distinct resources and ordered host envelope required")
        if self.paint is not None and not (
                self.before_ns <= self.paint.before_capture_ns
                <= self.paint.after_summary_ns <= self.after_ns):
            raise ValueError("paint observation must belong to the same capture envelope")


@dataclass(frozen=True, slots=True)
class WindowPlan:
    warmup_s: float = 15.0
    steady_s: float = 60.0
    spacing_s: float = 1.0

    def __post_init__(self):
        if (any(type(value) not in (int, float) or not isfinite(value)
                for value in (self.warmup_s, self.steady_s, self.spacing_s))
                or self.warmup_s < 0 or self.steady_s < .05 or self.spacing_s < .01
                or self.spacing_s > self.steady_s
                or ceil(self.steady_s / self.spacing_s) + 2 > MAX_WINDOW_OBSERVATIONS):
            raise ValueError("finite window must fit the unchanged 64-observation bound")


def summarize_window(observations: tuple[Observation, ...], *, start_ns: int, end_ns: int) -> dict:
    """Fail closed on resets/config changes; do not merge unrelated epochs.

Read-cut rate bounds allow counter reads anywhere in first/last envelopes.
They DO NOT bound the source cache freshness delay, which remains unknown.
Gauges are observed sample extrema, not time-weighted means or true peaks.
"""
    if (type(observations) is not tuple or not 2 <= len(observations) <= MAX_WINDOW_OBSERVATIONS
            or any(type(item) is not Observation for item in observations)
            or type(start_ns) is not int or type(end_ns) is not int or not 0 <= start_ns < end_ns
            or observations[0].after_ns > start_ns or observations[-1].before_ns < end_ns
            or any(b.before_ns < a.after_ns for a, b in zip(observations, observations[1:]))):
        raise ValueError("ordered observations must bracket one explicit host window")
    first, last = observations[0], observations[-1]
    ids = tuple(row.resource_id for row in first.resources)
    if any(tuple(row.resource_id for row in observation.resources) != ids for observation in observations):
        raise ValueError("resource roster/order changed")
    minimum_s = (last.before_ns - first.after_ns) / 1e9
    maximum_s = (last.after_ns - first.before_ns) / 1e9
    resources = []
    for index, initial in enumerate(first.resources):
        rows = [observation.resources[index] for observation in observations]
        if any(row.anchor != initial.anchor or tuple(k for k, _ in row.counters)
               != tuple(k for k, _ in initial.counters) or tuple(k for k, _ in row.gauges)
               != tuple(k for k, _ in initial.gauges) for row in rows):
            raise ValueError("resource authority/profile/scalar schema changed inside window")
        counters = {}
        for position, (name, _) in enumerate(initial.counters):
            values = [row.counters[position][1] for row in rows]
            known = [value for value in values if value is not None]
            if any(b < a for a, b in zip(known, known[1:])):
                raise ValueError("cumulative counter regressed: " + name)
            delta = None if len(known) != len(values) else values[-1] - values[0]
            counters[name] = dict(delta=delta, observed_samples=len(known),
                cached_read_rate_bounds_per_s=None if delta is None else (delta / maximum_s, delta / minimum_s))
        gauges = {}
        for position, (name, _) in enumerate(initial.gauges):
            values = [row.gauges[position][1] for row in rows]
            known = [value for value in values if value is not None]
            gauges[name] = dict(observed_samples=len(known), first=values[0], last=values[-1],
                                sampled_min=min(known) if known else None, sampled_max=max(known) if known else None)
        resources.append(dict(resource_id=initial.resource_id, anchor=initial.anchor,
                              counters=counters, gauges=gauges))
    paints = tuple(item.paint for item in observations if item.paint is not None)
    paint_audit = None
    if paints:
        if len(paints) != len(observations) or paints[0].observation_clock is None:
            raise ValueError("paint coverage/clock changed inside measurement")
        paint_audit = audit_pane_paint_window(paints, window_start_ns=start_ns,
                                             window_end_ns=end_ns, host_clock=paints[0].observation_clock)
    return dict(window_start_ns=start_ns, window_end_ns=end_ns, declared_window_s=(end_ns-start_ns)/1e9,
        observation_count=len(observations), counter_read_interval_s=(minimum_s, maximum_s),
        max_query_elapsed_ns=max(item.after_ns-item.before_ns for item in observations),
        max_observation_spacing_ns=max(b.before_ns-a.before_ns for a,b in zip(observations, observations[1:])),
        resources=resources, paint_audit=paint_audit, lifetime_complete=False,
        scope="Cached cumulative read deltas; unknown cache freshness delay. Conditional retained Qt paint receipts, "
              "not all offers/paints, DWM, RF duty, lossless ADC throughput, or a latency SLA.")


class SteadyWindowProbe:
    """One explicit diagnostic worker. Errors surface to caller, no RX actions.

Caller must stop/join this worker before closing cached readers. A callback
cannot be forcibly interrupted; join has a finite bound and refuses success
if that worker remains alive. No daemon thread or silent success on timeout.
"""
    def __init__(self, capture: Callable[[], Observation], *, plan: WindowPlan = WindowPlan()):
        if not callable(capture) or type(plan) is not WindowPlan:
            raise ValueError("cached capture callable and exact window plan required")
        self.plan, self.capture = plan, capture
        self.done, self.cancelled = Event(), Event()
        self.thread = Thread(target=self._run, name="m78-steady-observer")
        self.observations: tuple[Observation, ...] = ()
        self.report: dict | None = None
        self.error: BaseException | None = None
        self.started = False
        self.missed_due_observations = 0

    def start(self):
        if self.started:
            raise RuntimeError("probe cannot be restarted")
        self.started = True
        self.thread.start()

    def _wait_until(self, deadline_ns):
        if self.cancelled.wait(max(0, (deadline_ns-perf_counter_ns())/1e9)):
            raise RuntimeError("steady observer cancelled; no baseline")

    def _run(self):
        rows = []
        try:
            self._wait_until(perf_counter_ns() + round(self.plan.warmup_s * 1e9))
            first = self.capture()
            rows.append(first)
            start = first.after_ns
            end = start + round(self.plan.steady_s * 1e9)
            spacing = round(self.plan.spacing_s * 1e9)
            due = start + spacing
            while True:
                deadline = min(due, end)
                self._wait_until(deadline)
                observed = self.capture()
                rows.append(observed)
                if len(rows) > MAX_WINDOW_OBSERVATIONS:
                    raise OverflowError("steady observer bound exhausted")
                if observed.before_ns >= end:
                    break
                # Skip missed sample due-times, not a catch-up burst or invented observations.
                next_due = due + spacing
                skipped = max(0, (min(observed.after_ns, end-1)-next_due)//spacing + 1)
                self.missed_due_observations += skipped
                due = next_due + skipped * spacing
            if self.cancelled.is_set():
                raise RuntimeError("steady observer cancelled; no baseline")
            self.report = summarize_window(tuple(rows), start_ns=start, end_ns=end)
            self.report["missed_due_observations"] = self.missed_due_observations
        except BaseException as error:
            self.error = error
        finally:
            self.observations = tuple(rows)
            self.done.set()

    def stop_and_join(self, timeout_s=5.0):
        if type(timeout_s) not in (int, float) or not isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("finite nonnegative diagnostic join bound required")
        if not self.started:
            return
        self.cancelled.set()
        self.thread.join(timeout_s)
        if self.thread.is_alive():
            raise TimeoutError("steady observer retained; cached readers must remain alive")

    def result(self):
        if not self.done.is_set():
            raise RuntimeError("observer has not finished")
        if self.error is not None:
            raise self.error
        if self.report is None:
            raise RuntimeError("observer produced no baseline")
        return self.report


def close_after_observer_join(probe: SteadyWindowProbe | None, close: Callable[[], object], *, timeout_s=5.0):
    """Never invoke reader/owner teardown when the observer join fails.

Caller supplies teardown; this test/HIL utility owns no hardware and makes no
cleanup claim from a timeout. Retained readers require explicit later custody.
"""
    if probe is not None:
        probe.stop_and_join(timeout_s)
    return close()

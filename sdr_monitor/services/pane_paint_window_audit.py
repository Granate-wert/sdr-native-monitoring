"""Bounded offline audit of original scalar observations, not an RX monitor.

Input storage belongs to the diagnostic caller. No product queue enlargement,
SDK/Qt calls, file loading, persistent observer or presumed all-offers coverage.
Never sum snapshot quantiles or overlapping paint counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sdr_monitor.domain.host_clock import HostClockScope
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryEvent, PaneDeliveryStage
from sdr_monitor.domain.pane_paint_statistics import (
    PanePaintStatistics, PanePaintStatisticsScope, _scope, _summarize_groups, summarize_pane_paint_snapshot,
)
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef
from sdr_monitor.domain.pane_paint_timing import PaintTimingState, evaluate_ready_to_paint

from .pane_paint_diagnostics import PanePaintDiagnosticObservation

MAX_WINDOW_OBSERVATIONS = 64  # Offline input, independent of product retention.
MAX_WINDOW_EVENTS = MAX_WINDOW_OBSERVATIONS * 128


class PaintWindowCohort(StrEnum):
    BEFORE = "before_window"
    INSIDE = "base_return_enclosure_inside_window"
    CROSS_START = "crosses_start"
    CROSS_END = "crosses_end"
    CROSS_BOTH = "crosses_both_boundaries"
    AFTER = "after_window"
    UNKNOWN = "unusable_paint_clock_or_receipt"


class AdmissionWindowCohort(StrEnum):
    BEFORE = "admission_ledger_stamp_before_window"
    INSIDE = "admission_ledger_stamp_inside_window"
    AFTER = "admission_ledger_stamp_after_window"
    UNKNOWN = "admission_not_observed_or_clock_unknown"


@dataclass(frozen=True, slots=True)
class PaintWindowAudit:
    graph_instance_id: str
    window_start_ns: int
    window_end_ns: int
    host_clock: HostClockScope
    observation_count: int
    observation_envelope_ns: tuple[int, int]
    maximum_query_elapsed_ns: int
    unique_events: int
    repeated_event_observations: int
    unobserved_ids_between_retained_events: int
    paint_cohorts: tuple[tuple[PaintWindowCohort, int], ...]
    # Admissions paired with INSIDE paint enclosures ONLY, not all admissions.
    inside_paint_admission_cohorts: tuple[tuple[AdmissionWindowCohort, int], ...]
    inside_paint_statistics: tuple[PanePaintStatistics, ...]
    observation_counter_deltas: tuple[tuple[str, int], ...]
    lifetime_complete: bool = False


_COUNTERS = ("record_evictions", "event_evictions", "event_drops", "duplicate_events",
             "accounting_failures", "clock_failures")


def _paint_cohort(event: PaneDeliveryEvent, start: int, end: int, clock: HostClockScope) -> PaintWindowCohort:
    timing = evaluate_ready_to_paint(event)
    paint = event.paint_return
    if (paint is None or paint.host_clock != clock
            or timing.state not in (PaintTimingState.BOUNDED, PaintTimingState.ORDER_UNCERTAIN)):
        return PaintWindowCohort.UNKNOWN
    low, high = paint.before_paint_ns, paint.sampled_after_return_ns
    if high < start:
        return PaintWindowCohort.BEFORE
    if low >= end:
        return PaintWindowCohort.AFTER
    if low < start:
        return PaintWindowCohort.CROSS_BOTH if high >= end else PaintWindowCohort.CROSS_START
    return PaintWindowCohort.CROSS_END if high >= end else PaintWindowCohort.INSIDE


def audit_pane_paint_window(
    observations: tuple[PanePaintDiagnosticObservation, ...], *,
    window_start_ns: int, window_end_ns: int, host_clock: HostClockScope,
) -> PaintWindowAudit:
    """Validate, deduplicate and classify one explicitly bounded host window.

    INSIDE refers to a before-paint/after-return enclosure, not exact Qt return.
    Admission cohorts use ledger stamps, not invented exact native admission.
    Sequence gaps mean unobserved ledger IDs, never inferred paints/RF loss.
    Counter deltas cover first/last observation envelopes, not exact window
    boundaries. Missing coverage stays explicit even when all counters are zero.
    """
    if (type(observations) is not tuple or not 1 <= len(observations) <= MAX_WINDOW_OBSERVATIONS
            or not isinstance(host_clock, HostClockScope)
            or any(type(value) is not int or not 0 <= value < (1 << 63)
                   for value in (window_start_ns, window_end_ns))
            or window_start_ns >= window_end_ns):
        raise ValueError("paint audit requires a bounded tuple and explicit ordered host window")
    events: dict[int, PaneDeliveryEvent] = {}
    repeated = 0
    graph: str | None = None
    prior: PanePaintDiagnosticObservation | None = None
    for observation in observations:
        if (not isinstance(observation, PanePaintDiagnosticObservation)
                or observation.observation_clock != host_clock
                or any(type(value) is not int or not 0 <= value < (1 << 63)
                       for value in (observation.before_capture_ns, observation.after_summary_ns))
                or observation.after_summary_ns < observation.before_capture_ns
                or prior is not None and observation.before_capture_ns < prior.after_summary_ns):
            raise ValueError("paint audit observation clock/envelope is unknown, foreign or regressed")
        snapshot = observation.snapshot
        if observation.summary != summarize_pane_paint_snapshot(snapshot):
            raise ValueError("paint audit summary does not belong to its exact snapshot")
        if graph is None:
            graph = snapshot.graph_instance_id
        if snapshot.graph_instance_id != graph:
            raise ValueError("one paint audit cannot merge different graphs")
        if prior is not None and any(getattr(snapshot, name) < getattr(prior.snapshot, name) for name in _COUNTERS):
            raise ValueError("paint audit cumulative coverage counters regressed")
        for event in snapshot.events:
            if event.host_clock == host_clock and event.host_perf_ns is not None:
                if (type(event.host_perf_ns) is not int or not 0 <= event.host_perf_ns <= observation.after_summary_ns):
                    raise ValueError("ledger stamp contradicts its observation envelope")
            if (event.paint_return is not None and event.paint_return.host_clock == host_clock
                    and event.paint_return.sampled_after_return_ns > observation.after_summary_ns):
                raise ValueError("paint return occurred after its captured snapshot")
            existing = events.get(event.sequence)
            if existing is not None:
                if existing != event:
                    raise ValueError("same graph/event ID changed its original evidence")
                repeated += 1
            else:
                events[event.sequence] = event
        prior = observation
    first, last = observations[0], observations[-1]
    if not first.before_capture_ns <= window_start_ns < window_end_ns <= last.after_summary_ns:
        raise ValueError("observations do not bracket the requested host window")
    if len(events) > MAX_WINDOW_EVENTS:
        raise ValueError("offline observation event capacity exceeded")
    admissions: dict[int, PaneDeliveryEvent] = {}
    paints: dict[int, PaneDeliveryEvent] = {}
    cohorts = {cohort: 0 for cohort in PaintWindowCohort}
    admitted = {cohort: 0 for cohort in AdmissionWindowCohort}
    groups: dict[PanePaintStatisticsScope, list[PaneDeliveryEvent]] = {}
    originals: dict[int, PaneDeliveryObligationRef] = {}
    for event in sorted(events.values(), key=lambda value: value.sequence):
        original = originals.setdefault(event.ref.sequence, event.ref)
        if original != event.ref:
            raise ValueError("one obligation changed its original identity")
        if event.stage is PaneDeliveryStage.ADMITTED:
            if event.ref.sequence in paints:
                raise ValueError("admission event follows its paint return")
            if event.ref.sequence in admissions:
                raise ValueError("one obligation has multiple admission events")
            admissions[event.ref.sequence] = event
        if event.stage is not PaneDeliveryStage.PAINT_RETURNED:
            continue
        if event.ref.sequence in paints:
            raise ValueError("one obligation has multiple paint return events")
        paints[event.ref.sequence] = event
        cohort = _paint_cohort(event, window_start_ns, window_end_ns, host_clock)
        cohorts[cohort] += 1
        if cohort is not PaintWindowCohort.INSIDE:
            continue
        groups.setdefault(_scope(event.ref), []).append(event)
        admission = admissions.get(event.ref.sequence)
        admission_cohort = AdmissionWindowCohort.UNKNOWN
        if admission is not None:
            if admission.ref != event.ref:
                raise ValueError("paint obligation differs from its original admission")
            stamp = admission.host_perf_ns
            if admission.host_clock == host_clock and stamp is not None:
                admission_cohort = (AdmissionWindowCohort.BEFORE if stamp < window_start_ns
                    else AdmissionWindowCohort.INSIDE if stamp < window_end_ns else AdmissionWindowCohort.AFTER)
        admitted[admission_cohort] += 1
    missing = max(events) - min(events) + 1 - len(events) if events else 0
    assert graph is not None
    return PaintWindowAudit(graph, window_start_ns, window_end_ns, host_clock, len(observations),
        (first.before_capture_ns, last.after_summary_ns), max(item.query_elapsed_ns for item in observations),
        len(events), repeated, missing, tuple((key, count) for key, count in cohorts.items() if count),
        tuple((key, count) for key, count in admitted.items() if count), _summarize_groups(groups),
        tuple((name, getattr(last.snapshot, name) - getattr(first.snapshot, name)) for name in _COUNTERS))

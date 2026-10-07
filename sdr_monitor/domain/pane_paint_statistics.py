"""Finite, read-only ready-to-Qt-return interval statistics.

Nearest-rank order statistics of interval endpoints enclose the corresponding
order statistic of any point assignment within those intervals. No midpoint,
interpolation, clipping, RF timestamp, FPS or DWM presentation is inferred.
Unknown/invalid returns remain an explicit denominator. Never a lifetime SLA.
Call from a diagnostic/report consumer, NOT an acquisition or paint callback.
"""
from __future__ import annotations

from dataclasses import dataclass

from .analytical_journal import OwnerJournalScope
from .layer_journal import SweepLayerScope
from .pane_delivery_obligation import (
    PaneDeliveryCounters, PaneDeliveryEvent, PaneDeliveryLedgerSnapshot,
    PaneDeliveryObligationRef, PaneDeliveryStage, PaneViewDeliveryCounters,
)
from .pane_layer_identity import PaneDeliveryView
from .pane_paint_timing import PaintTimingState, evaluate_ready_to_paint

# Existing ledger contract; not a request to expand producer retention.
PAINT_SNAPSHOT_EVENT_LIMIT = 128
PAINT_SNAPSHOT_RECORD_LIMIT = 256


@dataclass(frozen=True, slots=True)
class PanePaintStatisticsScope:
    graph_instance_id: str
    owner_scope: OwnerJournalScope | SweepLayerScope
    physical_stream_resource_id: str
    capture_id: str
    receiver_endpoint_id: str
    pane_id: str
    host_run_serial: int
    host_activation_serial: int
    producer_instance_id: int
    view: PaneDeliveryView
    ready_kind: str


@dataclass(frozen=True, slots=True)
class PaintIntervalQuantiles:
    p50_ns: tuple[int, int]
    p95_ns: tuple[int, int]
    p99_ns: tuple[int, int]


@dataclass(frozen=True, slots=True)
class PanePaintStatistics:
    scope: PanePaintStatisticsScope
    paint_returns: int
    timed_returns: int
    state_counts: tuple[tuple[PaintTimingState, int], ...]
    base_return: PaintIntervalQuantiles | None
    after_return_sample: PaintIntervalQuantiles | None


@dataclass(frozen=True, slots=True)
class PaintSnapshotCoverage:
    supported: bool
    retained_events: int
    retained_records: int
    record_evictions: int
    event_evictions: int
    event_drops: int
    duplicate_events: int
    accounting_failures: int
    clock_failures: int
    # Cumulative graph counters, NOT group/window or native FFT denominators.
    panes: tuple[PaneDeliveryCounters, ...]
    views: tuple[PaneViewDeliveryCounters, ...]


@dataclass(frozen=True, slots=True)
class PanePaintSnapshotSummary:
    graph_instance_id: str
    groups: tuple[PanePaintStatistics, ...]
    coverage: PaintSnapshotCoverage
    population: str = "retained_unique_paint_return_events_only"
    quantile_method: str = "nearest_rank_separate_signed_interval_endpoints"
    lifetime_complete: bool = False


def _scope(ref: PaneDeliveryObligationRef) -> PanePaintStatisticsScope:
    identity = ref.identity
    kind = getattr(identity.ready, "kind", None)
    return PanePaintStatisticsScope(
        ref.graph_instance_id, identity.owner_scope, identity.physical_stream_resource_id,
        identity.capture_id, identity.receiver_endpoint_id, identity.pane_id,
        identity.host_run_serial, identity.host_activation_serial,
        identity.ready.producer_instance_id, ref.view,
        "detector" if kind is None else str(kind),
    )


def _quantiles(intervals: list[tuple[int, int]]) -> PaintIntervalQuantiles | None:
    if not intervals:
        return None
    low = sorted(interval[0] for interval in intervals)
    high = sorted(interval[1] for interval in intervals)
    size = len(intervals)

    def ranked(percent: int) -> tuple[int, int]:
        index = (percent * size + 99) // 100 - 1
        return low[index], high[index]

    return PaintIntervalQuantiles(ranked(50), ranked(95), ranked(99))


def summarize_pane_paint_snapshot(snapshot: PaneDeliveryLedgerSnapshot) -> PanePaintSnapshotSummary:
    """Summarize an already captured immutable ledger without side effects.

    Groups never cross graph/source/RX/pane/run/activation/ready-layer boundaries.
    The snapshot has bounded historical retention: evictions and unmeasured
    dispositions are reported but never fabricated as additional samples.
    A duplicate event/return or foreign graph indicates corrupt input and is
    explicitly refused, not deduplicated to produce nicer statistics.
    """
    if not isinstance(snapshot, PaneDeliveryLedgerSnapshot):
        raise ValueError("paint statistics require a typed ledger snapshot")
    if (any(type(value) is not tuple for value in (snapshot.events, snapshot.records, snapshot.panes, snapshot.views))
            or len(snapshot.events) > PAINT_SNAPSHOT_EVENT_LIMIT
            or len(snapshot.records) > PAINT_SNAPSHOT_RECORD_LIMIT
            or len(snapshot.panes) > 4 or len(snapshot.views) > 12):
        raise ValueError("paint statistics require the existing bounded immutable ledger window")
    counters = (snapshot.record_evictions, snapshot.event_evictions, snapshot.event_drops,
                snapshot.duplicate_events, snapshot.accounting_failures, snapshot.clock_failures)
    if type(snapshot.supported) is not bool or any(type(value) is not int or value < 0 for value in counters):
        raise ValueError("paint snapshot coverage counters must remain nonnegative integers")
    event_ids: set[int] = set()
    paint_ids: set[int] = set()
    groups: dict[PanePaintStatisticsScope, list[PaneDeliveryEvent]] = {}
    for event in snapshot.events:
        if (not isinstance(event, PaneDeliveryEvent) or type(event.sequence) is not int or event.sequence < 1
                or event.sequence in event_ids or not isinstance(event.ref, PaneDeliveryObligationRef)
                or event.ref.graph_instance_id != snapshot.graph_instance_id
                or not isinstance(event.stage, PaneDeliveryStage)):
            raise ValueError("paint statistics refuse duplicate, malformed or foreign ledger events")
        event_ids.add(event.sequence)
        if event.stage is not PaneDeliveryStage.PAINT_RETURNED:
            continue
        if event.ref.sequence in paint_ids:
            raise ValueError("one retained view obligation cannot paint-return twice")
        paint_ids.add(event.ref.sequence)
        groups.setdefault(_scope(event.ref), []).append(event)

    results: list[PanePaintStatistics] = []
    for scope, events in groups.items():
        counts = {state: 0 for state in PaintTimingState}
        base: list[tuple[int, int]] = []
        after: list[tuple[int, int]] = []
        for event in events:
            timing = evaluate_ready_to_paint(event)
            counts[timing.state] += 1
            if timing.base_return_elapsed_ns is not None and timing.after_return_sample_elapsed_ns is not None:
                base.append(timing.base_return_elapsed_ns)
                after.append(timing.after_return_sample_elapsed_ns)
        results.append(PanePaintStatistics(scope, len(events), len(base),
            tuple((state, count) for state, count in counts.items() if count), _quantiles(base), _quantiles(after)))

    return PanePaintSnapshotSummary(snapshot.graph_instance_id, tuple(results), PaintSnapshotCoverage(
        snapshot.supported, len(snapshot.events), len(snapshot.records), *counters, snapshot.panes, snapshot.views))

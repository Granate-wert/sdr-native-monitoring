"""One cached diagnostic observation, never a second receiver or renderer.

The raw scalar snapshot accompanies its conditional statistics so a later
window audit can deduplicate original event IDs and disclose crossing cohorts.
Repeated observations are overlapping windows, NOT summable paint counters.
No persistent observer buffer, acquisition callback, Qt call or SDK query.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from time import perf_counter_ns
from typing import Protocol

from sdr_monitor.domain.host_clock import HostClockKind, HostClockScope
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryLedgerSnapshot
from sdr_monitor.domain.pane_paint_statistics import PanePaintSnapshotSummary, summarize_pane_paint_snapshot


class PanePaintSnapshotReader(Protocol):
    def pane_delivery_ledger_snapshot(self) -> PaneDeliveryLedgerSnapshot: ...


@dataclass(frozen=True, slots=True)
class PanePaintDiagnosticObservation:
    before_capture_ns: int
    after_summary_ns: int
    observation_clock: HostClockScope | None
    snapshot: PaneDeliveryLedgerSnapshot
    summary: PanePaintSnapshotSummary

    @property
    def query_elapsed_ns(self) -> int:
        """Observer query/calculation cost, NOT ready-to-paint latency."""
        return self.after_summary_ns - self.before_capture_ns


def _stamp(clock: Callable[[], int]) -> int:
    value = clock()
    if type(value) is not int or not 0 <= value < (1 << 63):
        raise ValueError("diagnostic observation clock must return signed-range nanoseconds")
    return value


def capture_pane_paint_observation(
    reader: PanePaintSnapshotReader, *, now_ns: Callable[[], int] = perf_counter_ns,
) -> PanePaintDiagnosticObservation:
    """Read SAME graph snapshot exactly once; calculate outside ledger lock.

    The envelope includes snapshot copying and summary calculation, not JSON
    serialization or queue scheduling. Only the retained built-in clock is
    tagged perf_counter_ns; injected clocks have unknown origin. Preserve the
    ledger's original host clock independently rather than relabeling it.
    Observation errors are diagnostic errors, not instructions to stop RX.
    """
    before = _stamp(now_ns)
    snapshot = reader.pane_delivery_ledger_snapshot()
    summary = summarize_pane_paint_snapshot(snapshot)
    after = _stamp(now_ns)
    if after < before:
        raise ValueError("diagnostic observation clock regressed")
    clock = HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid()) if now_ns is perf_counter_ns else None
    return PanePaintDiagnosticObservation(before, after, clock, snapshot, summary)

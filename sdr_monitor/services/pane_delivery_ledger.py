"""Bounded graph-scoped scalar custody. Never retains arrays or hardware.

One declared 1 MiB HOST component per graph, not per pane/receiver. Native
budgets are unchanged; this is not an RSS/allocator or external snapshot cap.
UI transitions do O(1) lookup/accounting; only snapshot/Stop scan finite state.
Missing measurement support or telemetry capacity never changes acquisition.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from hashlib import sha256
import os
import sys
from threading import Lock
from time import perf_counter_ns
from uuid import uuid4

from ..domain.pane_analytical_identity import PaneAnalyticalIdentity
from ..domain.host_clock import HostClockKind, HostClockScope
from ..domain.pane_paint_timing import PanePaintReturnReceipt
from ..domain.pane_layer_identity import PaneDeliveryView, PaneLayerAnalyticalIdentity
from ..domain.pane_delivery_obligation import (
    PaneDeliveryCounters, PaneDeliveryEvent, PaneDeliveryLedgerSnapshot,
    PaneDeliveryObligationRef, PaneDeliveryRecord, PaneDeliveryStage as Stage, PaneViewDeliveryCounters,
)

HOST_GRAPH_SCALAR_BUDGET = 1_048_576
RECORD_CAPACITY = 256
EVENT_CAPACITY = 128
PANE_CAPACITY = 4
BASE_RESERVATION = 16_384  # Fixed indexes/counters/snapshot tuple allowances.

_TRANSITIONS = {
    Stage.ADMITTED: frozenset((Stage.PREPARING, Stage.ADMISSION_CANCELLED)),
    Stage.PREPARING: frozenset((Stage.PREPARED, Stage.PREPARATION_FAILED, Stage.PREPARATION_CANCELLED)),
    Stage.PREPARED: frozenset((Stage.QUEUED, Stage.QUEUE_FAILED, Stage.QUEUE_REJECTED, Stage.PREPARATION_CANCELLED)),
    Stage.QUEUED: frozenset((Stage.QUEUE_DRAINED, Stage.QUEUE_SUPERSEDED, Stage.STOP_CLEARED)),
    Stage.QUEUE_DRAINED: frozenset((Stage.UI_ADMITTED, Stage.UI_REJECTED, Stage.STOP_CLEARED)),
    Stage.UI_ADMITTED: frozenset((Stage.PAINT_SCHEDULED, Stage.PAINT_SUPERSEDED, Stage.UI_REJECTED, Stage.STOP_CLEARED)),
    Stage.PAINT_SCHEDULED: frozenset((Stage.PAINT_RETURNED, Stage.PAINT_SUPERSEDED, Stage.STOP_CLEARED)),
}
_TERMINAL = frozenset(Stage) - _TRANSITIONS.keys()


def _weight(value: object) -> int:
    """Conservative scalar object size; computed only for new admission."""
    result = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        result += sum(_weight(getattr(value, item.name)) for item in fields(value))
    elif isinstance(value, tuple):
        result += sum(_weight(item) for item in value)
    return result


class PaneDeliveryLedger:
    def __init__(self, pane_ids: tuple[str, ...], *, now_ns: Callable[[], int] = perf_counter_ns) -> None:
        self._graph_id = str(uuid4())
        self._supported = (0 < len(pane_ids) <= PANE_CAPACITY and len(set(pane_ids)) == len(pane_ids)
                           and all(type(pane) is str and 0 < len(pane) <= 4096 for pane in pane_ids))
        self._counts = {pane: PaneDeliveryCounters(pane) for pane in pane_ids} if self._supported else {}
        self._view_counts = {(pane, view): PaneDeliveryCounters(pane)
                             for pane in self._counts for view in PaneDeliveryView}
        # Fixed high-water dedup metadata, NOT an identity authority. Exact
        # original references below (not digests) authorize all transitions.
        self._markers: dict[tuple[str, PaneDeliveryView], tuple[int, int, int, int, bytes, bytes]] = {}
        self._records: OrderedDict[int, tuple[PaneDeliveryRecord, int]] = OrderedDict()
        self._terminal_order: OrderedDict[int, None] = OrderedDict()
        self._events: deque[tuple[PaneDeliveryEvent, int]] = deque()
        self._bytes = BASE_RESERVATION + 2 * _weight(tuple(self._counts.values())) + 2 * _weight(tuple(self._view_counts.values()))
        self._sequence = self._event_sequence = 0
        self._record_evictions = self._event_evictions = self._event_drops = 0
        self._duplicate_events = self._accounting_failures = self._clock_failures = 0
        self._last_ns: int | None = None
        self._now_ns = now_ns
        self._host_clock = (HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid())
                            if now_ns is perf_counter_ns else None)
        self._lock = Lock()

    def _count(self, pane: str, view: PaneDeliveryView, **increments: int) -> None:
        current = self._counts[pane]
        self._counts[pane] = replace(current, **{
            key: getattr(current, key) + value for key, value in increments.items()})
        current = self._view_counts[pane, view]
        self._view_counts[pane, view] = replace(current, **{
            key: getattr(current, key) + value for key, value in increments.items()})

    def admit(self, pane_id: str, identity: PaneAnalyticalIdentity | PaneLayerAnalyticalIdentity | None,
              *, view: PaneDeliveryView = PaneDeliveryView.SPECTRUM) -> PaneDeliveryObligationRef | None:
        """Called ONLY after exact graph measurement admission, not from UI.

        A high-water marker per pane/view prevents a repeated native offer becoming
        a new obligation, including after its terminal record was evicted.
        """
        with self._lock:
            if not self._supported:
                return None
            if pane_id not in self._counts or not isinstance(view, PaneDeliveryView):
                self._accounting_failures += 1
                return None
            if identity is None:
                self._count(pane_id, view, unqualified_deliveries=1)
                return None
            if not isinstance(identity, (PaneAnalyticalIdentity, PaneLayerAnalyticalIdentity)) or identity.pane_id != pane_id:
                self._accounting_failures += 1
                return None
            try:
                # Validate the view BEFORE counters/high-water mutation.
                PaneDeliveryObligationRef(self._graph_id, 1, identity, view)
            except ValueError:
                self._accounting_failures += 1
                return None
            offer_sequence = (identity.ready.offer_sequence if isinstance(identity, PaneAnalyticalIdentity)
                              else identity.ready.creation_sequence)
            prior = self._markers.get((pane_id, view))
            fingerprint = sha256(repr(identity).encode("utf-8")).digest()
            namespace = sha256(repr((identity.owner_scope, identity.physical_stream_resource_id,
                identity.capture_id, identity.receiver_endpoint_id,
                identity.ready.producer_instance_id)).encode("utf-8")).digest()
            if prior is not None:
                old = prior[:2]
                new = (identity.host_run_serial, identity.host_activation_serial)
                if new < old:
                    self._accounting_failures += 1
                    return None
                if new == old:
                    if identity.ready.producer_instance_id != prior[2] or namespace != prior[5]:
                        self._accounting_failures += 1
                        return None
                    if offer_sequence <= prior[3]:
                        if offer_sequence == prior[3] and fingerprint != prior[4]:
                            self._accounting_failures += 1
                        else:
                            self._count(pane_id, view, duplicate_admissions=1)
                        return None
            # Markers remain fixed scalar tuples even when admitted identities
            # contain maximum-length strings. Charged in BASE_RESERVATION.
            self._markers[pane_id, view] = (identity.host_run_serial, identity.host_activation_serial,
                identity.ready.producer_instance_id, offer_sequence, fingerprint, namespace)
            self._count(pane_id, view, qualified_admissions=1)
            self._sequence += 1
            if self._sequence >= (1 << 64):
                self._count(pane_id, view, untracked_admissions=1)
                return None
            ref = PaneDeliveryObligationRef(self._graph_id, self._sequence, identity, view)
            weight = 2 * _weight(ref) + 512  # record + index + internal snapshot duplicate allowance
            self._make_room(weight)
            if len(self._records) >= RECORD_CAPACITY or self._bytes + weight > HOST_GRAPH_SCALAR_BUDGET:
                self._count(pane_id, view, untracked_admissions=1)
                return None
            self._records[ref.sequence] = (PaneDeliveryRecord(ref, Stage.ADMITTED), weight)
            self._bytes += weight
            self._count(pane_id, view, pending=1)
            self._append_event(ref, Stage.ADMITTED)
            return ref

    def _make_room(self, weight: int) -> None:
        while self._terminal_order and (len(self._records) >= RECORD_CAPACITY
                or self._bytes + weight > HOST_GRAPH_SCALAR_BUDGET):
            sequence, _ = self._terminal_order.popitem(last=False)
            _, size = self._records.pop(sequence)
            self._bytes -= size
            self._record_evictions += 1
        while self._events and self._bytes + weight > HOST_GRAPH_SCALAR_BUDGET:
            _, size = self._events.popleft()
            self._bytes -= size
            self._event_evictions += 1

    def _timestamp(self) -> int | None:
        try:
            value = self._now_ns()
        except Exception:
            value = None
        if (type(value) is not int or not 0 <= value < (1 << 63)
                or self._last_ns is not None and value < self._last_ns):
            self._clock_failures += 1
            return None
        self._last_ns = value
        return value

    def _append_event(self, ref: PaneDeliveryObligationRef, stage: Stage,
                      paint_return: PanePaintReturnReceipt | None = None) -> None:
        self._event_sequence += 1
        event = PaneDeliveryEvent(self._event_sequence, ref, stage, self._timestamp(),
                                  self._host_clock, paint_return)
        # Ref is already charged in the record/marker. Include an additional
        # conservative ref allowance so eviction cannot hide retained identities.
        size = self._records[ref.sequence][1] + 256
        if paint_return is not None:
            # Reuse the admission's conservative ref weight, rather than
            # recursively traversing up to 2048 Sweep segments on the Qt
            # callback. Includes a separately allocated but equal receipt ref.
            size += self._records[ref.sequence][1] + 512
        if len(self._events) == EVENT_CAPACITY:
            _, dropped = self._events.popleft()
            self._bytes -= dropped
            self._event_evictions += 1
        if self._bytes + size > HOST_GRAPH_SCALAR_BUDGET:
            self._event_drops += 1
            return
        self._events.append((event, size))
        self._bytes += size

    def delivery_stage_if_retained(self, ref: PaneDeliveryObligationRef) -> Stage | None:
        """Read the exact original token's cached stage without waiting.

        None is UNKNOWN, never approval: unsupported/foreign/copied/evicted
        tokens and a contended ledger all have no observed stage. Identity
        comparison deliberately uses the original object, not recursive
        equality over up to 2048 Sweep segments. No clock, counters, allocation,
        native drain or hardware call. This is a point-in-time observation,
        NOT a reservation; note() remains the authoritative transition.
        """
        if (not isinstance(ref, PaneDeliveryObligationRef)
                or ref.graph_instance_id != self._graph_id
                or not self._lock.acquire(blocking=False)):
            return None
        try:
            retained = self._records.get(ref.sequence)
            if retained is None or retained[0].ref is not ref:
                return None
            return retained[0].stage
        finally:
            self._lock.release()

    def note(self, ref: PaneDeliveryObligationRef, stage: Stage, *,
             paint_return: PanePaintReturnReceipt | None = None) -> bool:
        """Record only the actual boundary that owns the original reference."""
        with self._lock:
            if not isinstance(ref, PaneDeliveryObligationRef) or not isinstance(stage, Stage):
                self._accounting_failures += 1
                return False
            if paint_return is not None and (stage is not Stage.PAINT_RETURNED
                    or not isinstance(paint_return, PanePaintReturnReceipt)
                    or paint_return.ref != ref or paint_return.host_clock.process_id != os.getpid()):
                self._accounting_failures += 1
                return False
            record = self._records.get(ref.sequence) if ref.graph_instance_id == self._graph_id else None
            if record is None or record[0].ref != ref:
                self._accounting_failures += 1
                return False
            if record[0].stage is stage:
                self._duplicate_events += 1
                return False
            if stage not in _TRANSITIONS.get(record[0].stage, ()):
                self._accounting_failures += 1
                return False
            self._transition(record[0], record[1], stage, paint_return)
            return True

    def _transition(self, record: PaneDeliveryRecord, weight: int, stage: Stage,
                    paint_return: PanePaintReturnReceipt | None = None) -> None:
        self._records[record.ref.sequence] = (replace(record, stage=stage), weight)
        if stage in _TERMINAL:
            self._terminal_order[record.ref.sequence] = None
            self._count(record.ref.identity.pane_id, record.ref.view, pending=-1, terminal=1)
        self._append_event(record.ref, stage, paint_return)

    def cancel_unclaimed(self, resource_id: str) -> None:
        """Routing closes: cancel ONLY ADMITTED, never UI/queue-owned refs."""
        with self._lock:
            for record, weight in tuple(self._records.values()):
                if (record.stage is Stage.ADMITTED
                        and record.ref.identity.physical_stream_resource_id == resource_id):
                    self._transition(record, weight, Stage.ADMISSION_CANCELLED)

    def retained_ui_refs(self, resource_id: str) -> tuple[PaneDeliveryObligationRef, ...]:
        """Finite off-Qt Stop capture, including UI-declined unowned refs.

        These are original tokens, not stage/absence or hardware receipts.
        Capture alone neither clears a record nor authorizes a paint. The
        session must first confirm this resource's completed Stop.
        """
        with self._lock:
            return tuple(record.ref for record, _weight_bytes in self._records.values()
                         if record.ref.identity.physical_stream_resource_id == resource_id
                         and record.stage in {Stage.QUEUE_DRAINED, Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED})

    def reconcile_ui_stop_cleared(
        self, refs: tuple[PaneDeliveryObligationRef, ...],
    ) -> tuple[PaneDeliveryObligationRef, ...]:
        """Acknowledge actual UI relinquishment of a captured Stop batch.

        Call off Qt AFTER all exact captured refs have been detached from
        pending presentation, including declined refs never attached to a
        view. Receiver Stop alone is not that boundary. Under the existing
        lock only still-UI-owned original tokens transition. A paint return,
        eviction, copy, foreign graph or repeated acknowledgement is a no-op,
        not a duplicate event/accounting failure or a guessed paint receipt.
        There is no scan, resident batch/tombstone or new memory allowance.
        """
        if (not isinstance(refs, tuple) or len(refs) > RECORD_CAPACITY
                or any(not isinstance(ref, PaneDeliveryObligationRef) for ref in refs)):
            raise ValueError("UI Stop reconciliation requires a bounded original-reference tuple")
        settled: list[PaneDeliveryObligationRef] = []
        with self._lock:
            for ref in refs:
                retained = self._records.get(ref.sequence) if ref.graph_instance_id == self._graph_id else None
                if (retained is None or retained[0].ref is not ref
                        or retained[0].stage not in {Stage.QUEUE_DRAINED, Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED}):
                    continue
                self._transition(retained[0], retained[1], Stage.STOP_CLEARED)
                settled.append(ref)
        return tuple(settled)

    def snapshot(self) -> PaneDeliveryLedgerSnapshot:
        """Cached scalars only. No clock, SDK, native drain or RF action."""
        with self._lock:
            return PaneDeliveryLedgerSnapshot(self._graph_id, self._supported,
                tuple(self._counts.values()), tuple(record for record, _ in self._records.values()),
                tuple(event for event, _ in self._events), self._record_evictions,
                self._event_evictions, self._event_drops, self._duplicate_events,
                self._accounting_failures, self._clock_failures,
                HOST_GRAPH_SCALAR_BUDGET, self._bytes,
                tuple(PaneViewDeliveryCounters(view, counts)
                      for (_, view), counts in self._view_counts.items()), self._host_clock)

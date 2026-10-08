"""SAME worker/owner creation drain and bounded original-ref authentication.

Cached snapshots and receipt conversion never drain, probe or open hardware.
Event loss/finite host eviction remains explicit; a missing event never gains
owner authentication merely because its sequence is below the creation count.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, replace
from itertools import islice
import threading
from typing import Any

from ..domain.analytical_journal import OwnerJournalScope
from ..domain.layer_journal import (
    LayerCreationCounters, LayerCreationEvent, LayerJournalSnapshot, LayerJournalState, SweepLayerScope,
)
from ..domain.layer_ready import DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt, SweepLayerIdentity, MAX_LAYER_SEGMENTS
from .native_owner_journal import _scalar_bytes
from .native_ready_bridge import NativeReadyBridge

LAYER_EVENT_CAPACITY = 64
LAYER_BATCH_CAPACITY = 32
LAYER_HOST_EVENT_CAPACITY = 32
HOST_LAYER_RESERVATION = 262_144  # carved out of existing 1 MiB per-chain host diagnostics
LAYER_RECEIPT_RESERVATION = 4096
DENSITY_CONTEXT_CACHE_RESERVATION = 4096
# Includes the original readiness receipt and its density-specific HOST context.
# Carved out of HOST_LAYER_RESERVATION; no larger host/native budget.
DENSITY_RECEIPT_WINDOW_RESERVATION = 8192
LAYER_RECEIPT_WINDOW = 16  # current/converting + bounded presentation references; external retention excluded


def sweep_layer_host_reservation(segment_count: int) -> int:
    """Owned scalar tuples/receipt window, charged to existing Sweep reduced budget.

    256 bytes/segment bounds duplicate-accounted Python tuple/pair/integers at
    either complete or mixed acquired/pending coverage. Not RSS or external
    retention. The 4KiB fixed portion is identical to density's scalar margin.
    """
    if type(segment_count) is not int or not 1 <= segment_count <= MAX_LAYER_SEGMENTS:
        raise ValueError("Sweep layer coverage exceeds bounded plan geometry")
    return HOST_LAYER_RESERVATION + LAYER_RECEIPT_WINDOW * 256 * segment_count


def layer_journal_capacity(native: object, *, hackrf: bool = False) -> int:
    """Pure loaded-API gate, NOT a hardware probe or an RF capability."""
    names = ["LAYER_CREATION_CONTRACT_VERSION", "ANALYTICAL_READY_CONTRACT_VERSION"]
    if hackrf:
        names.append("HACKRF_LAYER_CREATION_CONTRACT_VERSION")
    if any(type(getattr(native, name, None)) is not int or getattr(native, name) != 1 for name in names):
        return 0
    if (not callable(getattr(native, "analytical_ready_clock_ns", None))
            or any(getattr(native, name, None) is None for name in
                   ("LayerReadyKind", "AnalyticalReadyClock", "AnalyticalReadyClockState"))):
        return 0
    if hackrf and any(not callable(getattr(getattr(native, cls, None), method, None))
                      for cls, method in (("HackrfRuntimeDspControl", "drain_density_layer_ready_events"),
                          ("HackrfSweepRuntimeAnalysisControl", "drain_sweep_layer_ready_events"))):
        return 0
    return LAYER_EVENT_CAPACITY


class NativeLayerJournal:
    def __init__(self, native: object, capacity: int = 0, *, density: bool = True,
                 sweep_segments: int = 1) -> None:
        if type(capacity) is not int or capacity not in (0, LAYER_EVENT_CAPACITY):
            raise ValueError("layer journal requires its fixed admitted capacity")
        if capacity and layer_journal_capacity(native) != capacity:
            raise ValueError("native layer journal contract unavailable")
        if type(density) is not bool:
            raise ValueError("layer owner variant must be explicit")
        self._native, self._capacity = native, capacity
        self._density = density
        self._receipt_budget = (DENSITY_RECEIPT_WINDOW_RESERVATION if density else
                               LAYER_RECEIPT_RESERVATION + 256 * sweep_segments)
        self._processing_cache_budget = DENSITY_CONTEXT_CACHE_RESERVATION if density else 0
        self._host_budget = HOST_LAYER_RESERVATION if density else sweep_layer_host_reservation(sweep_segments)
        self._lock = threading.RLock()
        self._snapshot = LayerJournalSnapshot()
        self._last_event = self._missing = 0

    @property
    def enabled(self) -> bool:
        return bool(self._capacity)

    def current(self) -> LayerJournalSnapshot:
        with self._lock:
            return self._snapshot

    def prepare(self, *, capacity: int) -> None:
        """Admit/reset diagnostics before native Start; scope follows readback.

        Never carry prior producer evidence into a fresh owner. No native call,
        guessed session or counters before the actual owner scope exists.
        """
        with self._lock:
            if type(capacity) is not int or capacity not in (0, LAYER_EVENT_CAPACITY) or (
                    capacity and layer_journal_capacity(self._native) != capacity):
                raise ValueError("layer journal capacity/protocol was not admitted")
            if self._snapshot.scope is not None and not self._snapshot.native_stop_confirmed:
                raise ValueError("previous layer owner lacks confirmed Stop")
            self._capacity = capacity
            self._snapshot = LayerJournalSnapshot(
                state=LayerJournalState.ARMED if capacity else LayerJournalState.UNSUPPORTED)
            self._last_event = self._missing = 0

    def begin(self, scope: OwnerJournalScope | SweepLayerScope, *, enabled: bool = True) -> None:
        """Explicit Start before factory; no native counters invented."""
        with self._lock:
            if (not isinstance(scope, (OwnerJournalScope, SweepLayerScope)) or type(enabled) is not bool
                    or (self._density and not isinstance(scope, OwnerJournalScope))):
                raise ValueError("layer owner requires immutable typed scope/enable state")
            if self._snapshot.scope is not None and not self._snapshot.native_stop_confirmed:
                raise ValueError("previous layer owner lacks confirmed Stop")
            state = LayerJournalState.ARMED if self.enabled and enabled else LayerJournalState.UNSUPPORTED
            candidate = LayerJournalSnapshot(state=state, scope=scope)
            self._preflight_scope(scope)
            self._snapshot = candidate
            self._last_event = self._missing = 0

    @staticmethod
    def preflight_scope(scope: OwnerJournalScope) -> None:
        """Pure host-size check; does not admit a scope or create evidence."""
        if not isinstance(scope, OwnerJournalScope):
            raise ValueError("layer owner scope must be immutable")
        if (_scalar_bytes(LayerJournalSnapshot(scope=scope)) + LAYER_BATCH_CAPACITY * 1024
                + LAYER_RECEIPT_WINDOW * DENSITY_RECEIPT_WINDOW_RESERVATION
                + DENSITY_CONTEXT_CACHE_RESERVATION > HOST_LAYER_RESERVATION):
            raise ValueError("layer owner scope exceeds existing host reservation")

    def _preflight_scope(self, scope: OwnerJournalScope | SweepLayerScope) -> None:
        if (_scalar_bytes(LayerJournalSnapshot(scope=scope)) + LAYER_BATCH_CAPACITY * 1024
                + LAYER_RECEIPT_WINDOW * self._receipt_budget
                + self._processing_cache_budget > self._host_budget):
            raise ValueError("layer owner scope exceeds admitted host reservation")

    def _event(self, raw: Any) -> LayerCreationEvent:
        kinds = getattr(self._native, "LayerReadyKind")
        mapping = {kinds.Density: LayerReadyKind.DENSITY, kinds.SweepProgress: LayerReadyKind.SWEEP_PROGRESS,
                   kinds.SweepTerminal: LayerReadyKind.SWEEP_TERMINAL}
        clock = getattr(self._native, "AnalyticalReadyClock")
        states = getattr(self._native, "AnalyticalReadyClockState")
        if (raw.kind not in mapping or raw.clock != clock.NativeSteady
                or raw.clock_state not in (states.Monotonic, states.Regressed)):
            raise ValueError("layer creation variant/clock contract differs")
        return LayerCreationEvent(kind=mapping[raw.kind], clock_regressed=raw.clock_state == states.Regressed,
            **{item.name: getattr(raw, item.name) for item in fields(LayerCreationEvent)
               if item.name not in ("kind", "clock_regressed")})

    def drain(self, reader: Callable[[int], object]) -> None:
        with self._lock:
            old = self._snapshot
            if old.state not in (LayerJournalState.ARMED, LayerJournalState.ACTIVE):
                return
            try:
                batch = reader(LAYER_BATCH_CAPACITY)
                raw = getattr(batch, "summary")
                counters = LayerCreationCounters(**{item.name: getattr(raw, item.name)
                    for item in fields(LayerCreationCounters)})
                if counters.event_capacity != self._capacity or old.scope is None:
                    raise ValueError("native layer owner capacity/scope differs")
                previous = old.counters
                if previous is not None:
                    if previous.producer_instance_id != counters.producer_instance_id:
                        raise ValueError("layer producer changed without explicit Start")
                    if any(getattr(counters, name) < getattr(previous, name) for name in
                           ("created", "clock_regressions", "events_drained", "events_lost",
                            "last_lost_creation_sequence")):
                        raise ValueError("layer owner lifetime counters regressed")
                    if previous.events_lost and previous.first_lost_creation_sequence != counters.first_lost_creation_sequence:
                        raise ValueError("layer first loss identity changed")
                # Refuse oversized/competing consumer evidence before retention.
                creations = tuple(islice(iter(getattr(batch, "creations")), LAYER_BATCH_CAPACITY + 1))
                if (len(creations) > LAYER_BATCH_CAPACITY or counters.events_drained -
                        (previous.events_drained if previous else 0) != len(creations)):
                    raise ValueError("layer drain has competing consumer or oversized payload")
                events = tuple(self._event(ref) for ref in creations)
                last, missing = self._last_event, self._missing
                scope = old.scope
                for event in events:
                    if (event.producer_instance_id != counters.producer_instance_id
                            or not last < event.creation_sequence <= counters.created
                            or (event.kind is LayerReadyKind.DENSITY) != self._density):
                        raise ValueError("layer event belongs to a foreign/stale lifetime")
                    if event.kind is LayerReadyKind.DENSITY:
                        if (not isinstance(old.scope, OwnerJournalScope)
                                or event.config_generation != old.scope.configuration_generation):
                            raise ValueError("density event generation differs from the SAME owner")
                    elif isinstance(scope, SweepLayerScope) and scope.acquisition_epoch is None:
                        # Observe, do not derive max(requested, previous+1).
                        # Admission becomes visible only if the WHOLE batch and
                        # retention budget validate successfully below.
                        scope = replace(scope, acquisition_epoch=event.sweep_epoch)
                    elif event.sweep_epoch != scope.acquisition_epoch:
                        raise ValueError("Sweep event epoch differs from the SAME owner")
                    missing += event.creation_sequence - last - 1
                    last = event.creation_sequence
                if missing > counters.events_lost:
                    raise ValueError("layer sequence gaps exceed explicit native loss")
                retained = old.events + events
                evicted = max(0, len(retained) - LAYER_HOST_EVENT_CAPACITY)
                candidate = replace(old, state=LayerJournalState.ACTIVE, scope=scope, counters=counters,
                    events=retained[-LAYER_HOST_EVENT_CAPACITY:], host_events_evicted=old.host_events_evicted + evicted)
                # Old/new snapshot + intermediate tuples + one raw native batch.
                if (_scalar_bytes(old) + _scalar_bytes(candidate) + _scalar_bytes(retained)
                        + LAYER_BATCH_CAPACITY * 1024
                        + LAYER_RECEIPT_WINDOW * self._receipt_budget
                        + self._processing_cache_budget > self._host_budget):
                    raise ValueError("layer retention exceeds existing host scalar reservation")
                self._snapshot, self._last_event, self._missing = candidate, last, missing
            except Exception:  # noqa: BLE001 - diagnostic failure cannot change RF/acquisition.
                self._snapshot = replace(old, state=LayerJournalState.INCOMPLETE, drain_failures=old.drain_failures + 1)

    def finish(self, reader: Callable[[int], object]) -> None:
        """After joined Stop, BEFORE control release; no SDK or re-open."""
        with self._lock:
            if self._snapshot.native_stop_confirmed:
                return
            for _ in range(LAYER_EVENT_CAPACITY // LAYER_BATCH_CAPACITY):
                self.drain(reader)
                value = self._snapshot
                if value.state is not LayerJournalState.ACTIVE or (value.counters and value.counters.events_pending == 0):
                    break
            value = self._snapshot
            state = value.state
            if state is not LayerJournalState.UNSUPPORTED:
                state = (LayerJournalState.FINAL if state is LayerJournalState.ACTIVE
                         and value.counters is not None and value.counters.events_pending == 0
                         else LayerJournalState.INCOMPLETE)
            self._snapshot = replace(value, state=state, native_stop_confirmed=True)

    def receipt(self, raw_ref: object | None, identity: DensityLayerIdentity | SweepLayerIdentity | None,
                bridge: NativeReadyBridge) -> LayerReadyReceipt | None:
        """Exact retained creation match; finite eviction/loss stays unmatched.

        Calls are adapter attempts, not newly created events. Current/cached UI
        reads never call this method or mutate these diagnostic counters.
        """
        with self._lock:
            value = self._snapshot
            if value.state is LayerJournalState.UNSUPPORTED:
                return None
            attempts = value.receipt_attempts + 1
            try:
                if not isinstance(identity, (DensityLayerIdentity, SweepLayerIdentity)):
                    raise ValueError("layer frame identity is not qualified")
                scope = value.scope
                if (scope is None or scope.clock_scope_id != bridge.clock_scope_id
                        or scope.host_process_id != bridge.host_process_id
                        or identity.source_id != scope.source_id or identity.receiver_id != scope.receiver_id):
                    raise ValueError("layer reference has foreign clock/source/receiver scope")
                event = None if raw_ref is None else self._event(raw_ref)
                if (value.state not in (LayerJournalState.ACTIVE, LayerJournalState.FINAL)
                        or event not in value.events):
                    self._snapshot = replace(value, receipt_attempts=attempts, unmatched_refs=value.unmatched_refs + 1)
                    return None
                assert event is not None
                if isinstance(identity, DensityLayerIdentity):
                    if (not isinstance(scope, OwnerJournalScope) or event.kind is not LayerReadyKind.DENSITY
                            or identity.accumulation_id != scope.session_id
                            or identity.acquisition_epoch != scope.acquisition_epoch
                            or identity.config_generation != scope.configuration_generation
                            or (identity.config_generation, identity.update_sequence, identity.source_frame_sequence,
                                identity.native_accumulation_sequence) != (event.config_generation,
                                event.update_sequence, event.source_frame_sequence, event.accumulation_sequence)):
                        raise ValueError("density original creation differs from application frame")
                elif (event.kind is LayerReadyKind.DENSITY or identity.epoch != scope.acquisition_epoch
                        or (identity.epoch, identity.line_sequence, identity.revision or 0) !=
                            (event.sweep_epoch, event.line_sequence, event.revision)):
                    raise ValueError("Sweep original creation differs from application frame")
                mapping, bounds = bridge.map_native_clock(raw_ref)
                result = LayerReadyReceipt(event.kind, identity, bridge.clock_scope_id, bridge.host_process_id,
                    event.producer_instance_id, event.creation_sequence, event.ready_native_ns,
                    mapping, bounds, scope.owner_run_id, scope.session_id)
                if _scalar_bytes(result) > self._receipt_budget:
                    # Measurement stays valid. Oversized diagnostic text or
                    # future Sweep coverage cannot borrow another pool's bytes.
                    raise ValueError("layer receipt exceeds admitted scalar window")
                self._snapshot = replace(value, receipt_attempts=attempts, matched_refs=value.matched_refs + 1)
                return result
            except Exception:  # noqa: BLE001 - retain unqualified measurement, never fabricate readiness.
                self._snapshot = replace(value, receipt_attempts=attempts, refused_refs=value.refused_refs + 1)
                return None

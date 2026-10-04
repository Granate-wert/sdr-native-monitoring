"""One worker-side scalar consumer; cached reads never touch hardware/native.

Native counters cover all native offers. Retained events are a finite window,
not a complete downstream disposition ledger or ready-to-paint measurement.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from itertools import islice
import sys
import threading
from typing import Any

from ..domain.analytical_journal import (
    JournalCounters, JournalEvent, JournalEventKind, JournalState, OwnerJournalScope, OwnerJournalSnapshot,
)

EVENT_CAPACITY = 4096
BATCH_CAPACITY = 256
TERMINAL_HISTORY_CAPACITY = 4
HOST_SCALAR_BUDGET = 1_048_576  # per chain; paired reservation is twice this, not twice native ceilings


def owner_journal_capacity(native: object, backend: str = "cpu") -> int:
    """Pure protocol gate, no SDK/capability probe/backend substitution.

The current protocol1 factory admits CPU/Auto journals; forced vendor paths
stay unchanged and unsupported. Do not turn an unavailable vendor into CPU.
"""
    version = getattr(native, "OWNER_ANALYTICAL_READY_CONTRACT_VERSION", None)
    ready = getattr(native, "ANALYTICAL_READY_CONTRACT_VERSION", None)
    return EVENT_CAPACITY if (type(version) is int and version == 1 and type(ready) is int and ready == 1
        and backend in ("cpu", "auto") and getattr(native, "AnalyticalReadyEventKind", None) is not None) else 0


def _scalar_bytes(value: object) -> int:
    # Conservative duplicate accounting for scalar Python objects. Not allocator
    # overhead, native SDK allocations, externally retained snapshots or RSS.
    total = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        total += sum(_scalar_bytes(getattr(value, item.name)) for item in fields(value))
    elif isinstance(value, tuple):
        total += sum(_scalar_bytes(item) for item in value)
    return total


class NativeOwnerJournal:
    def __init__(self, native: object, capacity: int = 0) -> None:
        if type(capacity) is not int or capacity not in (0, EVENT_CAPACITY):
            raise ValueError("owner journal requires its fixed admitted capacity")
        if capacity and owner_journal_capacity(native) != capacity:
            raise ValueError("owner journal protocol not supported")
        self._native, self._capacity = native, capacity
        self._lock = threading.RLock()
        self._snapshot = OwnerJournalSnapshot()
        self._history: deque[OwnerJournalSnapshot] = deque(maxlen=TERMINAL_HISTORY_CAPACITY)
        self._last_event = self._missing_events = 0
        self._failed = False
        self._history_evictions = 0

    @property
    def enabled(self) -> bool:
        return self._capacity != 0

    def current(self) -> OwnerJournalSnapshot:
        with self._lock:
            return self._snapshot

    def terminal_history(self) -> tuple[OwnerJournalSnapshot, ...]:
        with self._lock:
            return tuple(self._history)

    def _archive(self) -> None:
        if len(self._history) == TERMINAL_HISTORY_CAPACITY:
            self._history_evictions += 1
        self._history.append(self._snapshot)

    def prepare(self, *, native: object | None = None, capacity: int | None = None) -> None:
        """New actual Start attempt, before owner creation; no counters are fabricated."""
        with self._lock:
            selected_native = self._native if native is None else native
            selected = self._capacity if capacity is None else capacity
            if type(selected) is not int or selected not in (0, EVENT_CAPACITY) or (
                    selected and owner_journal_capacity(selected_native) != selected):
                raise ValueError("owner journal capacity/protocol was not admitted")
            if self._snapshot.scope is not None or self._snapshot.state is not JournalState.UNSUPPORTED:
                if not self._snapshot.native_stop_confirmed:
                    raise ValueError("previous owner journal has no confirmed native Stop")
                self._archive()
            self._native, self._capacity = selected_native, selected
            self._snapshot = OwnerJournalSnapshot(
                state=JournalState.ACTIVE if self.enabled else JournalState.UNSUPPORTED,
                terminal_windows_evicted=self._history_evictions)
            self._last_event = self._missing_events = 0
            self._failed = False

    def begin(self, scope: OwnerJournalScope, *, capacity: int | None = None) -> None:
        with self._lock:
            selected = self._capacity if capacity is None else capacity
            if type(selected) is not int or selected not in (0, EVENT_CAPACITY) or (
                    selected and owner_journal_capacity(self._native) != selected):
                raise ValueError("owner journal capacity/protocol was not admitted")
            if self._snapshot.scope is not None:
                if not self._snapshot.native_stop_confirmed:
                    raise ValueError("previous owner journal has no confirmed native Stop")
                self._archive()
            self._capacity = selected
            self._snapshot = OwnerJournalSnapshot(scope=scope,
                state=JournalState.ACTIVE if self.enabled else JournalState.UNSUPPORTED,
                terminal_windows_evicted=self._history_evictions)
            self._last_event = self._missing_events = 0
            self._failed = False

    def _convert(self, batch: Any) -> tuple[JournalCounters, tuple[JournalEvent, ...], int]:
        raw = batch.summary
        if raw.supported is not True:
            raise ValueError("admitted native owner lost journal support")
        counters = JournalCounters(**{item.name: getattr(raw, item.name) for item in fields(JournalCounters)})
        if counters.event_capacity != self._capacity:
            raise ValueError("actual owner journal capacity differs from admitted request")
        previous = self._snapshot.counters
        if previous is not None:
            if previous.producer_instance_id != counters.producer_instance_id:
                raise ValueError("owner journal producer changed without a new Start")
            for name in ("offered", "handed_off", "producer_superseded", "producer_cancelled", "clock_regressions",
                         "events_generated", "events_drained", "events_lost"):
                if getattr(counters, name) < getattr(previous, name):
                    raise ValueError("owner journal lifetime counters regressed")
        raw_events = tuple(islice(iter(batch.events), BATCH_CAPACITY + 1))
        if len(raw_events) > BATCH_CAPACITY or counters.events_drained - (previous.events_drained if previous else 0) != len(raw_events):
            raise ValueError("owner journal has a competing consumer or oversized batch")
        kinds = getattr(self._native, "AnalyticalReadyEventKind")
        mapping = {kinds.Offered: JournalEventKind.OFFERED, kinds.HandedOff: JournalEventKind.HANDED_OFF,
            kinds.ProducerSuperseded: JournalEventKind.SUPERSEDED, kinds.ProducerCancelled: JournalEventKind.CANCELLED}
        clock = getattr(self._native, "AnalyticalReadyClock")
        states = getattr(self._native, "AnalyticalReadyClockState")
        scope = self._snapshot.scope
        if scope is None:
            raise ValueError("journal drain has no admitted owner scope")
        events = []
        last, missing = self._last_event, self._missing_events
        for raw_event in raw_events:
            ref = raw_event.ref
            if (ref.clock != clock.NativeSteady or ref.clock_state not in (states.Monotonic, states.Regressed)
                    or raw_event.kind not in mapping):
                raise ValueError("owner journal clock/kind contract differs")
            event = JournalEvent(raw_event.event_sequence, ref.producer_instance_id, ref.offer_sequence,
                ref.config_generation, ref.ready_native_ns, ref.clock_state == states.Regressed, mapping[raw_event.kind])
            if (event.producer_instance_id != counters.producer_instance_id
                    or event.configuration_generation != scope.configuration_generation
                    or event.offer_sequence > counters.offered or not last < event.event_sequence <= counters.events_generated):
                raise ValueError("owner journal has a stale/foreign/duplicate event")
            missing += event.event_sequence - last - 1
            last = event.event_sequence
            events.append(event)
        if missing > counters.events_lost:
            raise ValueError("owner journal gaps exceed declared evidence loss")
        return counters, tuple(events), missing

    def drain(self, reader: Callable[[int], object]) -> None:
        with self._lock:
            if not self.enabled or self._failed:
                return
            try:
                counters, events, missing = self._convert(reader(BATCH_CAPACITY))
                candidate = replace(self._snapshot, counters=counters,
                    events=events if events else self._snapshot.events,
                    host_window_events_evicted=self._snapshot.host_window_events_evicted +
                        (len(self._snapshot.events) if events else 0))
                # Includes old/new host snapshots and retained terminal windows;
                # allow bounded raw proxy payload while converting one native batch.
                if (_scalar_bytes(candidate) + _scalar_bytes(self._snapshot)
                        + sum(_scalar_bytes(item) for item in self._history) + BATCH_CAPACITY * 512 > HOST_SCALAR_BUDGET):
                    raise ValueError("owner journal exceeds host scalar reservation")
                self._snapshot = candidate
                self._missing_events = missing
                if events:
                    self._last_event = events[-1].event_sequence
            except Exception:  # noqa: BLE001 - telemetry failure must not alter RF/lifecycle or claim coverage.
                self._failed = True
                self._snapshot = replace(self._snapshot, state=JournalState.INCOMPLETE,
                    drain_failures=self._snapshot.drain_failures + 1)

    def finish(self, reader: Callable[[int], object]) -> None:
        """After confirmed native Stop/join, BEFORE control release. No SDK calls."""
        # A stopped finite ring requires at most16 batches. No unbounded retry.
        for _ in range(EVENT_CAPACITY // BATCH_CAPACITY):
            self.drain(reader)
            value = self.current()
            if not self.enabled or self._failed or (value.counters is not None and value.counters.events_pending == 0):
                break
        with self._lock:
            value = self._snapshot
            state = JournalState.UNSUPPORTED if not self.enabled else JournalState.INCOMPLETE
            if (self.enabled and not self._failed and value.counters is not None
                    and value.counters.events_pending == 0 and value.counters.outstanding == 0):
                state = JournalState.FINAL
            self._snapshot = replace(value, state=state, native_stop_confirmed=True)

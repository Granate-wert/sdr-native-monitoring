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
    AdapterDispositionCounters, AdapterPacketDisposition, JournalCounters, JournalEvent,
    JournalEventKind, JournalState, NativePresentationCounters, OwnerJournalScope, OwnerJournalSnapshot,
)
from .owner_event_audit import OwnerEventAudit

EVENT_CAPACITY = 4096
BATCH_CAPACITY = 256
TERMINAL_HISTORY_CAPACITY = 4
HOST_SCALAR_BUDGET = 1_048_576  # per chain; paired reservation is twice this, not twice native ceilings


def discard_terminal_owner_presentation(native: object, owner: object) -> int | None:
    """SAME confirmed stopped owner, never a poll/SDK read. Legacy is unknown.

    Count is queued frames/pairs, not all FFTs. Native guards enforce terminal
    lifecycle; a malformed receipt cannot be substituted with a synthetic zero.
    """
    version = getattr(native, "OWNER_PRESENTATION_RELEASE_CONTRACT_VERSION", None)
    if version is None:
        return None
    if type(version) is not int or version != 1:
        raise ValueError("unsupported owner presentation release contract")
    discard = getattr(owner, "discard_terminal_spectrum_frames", None)
    if not callable(discard):
        raise ValueError("admitted native owner has no terminal presentation release")
    result = discard()
    if type(result) is not int or not 0 <= result < (1 << 64):
        raise ValueError("invalid terminal presentation release receipt")
    return result


def owner_journal_capacity(native: object, backend: str = "cpu") -> int:
    """Pure protocol gate, no SDK/capability probe/backend substitution.

Known protocol1/2 factories admit CPU/Auto journals; forced vendor paths
stay unchanged and unsupported. Do not turn an unavailable vendor into CPU.
"""
    version = getattr(native, "OWNER_ANALYTICAL_READY_CONTRACT_VERSION", None)
    ready = getattr(native, "ANALYTICAL_READY_CONTRACT_VERSION", None)
    kinds = getattr(native, "AnalyticalReadyEventKind", None)
    if (type(version) is not int or version not in (1, 2) or type(ready) is not int or ready != 1
            or backend not in ("cpu", "auto") or kinds is None):
        return 0
    presentation_version = getattr(native, "OWNER_PRESENTATION_DISPOSITION_CONTRACT_VERSION", None)
    if version == 2 and (type(presentation_version) is not int or presentation_version != 1
            or any(not hasattr(kinds, name) for name in (
                "OwnerForwarded", "OwnerSuperseded", "OwnerCoalesced", "OwnerCancelled", "OwnerCadenceSuppressed"))):
        return 0
    return EVENT_CAPACITY


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
    def __init__(self, native: object, capacity: int = 0, *, reserved_host_bytes: int = 0) -> None:
        if type(capacity) is not int or capacity not in (0, EVENT_CAPACITY):
            raise ValueError("owner journal requires its fixed admitted capacity")
        if capacity and owner_journal_capacity(native) != capacity:
            raise ValueError("owner journal protocol not supported")
        if type(reserved_host_bytes) is not int or not 0 <= reserved_host_bytes <= HOST_SCALAR_BUDGET // 4:
            raise ValueError("shared host scalar reservation is out of bounds")
        self._host_budget = HOST_SCALAR_BUDGET - reserved_host_bytes
        self._native, self._capacity = native, capacity
        self._lock = threading.RLock()
        self._snapshot = OwnerJournalSnapshot()
        self._history: deque[OwnerJournalSnapshot] = deque(maxlen=TERMINAL_HISTORY_CAPACITY)
        self._last_event = self._missing_events = 0
        self._failed = False
        self._history_evictions = 0
        self._id_audit = OwnerEventAudit()

    @property
    def enabled(self) -> bool:
        return self._capacity != 0

    def current(self) -> OwnerJournalSnapshot:
        with self._lock:
            return self._snapshot

    def terminal_history(self) -> tuple[OwnerJournalSnapshot, ...]:
        with self._lock:
            return tuple(self._history)

    def observe_adapter_result(self, frame: object, coalesced: int,
                               disposition: AdapterPacketDisposition, *,
                               expected_scope: OwnerJournalScope | None) -> None:
        """Once per actual latest-drain result AFTER its publication decision.

        No native/SDK call or perFFT callback. Telemetry refusal must never
        alter acquisition. A foreign/retired lifetime is not assigned to the
        current owner; missing identity is explicitly unqualified, not zero.
        """
        with self._lock:
            value = self._snapshot
            if (not self.enabled or expected_scope is None or value.scope is not expected_scope
                    or value.native_stop_confirmed):
                return
            old = value.adapter or AdapterDispositionCounters()
            try:
                if type(coalesced) is not int or not 0 <= coalesced < (1 << 64) - 1:
                    raise ValueError("invalid actual adapter batch size")
                if not isinstance(disposition, AdapterPacketDisposition):
                    raise ValueError("adapter outcome must be typed")
                ref = getattr(frame, "analytical_ready", None)
                counters = value.counters
                if (value.state is not JournalState.ACTIVE or counters is None or ref is None):
                    updated = replace(old, batches=old.batches + 1,
                        unqualified_batches=old.unqualified_batches + 1,
                        unqualified_packets=old.unqualified_packets + coalesced + 1)
                else:
                    clock = getattr(self._native, "AnalyticalReadyClock")
                    states = getattr(self._native, "AnalyticalReadyClockState")
                    if (type(ref.producer_instance_id) is not int
                            or ref.producer_instance_id != counters.producer_instance_id
                            or type(ref.config_generation) is not int
                            or ref.config_generation != expected_scope.configuration_generation
                            or type(ref.offer_sequence) is not int
                            or not old.last_offer_sequence < ref.offer_sequence <= counters.offered
                            or ref.clock != clock.NativeSteady
                            or ref.clock_state not in (states.Monotonic, states.Regressed)):
                        raise ValueError("foreign/stale adapter producer receipt")
                    name = {AdapterPacketDisposition.PUBLISHED: "published_packets",
                            AdapterPacketDisposition.REJECTED: "rejected_packets",
                            AdapterPacketDisposition.CANCELLED: "cancelled_packets"}[disposition]
                    updated = replace(old, batches=old.batches + 1,
                        coalesced_packets=old.coalesced_packets + coalesced,
                        last_offer_sequence=ref.offer_sequence, last_ready_native_ns=ref.ready_native_ns,
                        **{name: getattr(old, name) + 1})
                candidate = replace(value, adapter=updated)
                if (_scalar_bytes(candidate) + _scalar_bytes(value)
                        + sum(_scalar_bytes(item) for item in self._history)
                        + _scalar_bytes(self._id_audit.scalar_payload)
                        + self._id_audit.index_storage_bytes
                        + BATCH_CAPACITY * 512 > self._host_budget):
                    raise ValueError("adapter accounting exceeds existing host scalar reservation")
                self._snapshot = candidate
            except Exception:  # noqa: BLE001 - evidence failure is NOT a hardware failure.
                self._snapshot = replace(value, adapter=replace(old, binding_failures=old.binding_failures + 1))

    def _archive(self) -> None:
        if len(self._history) == TERMINAL_HISTORY_CAPACITY:
            self._history_evictions += 1
        self._history.append(self._snapshot)

    def prepare(self, *, native: object | None = None, capacity: int | None = None,
                reserved_host_bytes: int | None = None) -> None:
        """New actual Start attempt, before owner creation; no counters are fabricated."""
        with self._lock:
            selected_native = self._native if native is None else native
            selected = self._capacity if capacity is None else capacity
            if type(selected) is not int or selected not in (0, EVENT_CAPACITY) or (
                    selected and owner_journal_capacity(selected_native) != selected):
                raise ValueError("owner journal capacity/protocol was not admitted")
            if reserved_host_bytes is not None and (type(reserved_host_bytes) is not int
                    or not 0 <= reserved_host_bytes <= HOST_SCALAR_BUDGET // 4):
                raise ValueError("shared host scalar reservation is out of bounds")
            if self._snapshot.scope is not None or self._snapshot.state is not JournalState.UNSUPPORTED:
                if not self._snapshot.native_stop_confirmed:
                    raise ValueError("previous owner journal has no confirmed native Stop")
                self._archive()
            self._native, self._capacity = selected_native, selected
            if reserved_host_bytes is not None:
                # Snapshot's 1MiB remains the TOTAL per-chain host reservation,
                # including the layer slice, not another independent journal pool.
                self._host_budget = HOST_SCALAR_BUDGET - reserved_host_bytes
            self._snapshot = OwnerJournalSnapshot(
                state=JournalState.ACTIVE if self.enabled else JournalState.UNSUPPORTED,
                terminal_windows_evicted=self._history_evictions)
            self._last_event = self._missing_events = 0
            self._failed = False
            self._id_audit = OwnerEventAudit()

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
            self._id_audit = OwnerEventAudit()

    def _convert(self, batch: Any) -> tuple[JournalCounters, tuple[JournalEvent, ...], int, NativePresentationCounters | None]:
        raw = batch.summary
        if raw.supported is not True:
            raise ValueError("admitted native owner lost journal support")
        event_version = getattr(self._native, "OWNER_ANALYTICAL_READY_CONTRACT_VERSION")
        if type(event_version) is not int or event_version not in (1, 2):
            raise ValueError("owner event protocol differs")
        counters = JournalCounters(**{item.name: getattr(raw, item.name) for item in fields(JournalCounters)
            if item.name not in ("event_contract_version", "owner_disposition_events")},
            event_contract_version=event_version,
            owner_disposition_events=getattr(raw, "owner_disposition_events") if event_version == 2 else 0)
        presentation = None
        version = getattr(self._native, "OWNER_PRESENTATION_DISPOSITION_CONTRACT_VERSION", None)
        if event_version == 2 and version is None:
            raise ValueError("owner event protocol lacks its disposition contract")
        if version is not None:
            if type(version) is not int or version != 1:
                raise ValueError("native owner presentation protocol differs")
            actual = raw.presentation
            if type(actual.supported) is not bool:
                raise ValueError("native owner presentation support must be typed")
            if actual.supported:
                presentation = NativePresentationCounters(**{
                    item.name: getattr(actual, item.name) for item in fields(NativePresentationCounters)})
                if presentation.classified_handoffs > counters.handed_off:
                    raise ValueError("native owner decisions exceed actual DSP handoffs")
            elif any(type(getattr(actual, item.name)) is not int or getattr(actual, item.name) != 0
                     for item in fields(NativePresentationCounters)):
                raise ValueError("unsupported native owner claims disposition counters")
            if event_version == 2 and counters.owner_disposition_events != (
                    presentation.classified_handoffs if presentation is not None else 0):
                raise ValueError("owner event count differs from actual dispositions")
            old_presentation = self._snapshot.native_presentation
            if old_presentation is not None:
                if presentation is None or any(getattr(presentation, item.name) < getattr(old_presentation, item.name)
                        for item in fields(NativePresentationCounters)):
                    raise ValueError("native owner presentation lifetime counters regressed")
        if counters.event_capacity != self._capacity:
            raise ValueError("actual owner journal capacity differs from admitted request")
        previous = self._snapshot.counters
        if previous is not None:
            if (previous.producer_instance_id != counters.producer_instance_id
                    or previous.event_contract_version != counters.event_contract_version):
                raise ValueError("owner journal producer changed without a new Start")
            for name in ("offered", "handed_off", "producer_superseded", "producer_cancelled", "clock_regressions",
                         "events_generated", "events_drained", "events_lost", "owner_disposition_events"):
                if getattr(counters, name) < getattr(previous, name):
                    raise ValueError("owner journal lifetime counters regressed")
        raw_events = tuple(islice(iter(batch.events), BATCH_CAPACITY + 1))
        if len(raw_events) > BATCH_CAPACITY or counters.events_drained - (previous.events_drained if previous else 0) != len(raw_events):
            raise ValueError("owner journal has a competing consumer or oversized batch")
        kinds = getattr(self._native, "AnalyticalReadyEventKind")
        mapping = {kinds.Offered: JournalEventKind.OFFERED, kinds.HandedOff: JournalEventKind.HANDED_OFF,
            kinds.ProducerSuperseded: JournalEventKind.SUPERSEDED, kinds.ProducerCancelled: JournalEventKind.CANCELLED}
        if event_version == 2:
            mapping.update({kinds.OwnerForwarded: JournalEventKind.OWNER_FORWARDED,
                kinds.OwnerSuperseded: JournalEventKind.OWNER_SUPERSEDED,
                kinds.OwnerCoalesced: JournalEventKind.OWNER_COALESCED,
                kinds.OwnerCancelled: JournalEventKind.OWNER_CANCELLED,
                kinds.OwnerCadenceSuppressed: JournalEventKind.OWNER_CADENCE_SUPPRESSED})
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
        return counters, tuple(events), missing, presentation

    def drain(self, reader: Callable[[int], object]) -> None:
        with self._lock:
            if not self.enabled or self._failed:
                return
            try:
                counters, events, missing, presentation = self._convert(reader(BATCH_CAPACITY))
                if counters.event_contract_version == 2 and presentation is not None:
                    self._id_audit.consume(events)
                evicted = self._snapshot.host_window_events_evicted + (
                    len(self._snapshot.events) if events else 0)
                candidate = replace(self._snapshot, counters=counters, native_presentation=presentation,
                    events=events if events else self._snapshot.events,
                    host_window_events_evicted=evicted,
                    owner_id_coverage=self._id_audit.snapshot(counters, presentation, host_evictions=evicted))
                # Includes old/new host snapshots and retained terminal windows;
                # allow bounded raw proxy payload while converting one native batch.
                if (_scalar_bytes(candidate) + _scalar_bytes(self._snapshot)
                        + sum(_scalar_bytes(item) for item in self._history)
                        + _scalar_bytes(self._id_audit.scalar_payload) + self._id_audit.index_storage_bytes
                        + BATCH_CAPACITY * 512 > self._host_budget):
                    raise ValueError("owner journal exceeds host scalar reservation")
                self._snapshot = candidate
                self._missing_events = missing
                if events:
                    self._last_event = events[-1].event_sequence
            except Exception:  # noqa: BLE001 - telemetry failure must not alter RF/lifecycle or claim coverage.
                self._failed = True
                self._snapshot = replace(self._snapshot, state=JournalState.INCOMPLETE,
                    drain_failures=self._snapshot.drain_failures + 1,
                    owner_id_coverage=self._id_audit.snapshot(
                        self._snapshot.counters, self._snapshot.native_presentation,
                        evidence_failed=True))

    def finish(self, reader: Callable[[int], object], *, before_capture: Callable[[], object] | None = None) -> None:
        """After confirmed native Stop/join, BEFORE control release. No SDK calls."""
        release_failed = False
        if before_capture is not None:
            try:
                before_capture()
            except Exception:  # noqa: BLE001 - retain truthful incomplete evidence, do not reopen hardware.
                release_failed = True
        # A stopped finite ring requires at most16 batches. No unbounded retry.
        for _ in range(EVENT_CAPACITY // BATCH_CAPACITY):
            self.drain(reader)
            value = self.current()
            if not self.enabled or self._failed or (value.counters is not None and value.counters.events_pending == 0):
                break
        with self._lock:
            value = self._snapshot
            state = JournalState.UNSUPPORTED if not self.enabled else JournalState.INCOMPLETE
            release_failed = release_failed or value.native_presentation_release_failed
            if (self.enabled and not self._failed and not release_failed and value.counters is not None
                    and value.counters.events_pending == 0 and value.counters.outstanding == 0):
                state = JournalState.FINAL
            self._snapshot = replace(value, state=state, native_stop_confirmed=True,
                native_presentation_release_failed=release_failed,
                owner_id_coverage=self._id_audit.snapshot(value.counters, value.native_presentation,
                    stopped=True, host_evictions=value.host_window_events_evicted,
                    evidence_failed=self._failed or release_failed))

"""Bounded original-ref custody validation, no hardware, arrays or ID inference.

This finite audit never evicts an offer to pretend a later complete lifetime.
Loss, window eviction, overflow, duplicate or mismatched evidence is incomplete.
Native scalar all-offers counters remain the denominator for larger runs.
"""
from __future__ import annotations

import sys

from ..domain.analytical_journal import (
    JournalCounters, JournalEvent, JournalEventKind, NativePresentationCounters,
    OwnerIdCoverage, OwnerIdCoverageState,
)

_OWNER_KINDS = frozenset((
    JournalEventKind.OWNER_FORWARDED, JournalEventKind.OWNER_SUPERSEDED,
    JournalEventKind.OWNER_COALESCED, JournalEventKind.OWNER_CANCELLED,
    JournalEventKind.OWNER_CADENCE_SUPPRESSED,
))


class OwnerEventAudit:
    def __init__(self) -> None:
        self._offers: dict[int, tuple[JournalEvent, JournalEventKind]] = {}
        self._failures = 0
        self._capacity_exceeded = False

    @property
    def scalar_payload(self) -> tuple[tuple[JournalEvent, JournalEventKind], ...]:
        # Include this retained state in the existing host reservation, not RSS.
        return tuple(self._offers.values())

    @property
    def index_storage_bytes(self) -> int:
        return sys.getsizeof(self._offers) + sys.getsizeof(self._failures) + sys.getsizeof(self._capacity_exceeded)

    def consume(self, events: tuple[JournalEvent, ...]) -> None:
        for event in events:
            previous = self._offers.get(event.offer_sequence)
            if event.kind is JournalEventKind.OFFERED:
                if previous is not None:
                    self._failures += 1
                elif len(self._offers) == 256:
                    self._capacity_exceeded = True
                else:
                    self._offers[event.offer_sequence] = (event, event.kind)
                continue
            if previous is None:
                self._failures += 1
                continue
            original, state = previous
            same_ref = (event.producer_instance_id == original.producer_instance_id
                and event.configuration_generation == original.configuration_generation
                and event.ready_native_ns == original.ready_native_ns
                and event.clock_regressed == original.clock_regressed)
            allowed = ((state is JournalEventKind.OFFERED and event.kind in (
                JournalEventKind.HANDED_OFF, JournalEventKind.SUPERSEDED, JournalEventKind.CANCELLED))
                or (state is JournalEventKind.HANDED_OFF and event.kind in _OWNER_KINDS))
            if not same_ref or not allowed:
                self._failures += 1
                continue  # Never settle this or another obligation from invalid evidence.
            self._offers[event.offer_sequence] = (original, event.kind)

    def snapshot(self, counters: JournalCounters | None, presentation: NativePresentationCounters | None, *,
                 stopped: bool = False, host_evictions: int = 0, evidence_failed: bool = False) -> OwnerIdCoverage:
        if counters is None or counters.event_contract_version != 2 or presentation is None:
            return OwnerIdCoverage()
        states = tuple(state for _, state in self._offers.values())
        retired = sum(state is not JournalEventKind.OFFERED for state in states)
        owner = sum(state in _OWNER_KINDS for state in states)
        state = OwnerIdCoverageState.ACTIVE
        incomplete = (self._failures or self._capacity_exceeded or counters.events_lost or host_evictions
                      or presentation.accounting_failures or evidence_failed)
        if stopped or incomplete:
            state = OwnerIdCoverageState.INCOMPLETE
        if stopped and not incomplete and (
                counters.outstanding == 0 and counters.events_pending == 0
                and len(states) == retired == counters.offered and owner == counters.handed_off
                and sum(item is JournalEventKind.SUPERSEDED for item in states) == counters.producer_superseded
                and sum(item is JournalEventKind.CANCELLED for item in states) == counters.producer_cancelled
                and owner == counters.owner_disposition_events == presentation.classified_handoffs
                and all(sum(item is kind for item in states) == getattr(presentation, name) for kind, name in (
                    (JournalEventKind.OWNER_FORWARDED, "forwarded"),
                    (JournalEventKind.OWNER_SUPERSEDED, "superseded"),
                    (JournalEventKind.OWNER_COALESCED, "coalesced"),
                    (JournalEventKind.OWNER_CANCELLED, "cancelled"),
                    (JournalEventKind.OWNER_CADENCE_SUPPRESSED, "cadence_suppressed")))):
            state = OwnerIdCoverageState.COMPLETE
        return OwnerIdCoverage(state, len(states), retired, owner, self._failures, self._capacity_exceeded)

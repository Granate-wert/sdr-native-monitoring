"""Bounded original-ref custody validation, no hardware, arrays or ID inference.

This finite audit never evicts an offer to pretend a later complete lifetime.
Loss, window eviction, overflow, duplicate or mismatched evidence is incomplete.
Native scalar all-offers counters remain the denominator for larger runs.
"""
from __future__ import annotations

from dataclasses import fields
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
        # Revision covers retained payload changes, not evidence qualification.
        # Counts replace repeated scans; counters/loss/terminal flags are still
        # validated on EVERY snapshot. No additional event roots are retained.
        self._revision = 0
        self._state_counts = dict.fromkeys(JournalEventKind, 0)
        self._payload_cacheable = True
        self._entry_bytes = 0
        self._payload_tuple_bytes = sys.getsizeof(())

    @property
    def payload_revision(self) -> int:
        return self._revision

    @property
    def payload_cacheable(self) -> bool:
        # Exact JournalEvent validates all fields as immutable scalar values.
        # Subclasses/arbitrary objects remain an oracle fallback for this audit
        # lifetime; do not retain a second event graph or rewalk every batch.
        return self._payload_cacheable

    @property
    def scalar_payload_bytes(self) -> int | None:
        # Exact scalar-only charge of the original scalar_payload tuple, without
        # retaining that tuple. Unknown graphs MUST use the recursive oracle.
        return (self._payload_tuple_bytes + self._entry_bytes
            if self._payload_cacheable else None)

    def _retain(self, original: JournalEvent, state: JournalEventKind,
                previous_state: JournalEventKind | None = None) -> None:
        self._offers[original.offer_sequence] = (original, state)
        self._payload_cacheable = (self._payload_cacheable
            and type(original) is JournalEvent and type(state) is JournalEventKind)
        if self._payload_cacheable:
            if previous_state is None:
                # JournalEvent.__post_init__ admits only immutable scalar
                # fields. Size once per original retained offer, NOT per poll
                # or transition. Tuple size is measured, never ABI-inferred.
                self._entry_bytes += (sys.getsizeof((original, state)) + sys.getsizeof(original)
                    + sum(sys.getsizeof(getattr(original, item.name)) for item in fields(original))
                    + sys.getsizeof(state))
                self._payload_tuple_bytes = sys.getsizeof(tuple(self._offers.values()))
            else:
                self._entry_bytes += sys.getsizeof(state) - sys.getsizeof(previous_state)
        if previous_state is not None:
            self._state_counts[previous_state] -= 1
        self._state_counts[state] += 1
        self._revision += 1

    @property
    def scalar_payload(self) -> tuple[tuple[JournalEvent, JournalEventKind], ...]:
        # Include this retained state in the existing host reservation, not RSS.
        return tuple(self._offers.values())

    @property
    def index_storage_bytes(self) -> int:
        # Original index charge plus conservative ADDITIONAL scalar metadata:
        # dict storage, enum keys/int widths, revision, instance storage/names.
        return (sys.getsizeof(self._offers) + sys.getsizeof(self._failures)
            + sys.getsizeof(self._capacity_exceeded) + sys.getsizeof(self._revision)
            + sys.getsizeof(self._payload_cacheable) + sys.getsizeof("_payload_cacheable")
            + sys.getsizeof(self._entry_bytes) + sys.getsizeof("_entry_bytes")
            + sys.getsizeof(self._payload_tuple_bytes) + sys.getsizeof("_payload_tuple_bytes")
            + sys.getsizeof(self._state_counts)
            + sum(sys.getsizeof(kind) + sys.getsizeof(count)
                  for kind, count in self._state_counts.items())
            + sys.getsizeof(self.__dict__) + sys.getsizeof("_revision")
            + sys.getsizeof("_state_counts"))

    def consume(self, events: tuple[JournalEvent, ...]) -> None:
        for event in events:
            previous = self._offers.get(event.offer_sequence)
            if event.kind is JournalEventKind.OFFERED:
                if previous is not None:
                    self._failures += 1
                elif len(self._offers) == 256:
                    self._capacity_exceeded = True
                else:
                    self._retain(event, event.kind)
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
            self._retain(original, event.kind, state)

    def snapshot(self, counters: JournalCounters | None, presentation: NativePresentationCounters | None, *,
                 stopped: bool = False, host_evictions: int = 0, evidence_failed: bool = False) -> OwnerIdCoverage:
        if counters is None or counters.event_contract_version != 2 or presentation is None:
            return OwnerIdCoverage()
        retained = len(self._offers)
        counts = self._state_counts
        retired = retained - counts[JournalEventKind.OFFERED]
        owner = sum(counts[kind] for kind in _OWNER_KINDS)
        if not self._payload_cacheable:
            # Preserve the original identity-vs-membership semantics even for
            # unsupported callers/subclasses (e.g. string enum aliases).
            states = tuple(state for _, state in self._offers.values())
            counts = {kind: sum(item is kind for item in states) for kind in JournalEventKind}
            retired = sum(item is not JournalEventKind.OFFERED for item in states)
            owner = sum(item in _OWNER_KINDS for item in states)
        state = OwnerIdCoverageState.ACTIVE
        incomplete = (self._failures or self._capacity_exceeded or counters.events_lost or host_evictions
                      or presentation.accounting_failures or evidence_failed)
        if stopped or incomplete:
            state = OwnerIdCoverageState.INCOMPLETE
        if stopped and not incomplete and (
                counters.outstanding == 0 and counters.events_pending == 0
                and retained == retired == counters.offered and owner == counters.handed_off
                and counts[JournalEventKind.SUPERSEDED] == counters.producer_superseded
                and counts[JournalEventKind.CANCELLED] == counters.producer_cancelled
                and owner == counters.owner_disposition_events == presentation.classified_handoffs
                and all(counts[kind] == getattr(presentation, name) for kind, name in (
                    (JournalEventKind.OWNER_FORWARDED, "forwarded"),
                    (JournalEventKind.OWNER_SUPERSEDED, "superseded"),
                    (JournalEventKind.OWNER_COALESCED, "coalesced"),
                    (JournalEventKind.OWNER_CANCELLED, "cancelled"),
                    (JournalEventKind.OWNER_CADENCE_SUPPRESSED, "cadence_suppressed")))):
            state = OwnerIdCoverageState.COMPLETE
        return OwnerIdCoverage(state, retained, retired, owner, self._failures, self._capacity_exceeded)

"""Bounded ephemeral owner journal evidence, NOT a pane/paint obligation ledger."""
from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum

from .analytical_ready import _integer


class JournalState(StrEnum):
    UNSUPPORTED = "unsupported"
    ACTIVE = "active"
    FINAL = "native_terminal"
    INCOMPLETE = "incomplete_evidence"


class JournalEventKind(StrEnum):
    OFFERED = "offered"
    HANDED_OFF = "native_output_handed_off"
    SUPERSEDED = "native_output_superseded"
    CANCELLED = "native_output_cancelled"
    OWNER_FORWARDED = "owner_forwarded"
    OWNER_SUPERSEDED = "owner_superseded"
    OWNER_COALESCED = "owner_coalesced"
    OWNER_CANCELLED = "owner_cancelled"
    OWNER_CADENCE_SUPPRESSED = "owner_cadence_suppressed"


class AdapterPacketDisposition(StrEnum):
    PUBLISHED = "adapter_snapshot_published"
    REJECTED = "adapter_packet_rejected"
    CANCELLED = "adapter_cancelled_before_publication"


class OwnerIdCoverageState(StrEnum):
    UNSUPPORTED = "unsupported"
    ACTIVE = "active_bounded_window"
    INCOMPLETE = "incomplete_id_evidence"
    COMPLETE = "complete_native_owner_ids"


@dataclass(frozen=True, slots=True)
class OwnerIdCoverage:
    """Native owner custody ONLY; never adapter/pane/paint or RF coverage."""

    state: OwnerIdCoverageState = OwnerIdCoverageState.UNSUPPORTED
    retained_offers: int = 0
    retired_offers: int = 0
    owner_dispositions: int = 0
    transition_failures: int = 0
    capacity_exceeded: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, OwnerIdCoverageState) or type(self.capacity_exceeded) is not bool:
            raise ValueError("owner ID coverage must be typed")
        for name in ("retained_offers", "retired_offers", "owner_dispositions", "transition_failures"):
            _integer(getattr(self, name), name, 0, (1 << 64) - 1)
        if (self.retained_offers > 256 or self.retired_offers > self.retained_offers
                or self.owner_dispositions > self.retired_offers):
            raise ValueError("owner ID custody exceeds bounded offers")
        if self.state is OwnerIdCoverageState.COMPLETE and (self.transition_failures or self.capacity_exceeded):
            raise ValueError("incomplete owner ID evidence cannot be complete")


@dataclass(frozen=True, slots=True)
class NativePresentationCounters:
    """Actual native-owner decisions, scalar totals, NOT per-ID/paint coverage.

    Unclassified handoffs include frames still queued/in flight and exceptional
    paths without a decision. Never fill that residual from sequence gaps.
    Native coalescing and adapter batch coalescing describe the SAME discarded
    packets, not two separate losses. Pair members remain separate producers.
    """

    forwarded: int = 0
    superseded: int = 0
    coalesced: int = 0
    cancelled: int = 0
    cadence_suppressed: int = 0
    accounting_failures: int = 0

    def __post_init__(self) -> None:
        for item in fields(self):
            _integer(getattr(self, item.name), item.name, 0, (1 << 64) - 1)
        _integer(self.classified_handoffs, "native owner classified", 0, (1 << 64) - 1)

    @property
    def classified_handoffs(self) -> int:
        return self.forwarded + self.superseded + self.coalesced + self.cancelled + self.cadence_suppressed


@dataclass(frozen=True, slots=True)
class AdapterDispositionCounters:
    """Actual latest-drain packets, not pane obligations, paints or RF loss.

    Coalesced packets have SAME owner batch attribution but no individual
    retained offer identity. Do not infer their offer IDs from sequence gaps.
    The enclosing journal's handed_off/offered remain the ALL-offers denominator.
    """

    batches: int = 0
    coalesced_packets: int = 0
    published_packets: int = 0
    rejected_packets: int = 0
    cancelled_packets: int = 0
    unqualified_batches: int = 0
    unqualified_packets: int = 0
    binding_failures: int = 0
    last_offer_sequence: int = 0
    last_ready_native_ns: int | None = None

    def __post_init__(self) -> None:
        for item in fields(self):
            if item.name != "last_ready_native_ns":
                _integer(getattr(self, item.name), item.name, 0, (1 << 64) - 1)
        if self.last_ready_native_ns is not None:
            _integer(self.last_ready_native_ns, "last_ready_native_ns", -(1 << 63), (1 << 63) - 1)
        latest = self.published_packets + self.rejected_packets + self.cancelled_packets
        _integer(self.qualified_drained_packets + self.unqualified_packets,
                 "adapter total packets", 0, (1 << 64) - 1)
        if (self.batches != self.published_packets + self.rejected_packets + self.cancelled_packets
                + self.unqualified_batches or self.unqualified_packets < self.unqualified_batches
                or (self.last_offer_sequence == 0) != (self.last_ready_native_ns is None)
                or (latest == 0) != (self.last_offer_sequence == 0)):
            raise ValueError("adapter packet conservation/last identity failed")

    @property
    def qualified_drained_packets(self) -> int:
        return self.coalesced_packets + self.published_packets + self.rejected_packets + self.cancelled_packets


@dataclass(frozen=True, slots=True)
class OwnerJournalScope:
    # Host assignments from the SAME admitted owner, not native RF attestation.
    clock_scope_id: str
    host_process_id: int
    owner_run_id: str
    source_id: str
    receiver_id: str | None
    session_id: str
    configuration_generation: int
    acquisition_epoch: int

    def __post_init__(self) -> None:
        for name in ("clock_scope_id", "owner_run_id", "source_id", "session_id"):
            value = getattr(self, name)
            if (type(value) is not str or not value.strip() or value != value.strip() or value == "unknown"
                    or len(value) > 4096 or "\x00" in value):
                raise ValueError(f"journal requires exact {name}")
        if self.receiver_id not in (None, "RX1", "RX2"):
            raise ValueError("journal requires a typed admitted chain")
        _integer(self.host_process_id, "host_process_id", 1, (1 << 64) - 1)
        _integer(self.configuration_generation, "configuration_generation", 0, (1 << 64) - 1)
        _integer(self.acquisition_epoch, "acquisition_epoch", 1, (1 << 64) - 1)


@dataclass(frozen=True, slots=True)
class JournalCounters:
    producer_instance_id: int
    offered: int
    handed_off: int
    producer_superseded: int
    producer_cancelled: int
    outstanding: int
    clock_regressions: int
    events_generated: int
    events_drained: int
    events_lost: int
    first_lost_event_sequence: int
    last_lost_event_sequence: int
    event_capacity: int
    events_pending: int
    event_storage_bytes: int
    event_contract_version: int = 1
    owner_disposition_events: int = 0

    def __post_init__(self) -> None:
        for item in fields(self):
            _integer(getattr(self, item.name), item.name, 0, (1 << 64) - 1)
        if self.producer_instance_id == 0 or not 1 <= self.event_capacity <= 4096:
            raise ValueError("journal producer/capacity not admitted")
        if self.offered != self.handed_off + self.producer_superseded + self.producer_cancelled + self.outstanding:
            raise ValueError("journal offer conservation failed")
        if self.events_generated != self.events_drained + self.events_pending + self.events_lost:
            raise ValueError("journal evidence conservation failed")
        if (self.event_contract_version not in (1, 2)
                or (self.event_contract_version == 1 and self.owner_disposition_events != 0)
                or self.owner_disposition_events > self.handed_off):
            raise ValueError("journal owner event version/count not admitted")
        if (self.events_generated != 2 * self.offered - self.outstanding + self.owner_disposition_events
                or self.events_pending > self.event_capacity):
            raise ValueError("journal generation/pending accounting failed")
        if self.clock_regressions > self.offered or not self.event_capacity * 48 <= self.event_storage_bytes <= self.event_capacity * 256:
            raise ValueError("journal scalar storage/clock accounting failed")
        if self.events_lost:
            if not 1 <= self.first_lost_event_sequence <= self.last_lost_event_sequence <= self.events_generated:
                raise ValueError("journal loss bounds invalid")
        elif self.first_lost_event_sequence or self.last_lost_event_sequence:
            raise ValueError("journal claims loss bounds without evidence loss")


@dataclass(frozen=True, slots=True)
class JournalEvent:
    event_sequence: int
    producer_instance_id: int
    offer_sequence: int
    configuration_generation: int
    ready_native_ns: int
    clock_regressed: bool
    kind: JournalEventKind

    def __post_init__(self) -> None:
        for name in ("event_sequence", "producer_instance_id", "offer_sequence"):
            _integer(getattr(self, name), name, 1, (1 << 64) - 1)
        _integer(self.configuration_generation, "configuration_generation", 0, (1 << 64) - 1)
        _integer(self.ready_native_ns, "ready_native_ns", -(1 << 63), (1 << 63) - 1)
        if type(self.clock_regressed) is not bool or not isinstance(self.kind, JournalEventKind):
            raise ValueError("journal event clock/kind must be typed")


@dataclass(frozen=True, slots=True)
class OwnerJournalSnapshot:
    scope: OwnerJournalScope | None = None
    state: JournalState = JournalState.UNSUPPORTED
    counters: JournalCounters | None = None
    # Latest nonempty finite batch, not all offers. Window replacement is counted.
    events: tuple[JournalEvent, ...] = ()
    host_window_events_evicted: int = 0
    terminal_windows_evicted: int = 0
    drain_failures: int = 0
    native_stop_confirmed: bool = False
    host_scalar_reserved_bytes: int = 1_048_576
    adapter: AdapterDispositionCounters | None = None
    native_presentation: NativePresentationCounters | None = None
    native_presentation_release_failed: bool = False
    owner_id_coverage: OwnerIdCoverage = OwnerIdCoverage()

    def __post_init__(self) -> None:
        if not isinstance(self.state, JournalState) or type(self.events) is not tuple or len(self.events) > 256:
            raise ValueError("journal snapshot exceeds the scalar batch contract")
        if any(not isinstance(event, JournalEvent) for event in self.events):
            raise ValueError("journal snapshot cannot retain frames/arrays/proxies")
        if self.counters is not None and not isinstance(self.counters, JournalCounters):
            raise ValueError("journal counters must be immutable")
        if not isinstance(self.owner_id_coverage, OwnerIdCoverage):
            raise ValueError("owner ID coverage must be immutable")
        if self.scope is not None and not isinstance(self.scope, OwnerJournalScope):
            raise ValueError("journal scope must be immutable")
        for name in ("host_window_events_evicted", "terminal_windows_evicted", "drain_failures"):
            _integer(getattr(self, name), name, 0, (1 << 64) - 1)
        if (type(self.native_stop_confirmed) is not bool
                or type(self.native_presentation_release_failed) is not bool
                or self.host_scalar_reserved_bytes != 1_048_576):
            raise ValueError("journal host reservation/terminal flag differs")
        if self.counters is not None and self.scope is None:
            raise ValueError("journal counters require the actual owner scope")
        if self.native_presentation is not None:
            if (not isinstance(self.native_presentation, NativePresentationCounters) or self.counters is None
                    or self.native_presentation.classified_handoffs > self.counters.handed_off):
                raise ValueError("native owner disposition conservation failed")
        if self.adapter is not None and (not isinstance(self.adapter, AdapterDispositionCounters)
                or self.scope is None):
            raise ValueError("adapter accounting requires its immutable actual owner scope")
        if (self.adapter is not None and self.counters is not None
                and (self.adapter.qualified_drained_packets > self.counters.handed_off
                     or self.adapter.last_offer_sequence > self.counters.offered)):
            raise ValueError("adapter attribution exceeds native all-handoff denominator")
        if self.state is JournalState.FINAL and (not self.native_stop_confirmed or self.counters is None
                or self.counters.outstanding or self.counters.events_pending or self.drain_failures
                or self.native_presentation_release_failed):
            raise ValueError("journal cannot claim a complete native terminal")
        if self.state is JournalState.UNSUPPORTED and (self.counters is not None or self.events
                                                     or self.adapter is not None):
            raise ValueError("unsupported journal cannot claim event coverage")
        if self.owner_id_coverage.state is not OwnerIdCoverageState.UNSUPPORTED and (
                self.counters is None or self.counters.event_contract_version != 2
                or self.native_presentation is None):
            raise ValueError("owner ID evidence requires supported version2 owner counters")
        if self.owner_id_coverage.state is OwnerIdCoverageState.COMPLETE:
            c, p, ids = self.counters, self.native_presentation, self.owner_id_coverage
            if (self.state is not JournalState.FINAL or c is None or p is None
                    or c.event_contract_version != 2 or c.events_lost or self.host_window_events_evicted
                    or self.native_presentation_release_failed or p.accounting_failures
                    or ids.retained_offers != c.offered or ids.retired_offers != c.offered
                    or ids.owner_dispositions != c.handed_off or c.owner_disposition_events != c.handed_off):
                raise ValueError("complete native ID custody lacks terminal all-offer evidence")

    @property
    def adapter_native_handoffs_unclassified(self) -> int | None:
        """Residual native handoffs; NOT inferred dropped packets/RF samples."""
        if self.counters is None or self.adapter is None:
            return None
        return self.counters.handed_off - self.adapter.qualified_drained_packets

    @property
    def adapter_handoff_reconciled(self) -> bool | None:
        """This boundary only. Even True does NOT mean pane/paint complete."""
        if self.counters is None or self.adapter is None:
            return None
        return (self.state is JournalState.FINAL and self.adapter_native_handoffs_unclassified == 0
                and self.adapter.binding_failures == 0 and self.adapter.unqualified_packets == 0)

    @property
    def native_owner_handoffs_unclassified(self) -> int | None:
        if self.counters is None or self.native_presentation is None:
            return None
        return self.counters.handed_off - self.native_presentation.classified_handoffs

    @property
    def owner_adapter_scalar_reconciled(self) -> bool | None:
        """Scalar boundary conservation only, NOT exact offer/pane/paint coverage."""
        owner, adapter = self.native_presentation, self.adapter
        if self.counters is None or owner is None or adapter is None:
            return None
        latest = adapter.published_packets + adapter.rejected_packets + adapter.cancelled_packets
        return (self.state is JournalState.FINAL and self.native_owner_handoffs_unclassified == 0
                and owner.accounting_failures == 0 and adapter.binding_failures == 0
                and adapter.unqualified_packets == 0 and owner.forwarded == latest
                and owner.coalesced == adapter.coalesced_packets)

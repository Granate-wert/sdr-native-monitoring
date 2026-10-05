"""Bounded scalar owner evidence; never RF time or a complete paint ledger."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .analytical_journal import OwnerJournalScope
from .layer_ready import LayerReadyKind, _number, _text


@dataclass(frozen=True, slots=True)
class SweepLayerScope:
    """Actual owner lifetime; retuning Sweep has no global configuration generation."""
    clock_scope_id: str
    host_process_id: int
    owner_run_id: str
    source_id: str
    receiver_id: str | None
    session_id: str
    acquisition_epoch: int | None
    # Paired Configure may raise the requested epoch. Until an ORIGINAL native
    # creation arrives this is only an admission floor, never actual readback.
    minimum_epoch: int = 0

    def __post_init__(self) -> None:
        for name in ("clock_scope_id", "owner_run_id", "source_id", "session_id"):
            _text(getattr(self, name))
        _number(self.host_process_id, minimum=1)
        _number(self.minimum_epoch)
        if self.acquisition_epoch is not None:
            _number(self.acquisition_epoch, minimum=self.minimum_epoch)
        if self.receiver_id not in (None, "RX1", "RX2"):
            raise ValueError("Sweep journal requires an admitted typed receiver")


class LayerJournalState(StrEnum):
    UNSUPPORTED = "unsupported"
    ARMED = "armed_no_native_evidence"
    ACTIVE = "active"
    FINAL = "stopped_drained"
    INCOMPLETE = "evidence_incomplete"


@dataclass(frozen=True, slots=True)
class LayerCreationCounters:
    producer_instance_id: int
    created: int
    clock_regressions: int
    event_capacity: int
    events_pending: int
    events_drained: int
    events_lost: int
    first_lost_creation_sequence: int
    last_lost_creation_sequence: int

    def __post_init__(self) -> None:
        _number(self.producer_instance_id, minimum=1)
        for name in ("created", "clock_regressions", "events_drained", "events_lost",
                     "first_lost_creation_sequence", "last_lost_creation_sequence"):
            _number(getattr(self, name))
        _number(self.event_capacity, minimum=1, maximum=4096)
        _number(self.events_pending, maximum=self.event_capacity)
        if self.created != self.events_pending + self.events_drained + self.events_lost:
            raise ValueError("layer creation denominator differs from its native outcomes")
        if self.clock_regressions > self.created:
            raise ValueError("layer clock regressions exceed actual creations")
        if self.events_lost:
            if not 1 <= self.first_lost_creation_sequence <= self.last_lost_creation_sequence <= self.created:
                raise ValueError("layer evidence loss bounds differ from actual creations")
            if self.events_lost > self.last_lost_creation_sequence - self.first_lost_creation_sequence + 1:
                raise ValueError("layer loss count exceeds its original sequence bounds")
        elif self.first_lost_creation_sequence or self.last_lost_creation_sequence:
            raise ValueError("zero layer loss cannot claim lost sequence bounds")


@dataclass(frozen=True, slots=True)
class LayerCreationEvent:
    kind: LayerReadyKind
    producer_instance_id: int
    creation_sequence: int
    ready_native_ns: int
    clock_regressed: bool
    sweep_epoch: int
    line_sequence: int
    revision: int
    config_generation: int
    update_sequence: int
    source_frame_sequence: int
    accumulation_sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LayerReadyKind) or type(self.clock_regressed) is not bool:
            raise ValueError("layer event variant/clock must be typed")
        _number(self.producer_instance_id, minimum=1)
        _number(self.creation_sequence, minimum=1)
        _number(self.ready_native_ns, -(1 << 63), (1 << 63) - 1)
        for name in ("sweep_epoch", "line_sequence", "revision", "config_generation",
                     "update_sequence", "source_frame_sequence", "accumulation_sequence"):
            _number(getattr(self, name))
        if self.kind is LayerReadyKind.DENSITY:
            if (not self.config_generation or not self.update_sequence or not self.accumulation_sequence
                    or self.sweep_epoch or self.line_sequence or self.revision):
                raise ValueError("density creation has missing or mixed native identity")
        elif (self.config_generation or self.update_sequence or self.source_frame_sequence
                or self.accumulation_sequence or
                (bool(self.revision) != (self.kind is LayerReadyKind.SWEEP_PROGRESS))):
            raise ValueError("Sweep creation has mixed progress/terminal/density identity")


@dataclass(frozen=True, slots=True)
class LayerJournalSnapshot:
    state: LayerJournalState = LayerJournalState.UNSUPPORTED
    scope: OwnerJournalScope | SweepLayerScope | None = None
    counters: LayerCreationCounters | None = None
    events: tuple[LayerCreationEvent, ...] = ()
    host_events_evicted: int = 0
    drain_failures: int = 0
    # Adapter attempts, NOT native creation, display or FFT counts.
    receipt_attempts: int = 0
    matched_refs: int = 0
    unmatched_refs: int = 0
    refused_refs: int = 0
    native_stop_confirmed: bool = False

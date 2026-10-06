"""Ephemeral layer readiness, separate from detector, RF and paint timing.

These scalar contracts do not authenticate an owner or provide an all-offers
ledger. Actual native producers, bounded accounting and product adapters must
supply that evidence before readiness can be enabled or latency qualified.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .analytical_ready import ReadyClockMapping, ReadyHostBounds

_U64 = (1 << 64) - 1
MAX_LAYER_SEGMENTS = 2048
DENSITY_LAYER_SCALAR_RESERVATION_BYTES = 1024


def _number(value: object, minimum: int = 0, maximum: int = _U64) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("layer identity integer is out of bounds")


def _text(value: object) -> None:
    if (type(value) is not str or not value or value != value.strip()
            or len(value) > 4096 or any(ord(char) < 32 for char in value)
            or value == "unknown"):
        raise ValueError("layer identity requires bounded exact text")


class LayerReadyKind(StrEnum):
    SWEEP_PROGRESS = "sweep_progress"
    SWEEP_TERMINAL = "sweep_terminal"
    DENSITY = "density"


@dataclass(frozen=True, slots=True)
class SweepLayerIdentity:
    source_id: str
    epoch: int
    line_sequence: int
    revision: int | None
    acquired_segment_generations: tuple[tuple[int, int], ...]
    pending_segment_indices: tuple[int, ...]
    receiver_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.source_id)
        if self.receiver_id is not None:
            _text(self.receiver_id)
        _number(self.epoch)
        _number(self.line_sequence)
        acquired, pending = self.acquired_segment_generations, self.pending_segment_indices
        if (type(acquired) is not tuple or type(pending) is not tuple
                or not 0 < len(acquired) + len(pending) <= MAX_LAYER_SEGMENTS):
            raise ValueError("layer coverage must be bounded immutable tuples")
        indices = []
        for pair in acquired:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError("layer segment generation must be an immutable pair")
            _number(pair[0], maximum=(1 << 32) - 1)
            _number(pair[1], minimum=1)
            indices.append(pair[0])
        for index in pending:
            _number(index, maximum=(1 << 32) - 1)
        if (indices != sorted(set(indices)) or pending != tuple(sorted(set(pending)))
                or set(indices).intersection(pending)):
            raise ValueError("layer segment coverage is not ordered and disjoint")
        if self.revision is not None:
            _number(self.revision, minimum=1)
            if self.revision != len(acquired) or not pending:
                raise ValueError("progress revision must match nonterminal acquired coverage")
        # No global configuration generation is invented for a retuning Sweep.


@dataclass(frozen=True, slots=True)
class DensityLayerIdentity:
    source_id: str
    config_generation: int
    update_sequence: int
    source_frame_sequence: int
    accumulation_id: str
    receiver_id: str | None
    acquisition_epoch: int | None
    native_accumulation_sequence: int | None = None

    def __post_init__(self) -> None:
        _text(self.source_id)
        _text(self.accumulation_id)
        _number(self.config_generation, minimum=1)
        _number(self.update_sequence, minimum=1)
        _number(self.source_frame_sequence)
        if self.receiver_id is not None:
            _text(self.receiver_id)
        if self.acquisition_epoch is not None:
            _number(self.acquisition_epoch, minimum=1)
        if self.native_accumulation_sequence is not None:
            _number(self.native_accumulation_sequence, minimum=1)


@dataclass(frozen=True, slots=True)
class LayerReadyReceipt:
    kind: LayerReadyKind
    identity: SweepLayerIdentity | DensityLayerIdentity
    adapter_clock_scope_id: str
    host_process_id: int
    producer_instance_id: int
    creation_sequence: int
    ready_native_ns: int
    mapping: ReadyClockMapping
    host_bounds: ReadyHostBounds | None = None
    owner_run_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LayerReadyKind):
            raise ValueError("layer kind must be typed")
        if self.kind is LayerReadyKind.DENSITY:
            if not isinstance(self.identity, DensityLayerIdentity):
                raise ValueError("density receipt requires density identity")
        else:
            if not isinstance(self.identity, SweepLayerIdentity):
                raise ValueError("Sweep receipt requires Sweep identity")
            progressive = self.identity.revision is not None
            if progressive != (self.kind is LayerReadyKind.SWEEP_PROGRESS):
                raise ValueError("terminal and progress identities cannot be interchanged")
        _text(self.adapter_clock_scope_id)
        for value in (self.host_process_id, self.producer_instance_id, self.creation_sequence):
            _number(value, minimum=1)
        _number(self.ready_native_ns, -(1 << 63), (1 << 63) - 1)
        if not isinstance(self.mapping, ReadyClockMapping):
            raise ValueError("layer clock mapping must be typed")
        if self.mapping is ReadyClockMapping.BOUNDED:
            bounds = self.host_bounds
            if (not isinstance(bounds, ReadyHostBounds)
                    or not bounds.lower.native_ns < self.ready_native_ns < bounds.upper.native_ns):
                raise ValueError("layer readiness must be enclosed by actual native clock probes")
            if bounds.host_clock is not None and bounds.host_clock.process_id != self.host_process_id:
                raise ValueError("layer ready bounds belong to another host process")
        elif self.host_bounds is not None:
            raise ValueError("unknown layer clock mapping cannot claim host bounds")
        if (self.owner_run_id is None) != (self.session_id is None):
            raise ValueError("layer owner authentication requires run and session together")
        if self.owner_run_id is not None:
            _text(self.owner_run_id)
            _text(self.session_id)


def validate_sweep_layer_receipt(receipt: LayerReadyReceipt | None, *, source_id: str,
        epoch: int, sequence: int, revision: int | None,
        acquired: tuple[tuple[int, int], ...], pending: tuple[int, ...], receiver_id: str | None) -> None:
    """Identity/coverage guard only, NOT authentication or timing generation."""
    if receipt is None:
        return
    kind = LayerReadyKind.SWEEP_TERMINAL if revision is None else LayerReadyKind.SWEEP_PROGRESS
    if (not isinstance(receipt, LayerReadyReceipt) or receipt.kind is not kind
            or receipt.identity != SweepLayerIdentity(source_id, epoch, sequence, revision,
                acquired, pending, receiver_id)):
        raise ValueError("Sweep layer readiness does not belong to its exact frame/coverage/receiver")

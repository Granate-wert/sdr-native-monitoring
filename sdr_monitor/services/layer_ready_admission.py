"""Pure diagnostic admission; never generate timestamps or change measurement.

This is not a native producer, clock probe, owner authenticator or UI callback.
Absence/refusal leaves measurement data intact and readiness unqualified.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..domain.layer_ready import (
    DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt, SweepLayerIdentity,
)
from ..domain.live import LivePersistenceFrame
from ..domain.sweep_lines import SweepLineFrame
from ..domain.sweep_progress import SweepProgressFrame


class LayerReadyAdmissionState(StrEnum):
    MISSING = "missing_native_layer_evidence"
    REFUSED = "layer_evidence_does_not_match_frame"
    MATCHED = "layer_identity_matched_not_owner_authenticated"


@dataclass(frozen=True, slots=True)
class LayerReadyAdmission:
    state: LayerReadyAdmissionState
    receipt: LayerReadyReceipt | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, LayerReadyAdmissionState):
            raise ValueError("layer admission state must be typed")
        if ((self.state is LayerReadyAdmissionState.MATCHED and not isinstance(self.receipt, LayerReadyReceipt))
                or (self.state is not LayerReadyAdmissionState.MATCHED and self.receipt is not None)):
            raise ValueError("only matched admission may retain a layer receipt")


def admit_layer_ready(
    frame: SweepProgressFrame | SweepLineFrame | LivePersistenceFrame,
    receipt: object | None,
) -> LayerReadyAdmission:
    if receipt is None:
        return LayerReadyAdmission(LayerReadyAdmissionState.MISSING)
    if not isinstance(receipt, LayerReadyReceipt):
        return LayerReadyAdmission(LayerReadyAdmissionState.REFUSED)
    try:
        if isinstance(frame, SweepProgressFrame):
            kind = LayerReadyKind.SWEEP_PROGRESS
            identity: SweepLayerIdentity | DensityLayerIdentity = SweepLayerIdentity(
                frame.source_id, frame.epoch, frame.sequence, frame.revision,
                frame.acquired_segment_generations, frame.pending_segment_indices)
        elif isinstance(frame, SweepLineFrame):
            if frame.instrument is not None:
                # Instrument completion timing cannot become native SDR readiness.
                return LayerReadyAdmission(LayerReadyAdmissionState.REFUSED)
            kind = LayerReadyKind.SWEEP_TERMINAL
            missing = frame.missing_segment_indices
            missing_set = frozenset(missing)
            identity = SweepLayerIdentity(frame.source_id, frame.epoch, frame.sequence, None,
                tuple(pair for pair in frame.segment_config_generations if pair[0] not in missing_set),
                missing)
        elif isinstance(frame, LivePersistenceFrame):
            if frame.producer_identity_available is not True or frame.accumulation_id is None:
                return LayerReadyAdmission(LayerReadyAdmissionState.REFUSED)
            kind = LayerReadyKind.DENSITY
            identity = DensityLayerIdentity(frame.source_id, frame.config_generation,
                frame.update_sequence, frame.source_frame_sequence, frame.accumulation_id,
                frame.receiver_id, frame.acquisition_epoch)
        else:
            return LayerReadyAdmission(LayerReadyAdmissionState.REFUSED)
    except (TypeError, ValueError):
        return LayerReadyAdmission(LayerReadyAdmissionState.REFUSED)
    if receipt.kind is not kind or receipt.identity != identity:
        return LayerReadyAdmission(LayerReadyAdmissionState.REFUSED)
    return LayerReadyAdmission(LayerReadyAdmissionState.MATCHED, receipt)

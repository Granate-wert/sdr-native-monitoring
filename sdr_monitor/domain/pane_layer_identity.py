"""Scalar pane binding of an ORIGINAL native layer creation; no paint proof."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .analytical_journal import OwnerJournalScope
from .layer_journal import LayerCreationEvent, SweepLayerScope
from .layer_ready import DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt, _number, _text


class PaneDeliveryView(StrEnum):
    SPECTRUM = "spectrum"
    WATERFALL = "waterfall"
    PERSISTENCE = "persistence"


@dataclass(frozen=True, slots=True)
class PaneLayerAnalyticalIdentity:
    """Construct only after frame admission and SAME cached-owner event match.

    The scalar constructor checks consistency, not hardware authenticity. It
    retains no arrays. Separate views may reference one creation; their custody
    must never be summed as separate RF captures, FFTs or native creations.
    """

    owner_scope: OwnerJournalScope | SweepLayerScope
    ready: LayerReadyReceipt
    original_event: LayerCreationEvent
    physical_stream_resource_id: str
    capture_id: str
    receiver_endpoint_id: str
    pane_id: str
    host_run_serial: int
    host_activation_serial: int

    def __post_init__(self) -> None:
        for name in ("physical_stream_resource_id", "capture_id", "receiver_endpoint_id", "pane_id"):
            _text(getattr(self, name))
        for name in ("host_run_serial", "host_activation_serial"):
            _number(getattr(self, name), minimum=1)
        scope, ready, event = self.owner_scope, self.ready, self.original_event
        if (not isinstance(scope, (OwnerJournalScope, SweepLayerScope))
                or not isinstance(ready, LayerReadyReceipt) or not isinstance(event, LayerCreationEvent)
                or ready.owner_run_id != scope.owner_run_id or ready.session_id != scope.session_id
                or ready.adapter_clock_scope_id != scope.clock_scope_id
                or ready.host_process_id != scope.host_process_id
                or ready.identity.source_id != scope.source_id
                or ready.identity.receiver_id != scope.receiver_id
                or (ready.kind, ready.producer_instance_id, ready.creation_sequence, ready.ready_native_ns)
                != (event.kind, event.producer_instance_id, event.creation_sequence, event.ready_native_ns)):
            raise ValueError("pane layer requires its exact owner and original creation")
        identity = ready.identity
        if isinstance(identity, DensityLayerIdentity):
            if (not isinstance(scope, OwnerJournalScope) or event.kind is not LayerReadyKind.DENSITY
                    or identity.accumulation_id != scope.session_id
                    or identity.acquisition_epoch != scope.acquisition_epoch
                    or identity.config_generation != scope.configuration_generation
                    or (identity.config_generation, identity.update_sequence, identity.source_frame_sequence,
                        identity.native_accumulation_sequence) != (event.config_generation,
                        event.update_sequence, event.source_frame_sequence, event.accumulation_sequence)):
                raise ValueError("pane density differs from the admitted accumulation")
        elif (not isinstance(scope, SweepLayerScope) or identity.epoch != scope.acquisition_epoch
                or (identity.epoch, identity.line_sequence, identity.revision or 0)
                != (event.sweep_epoch, event.line_sequence, event.revision)):
            raise ValueError("pane Sweep differs from its original creation")

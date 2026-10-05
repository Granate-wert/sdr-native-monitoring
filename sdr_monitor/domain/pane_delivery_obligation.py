"""Exact scalar pane custody, not FFT counts, RF time or compositor proof."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .pane_analytical_identity import PaneAnalyticalIdentity
from .pane_layer_identity import PaneDeliveryView, PaneLayerAnalyticalIdentity
from .layer_ready import LayerReadyKind


class PaneDeliveryStage(StrEnum):
    ADMITTED = "admitted"
    PREPARING = "preparing"
    PREPARED = "prepared"
    QUEUED = "queued"
    QUEUE_DRAINED = "queue_drained"
    UI_ADMITTED = "ui_admitted"
    PAINT_SCHEDULED = "paint_scheduled"
    PAINT_RETURNED = "paint_returned"
    ADMISSION_CANCELLED = "admission_cancelled"
    PREPARATION_FAILED = "preparation_failed"
    PREPARATION_CANCELLED = "preparation_cancelled"
    QUEUE_FAILED = "queue_failed"
    QUEUE_REJECTED = "queue_rejected"
    QUEUE_SUPERSEDED = "queue_superseded"
    STOP_CLEARED = "stop_cleared"
    UI_REJECTED = "ui_rejected"
    PAINT_SUPERSEDED = "paint_superseded"


@dataclass(frozen=True, slots=True)
class PaneDeliveryObligationRef:
    """One view's custody, with its exact native spectrum/layer provenance.

    The random graph ID scopes custody, never identifies a physical device.
    A successful paint return is still not a DWM/photon presentation receipt.
    """

    graph_instance_id: str
    sequence: int
    identity: PaneAnalyticalIdentity | PaneLayerAnalyticalIdentity
    view: PaneDeliveryView = PaneDeliveryView.SPECTRUM

    def __post_init__(self) -> None:
        if (type(self.graph_instance_id) is not str or not self.graph_instance_id
                or self.graph_instance_id != self.graph_instance_id.strip()
                or len(self.graph_instance_id) > 128 or "\x00" in self.graph_instance_id
                or type(self.sequence) is not int or not 1 <= self.sequence < (1 << 64)
                or not isinstance(self.identity, (PaneAnalyticalIdentity, PaneLayerAnalyticalIdentity))
                or not isinstance(self.view, PaneDeliveryView)):
            raise ValueError("pane custody requires an exact graph and analytical identity")
        allowed: tuple[PaneDeliveryView, ...]
        if isinstance(self.identity, PaneAnalyticalIdentity):
            allowed = (PaneDeliveryView.SPECTRUM, PaneDeliveryView.WATERFALL)
        elif self.identity.ready.kind is LayerReadyKind.DENSITY:
            allowed = (PaneDeliveryView.PERSISTENCE,)
        elif self.identity.ready.kind is LayerReadyKind.SWEEP_PROGRESS:
            allowed = (PaneDeliveryView.SPECTRUM,)
        else:
            allowed = (PaneDeliveryView.SPECTRUM, PaneDeliveryView.WATERFALL)
        if self.view not in allowed:
            raise ValueError("pane view cannot borrow another layer's ready provenance")


@dataclass(frozen=True, slots=True)
class PaneDeliveryEvent:
    sequence: int
    ref: PaneDeliveryObligationRef
    stage: PaneDeliveryStage
    host_perf_ns: int | None


@dataclass(frozen=True, slots=True)
class PaneDeliveryRecord:
    ref: PaneDeliveryObligationRef
    stage: PaneDeliveryStage


@dataclass(frozen=True, slots=True)
class PaneDeliveryCounters:
    pane_id: str
    qualified_admissions: int = 0
    unqualified_deliveries: int = 0
    duplicate_admissions: int = 0
    untracked_admissions: int = 0
    pending: int = 0
    terminal: int = 0


@dataclass(frozen=True, slots=True)
class PaneViewDeliveryCounters:
    view: PaneDeliveryView
    counters: PaneDeliveryCounters


@dataclass(frozen=True, slots=True)
class PaneDeliveryLedgerSnapshot:
    graph_instance_id: str
    supported: bool
    panes: tuple[PaneDeliveryCounters, ...]
    records: tuple[PaneDeliveryRecord, ...]
    events: tuple[PaneDeliveryEvent, ...]
    record_evictions: int
    event_evictions: int
    event_drops: int
    duplicate_events: int
    accounting_failures: int
    clock_failures: int
    reserved_bytes: int
    retained_scalar_bytes: int
    views: tuple[PaneViewDeliveryCounters, ...] = ()

    # Native all-offers counters remain a different denominator. No lifetime
    # completeness or latency verdict is implied by this finite host window.

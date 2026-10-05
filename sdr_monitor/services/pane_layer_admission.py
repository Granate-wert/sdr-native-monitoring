"""Authenticate admitted layer frames against SAME owner's cached scalar events.

Never poll/drain/start an SDR. Missing, evicted or malformed diagnostic evidence
leaves the measurement valid and the layer obligation unqualified.
"""
from __future__ import annotations

from ..domain.analytical_journal import OwnerJournalScope
from ..domain.layer_journal import (
    LayerCreationCounters, LayerCreationEvent, LayerJournalSnapshot, LayerJournalState, SweepLayerScope,
)
from ..domain.live import LivePersistenceFrame
from ..domain.pane_layer_identity import PaneLayerAnalyticalIdentity
from ..domain.sweep_lines import SweepLineFrame
from ..domain.sweep_progress import SweepProgressFrame
from .layer_ready_admission import LayerReadyAdmissionState, admit_layer_ready

MAX_CACHED_LAYER_EVENTS = 32  # SAME native journal's finite host retention.


def _valid_snapshots(result: object) -> bool:
    return (type(result) is tuple and len(result) <= 2
            and all(isinstance(item, LayerJournalSnapshot) and isinstance(item.state, LayerJournalState)
                    and (item.scope is None or isinstance(item.scope, (OwnerJournalScope, SweepLayerScope)))
                    and (item.counters is None or isinstance(item.counters, LayerCreationCounters))
                    and type(item.events) is tuple and len(item.events) <= MAX_CACHED_LAYER_EVENTS
                    and all(isinstance(event, LayerCreationEvent) for event in item.events) for item in result))


def cached_layer_journals(port: object) -> tuple[LayerJournalSnapshot, ...]:
    """Optional cached contract, bounded to one snapshot per native RX chain."""
    for plural, singular in (("layer_journal_snapshots", "layer_journal_snapshot"),
                             ("density_layer_journal_snapshots", "density_layer_journal_snapshot")):
        many, one = getattr(port, plural, None), getattr(port, singular, None)
        if not callable(many) and not callable(one):
            continue
        if callable(many):
            result = many()
        else:
            assert callable(one)
            result = (one(),)
        if not _valid_snapshots(result):
            raise ValueError("cached layer journal contract differs")
        return result
    return ()


def bind_pane_layer_identity(frame: SweepProgressFrame | SweepLineFrame | LivePersistenceFrame,
                            snapshots: tuple[LayerJournalSnapshot, ...], *,
                            physical_stream_resource_id: str, capture_id: str,
                            receiver_endpoint_id: str, pane_id: str, host_run_serial: int,
                            host_activation_serial: int,
                            admitted_density_scope: OwnerJournalScope | None = None
                            ) -> PaneLayerAnalyticalIdentity | None:
    """Called after graph measurement admission; exact match or explicit None."""
    if not _valid_snapshots(snapshots):
        return None
    admitted = admit_layer_ready(frame, frame.layer_ready)
    ready = admitted.receipt
    if admitted.state is not LayerReadyAdmissionState.MATCHED or ready is None:
        return None
    matches: list[PaneLayerAnalyticalIdentity] = []
    for snapshot in snapshots:
        if (snapshot.state not in (LayerJournalState.ACTIVE, LayerJournalState.FINAL)
                or snapshot.scope is None or snapshot.counters is None
                or snapshot.counters.producer_instance_id != ready.producer_instance_id
                or ready.creation_sequence > snapshot.counters.created):
            continue
        if isinstance(frame, LivePersistenceFrame) and snapshot.scope != admitted_density_scope:
            continue  # Density must match Start's captured RTBW scope too.
        for event in snapshot.events:
            if event.creation_sequence != ready.creation_sequence:
                continue
            try:
                matches.append(PaneLayerAnalyticalIdentity(snapshot.scope, ready, event,
                    physical_stream_resource_id, capture_id, receiver_endpoint_id, pane_id,
                    host_run_serial, host_activation_serial))
            except ValueError:
                continue
    return matches[0] if len(matches) == 1 else None  # Ambiguity cannot authenticate.

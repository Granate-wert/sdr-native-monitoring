"""Bounded helpers for the distinct view receipts carried by a pane delivery."""

from __future__ import annotations

from collections.abc import Callable

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef, PaneDeliveryStage
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView


def delivery_obligation_refs(delivery: object) -> tuple[PaneDeliveryObligationRef, ...]:
    """Return original and optional per-view refs once, preserving legacy packets."""
    active = getattr(delivery, "active_obligation_refs", None)
    if isinstance(active, tuple):
        if len(active) > 3:
            return ()
        active_refs: list[PaneDeliveryObligationRef] = []
        active_views: set[PaneDeliveryView] = set()
        for ref in active:
            if isinstance(ref, PaneDeliveryObligationRef) and ref.view not in active_views:
                active_refs.append(ref)
                active_views.add(ref.view)
        return tuple(active_refs)
    delivery = getattr(delivery, "delivery", delivery)
    source_refs: list[PaneDeliveryObligationRef] = []
    source_views: set[PaneDeliveryView] = set()
    original = getattr(delivery, "obligation_ref", None)
    if isinstance(original, PaneDeliveryObligationRef):
        source_refs.append(original)
        source_views.add(original.view)
    layers = getattr(delivery, "layer_obligation_refs", ())
    if isinstance(layers, tuple) and len(layers) <= 3:
        for ref in layers:
            if (isinstance(ref, PaneDeliveryObligationRef) and ref.view not in source_views
                    and len(source_refs) < 3):
                source_refs.append(ref)
                source_views.add(ref.view)
    return tuple(source_refs)


def refs_by_view(delivery: object) -> dict[PaneDeliveryView, tuple[PaneDeliveryObligationRef, ...]]:
    grouped: dict[PaneDeliveryView, list[PaneDeliveryObligationRef]] = {
        view: [] for view in PaneDeliveryView
    }
    for ref in delivery_obligation_refs(delivery):
        grouped[ref.view].append(ref)
    return {view: tuple(refs) for view, refs in grouped.items()}


def report_delivery_refs(
    delivery: object,
    callback: Callable[[PaneDeliveryObligationRef, PaneDeliveryStage], object] | None,
    stage: PaneDeliveryStage,
) -> None:
    """Fan one actual boundary outcome to all distinct refs without creating metadata."""
    if callback is None:
        return
    for ref in delivery_obligation_refs(delivery):
        try:
            callback(ref, stage)
        except Exception:
            # Custody telemetry cannot interrupt presentation or receiver control.
            pass


__all__ = [
    "PaneDeliveryObligationRef", "PaneDeliveryStage", "PaneDeliveryView",
    "delivery_obligation_refs", "refs_by_view", "report_delivery_refs",
]

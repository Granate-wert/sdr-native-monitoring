"""SAME selected route and revision retained by the existing capture owner."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.device_capabilities import DeviceFamily
from ..domain.live import LiveAdmissionRejected
from ..domain.pluto_route_intent import PlutoOperationalRouteIntent
from .interfaces import PlutoOperationalRouteOwner


@dataclass(frozen=True, slots=True)
class PlutoPaneRouteAdmission:
    """Low-rate admission only, no new context/owner/lease or acquisition path."""

    intent: PlutoOperationalRouteIntent
    selection: AnalyzerSourceSelection
    selection_reader: Callable[[], AnalyzerSourceSelection | None]
    owner: PlutoOperationalRouteOwner

    def __post_init__(self) -> None:
        if (not isinstance(self.intent, PlutoOperationalRouteIntent)
                or not isinstance(self.selection, AnalyzerSourceSelection)
                or self.selection.selected is None or self.selection.release_pending
                or self.selection.selected.family is not DeviceFamily.AD936X
                or not callable(self.selection_reader) or not isinstance(self.owner, PlutoOperationalRouteOwner)):
            raise TypeError("pane route admission requires exact typed AD936x selection and route owner")

    def validate(self) -> None:
        current = self.selection_reader()
        if current is not self.selection or current.revision != self.selection.revision or current.release_pending:
            raise LiveAdmissionRejected("pane route selection changed after Stage; prepare a new plan")
        selected = current.selected
        if selected is None:
            raise LiveAdmissionRejected("pane route selected source disappeared")
        self.owner.validate_operational_route(self.intent, source_id=selected.device_id)

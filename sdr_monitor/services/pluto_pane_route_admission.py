"""SAME selected route and revision retained by the existing capture owner."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.device_capabilities import DeviceFamily
from ..domain.live import LiveAdmissionRejected
from ..domain.pluto_route_intent import PlutoOperationalRouteIntent
from ..domain.pluto_usb_alias import PlutoUsbAliasWitness
from .interfaces import PlutoOperationalRouteOwner, PlutoUsbAliasOwner


@dataclass(frozen=True, slots=True)
class PlutoPaneRouteAdmission:
    """Low-rate admission only, no new context/owner/lease or acquisition path."""

    intent: PlutoOperationalRouteIntent
    selection: AnalyzerSourceSelection
    selection_reader: Callable[[], AnalyzerSourceSelection | None]
    owner: PlutoOperationalRouteOwner
    usb_alias: PlutoUsbAliasWitness | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.intent, PlutoOperationalRouteIntent)
                or not isinstance(self.selection, AnalyzerSourceSelection)
                or self.selection.selected is None or self.selection.release_pending
                or self.selection.selected.family is not DeviceFamily.AD936X
                or not callable(self.selection_reader) or not isinstance(self.owner, PlutoOperationalRouteOwner)):
            raise TypeError("pane route admission requires exact typed AD936x selection and route owner")
        if self.usb_alias is not None and (
                not isinstance(self.usb_alias, PlutoUsbAliasWitness)
                or self.usb_alias.route != self.intent
                or self.usb_alias.source_id != self.selection.selected.device_id
                or self.usb_alias.calibration_identity != self.selection.selected.binding.calibration_identity
                or self.selection.selected.usb_connection is not None
                or not isinstance(self.owner, PlutoUsbAliasOwner)):
            raise ValueError("pane USB alias must belong to the SAME known IP route owner")

    def validate(self) -> None:
        current = self.selection_reader()
        if current is not self.selection or current.revision != self.selection.revision or current.release_pending:
            raise LiveAdmissionRejected("pane route selection changed after Stage; prepare a new plan")
        selected = current.selected
        if selected is None:
            raise LiveAdmissionRejected("pane route selected source disappeared")
        self.owner.validate_operational_route(self.intent, source_id=selected.device_id)
        if self.usb_alias is not None:
            assert isinstance(self.owner, PlutoUsbAliasOwner)
            self.owner.validate_operational_usb_alias(self.usb_alias)

    def refresh_alias(self) -> None:
        """Explicit stopped pre-Apply observation, never called in FFT hot path."""
        self.validate()
        if self.usb_alias is not None:
            assert isinstance(self.owner, PlutoUsbAliasOwner)
            self.owner.refresh_operational_usb_alias(self.usb_alias)


@dataclass(frozen=True, slots=True)
class PlutoPaneSelectionAdmission:
    """Pure peer-selection guard; no cross-owner locks, SDK calls or Stop."""

    peers: tuple[tuple[AnalyzerSourceSelection, Callable[[], AnalyzerSourceSelection | None]], ...]

    def __post_init__(self) -> None:
        if (type(self.peers) is not tuple or not 2 <= len(self.peers) <= 32
                or any(not isinstance(item, tuple) or len(item) != 2
                       or not isinstance(item[0], AnalyzerSourceSelection)
                       or item[0].selected is None or item[0].release_pending
                       or not callable(item[1]) for item in self.peers)
                or len({item[0].selected_id for item in self.peers}) != len(self.peers)):
            raise ValueError("parallel admission requires distinct exact staged selections")

    def validate(self) -> None:
        for captured, reader in self.peers:
            current = reader()
            if current is not captured or current.revision != captured.revision or current.release_pending:
                raise LiveAdmissionRejected("parallel source selection changed; prepare a new plan")

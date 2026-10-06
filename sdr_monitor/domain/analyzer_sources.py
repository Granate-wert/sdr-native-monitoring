"""Low-rate source selection, referencing existing capability truth verbatim."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice

from .device_capabilities import AdapterRuntimeSnapshot, DeviceCapabilityBinding, DeviceFamily
from .pluto_connection import PlutoUsbConnectionExpectation
from .pluto_route_intent import PlutoOperationalRouteIntent


@dataclass(frozen=True, slots=True)
class AnalyzerSourceChoice:
    binding: DeviceCapabilityBinding
    runtime: AdapterRuntimeSnapshot | None
    label: str
    transport_label: str
    # Fresh selected descriptor observation; never a stable calibration key.
    usb_connection: PlutoUsbConnectionExpectation | None = field(default=None, repr=False)
    # Copied discovery choices only, NOT a fresh route admission/identity receipt.
    # Empty means no bounded typed route observation is available to the editor.
    operational_routes: tuple[PlutoOperationalRouteIntent, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.binding, DeviceCapabilityBinding):
            raise TypeError("source choice needs existing capability binding")
        if self.runtime is not None and not isinstance(self.runtime, AdapterRuntimeSnapshot):
            raise TypeError("source choice needs an existing runtime snapshot")
        if self.runtime is not None and (self.runtime.adapter_id != self.binding.adapter_id
                                        or self.runtime.family is not self.binding.family):
            raise ValueError("source choice runtime must match its adapter")
        if self.usb_connection is not None:
            if self.family is not DeviceFamily.AD936X or not isinstance(self.usb_connection, PlutoUsbConnectionExpectation):
                raise ValueError("only AD936x sources may carry typed Pluto USB observations")
            self.usb_connection.__post_init__()
        routes = self.operational_routes
        if (type(routes) is not tuple or len(routes) > 32
                or any(not isinstance(route, PlutoOperationalRouteIntent) for route in routes)):
            raise ValueError("source route choices require a bounded typed tuple")
        if routes and self.family is not DeviceFamily.AD936X:
            raise ValueError("only AD936x choices may carry Pluto route observations")
        for route in routes:
            route.__post_init__()
        if len(set(routes)) != len(routes):
            raise ValueError("source route choices must be unique")
        for value in (self.label, self.transport_label):
            if (not isinstance(value, str) or not value or len(value) > 160
                    or any(ord(character) < 32 for character in value)):
                raise ValueError("source choice display text is invalid")

    @property
    def device_id(self) -> str:
        return self.binding.source_id  # Preserve the operational ID.

    @property
    def family(self) -> DeviceFamily:
        return self.binding.family


@dataclass(frozen=True, slots=True)
class AnalyzerSourceSelection:
    # Selection revision, NOT RF configuration generation/acquisition epoch.
    revision: int = 0
    choices: tuple[AnalyzerSourceChoice, ...] = ()
    selected_id: str | None = None
    release_pending: bool = False
    refusal: str | None = None

    def __post_init__(self) -> None:
        if type(self.revision) is not int or not 0 <= self.revision <= (1 << 64) - 1:
            raise ValueError("selection revision must fit uint64")
        choices = tuple(islice(self.choices, 33))
        if len(choices) > 32 or any(not isinstance(value, AnalyzerSourceChoice) for value in choices):
            raise ValueError("source choices must be bounded existing references")
        if len({value.device_id for value in choices}) != len(choices):
            raise ValueError("source choices require unique operational IDs")
        if self.selected_id is not None and not any(value.device_id == self.selected_id for value in choices):
            raise ValueError("selected source must exist in the published choices")
        if type(self.release_pending) is not bool:
            raise TypeError("release pending must be an explicit bool")
        if self.refusal is not None and self.refusal not in {"observation_failed", "release_pending", "selection_missing"}:
            raise ValueError("source refusal must be a redacted finite reason")
        object.__setattr__(self, "choices", choices)

    @property
    def selected(self) -> AnalyzerSourceChoice | None:
        return next((value for value in self.choices if value.device_id == self.selected_id), None)

    @property
    def ad936x_controls_available(self) -> bool:
        selected = self.selected
        return selected is not None and selected.family is DeviceFamily.AD936X and not self.release_pending


__all__ = ["AnalyzerSourceChoice", "AnalyzerSourceSelection"]

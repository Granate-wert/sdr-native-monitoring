"""Layout presets and dock-area layout for the AppShell.

A preset is a small, declarative description of which ``WorkspaceArea``
roles should be visible, their orientation and their relative weights.
The AppShell interprets this structure; it does not persist ``QByteArray``
state blobs or restore opaque legacy ``windowState`` data.

The preset design is intentionally decoupled from Qt's ``QDockWidget``
state so P16UI-03 can ship without re-implementing the legacy dock
machinery.  Later packages may extend the descriptor (for example, with
visualisation visibility flags) without breaking the migration contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class LayoutPresetId(StrEnum):
    DEFAULT = "default"
    COMPACT = "compact"
    ANALYSIS = "analysis"
    MONITORING = "monitoring"


@dataclass(frozen=True, slots=True)
class PresetArea:
    role: str
    visible: bool
    weight: float


@dataclass(frozen=True, slots=True)
class LayoutPreset:
    preset_id: LayoutPresetId
    locale_key: str
    areas: tuple[PresetArea, ...]

    def is_visible(self, role: str) -> bool:
        for area in self.areas:
            if area.role == role:
                return area.visible
        return False

    def weight(self, role: str) -> float:
        for area in self.areas:
            if area.role == role:
                return area.weight
        return 1.0


_ROLE_NAVIGATION = "navigation"
_ROLE_SOURCE_NAVIGATOR = "source_navigator"
_ROLE_WORKSPACE_HOST = "workspace_host"
_ROLE_CONTEXT_INSPECTOR = "context_inspector"
_ROLE_BOTTOM_TOOLS = "bottom_tools"
_ROLE_HEALTH_BAR = "health_bar"


def _default_areas() -> tuple[PresetArea, ...]:
    return (
        PresetArea(role=_ROLE_NAVIGATION, visible=True, weight=1.0),
        PresetArea(role=_ROLE_SOURCE_NAVIGATOR, visible=True, weight=1.5),
        PresetArea(role=_ROLE_WORKSPACE_HOST, visible=True, weight=4.0),
        PresetArea(role=_ROLE_CONTEXT_INSPECTOR, visible=True, weight=1.5),
        PresetArea(role=_ROLE_BOTTOM_TOOLS, visible=True, weight=1.0),
        PresetArea(role=_ROLE_HEALTH_BAR, visible=True, weight=0.5),
    )


def _compact_areas() -> tuple[PresetArea, ...]:
    return (
        PresetArea(role=_ROLE_NAVIGATION, visible=True, weight=1.0),
        PresetArea(role=_ROLE_SOURCE_NAVIGATOR, visible=False, weight=1.0),
        PresetArea(role=_ROLE_WORKSPACE_HOST, visible=True, weight=5.0),
        PresetArea(role=_ROLE_CONTEXT_INSPECTOR, visible=False, weight=1.0),
        PresetArea(role=_ROLE_BOTTOM_TOOLS, visible=False, weight=1.0),
        PresetArea(role=_ROLE_HEALTH_BAR, visible=True, weight=0.5),
    )


def _analysis_areas() -> tuple[PresetArea, ...]:
    return (
        PresetArea(role=_ROLE_NAVIGATION, visible=True, weight=1.0),
        PresetArea(role=_ROLE_SOURCE_NAVIGATOR, visible=True, weight=1.0),
        PresetArea(role=_ROLE_WORKSPACE_HOST, visible=True, weight=4.0),
        PresetArea(role=_ROLE_CONTEXT_INSPECTOR, visible=True, weight=2.0),
        PresetArea(role=_ROLE_BOTTOM_TOOLS, visible=True, weight=1.5),
        PresetArea(role=_ROLE_HEALTH_BAR, visible=True, weight=0.5),
    )


def _monitoring_areas() -> tuple[PresetArea, ...]:
    return (
        PresetArea(role=_ROLE_NAVIGATION, visible=True, weight=1.0),
        PresetArea(role=_ROLE_SOURCE_NAVIGATOR, visible=True, weight=1.0),
        PresetArea(role=_ROLE_WORKSPACE_HOST, visible=True, weight=4.0),
        PresetArea(role=_ROLE_CONTEXT_INSPECTOR, visible=False, weight=1.0),
        PresetArea(role=_ROLE_BOTTOM_TOOLS, visible=True, weight=1.0),
        PresetArea(role=_ROLE_HEALTH_BAR, visible=True, weight=0.5),
    )


_PRESETS: tuple[LayoutPreset, ...] = (
    LayoutPreset(
        preset_id=LayoutPresetId.DEFAULT,
        locale_key="layout.preset.default",
        areas=_default_areas(),
    ),
    LayoutPreset(
        preset_id=LayoutPresetId.COMPACT,
        locale_key="layout.preset.compact",
        areas=_compact_areas(),
    ),
    LayoutPreset(
        preset_id=LayoutPresetId.ANALYSIS,
        locale_key="layout.preset.analysis",
        areas=_analysis_areas(),
    ),
    LayoutPreset(
        preset_id=LayoutPresetId.MONITORING,
        locale_key="layout.preset.monitoring",
        areas=_monitoring_areas(),
    ),
)


class LayoutPresetCatalog:
    """Read-only catalog of all available layout presets."""

    def __init__(self, presets: Iterable[LayoutPreset] = _PRESETS) -> None:
        self._presets: dict[LayoutPresetId, LayoutPreset] = {}
        ids: list[LayoutPresetId] = []
        for preset in presets:
            if preset.preset_id in self._presets:
                raise ValueError(f"duplicate preset_id: {preset.preset_id}")
            self._presets[preset.preset_id] = preset
            ids.append(preset.preset_id)
        if {item.value for item in ids} != {item.value for item in LayoutPresetId}:
            raise ValueError("preset catalog does not cover every LayoutPresetId")

    def presets(self) -> tuple[LayoutPreset, ...]:
        return tuple(self._presets.values())

    def preset(self, preset_id: LayoutPresetId) -> LayoutPreset:
        if preset_id not in self._presets:
            raise KeyError(f"unknown preset_id: {preset_id}")
        return self._presets[preset_id]

    def default(self) -> LayoutPreset:
        return self.preset(LayoutPresetId.DEFAULT)


__all__ = [
    "LayoutPreset",
    "LayoutPresetCatalog",
    "LayoutPresetId",
    "PresetArea",
]

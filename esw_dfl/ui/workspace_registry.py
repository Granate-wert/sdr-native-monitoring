"""Static workspace catalog used by the AppShell navigation rail.

The registry holds stable ``WorkspaceId`` -> metadata mappings only.  The
*content* of each workspace is delivered by P16UI-04..08; this package is
limited to navigation, switching and visual placeholder placement.

Invariants:

* Exactly seven workspaces, one per ``WorkspaceId``.
* ``locale_key`` points at an entry in :mod:`esw_dfl.ui.i18n` so the catalog
  remains translation driven.
* The placeholder widget factory is injected so tests can supply their own
  host widgets without importing PySide6 graphics classes in headless mode.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .state import WorkspaceId


@dataclass(frozen=True, slots=True)
class WorkspaceDescriptor:
    workspace_id: WorkspaceId
    locale_key: str
    icon_id: str | None
    shortcut: str | None
    description_locale_key: str


class WorkspacePlaceholder(Protocol):
    """Minimal contract that the AppShell host can hold and reparent."""

    def setObjectName(self, name: str) -> None: ...
    def objectName(self) -> str: ...


PlaceholderFactory = Callable[[WorkspaceId], WorkspacePlaceholder]


_PLACEHOLDER_OBJECT_NAME = "p16AppShellWorkspacePlaceholder"


class WorkspaceRegistry:
    """Owns the catalog of navigable workspaces and validates identity."""

    _ENTRIES: tuple[WorkspaceDescriptor, ...] = (
        WorkspaceDescriptor(
            workspace_id=WorkspaceId.START,
            locale_key="workspace.start",
            icon_id=None,
            shortcut="Ctrl+1",
            description_locale_key="workspace.start.description",
        ),
        WorkspaceDescriptor(
            workspace_id=WorkspaceId.LIVE_MONITOR,
            locale_key="workspace.live_monitor",
            icon_id=None,
            shortcut="Ctrl+2",
            description_locale_key="workspace.live_monitor.description",
        ),
        WorkspaceDescriptor(
            workspace_id=WorkspaceId.WIDEBAND_SWEEP,
            locale_key="workspace.wideband_sweep",
            icon_id=None,
            shortcut="Ctrl+3",
            description_locale_key="workspace.wideband_sweep.description",
        ),
        WorkspaceDescriptor(
            workspace_id=WorkspaceId.OFFLINE_DFL,
            locale_key="workspace.offline_dfl",
            icon_id=None,
            shortcut="Ctrl+4",
            description_locale_key="workspace.offline_dfl.description",
        ),
        WorkspaceDescriptor(
            workspace_id=WorkspaceId.CALIBRATION,
            locale_key="workspace.calibration",
            icon_id=None,
            shortcut="Ctrl+5",
            description_locale_key="workspace.calibration.description",
        ),
        WorkspaceDescriptor(
            workspace_id=WorkspaceId.RECORDING_REPLAY,
            locale_key="workspace.recording_replay",
            icon_id=None,
            shortcut="Ctrl+6",
            description_locale_key="workspace.recording_replay.description",
        ),
        WorkspaceDescriptor(
            workspace_id=WorkspaceId.DIAGNOSTICS,
            locale_key="workspace.diagnostics",
            icon_id=None,
            shortcut="Ctrl+7",
            description_locale_key="workspace.diagnostics.description",
        ),
    )

    def __init__(self, placeholders: Mapping[WorkspaceId, WorkspacePlaceholder] | None = None) -> None:
        self._placeholders: dict[WorkspaceId, WorkspacePlaceholder] = dict(placeholders or {})
        ids = [descriptor.workspace_id for descriptor in self._ENTRIES]
        if len(set(ids)) != len(ids):
            raise ValueError("workspace catalog contains duplicate workspace_id")
        if {item.value for item in ids} != {item.value for item in WorkspaceId}:
            raise ValueError("workspace catalog does not cover every WorkspaceId")

    def descriptors(self) -> tuple[WorkspaceDescriptor, ...]:
        return self._ENTRIES

    def descriptor(self, workspace_id: WorkspaceId) -> WorkspaceDescriptor:
        for descriptor in self._ENTRIES:
            if descriptor.workspace_id is workspace_id:
                return descriptor
        raise KeyError(f"unknown workspace_id: {workspace_id}")

    def shortcuts(self) -> tuple[str, ...]:
        shortcuts: list[str] = []
        for descriptor in self._ENTRIES:
            if descriptor.shortcut:
                shortcuts.append(descriptor.shortcut)
        return tuple(shortcuts)

    def placeholders(self) -> Mapping[WorkspaceId, WorkspacePlaceholder]:
        return dict(self._placeholders)

    def placeholder(self, workspace_id: WorkspaceId) -> WorkspacePlaceholder:
        if workspace_id not in self._placeholders:
            raise KeyError(f"no placeholder registered for workspace: {workspace_id}")
        return self._placeholders[workspace_id]

    def attach_placeholders(self, factory: PlaceholderFactory) -> None:
        """Build placeholders for every workspace using the supplied factory."""

        for descriptor in self._ENTRIES:
            placeholder = factory(descriptor.workspace_id)
            placeholder.setObjectName(f"{_PLACEHOLDER_OBJECT_NAME}.{descriptor.workspace_id.value}")
            self._placeholders[descriptor.workspace_id] = placeholder

    def iter_placeholders(self) -> Iterable[tuple[WorkspaceDescriptor, WorkspacePlaceholder]]:
        for descriptor in self._ENTRIES:
            yield descriptor, self.placeholder(descriptor.workspace_id)


__all__ = [
    "PlaceholderFactory",
    "WorkspaceDescriptor",
    "WorkspacePlaceholder",
    "WorkspaceRegistry",
]

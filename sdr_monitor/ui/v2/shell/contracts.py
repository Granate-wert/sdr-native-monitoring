"""Immutable composition contracts for the presentation-only V2 shell."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from ..design.icons import V2IconId
from ..i18n import text
from .close_lifecycle import CloseState

WorkspaceFactory = Callable[[], QWidget]


@dataclass(frozen=True, slots=True)
class WorkspaceDefinition:
    """One lazy V2 workspace entry and its contextual inspector factory."""

    workspace_id: str
    label: str
    description: str
    icon: V2IconId
    workspace_factory: WorkspaceFactory
    inspector_factory: WorkspaceFactory
    optional: bool = False
    label_key: str | None = None
    description_key: str | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id must not be blank")
        if not self.label.strip() or not self.description.strip():
            raise ValueError("workspace labels must not be blank")
        if not callable(self.workspace_factory) or not callable(self.inspector_factory):
            raise TypeError("workspace factories must be callable")
        if self.label_key is not None and not self.label_key.strip():
            raise ValueError("workspace label_key must not be blank")
        if self.description_key is not None and not self.description_key.strip():
            raise ValueError("workspace description_key must not be blank")

    def resolved_label(self) -> str:
        """Resolve an optional V2 presentation key without changing workspace identity."""

        return self.label if self.label_key is None else text(self.label_key)

    def resolved_description(self) -> str:
        """Resolve an optional V2 presentation key without changing workspace identity."""

        return self.description if self.description_key is None else text(self.description_key)


@dataclass(frozen=True, slots=True)
class ClosePort:
    """An externally-owned lifecycle endpoint with no implicit recovery action."""

    name: str
    can_close: Callable[[], bool]
    shutdown: Callable[[], None]
    request_shutdown: Callable[[], CloseState] | None = None
    poll_shutdown: Callable[[], CloseState] | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("close port name must not be blank")
        if not callable(self.can_close) or not callable(self.shutdown):
            raise TypeError("close port callbacks must be callable")
        if (self.request_shutdown is None) != (self.poll_shutdown is None):
            raise ValueError("async close requires both request and poll callbacks")
        if self.request_shutdown is not None and (
            not callable(self.request_shutdown) or not callable(self.poll_shutdown)
        ):
            raise TypeError("async close callbacks must be callable")


@dataclass(frozen=True, slots=True)
class V2ShellContext:
    """Externally assembled, inert state supplied to one AppShellV2 instance."""

    workspaces: tuple[WorkspaceDefinition, ...]
    close_ports: tuple[ClosePort, ...] = ()
    automatic_discovery_enabled: bool = False
    initial_workspace_id: str = "home"

    def __post_init__(self) -> None:
        identifiers = tuple(item.workspace_id for item in self.workspaces)
        if not identifiers:
            raise ValueError("at least one V2 workspace is required")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("workspace ids must be unique")
        if self.initial_workspace_id not in identifiers:
            raise ValueError("initial workspace must be registered")
        names = tuple(port.name for port in self.close_ports)
        if len(set(names)) != len(names):
            raise ValueError("close port names must be unique")

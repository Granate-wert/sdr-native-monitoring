"""Inert UI V2 shell and composition contracts."""

from .app_shell import AppShellV2
from .contracts import ClosePort, V2ShellContext, WorkspaceDefinition

__all__ = ["AppShellV2", "ClosePort", "V2ShellContext", "WorkspaceDefinition"]

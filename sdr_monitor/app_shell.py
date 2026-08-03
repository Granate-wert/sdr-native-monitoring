"""Compatibility import for the standalone AppShell.

New code should import from ``sdr_monitor.ui.app_shell``.
"""

from .ui.app_shell import SDRAppShell, WorkspaceId

__all__ = ["SDRAppShell", "WorkspaceId"]

"""SDR-only AppShell: no Offline DFL workspace, no legacy fallback.

This package hosts the new surface (Home, Live, Sweep, Calibration,
Recording/Replay, Diagnostics) without registering the legacy Offline DFL
workspace.  Workspaces are imported at the last moment so Qt is not a hard
dependency for unit tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from esw_dfl.ui.app_shell import AppShell as BaseAppShell
from esw_dfl.ui.state import WorkspaceId
from esw_dfl.ui.workspace_registry import (
    WorkspaceDescriptor,
    WorkspaceRegistry as BaseWorkspaceRegistry,
)

if TYPE_CHECKING:
    from esw_dfl.ui.app_shell import AppShell as AppShellT


class SDRWorkspaceRegistry(BaseWorkspaceRegistry):
    """SDR workspace registry: DFL workspace excluded from navigation."""

    _ENTRIES: tuple = (
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

    def __init__(self, placeholders=None):
        # Bypass base __init__ validation: we intentionally contain a subset.
        # pylint: disable=super-init-not-called
        self._placeholders: dict = dict(placeholders or {})


class SDRAppShellBase(BaseAppShell):
    """Base shell with a workspace registry limited to SDR entries."""

    # Replace the default whole-tree registry with the SDR-only one.
    _workspace_registry_class: type[BaseWorkspaceRegistry] = SDRWorkspaceRegistry

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Replace the whole-tree registry with the SDR-only one and rebuild
        # the workspace host accordingly; no QShortcut or navigator links point
        # at the Offline DFL workspace.
        self._workspaces = SDRWorkspaceRegistry()
        self._populate_sdr_workspace_host()
        self._install_sdr_workspace_shortcuts()
        self.setWindowTitle(self._identity.display_name + " (SDR)")

    def _populate_sdr_workspace_host(self) -> None:
        self._workspace_host.clear()
        for descriptor in self._workspaces.descriptors():
            placeholder = self._make_placeholder(descriptor.workspace_id)
            self._workspaces._placeholders[descriptor.workspace_id] = placeholder
            self._workspace_host.addTab(placeholder, descriptor.locale_key)
        self.set_active_workspace(self._workspaces.descriptors()[0].workspace_id)

    def _install_sdr_workspace_shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut

        for shortcut in list(self._workspace_shortcuts):
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._workspace_shortcuts.clear()
        seen: set[str] = set()
        for descriptor in self._workspaces.descriptors():
            if descriptor.shortcut is None:
                continue
            if descriptor.shortcut in seen:
                raise ValueError(f"duplicate workspace shortcut: {descriptor.shortcut}")
            seen.add(descriptor.shortcut)
            action = QShortcut(QKeySequence(descriptor.shortcut), self)
            wid = descriptor.workspace_id
            action.activated.connect(lambda wid=wid: self.set_active_workspace(wid))
            self._workspace_shortcuts.append(action)


def create_sdr_app_shell(
    *,
    live_monitor_factory: "Callable[[], QWidget] | None" = None,
    sweep_workspace_factory: "Callable[[], QWidget] | None" = None,
    calibration_factory: "Callable[[], QWidget] | None" = None,
    recording_workspace_factory: "Callable[[], QWidget] | None" = None,
    diagnostics_workspace_factory: "Callable[[], QWidget] | None" = None,
    parent: QWidget | None = None,
) -> "type[SDRAppShellBase]":
    """Build an SDR-specific AppShell class wired with only SDR factories."""

    class _SDRAppShell(SDRAppShellBase):
        def __init__(self, **kwargs):
            merged = {
                "live_monitor_factory": live_monitor_factory,
                "sweep_workspace_factory": sweep_workspace_factory,
                # Offline DFL factory is intentionally left None.
                "offline_dfl_factory": None,
                "calibration_factory": calibration_factory,
                "measurement_panel_factory": None,
                "recording_workspace_factory": recording_workspace_factory,
                "diagnostics_workspace_factory": diagnostics_workspace_factory,
            }
            super().__init__(
                services=None,
                parent=parent,
                **{k: v for k, v in merged.items() if v is not None},
            )

    return _SDRAppShell


class SDRAppShell(SDRAppShellBase):
    """Real SDR AppShell pre-wired with real workplaces."""

    def __init__(self, **kwargs):
        from esw_dfl.ui.live_workspace import LiveMonitorWorkspace
        from esw_dfl.ui.sweep_workspace import SweepWorkspace
        from esw_dfl.ui.calibration_workspace import CalibrationWorkspace
        from esw_dfl.ui.recording_workspace import RecordingWorkspace
        from esw_dfl.ui.diagnostics_workspace import DiagnosticsWorkspace

        shell_cls = create_sdr_app_shell(
            live_monitor_factory=LiveMonitorWorkspace,
            sweep_workspace_factory=SweepWorkspace,
            calibration_factory=CalibrationWorkspace,
            recording_workspace_factory=RecordingWorkspace,
            diagnostics_workspace_factory=DiagnosticsWorkspace,
        )
        super().__init__(**kwargs)
        self._real_shell = shell_cls(**kwargs)
        # The adapter runs its own layout inside the same widget.
        # owner/member delegation is a shield until full migration (S03+).
        self._shell = self._real_shell

    def __getattr__(self, item):  # pragma: no cover - simple delegation
        return getattr(self._real_shell, item)


__all__ = ["SDRAppShell", "create_sdr_app_shell"]

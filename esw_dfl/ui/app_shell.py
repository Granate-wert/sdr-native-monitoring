"""Task-oriented AppShell used by the strangler presentation migration.

The shell owns:

* neutral identity (organisation/application/window title);
* navigation rail of seven workspace placeholders;
* a thin source navigator adapter over the existing measurement
  repository (read-only);
* a workspace host that reparents existing renderer widgets rather than
  recreating them;
* a context inspector, a bottom tool tab area and a compact health bar.

The shell does *not* own device calls, the DSP path, recording, sweep,
calibration, validation or renderer state.  Those responsibilities remain
with the existing controllers; the shell only adapts their public surface
to the new layout.

Workspace behaviour rules:

* :meth:`AppShell.set_active_workspace` is the single source of truth for
  the active workspace; downstream widgets observe
  :attr:`AppShell.active_workspace`.
* Renderer widgets are reparented **once** when the shell is built and
  never recreated on workspace switch.
* The shell exposes :meth:`AppShell.apply_preset` so the layout preset
  selector can operate without touching the legacy ``windowState`` blob.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import cast

from PySide6.QtCore import QByteArray, QObject, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from .commands import CommandRegistry, CommandSpec
from .identity import CURRENT_IDENTITY, ProductIdentity
from .i18n import LocaleId, Translator
from .layout_presets import (
    LayoutPreset,
    LayoutPresetCatalog,
    LayoutPresetId,
)
from .services import ApplicationServices
from .settings_migration import (
    MigrationResult,
    apply_migration,
    read_legacy_settings,
)
from .state import AppUiState, HealthUiState, WorkspaceId
from .workspace_registry import WorkspaceDescriptor, WorkspaceRegistry


# Recognised shell widget roles.  Kept as module constants so tests can
# import them without depending on layout-preset internals.
ROLE_NAVIGATION_RAIL = "p16AppShell.navigationRail"
ROLE_SOURCE_NAVIGATOR = "p16AppShell.sourceNavigator"
ROLE_WORKSPACE_HOST = "p16AppShell.workspaceHost"
ROLE_CONTEXT_INSPECTOR = "p16AppShell.contextInspector"
ROLE_BOTTOM_TOOLS = "p16AppShell.bottomTools"
ROLE_HEALTH_BAR = "p16AppShell.healthBar"
ROLE_PLACEHOLDER = "p16AppShell.workspacePlaceholder"

DEFAULT_GEOMETRY = (1500, 920)
MINIMUM_GEOMETRY = (1280, 720)


@dataclass(frozen=True, slots=True)
class ShellPresetSnapshot:
    """Concrete snapshot describing which areas a preset enables."""

    preset_id: LayoutPresetId
    visible_roles: tuple[str, ...]
    weights: Mapping[str, float] = field(default_factory=dict)


class _ShellCommandIds:
    RESET_LAYOUT = "view.reset_layout"
    APPLY_PRESET_DEFAULT = "view.layout_preset.default"
    APPLY_PRESET_COMPACT = "view.layout_preset.compact"
    APPLY_PRESET_ANALYSIS = "view.layout_preset.analysis"
    APPLY_PRESET_MONITORING = "view.layout_preset.monitoring"


def build_shell_command_registry(
    shell: "AppShell",
) -> CommandRegistry:
    """Build the registry of shell-owned commands (layout/preset)."""

    specifications: list[CommandSpec] = [
        CommandSpec(
            command_id=_ShellCommandIds.RESET_LAYOUT,
            text_key="layout.reset",
            icon_id=None,
            default_shortcut="Ctrl+Shift+R",
            handler=shell.reset_layout,
            audit_event="shell.reset_layout",
        ),
        CommandSpec(
            command_id=_ShellCommandIds.APPLY_PRESET_DEFAULT,
            text_key="layout.preset.default",
            icon_id=None,
            default_shortcut=None,
            handler=lambda: shell.apply_preset(LayoutPresetId.DEFAULT),
            audit_event="shell.apply_preset.default",
        ),
        CommandSpec(
            command_id=_ShellCommandIds.APPLY_PRESET_COMPACT,
            text_key="layout.preset.compact",
            icon_id=None,
            default_shortcut=None,
            handler=lambda: shell.apply_preset(LayoutPresetId.COMPACT),
            audit_event="shell.apply_preset.compact",
        ),
        CommandSpec(
            command_id=_ShellCommandIds.APPLY_PRESET_ANALYSIS,
            text_key="layout.preset.analysis",
            icon_id=None,
            default_shortcut=None,
            handler=lambda: shell.apply_preset(LayoutPresetId.ANALYSIS),
            audit_event="shell.apply_preset.analysis",
        ),
        CommandSpec(
            command_id=_ShellCommandIds.APPLY_PRESET_MONITORING,
            text_key="layout.preset.monitoring",
            icon_id=None,
            default_shortcut=None,
            handler=lambda: shell.apply_preset(LayoutPresetId.MONITORING),
            audit_event="shell.apply_preset.monitoring",
        ),
    ]
    return CommandRegistry(specifications)


class AppShell(QMainWindow):
    """Neutral application shell used while legacy ``MainWindow`` is kept."""

    workspace_changed = Signal(object)  # WorkspaceId
    preset_applied = Signal(object)  # LayoutPresetId
    session_changed = Signal(object)  # str | None
    workspace_attached = Signal(object)  # WorkspaceId

    def __init__(
        self,
        services: ApplicationServices | None = None,
        *,
        identity: ProductIdentity = CURRENT_IDENTITY,
        live_monitor_factory: Callable[[], QWidget] | None = None,
        sweep_workspace_factory: Callable[[], QWidget] | None = None,
        offline_dfl_factory: Callable[[], QWidget] | None = None,
        calibration_factory: Callable[[], QWidget] | None = None,
        measurement_panel_factory: Callable[[], QWidget] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._identity = identity
        self._services = services
        self.setObjectName("p16AppShell")
        self.setWindowTitle(identity.display_name)
        self.resize(*DEFAULT_GEOMETRY)
        self.setMinimumSize(*MINIMUM_GEOMETRY)
        self.setDockNestingEnabled(False)

        self._workspaces = WorkspaceRegistry()
        self._layout_catalog = LayoutPresetCatalog()
        self._active_workspace: WorkspaceId = WorkspaceId.START
        self._active_preset: LayoutPreset = self._layout_catalog.default()
        self._health_state: HealthUiState = HealthUiState()
        self._reparented_renderers: list[QWidget] = []
        self._shell_actions: dict[str, QAction] = {}
        self._migration_result: MigrationResult | None = None
        self._live_monitor_factory = live_monitor_factory
        self._sweep_workspace_factory = sweep_workspace_factory
        self._offline_dfl_factory = offline_dfl_factory
        self._calibration_factory = calibration_factory
        self._measurement_panel_factory = measurement_panel_factory
        self._attached_workspaces: dict[WorkspaceId, QWidget] = {}

        self._navigation_rail = QListWidget(self)
        self._navigation_rail.setObjectName(ROLE_NAVIGATION_RAIL)
        self._navigation_rail.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._navigation_rail.itemSelectionChanged.connect(self._on_navigation_selection)
        for descriptor in self._workspaces.descriptors():
            item = QListWidgetItem(descriptor.locale_key)
            item.setData(Qt.ItemDataRole.UserRole, descriptor.workspace_id.value)
            self._navigation_rail.addItem(item)

        self._source_navigator = QListWidget(self)
        self._source_navigator.setObjectName(ROLE_SOURCE_NAVIGATOR)
        self._refresh_source_navigator()

        self._workspace_host = QTabWidget(self)
        self._workspace_host.setObjectName(ROLE_WORKSPACE_HOST)
        self._workspace_host.setDocumentMode(True)
        self._workspaces.attach_placeholders(self._make_placeholder)
        for descriptor in self._workspaces.descriptors():
            placeholder = cast(QWidget, self._workspaces.placeholder(descriptor.workspace_id))
            self._workspace_host.addTab(placeholder, descriptor.locale_key)
        self._workspace_host.currentChanged.connect(self._on_host_tab_changed)

        self._context_inspector = QTabWidget(self)
        self._context_inspector.setObjectName(ROLE_CONTEXT_INSPECTOR)

        self._bottom_tools = QTabWidget(self)
        self._bottom_tools.setObjectName(ROLE_BOTTOM_TOOLS)

        self._health_bar_label = QLabel(self)
        self._health_bar_label.setObjectName(ROLE_HEALTH_BAR)

        self.status_bar = QStatusBar(self)
        self.status_bar.setObjectName("p16AppShell.statusBar")
        self.status_bar.addPermanentWidget(self._health_bar_label)
        self.setStatusBar(self.status_bar)

        self._install_dock_widgets()
        self._install_default_tabs()
        self._populate_workspace_host()
        self._apply_preset_snapshot(_snapshot(self._active_preset))
        self._build_shell_commands()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def identity(self) -> ProductIdentity:
        return self._identity

    @property
    def application_services(self) -> ApplicationServices | None:
        return self._services

    @property
    def workspaces(self) -> WorkspaceRegistry:
        return self._workspaces

    @property
    def layout_catalog(self) -> LayoutPresetCatalog:
        return self._layout_catalog

    @property
    def active_workspace(self) -> WorkspaceId:
        return self._active_workspace

    @property
    def active_preset_id(self) -> LayoutPresetId:
        return self._active_preset.preset_id

    @property
    def health_state(self) -> HealthUiState:
        return self._health_state

    @property
    def migration_result(self) -> MigrationResult | None:
        return self._migration_result

    @property
    def navigation_rail(self) -> QListWidget:
        return self._navigation_rail

    @property
    def source_navigator(self) -> QListWidget:
        return self._source_navigator

    @property
    def workspace_host(self) -> QTabWidget:
        return self._workspace_host

    @property
    def context_inspector(self) -> QTabWidget:
        return self._context_inspector

    @property
    def bottom_tools(self) -> QTabWidget:
        return self._bottom_tools

    @property
    def shell_actions(self) -> Mapping[str, QAction]:
        return dict(self._shell_actions)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_active_workspace(self, workspace_id: WorkspaceId) -> None:
        if workspace_id not in WorkspaceId:
            raise ValueError(f"unknown workspace: {workspace_id}")
        descriptor = self._workspaces.descriptor(workspace_id)
        if descriptor.workspace_id == self._active_workspace:
            self._select_navigation_row(descriptor)
            return
        self._active_workspace = descriptor.workspace_id
        self._ensure_workspace_attached(descriptor.workspace_id)
        widget = self._attached_workspaces.get(
            descriptor.workspace_id
        ) or cast(QWidget, self._workspaces.placeholder(descriptor.workspace_id))
        index = self._workspace_host.indexOf(widget)
        if index >= 0:
            self._workspace_host.setCurrentIndex(index)
        self._select_navigation_row(descriptor)
        self.workspace_changed.emit(descriptor.workspace_id)

    def attach_workspace(self, workspace_id: WorkspaceId, widget: QWidget) -> None:
        """Replace the placeholder tab for *workspace_id* with a real widget.

        The placeholder is removed from the host; the attached widget takes
        its tab position and object name suffix, so navigation and the
        host-tab-change handler keep working.  Attaching is idempotent for
        the same widget; attaching a second widget replaces the first.
        """

        if workspace_id not in WorkspaceId:
            raise ValueError(f"unknown workspace: {workspace_id}")
        descriptor = self._workspaces.descriptor(workspace_id)
        previous = self._attached_workspaces.get(workspace_id)
        if previous is widget:
            return
        index = -1
        if previous is not None:
            index = self._workspace_host.indexOf(previous)
            if index >= 0:
                self._workspace_host.removeTab(index)
        else:
            index = self._workspace_host.indexOf(
                cast(QWidget, self._workspaces.placeholder(workspace_id))
            )
        if index < 0:
            index = self._workspace_host.count()
        widget.setParent(self._workspace_host)
        widget.setObjectName(f"{ROLE_WORKSPACE_HOST}.{workspace_id.value}")
        self._workspace_host.insertTab(index, widget, descriptor.locale_key)
        self._attached_workspaces[workspace_id] = widget
        self.workspace_attached.emit(workspace_id)

    def attached_workspace(self, workspace_id: WorkspaceId) -> QWidget | None:
        return self._attached_workspaces.get(workspace_id)

    def apply_preset(self, preset_id: LayoutPresetId) -> None:
        preset = self._layout_catalog.preset(preset_id)
        self._active_preset = preset
        self._apply_preset_snapshot(_snapshot(preset))
        self.preset_applied.emit(preset_id)

    def reset_layout(self) -> None:
        self.apply_preset(LayoutPresetId.DEFAULT)

    def reparent_renderer_widgets(self, widgets: Iterable[QWidget]) -> None:
        for widget in widgets:
            if widget.parent() is not self._workspace_host:
                widget.setParent(self._workspace_host)
                self._reparented_renderers.append(widget)
        for widget in self._reparented_renderers:
            widget.show()

    def set_health_state(self, state: HealthUiState) -> None:
        self._health_state = state
        if state.items:
            self._health_bar_label.setText(_format_health_summary(state))
        else:
            self._health_bar_label.setText(str(Translator(LocaleId.RU).text("status.ok")))
        self._health_bar_label.setAccessibleDescription(_format_health_summary(state))

    def run_settings_migration(self) -> MigrationResult:
        legacy = read_legacy_settings()
        self._migration_result = apply_migration(legacy=legacy)
        return self._migration_result

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        # Close attached workspaces before accepting the shell close.
        for widget in tuple(self._attached_workspaces.values()):
            try:
                shutdown = getattr(widget, "request_shutdown", None)
                if callable(shutdown):
                    shutdown()
                else:
                    widget.close()
            except RuntimeError:
                continue
        super().closeEvent(event)

    def save_geometry(self) -> QByteArray:
        return self.saveGeometry()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    def _install_dock_widgets(self) -> None:
        from PySide6.QtWidgets import QDockWidget

        for role, widget, title_key, area in (
            ("navigation", self._navigation_rail, "shell.navigation", Qt.DockWidgetArea.LeftDockWidgetArea),
            ("source_navigator", self._source_navigator, "shell.source_navigator", Qt.DockWidgetArea.LeftDockWidgetArea),
            (
                "context_inspector",
                self._context_inspector,
                "shell.context_inspector",
                Qt.DockWidgetArea.RightDockWidgetArea,
            ),
            ("bottom_tools", self._bottom_tools, "shell.bottom_tools", Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            dock = QDockWidget(title_key, self)
            dock.setObjectName(f"p16AppShell.dock.{role}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            setattr(self, f"_dock_{role}", dock)
        # The workspace host is the central widget, not a dock: dock
        # nesting is disabled and the central area carries the workspace.
        self.setCentralWidget(self._workspace_host)
        setattr(self, "_central_dock_widget", self._workspace_host)

    def _install_default_tabs(self) -> None:
        measurement_widget = (
            self._measurement_panel_factory() if self._measurement_panel_factory is not None else QWidget(self._bottom_tools)
        )
        self._bottom_tools.addTab(measurement_widget, "shell.bottom_tools.measurements")
        self._bottom_tools.addTab(QWidget(self._bottom_tools), "shell.bottom_tools.events")
        self._bottom_tools.addTab(QWidget(self._bottom_tools), "shell.bottom_tools.logs")
        self._context_inspector.addTab(QWidget(self._context_inspector), "shell.context_inspector.properties")
        self._context_inspector.addTab(QWidget(self._context_inspector), "shell.context_inspector.history")

    def _populate_workspace_host(self) -> None:
        for descriptor in self._workspaces.descriptors():
            placeholder = cast(QWidget, self._workspaces.placeholder(descriptor.workspace_id))
            if self._workspace_host.indexOf(placeholder) < 0:
                self._workspace_host.addTab(placeholder, descriptor.locale_key)
        self.set_active_workspace(WorkspaceId.START)

    def _ensure_workspace_attached(self, workspace_id: WorkspaceId) -> None:
        """Lazily attach factory-provided workspaces on first activation.

        Only runs when a factory was supplied at construction time; the
        default shell keeps placeholders so tests and consumers that do
        not want device services are unaffected.
        """

        if workspace_id in self._attached_workspaces:
            return
        if workspace_id is WorkspaceId.LIVE_MONITOR:
            factory = self._live_monitor_factory
        elif workspace_id is WorkspaceId.WIDEBAND_SWEEP:
            factory = self._sweep_workspace_factory
        elif workspace_id is WorkspaceId.OFFLINE_DFL:
            factory = self._offline_dfl_factory
        elif workspace_id is WorkspaceId.CALIBRATION:
            factory = self._calibration_factory
        else:
            return
        if factory is None:
            return
        widget = factory()
        self.attach_workspace(workspace_id, widget)

    def _make_placeholder(self, workspace_id: WorkspaceId) -> QWidget:
        placeholder = QWidget(self._workspace_host)
        placeholder.setObjectName(f"{ROLE_PLACEHOLDER}.{workspace_id.value}")
        return placeholder

    def _refresh_source_navigator(self) -> None:
        self._source_navigator.clear()
        sessions: list[str] = []
        repository = getattr(self._services, "repository", None) if self._services is not None else None
        if repository is not None:
            list_sessions = getattr(repository, "list_sessions", None)
            if callable(list_sessions):
                try:
                    sessions = [str(item) for item in list_sessions()]
                except Exception:  # pragma: no cover - repository error fallback
                    sessions = []
        for session_id in sessions:
            item = QListWidgetItem(session_id)
            item.setData(Qt.ItemDataRole.UserRole, session_id)
            self._source_navigator.addItem(item)
        if not sessions:
            placeholder = QListWidgetItem("shell.source_navigator.empty")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._source_navigator.addItem(placeholder)
        self._source_navigator.itemSelectionChanged.connect(self._on_source_selection)

    def _on_source_selection(self) -> None:
        item = self._source_navigator.currentItem()
        if item is None:
            self.session_changed.emit(None)
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, str) and data:
            self.session_changed.emit(data)

    def _on_navigation_selection(self) -> None:
        item = self._navigation_rail.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            workspace_id = WorkspaceId(str(data))
        except ValueError:
            return
        self.set_active_workspace(workspace_id)

    def _on_host_tab_changed(self, index: int) -> None:
        widget = self._workspace_host.widget(index)
        if widget is None:
            return
        descriptor = self._descriptor_for_placeholder(widget)
        if descriptor is None:
            return
        if descriptor.workspace_id != self._active_workspace:
            self._active_workspace = descriptor.workspace_id
            self._ensure_workspace_attached(descriptor.workspace_id)
            self._select_navigation_row(descriptor)
            self.workspace_changed.emit(descriptor.workspace_id)

    def _descriptor_for_placeholder(self, widget: QWidget) -> WorkspaceDescriptor | None:
        name = widget.objectName()
        for descriptor in self._workspaces.descriptors():
            if name.endswith(f".{descriptor.workspace_id.value}"):
                return descriptor
        return None

    def _select_navigation_row(self, descriptor: WorkspaceDescriptor) -> None:
        for row in range(self._navigation_rail.count()):
            item = self._navigation_rail.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == descriptor.workspace_id.value:
                self._navigation_rail.setCurrentItem(item)
                return

    def _apply_preset_snapshot(self, snapshot: ShellPresetSnapshot) -> None:
        visibility = {
            "navigation": snapshot.visible_roles.__contains__("navigation"),
            "source_navigator": snapshot.visible_roles.__contains__("source_navigator"),
            "context_inspector": snapshot.visible_roles.__contains__("context_inspector"),
            "bottom_tools": snapshot.visible_roles.__contains__("bottom_tools"),
        }
        for role, visible in visibility.items():
            dock = getattr(self, f"_dock_{role}", None)
            if dock is None:
                continue
            dock.setVisible(visible)
        if snapshot.visible_roles.__contains__("workspace_host"):
            self._workspace_host.show()

    def _build_shell_commands(self) -> None:
        registry = build_shell_command_registry(self)
        parent: QObject = self
        for command_id in registry.command_ids():
            action = registry.create_action(
                command_id,
                parent,
                self._state_supplier,
                audit=self._audit_command,
            )
            self._shell_actions[command_id] = action

    def _state_supplier(self) -> AppUiState:
        return AppUiState(active_workspace=self._active_workspace)

    def _audit_command(self, specification: CommandSpec, checked: bool) -> None:
        # Audit hook stays inside the shell: tests can observe it through
        # :attr:`shell_actions`; production code is free to forward to a
        # logger in a later package.
        _ = (specification, checked)


def _snapshot(preset: LayoutPreset) -> ShellPresetSnapshot:
    visible = tuple(area.role for area in preset.areas if area.visible)
    weights = {area.role: area.weight for area in preset.areas}
    return ShellPresetSnapshot(preset_id=preset.preset_id, visible_roles=visible, weights=weights)


def _format_health_summary(state: HealthUiState) -> str:
    if not state.items:
        return state.overall.value
    parts: list[str] = []
    for item in state.items[:3]:
        parts.append(f"{item.key}: {item.text}")
    return "; ".join(parts)


def shell_placeholder_widget(
    host: QWidget | None = None,
    *,
    workspace_id: WorkspaceId = WorkspaceId.START,
) -> QWidget:
    """Test-only helper that builds a placeholder widget without a shell."""

    placeholder = QWidget(host)
    placeholder.setObjectName(f"{ROLE_PLACEHOLDER}.{workspace_id.value}")
    return placeholder


__all__ = [
    "AppShell",
    "DEFAULT_GEOMETRY",
    "MINIMUM_GEOMETRY",
    "ROLE_BOTTOM_TOOLS",
    "ROLE_CONTEXT_INSPECTOR",
    "ROLE_HEALTH_BAR",
    "ROLE_NAVIGATION_RAIL",
    "ROLE_PLACEHOLDER",
    "ROLE_SOURCE_NAVIGATOR",
    "ROLE_WORKSPACE_HOST",
    "ShellPresetSnapshot",
    "build_shell_command_registry",
    "shell_placeholder_widget",
]

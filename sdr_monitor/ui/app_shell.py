"""Independent application shell for the standalone SDR product."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from ..services import SdrApplicationServices, build_default_sdr_services
from .components import EmptyState, StatusChip
from .design_tokens import StatusTone
from .i18n import DEFAULT_TRANSLATOR, Translator
from .icons import IconId, IconRegistry


class WorkspaceId(StrEnum):
    HOME = "home"
    LIVE = "live"
    SWEEP = "sweep"
    CALIBRATION = "calibration"
    RECORDING = "recording"
    DIAGNOSTICS = "diagnostics"


_WORKSPACES = (
    (WorkspaceId.HOME, "workspace.home", "home.description", IconId.HOME, "Ctrl+1"),
    (WorkspaceId.LIVE, "workspace.live", "live.description", IconId.LIVE, "Ctrl+L"),
    (WorkspaceId.SWEEP, "workspace.sweep", "sweep.description", IconId.SWEEP, "Ctrl+W"),
    (WorkspaceId.CALIBRATION, "workspace.calibration", "calibration.description", IconId.CALIBRATION, "Ctrl+K"),
    (WorkspaceId.RECORDING, "workspace.recording", "recording.description", IconId.RECORDING, "Ctrl+R"),
    (WorkspaceId.DIAGNOSTICS, "workspace.diagnostics", "diagnostics.description", IconId.DIAGNOSTICS, "Ctrl+D"),
)


class WorkspacePlaceholder(QWidget):
    """Accessible transitional content, replaced by bounded S05–S10 workspaces."""
    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        heading = QLabel(title)
        heading.setProperty("role", "heading")
        heading.setAccessibleName(title)
        layout.addWidget(heading)
        layout.addWidget(EmptyState(title, description))
        layout.addStretch(1)

    def shutdown(self) -> None:
        """Common lifecycle hook for future live workspace resources."""


class SDRAppShell(QMainWindow):
    """The only production shell for SDR Native Monitoring.

    The shell owns navigation, workspace lifetime and presentation state only;
    device work remains behind ``SdrApplicationServices``.
    """
    workspace_changed = Signal(WorkspaceId)

    def __init__(self, *, services: SdrApplicationServices | None = None, translator: Translator | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services or build_default_sdr_services()
        self._translator = translator or DEFAULT_TRANSLATOR
        self._workspace_factories: dict[WorkspaceId, Callable[[], QWidget]] = {}
        self._workspace_pages: dict[WorkspaceId, QWidget] = {}
        self._nav_buttons: dict[WorkspaceId, QToolButton] = {}
        self._shortcuts: list[QShortcut] = []
        self._active_workspace = WorkspaceId.HOME
        self._rail_expanded = False
        self._inspector_visible = True
        self._build_shell()
        self._install_shortcuts()
        self.set_active_workspace(WorkspaceId.HOME)

    @property
    def active_workspace(self) -> WorkspaceId:
        return self._active_workspace

    def register_workspace(self, workspace_id: WorkspaceId, factory: Callable[[], QWidget]) -> None:
        self._workspace_factories[workspace_id] = factory
        old = self._workspace_pages.pop(workspace_id, None)
        if old is not None:
            self._dispose_workspace(old)
        if workspace_id is self._active_workspace:
            self.set_active_workspace(workspace_id, force=True)

    def set_active_workspace(self, workspace_id: WorkspaceId, *, force: bool = False) -> None:
        if workspace_id not in self._nav_buttons:
            raise ValueError(f"unknown standalone workspace: {workspace_id}")
        if workspace_id is self._active_workspace and not force and self._stack.currentWidget() is not None:
            return
        page = self._workspace_pages.get(workspace_id)
        if page is None:
            page = self._create_workspace(workspace_id)
            self._workspace_pages[workspace_id] = page
            self._stack.addWidget(page)
        self._stack.setCurrentWidget(page)
        self._active_workspace = workspace_id
        for current_id, button in self._nav_buttons.items():
            button.setChecked(current_id is workspace_id)
        self.workspace_changed.emit(workspace_id)

    def toggle_navigation(self) -> None:
        self._rail_expanded = not self._rail_expanded
        self._rail.setFixedWidth(216 if self._rail_expanded else 64)
        style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon if self._rail_expanded else Qt.ToolButtonStyle.ToolButtonIconOnly
        for button in self._nav_buttons.values():
            button.setToolButtonStyle(style)

    def toggle_inspector(self) -> None:
        self._inspector_visible = not self._inspector_visible
        self._inspector.setVisible(self._inspector_visible)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        for page in tuple(self._workspace_pages.values()):
            self._dispose_workspace(page)
        self._workspace_pages.clear()
        super().closeEvent(event)

    def _build_shell(self) -> None:
        self.setWindowTitle(self._translator.text("app.name"))
        self.setMinimumSize(1024, 640)
        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_top_bar())
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self._rail = self._build_navigation()
        content.addWidget(self._rail)
        self._stack = QStackedWidget()
        content.addWidget(self._stack, 1)
        self._inspector = self._build_inspector()
        content.addWidget(self._inspector)
        root_layout.addLayout(content, 1)
        self.setCentralWidget(root)
        status = self.statusBar()
        status.setFixedHeight(28)
        status.addPermanentWidget(StatusChip(self._translator.text("status.not_connected"), StatusTone.NEUTRAL))
        status.showMessage(self._translator.text("status.ready"))

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(48)
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        icon = QLabel()
        icon.setPixmap(IconRegistry.icon(IconId.APP, size=24).pixmap(24, 24))
        title = QLabel(self._translator.text("app.name"))
        title.setProperty("role", "heading")
        row.addWidget(icon)
        row.addWidget(title)
        row.addStretch(1)
        inspector_button = QPushButton(self._translator.text("action.collapse_inspector"))
        inspector_button.clicked.connect(self.toggle_inspector)
        row.addWidget(inspector_button)
        return bar

    def _build_navigation(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("navigationRail")
        rail.setFixedWidth(64)
        column = QVBoxLayout(rail)
        column.setContentsMargins(4, 8, 4, 8)
        expand = QToolButton()
        expand.setIcon(IconRegistry.icon(IconId.APP, size=20))
        expand.setToolTip(self._translator.text("action.expand_navigation"))
        expand.setAccessibleName(self._translator.text("action.expand_navigation"))
        expand.clicked.connect(self.toggle_navigation)
        column.addWidget(expand)
        for workspace_id, label_key, description_key, icon_id, _ in _WORKSPACES:
            button = QToolButton()
            button.setCheckable(True)
            button.setIcon(IconRegistry.icon(icon_id, size=20))
            button.setText(self._translator.text(label_key))
            button.setToolTip(self._translator.text(description_key))
            button.setAccessibleName(self._translator.text(label_key))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.clicked.connect(lambda checked=False, identifier=workspace_id: self.set_active_workspace(identifier))
            column.addWidget(button)
            self._nav_buttons[workspace_id] = button
        column.addStretch(1)
        return rail

    def _build_inspector(self) -> QScrollArea:
        area = QScrollArea()
        area.setObjectName("inspector")
        area.setWidgetResizable(True)
        area.setFixedWidth(320)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(EmptyState(self._translator.text("inspector.title"), self._translator.text("inspector.empty")))
        layout.addStretch(1)
        area.setWidget(content)
        return area

    def _create_workspace(self, workspace_id: WorkspaceId) -> QWidget:
        factory = self._workspace_factories.get(workspace_id)
        if factory is not None:
            return factory()
        _, label_key, description_key, _, _ = next(item for item in _WORKSPACES if item[0] is workspace_id)
        return WorkspacePlaceholder(self._translator.text(label_key), self._translator.text(description_key))

    def _install_shortcuts(self) -> None:
        for workspace_id, _, _, _, shortcut in _WORKSPACES:
            action = QShortcut(QKeySequence(shortcut), self)
            action.activated.connect(lambda identifier=workspace_id: self.set_active_workspace(identifier))
            self._shortcuts.append(action)

    @staticmethod
    def _dispose_workspace(widget: QWidget) -> None:
        shutdown = getattr(widget, "shutdown", None)
        if callable(shutdown):
            shutdown()
        widget.close()
        widget.setParent(None)
        widget.deleteLater()
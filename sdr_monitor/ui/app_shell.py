"""Independent application shell for the standalone SDR product."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..services import SdrApplicationServices, build_default_sdr_services
from ..services.diagnostics_session import DiagnosticsService
from .components import EmptyState, StatusChip
from .design_tokens import StatusTone
from .dialogs import DeviceDiscoveryDialog
from .dialogs.tinysa_source_activation import TinySaSourceActivationDialog
from .hackrf_activation_registration import HackrfActivationWorkspaceRegistration
from .i18n import DEFAULT_TRANSLATOR, Translator
from .icons import IconId, IconRegistry
from .presenters import CalibrationPresenter, DiagnosticsPresenter, LivePresenter, RecordingPresenter, SweepPresenter
from .presenters.tinysa_source_activation_presenter import TinySaSourceActivationPresenter
from .tinysa_analyzer_registration import TinySaAnalyzerWorkspaceRegistration
from .workspaces import (
    CalibrationWorkspace,
    DiagnosticsWorkspace,
    HomeWorkspace,
    LiveMonitorWorkspace,
    RecordingWorkspace,
    SweepWorkspace,
)


class WorkspaceId(StrEnum):
    HOME = "home"
    LIVE = "live"
    SWEEP = "sweep"
    CALIBRATION = "calibration"
    RECORDING = "recording"
    DIAGNOSTICS = "diagnostics"


class OptionalWorkspaceId(StrEnum):
    """A workspace that appears only after its explicit product hand-off."""

    HACKRF_ACTIVATION = "hackrf_activation"
    TINYSA_ANALYZER = "tinysa_analyzer"


WorkspaceKey = WorkspaceId | OptionalWorkspaceId


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
    workspace_changed = Signal(object)
    tinysa_activation_completed = Signal(object)
    tinysa_analyzer_workspace_ready = Signal(object)

    def __init__(
        self,
        *,
        services: SdrApplicationServices | None = None,
        translator: Translator | None = None,
        tinysa_activation_presenter_factory: Callable[[], TinySaSourceActivationPresenter] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.services = services or build_default_sdr_services()
        self._translator = translator or DEFAULT_TRANSLATOR
        self._live_presenter = LivePresenter(self.services.live_sdr, self)
        self._sweep_presenter = SweepPresenter(self.services.sweep, self)
        self._calibration_presenter = CalibrationPresenter(self.services.calibration, self)
        self._recording_presenter = RecordingPresenter(self.services.recording, self)
        self._diagnostics_presenter = DiagnosticsPresenter(
            cast(DiagnosticsService, self.services.diagnostics),
            self,
        )
        self._discovery_dialog: DeviceDiscoveryDialog | None = None
        self._tinysa_activation_dialog: TinySaSourceActivationDialog | None = None
        self._tinysa_activation_presenter_factory = (
            tinysa_activation_presenter_factory
            or self._make_default_tinysa_activation_presenter
        )
        if not callable(self._tinysa_activation_presenter_factory):
            raise TypeError("tinySA activation presenter factory must be callable")
        self._workspace_factories: dict[WorkspaceKey, Callable[[], QWidget]] = {}
        self._workspace_pages: dict[WorkspaceKey, QWidget] = {}
        self._nav_buttons: dict[WorkspaceKey, QToolButton] = {}
        self._shortcuts: list[QShortcut] = []
        self._active_workspace: WorkspaceKey = WorkspaceId.HOME
        self._rail_expanded = False
        self._inspector_visible = True
        self._auto_collapsed_inspector = False
        self._build_shell()
        self._register_s05_s07_workspaces()
        self._install_shortcuts()
        self.set_active_workspace(WorkspaceId.HOME)

    @property
    def active_workspace(self) -> WorkspaceKey:
        return self._active_workspace

    def register_workspace(self, workspace_id: WorkspaceId, factory: Callable[[], QWidget]) -> None:
        self._register_workspace_factory(workspace_id, factory)

    def _register_workspace_factory(self, workspace_id: WorkspaceKey, factory: Callable[[], QWidget]) -> None:
        self._workspace_factories[workspace_id] = factory
        old = self._workspace_pages.pop(workspace_id, None)
        if old is not None:
            self._dispose_workspace(old)
        if workspace_id is self._active_workspace:
            self.set_active_workspace(workspace_id, force=True)

    def set_active_workspace(self, workspace_id: WorkspaceKey, *, force: bool = False) -> None:
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

    @property
    def hackrf_activation_registered(self) -> bool:
        """Whether the optional HackRF UI has been explicitly made available."""

        return OptionalWorkspaceId.HACKRF_ACTIVATION in self._nav_buttons

    def register_hackrf_activation_workspace(
        self,
        registration: HackrfActivationWorkspaceRegistration,
    ) -> None:
        """Expose one inert HackRF entry after an external plan hand-off.

        This does not select the workspace, preflight an identity or invoke a
        native factory.  A second registration is refused so an active owner
        cannot be silently replaced.
        """

        if not isinstance(registration, HackrfActivationWorkspaceRegistration):
            raise TypeError("HackRF activation registration is invalid")
        workspace_id = OptionalWorkspaceId.HACKRF_ACTIVATION
        if workspace_id in self._nav_buttons:
            raise RuntimeError("HackRF activation workspace is already registered")
        self._register_workspace_factory(workspace_id, registration.create_workspace)
        self._add_navigation_button(
            workspace_id,
            "HackRF activation",
            "Open the already admitted HackRF activation workflow",
            IconId.LIVE,
        )

    @property
    def tinysa_analyzer_registered(self) -> bool:
        """Whether a source-bound tinySA workspace was explicitly registered."""

        return OptionalWorkspaceId.TINYSA_ANALYZER in self._nav_buttons

    def register_tinysa_analyzer_workspace(
        self,
        registration: TinySaAnalyzerWorkspaceRegistration,
    ) -> None:
        """Make an already composed tinySA analyzer available without I/O."""

        if not isinstance(registration, TinySaAnalyzerWorkspaceRegistration):
            raise TypeError("tinySA analyzer registration is invalid")
        workspace_id = OptionalWorkspaceId.TINYSA_ANALYZER
        if workspace_id in self._nav_buttons:
            raise RuntimeError("tinySA analyzer workspace is already registered")
        self._register_workspace_factory(workspace_id, registration.create_workspace)
        self._add_navigation_button(
            workspace_id,
            "tinySA analyzer",
            "Open the already verified tinySA analyzer workspace",
            IconId.SWEEP,
        )

    def open_tinysa_source_activation_dialog(
        self,
        presenter: TinySaSourceActivationPresenter,
    ) -> None:
        """Show an inert dialog; discovery remains a separate human action."""

        if not isinstance(presenter, TinySaSourceActivationPresenter):
            raise TypeError("tinySA activation presenter is invalid")
        if self.tinysa_analyzer_registered:
            raise RuntimeError("tinySA analyzer workspace is already registered")
        existing = self._tinysa_activation_dialog
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        dialog = TinySaSourceActivationDialog(presenter, self)
        dialog.activation_completed.connect(self.tinysa_activation_completed.emit)
        dialog.registration_ready.connect(self._complete_tinysa_source_activation)
        dialog.finished.connect(self._clear_tinysa_source_activation_dialog)
        self._tinysa_activation_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _activate_tinysa_product_entry(self) -> None:
        """Open the lazy tinySA activation entry without automatic discovery."""

        if self.tinysa_analyzer_registered:
            self.set_active_workspace(OptionalWorkspaceId.TINYSA_ANALYZER)
            return
        existing = self._tinysa_activation_dialog
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        presenter: TinySaSourceActivationPresenter | None = None
        try:
            presenter = self._tinysa_activation_presenter_factory()
            if not isinstance(presenter, TinySaSourceActivationPresenter):
                raise TypeError("tinySA activation presenter is invalid")
            self.open_tinysa_source_activation_dialog(presenter)
        except Exception:  # noqa: BLE001 - factory failure must release a partial presenter.
            if isinstance(presenter, TinySaSourceActivationPresenter):
                presenter.shutdown()
                presenter.deleteLater()
            self.statusBar().showMessage("tinySA activation is unavailable.")

    @staticmethod
    def _make_default_tinysa_activation_presenter() -> TinySaSourceActivationPresenter:
        """Construct the concrete stack lazily; constructors perform no I/O."""

        from ..application.tinysa_source_activation import (
            TinySaSourceActivationApplicationService,
        )
        from ..services.tinysa_serial_source_backend import TinySaSerialSourceBackend
        from ..services.tinysa_source_composition import TinySaSourceCompositionService

        return TinySaSourceActivationPresenter(
            TinySaSourceActivationApplicationService(
                TinySaSourceCompositionService(TinySaSerialSourceBackend())
            )
        )

    def toggle_navigation(self) -> None:
        self._rail_expanded = not self._rail_expanded
        self._rail.setFixedWidth(216 if self._rail_expanded else 64)
        style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon if self._rail_expanded else Qt.ToolButtonStyle.ToolButtonIconOnly
        for button in self._nav_buttons.values():
            button.setToolButtonStyle(style)

    def toggle_inspector(self) -> None:
        self._inspector_visible = not self._inspector_visible
        self._inspector.setVisible(self._inspector_visible)

    def resizeEvent(self, event) -> None:
        narrow = event.size().width() <= 1280
        if narrow and self._inspector_visible:
            self._inspector_visible = False
            self._auto_collapsed_inspector = True
            self._inspector.setVisible(False)
        elif not narrow and self._auto_collapsed_inspector:
            self._inspector_visible = True
            self._auto_collapsed_inspector = False
            self._inspector.setVisible(True)
        super().resizeEvent(event)
    def closeEvent(self, event) -> None:
        hackrf_page = self._workspace_pages.get(OptionalWorkspaceId.HACKRF_ACTIVATION)
        if hackrf_page is not None and callable(getattr(hackrf_page, "blocks_shell_close", None)):
            if hackrf_page.blocks_shell_close():
                self.statusBar().showMessage("Stop HackRF Live before closing the application.")
                event.ignore()
                return
        dialog = self._tinysa_activation_dialog
        if dialog is not None and dialog.blocks_shell_close:
            self.statusBar().showMessage("Wait for the tinySA source operation before closing.")
            event.ignore()
            return
        if dialog is not None:
            dialog.close()
        for page in tuple(self._workspace_pages.values()):
            self._dispose_workspace(page)
        self._workspace_pages.clear()
        self._live_presenter.shutdown()
        self._sweep_presenter.shutdown()
        self._calibration_presenter.shutdown()
        self._recording_presenter.shutdown()
        self._diagnostics_presenter.shutdown()
        super().closeEvent(event)

    def _complete_tinysa_source_activation(self, registration: object) -> None:
        if not isinstance(registration, TinySaAnalyzerWorkspaceRegistration):
            self.statusBar().showMessage("tinySA source registration failed.")
            return
        self.register_tinysa_analyzer_workspace(registration)
        self._tinysa_entry.setText("Open tinySA analyzer")
        self.set_active_workspace(OptionalWorkspaceId.TINYSA_ANALYZER)
        workspace = self._workspace_pages.get(OptionalWorkspaceId.TINYSA_ANALYZER)
        if workspace is not None:
            self.tinysa_analyzer_workspace_ready.emit(workspace)

    def _clear_tinysa_source_activation_dialog(self, _result: int) -> None:
        dialog = self._tinysa_activation_dialog
        self._tinysa_activation_dialog = None
        if dialog is not None:
            dialog.shutdown()
            dialog.deleteLater()

    def _register_s05_s07_workspaces(self) -> None:
        self.register_workspace(WorkspaceId.HOME, self._make_home_workspace)
        self.register_workspace(WorkspaceId.LIVE, self._make_live_workspace)
        self.register_workspace(WorkspaceId.SWEEP, self._make_sweep_workspace)
        self.register_workspace(WorkspaceId.CALIBRATION, self._make_calibration_workspace)
        self.register_workspace(WorkspaceId.RECORDING, self._make_recording_workspace)
        self.register_workspace(WorkspaceId.DIAGNOSTICS, self._make_diagnostics_workspace)

    def _make_calibration_workspace(self) -> CalibrationWorkspace:
        return CalibrationWorkspace(self._calibration_presenter)

    def _make_recording_workspace(self) -> RecordingWorkspace:
        return RecordingWorkspace(self._recording_presenter)

    def _make_diagnostics_workspace(self) -> DiagnosticsWorkspace:
        return DiagnosticsWorkspace(self._diagnostics_presenter)

    def _make_live_workspace(self) -> LiveMonitorWorkspace:
        live = LiveMonitorWorkspace(self._live_presenter, self.services.profiles.load())
        live.discovery_requested.connect(self._show_discovery)
        self._inspector.setWidget(live.inspector)
        return live

    def _make_sweep_workspace(self) -> SweepWorkspace:
        return SweepWorkspace(self._sweep_presenter)
    def _show_discovery(self) -> None:
        dialog = DeviceDiscoveryDialog(self._live_presenter, self)
        dialog.device_selected.connect(self._select_live_device)
        dialog.device_selected.connect(lambda _device_id: self.set_active_workspace(WorkspaceId.LIVE))
        dialog.finished.connect(dialog.deleteLater)
        self._discovery_dialog = dialog
        dialog.show()
        dialog.discover()
    def _select_live_device(self, device_id: str) -> None:
        if device_id.startswith("manual:"):
            self._live_presenter.select_manual_uri(device_id.removeprefix("manual:"))
        else:
            self._live_presenter.select_device(device_id)
        self.set_active_workspace(WorkspaceId.LIVE)
    def _make_home_workspace(self) -> HomeWorkspace:
        home = HomeWorkspace()
        home.live_requested.connect(lambda: self.set_active_workspace(WorkspaceId.LIVE))
        home.sweep_requested.connect(lambda: self.set_active_workspace(WorkspaceId.SWEEP))
        home.discover_requested.connect(self._show_discovery)
        return home
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
        self._tinysa_entry = QPushButton("Connect tinySA…")
        self._tinysa_entry.setAccessibleDescription(
            "Open tinySA activation; no device enumeration occurs until Discover devices"
        )
        self._tinysa_entry.clicked.connect(self._activate_tinysa_product_entry)
        row.addWidget(self._tinysa_entry)
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
        self._navigation_layout = column
        self._navigation_has_bottom_stretch = False
        for workspace_id, label_key, description_key, icon_id, _ in _WORKSPACES:
            self._add_navigation_button(
                workspace_id,
                self._translator.text(label_key),
                self._translator.text(description_key),
                icon_id,
            )
        column.addStretch(1)
        self._navigation_has_bottom_stretch = True
        return rail

    def _add_navigation_button(
        self,
        workspace_id: WorkspaceKey,
        label: str,
        description: str,
        icon_id: IconId,
    ) -> None:
        button = QToolButton()
        button.setCheckable(True)
        button.setIcon(IconRegistry.icon(icon_id, size=20))
        button.setText(label)
        button.setToolTip(description)
        button.setAccessibleName(label)
        button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if self._rail_expanded
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        button.clicked.connect(
            lambda checked=False, identifier=workspace_id: self.set_active_workspace(identifier)
        )
        if self._navigation_has_bottom_stretch:
            self._navigation_layout.insertWidget(self._navigation_layout.count() - 1, button)
        else:
            self._navigation_layout.addWidget(button)
        self._nav_buttons[workspace_id] = button

    def _build_inspector(self) -> QScrollArea:
        area = QScrollArea()
        area.setObjectName("inspector")
        area.setAccessibleName("Inspector")
        area.setWidgetResizable(True)
        area.setFixedWidth(320)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(EmptyState(self._translator.text("inspector.title"), self._translator.text("inspector.empty")))
        layout.addStretch(1)
        area.setWidget(content)
        return area

    def _create_workspace(self, workspace_id: WorkspaceKey) -> QWidget:
        factory = self._workspace_factories.get(workspace_id)
        if factory is not None:
            return factory()
        if not isinstance(workspace_id, WorkspaceId):
            raise TypeError(f"optional workspace has no registered factory: {workspace_id}")
        _, label_key, description_key, _, _ = next(item for item in _WORKSPACES if item[0] is workspace_id)
        return WorkspacePlaceholder(self._translator.text(label_key), self._translator.text(description_key))

    def _install_shortcuts(self) -> None:
        for workspace_id, _, _, _, shortcut in _WORKSPACES:
            action = QShortcut(QKeySequence(shortcut), self)
            action.activated.connect(lambda identifier=workspace_id: self.set_active_workspace(identifier))
            self._shortcuts.append(action)

    def _dispose_workspace(self, widget: QWidget) -> None:
        self._stack.removeWidget(widget)
        shutdown = getattr(widget, "shutdown", None)
        if callable(shutdown):
            shutdown()
        widget.close()
        widget.setParent(None)
        widget.deleteLater()

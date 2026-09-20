"""Standalone AppShellV2 with inert navigation and no Legacy widget ownership."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..components import MeasurementStripItem, NavigationItem, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.fonts import register_windows_ui_fonts
from ..i18n import UiLocale, resolve_locale, set_active_locale, text
from .appearance_popover import AppearancePopover, theme_label
from .contracts import V2ShellContext, WorkspaceDefinition
from .placeholders import default_shell_context

_SETTINGS_ROOT = "ui_v2"
_SETTINGS_PREFIX = f"{_SETTINGS_ROOT}/shell/v1"
_SETTINGS_SCHEMA_KEY = f"{_SETTINGS_ROOT}/schema_version"
_SETTINGS_THEME_KEY = f"{_SETTINGS_ROOT}/shell/theme"
_SETTINGS_LOCALE_KEY = f"{_SETTINGS_ROOT}/shell/locale"
_NARROW_WIDTH = 1600
_INSPECTOR_DRAWER_WIDTH = 320
_DEFAULT_SHELL_SIZE = (1280, 720)


class _NarrowInspectorDrawer(QFrame):
    """Context inspector overlay for narrow V2 shells, with no service ownership."""

    close_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("ui2Root", True)
        self.setProperty("ui2Role", "panel")
        self.setProperty("ui2FocusRing", True)
        self.setAccessibleName(text("shell.drawer_context_inspector"))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(_INSPECTOR_DRAWER_WIDTH)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(0)
        self.hide()

    def set_content(self, widget: QWidget) -> None:
        self.clear_content()
        self._layout.addWidget(widget)

    def clear_content(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AppShellV2(QMainWindow):
    """A V2-only presentation shell that never starts a receiver by itself."""

    workspace_changed = Signal(str)

    def __init__(
        self,
        context: V2ShellContext | None = None,
        *,
        settings: QSettings | None = None,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        register_windows_ui_fonts()
        self._settings = settings or QSettings()
        self._locale = resolve_locale(self._settings.value(_SETTINGS_LOCALE_KEY))
        set_active_locale(self._locale)
        self._context = context or default_shell_context()
        self._theme = theme
        self._definitions: dict[str, WorkspaceDefinition] = {
            item.workspace_id: item for item in self._context.workspaces if not item.optional
        }
        if self._context.initial_workspace_id not in self._definitions:
            raise ValueError("initial V2 workspace cannot be optional")
        self._workspace_pages: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, NavigationItem] = {}
        self._shutdown_port_names: set[str] = set()
        self._close_started = False
        self._close_timer = QTimer(self)
        self._close_timer.setInterval(25)
        self._close_timer.timeout.connect(self._poll_close)
        self._active_workspace_id = self._context.initial_workspace_id
        self._inspector_workspace_id: str | None = None
        self._navigation_expanded = False
        self._inspector_requested = True
        self._inspector_hidden_for_narrow_width = False
        self._narrow_inspector_drawer_open = False
        self._navigation_has_bottom_stretch = False
        self._appearance_popover: AppearancePopover | None = None
        self._is_closed = False
        self._build_shell()
        self._restore_settings()
        self.set_theme(self._theme)
        self.select_workspace(self._active_workspace_id, force=True)
        self._apply_responsive_layout()

    @property
    def active_workspace_id(self) -> str:
        return self._active_workspace_id

    @property
    def inspector_workspace_id(self) -> str | None:
        return self._inspector_workspace_id

    @property
    def navigation_expanded(self) -> bool:
        return self._navigation_expanded

    @property
    def current_theme(self) -> ThemeId:
        """Return the V2-owned presentation theme, never a backend setting."""

        return self._theme

    @property
    def current_locale(self) -> UiLocale:
        """Return the V2 presentation locale; it never configures a receiver."""

        return self._locale

    @property
    def inspector_hidden_for_narrow_width(self) -> bool:
        return self._inspector_hidden_for_narrow_width

    @property
    def automatic_discovery_label(self) -> str:
        state = text("shell.auto_search_enabled") if self._context.automatic_discovery_enabled else text("shell.auto_search_disabled")
        return text("shell.auto_search", state=state)

    @property
    def created_workspace_ids(self) -> frozenset[str]:
        return frozenset(self._workspace_pages)

    def select_workspace(self, workspace_id: str, *, force: bool = False) -> None:
        """Lazily show one V2 workspace without issuing a presenter command."""

        definition = self._definitions.get(workspace_id)
        if definition is None:
            raise ValueError(f"unknown V2 workspace: {workspace_id}")
        if not force and workspace_id == self._active_workspace_id and self._stack.currentWidget() is not None:
            return
        page = self._workspace_pages.get(workspace_id)
        if page is None:
            page = definition.workspace_factory()
            if not isinstance(page, QWidget):
                raise TypeError("workspace factory must return QWidget")
            self._apply_theme_to_widget(page)
            self._workspace_pages[workspace_id] = page
            self._stack.addWidget(page)
            self._connect_workspace_navigation(page)
        previous = self._stack.currentWidget()
        if previous is not None and previous is not page:
            # Suspend its plot before shared chrome changes resize the stack.
            # Navigation is presentation-only: acquisition/history keep running.
            previous.hide()
        self._active_workspace_id = workspace_id
        # Analyzer has its own measurement status strip; do not reserve a
        # second permanent row for the historical navigation-only disclaimer.
        self._status_bar.setVisible(workspace_id != "analyzer")
        for identifier, button in self._nav_buttons.items():
            button.set_active(identifier == workspace_id)
        self._replace_inspector(workspace_id, definition)
        self._apply_responsive_layout()
        # showEvent must observe the selected navigation/inspector/status
        # context, not the previous page's geometry policy and selection.
        self._stack.setCurrentWidget(page)
        self._position_narrow_inspector_drawer()
        self.workspace_changed.emit(workspace_id)

    def _connect_workspace_navigation(self, page: QWidget) -> None:
        """Accept optional V2-only navigation/registration signals; they cannot reach a backend."""

        signal = getattr(page, "workspace_requested", None)
        connect = getattr(signal, "connect", None)
        if callable(connect):
            connect(self.select_workspace)
        definition_signal = getattr(page, "workspace_definition_ready", None)
        definition_connect = getattr(definition_signal, "connect", None)
        if callable(definition_connect):
            definition_connect(self._register_workspace_from_page)

    def _register_workspace_from_page(self, definition: object) -> None:
        """Install an optional V2 page only after its active page explicitly emits it."""

        if not isinstance(definition, WorkspaceDefinition) or not definition.optional:
            self._status_message.set_value(text("shell.workspace_registration_rejected"))
            return
        if definition.workspace_id in self._definitions:
            self._status_message.set_value(text("shell.workspace_already_registered"))
            return
        try:
            self.register_optional_workspace(definition)
            self.select_workspace(definition.workspace_id)
        except (TypeError, ValueError):
            self._status_message.set_value(text("shell.workspace_registration_rejected"))

    def register_optional_workspace(self, definition: WorkspaceDefinition) -> None:
        """Expose a supplied optional V2 page without selecting or executing it."""

        if not definition.optional:
            raise ValueError("optional workspace registration requires optional=True")
        if definition.workspace_id in self._definitions:
            raise ValueError(f"V2 workspace already registered: {definition.workspace_id}")
        self._definitions[definition.workspace_id] = definition
        self._add_navigation_item(definition)

    def set_theme(self, theme: ThemeId) -> None:
        """Apply one V2 theme to this shell without modifying Legacy global state."""

        self._theme = theme
        self._root.setStyleSheet(stylesheet_for_theme(theme))
        self._discovery_chip.set_theme(theme)
        for button in self._nav_buttons.values():
            button.set_theme(theme)
        for page in self._workspace_pages.values():
            self._apply_theme_to_widget(page)
        for index in range(self._inspector_layout.count()):
            item = self._inspector_layout.itemAt(index)
            widget = item.widget()
            if widget is not None:
                self._apply_theme_to_widget(widget)
        for index in range(self._narrow_inspector_drawer._layout.count()):
            item = self._narrow_inspector_drawer._layout.itemAt(index)
            widget = item.widget()
            if widget is not None:
                self._apply_theme_to_widget(widget)
        if self._appearance_popover is not None:
            self._appearance_popover.set_theme(theme)

    def toggle_navigation(self) -> None:
        self._set_navigation_expanded(not self._navigation_expanded)

    def _set_navigation_expanded(self, expanded: bool) -> None:
        self._navigation_expanded = expanded
        self._rail.setFixedWidth(216 if expanded else 64)
        for item in self._nav_buttons.values():
            item.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                if expanded
                else Qt.ToolButtonStyle.ToolButtonIconOnly
            )
        self._navigation_toggle.setText(text("navigation.collapse") if expanded else text("navigation.expand"))

    def toggle_inspector(self) -> None:
        if self._inspector_hidden_for_narrow_width:
            if self._narrow_inspector_drawer_open:
                self._hide_narrow_inspector_drawer()
            else:
                self._show_narrow_inspector_drawer()
            return
        self._inspector_requested = not self._inspector_requested
        self._apply_responsive_layout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._is_closed:
            event.accept()
            return
        if not self._close_started and any(not port.can_close() for port in self._context.close_ports):
            self._status_bar.show()
            self._status_message.set_value(text("shell.close_requires_stop"))
            event.ignore()
            return
        for port in self._context.close_ports:
            if port.name not in self._shutdown_port_names:
                if port.request_shutdown is not None:
                    self._close_started = True
                    self._root.setEnabled(False)
                    try:
                        state = port.request_shutdown()
                    except Exception as error:
                        self._show_close_state("failed", str(error))
                        event.ignore()
                        return
                    if state.phase != "complete":
                        self._show_close_state(state.phase, state.detail)
                        if state.phase in {"pending", "timeout"}:
                            self._close_timer.start()
                        event.ignore()
                        return
                else:
                    try:
                        port.shutdown()
                    except Exception as error:
                        self._show_close_state("failed", str(error))
                        event.ignore()
                        return
                self._shutdown_port_names.add(port.name)
        self._close_timer.stop()
        self._hide_narrow_inspector_drawer()
        self._save_settings()
        self._is_closed = True
        event.accept()

    def _show_close_state(self, phase: str, detail: str) -> None:
        self._status_bar.show()  # Analyzer normally hides the navigation status.
        key = "shell.close." + (phase if phase in {"pending", "timeout", "failed"} else "failed")
        self._status_message.set_value(text(key, self._locale, detail=detail))

    def _poll_close(self) -> None:
        for port in self._context.close_ports:
            if port.name in self._shutdown_port_names:
                continue
            if port.poll_shutdown is None:
                break
            try:
                state = port.poll_shutdown()
            except Exception as error:
                self._close_timer.stop()
                self._show_close_state("failed", str(error))
                return
            if state.phase != "complete":
                self._show_close_state(state.phase, state.detail)
                if state.phase == "failed":
                    self._close_timer.stop()  # Retry requires another explicit Close.
                return
            self._shutdown_port_names.add(port.name)
            break  # Request the next owner through closeEvent before polling it.
        self._close_timer.stop()
        self.close()  # Every active owner acknowledged; finish settings/Qt close.

    def _build_shell(self) -> None:
        self.setWindowTitle(text("shell.title"))
        self.setMinimumSize(800, 520)
        self.resize(1280, 720)
        self._root = QWidget(self)
        self._root.setProperty("ui2Root", True)
        layout = QVBoxLayout(self._root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_top_bar())
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self._rail = self._build_navigation()
        content.addWidget(self._rail)
        self._stack = QStackedWidget(self._root)
        self._stack.setAccessibleName(text("shell.workspace"))
        content.addWidget(self._stack, 1)
        self._inspector = self._build_inspector()
        content.addWidget(self._inspector)
        layout.addLayout(content, 1)
        self._status_bar = self._build_status_bar()
        layout.addWidget(self._status_bar)
        self.setCentralWidget(self._root)
        self._narrow_inspector_drawer = _NarrowInspectorDrawer(self._root)
        self._narrow_inspector_drawer.close_requested.connect(self._hide_narrow_inspector_drawer)
        self.set_theme(self._theme)

    def _build_top_bar(self) -> QWidget:
        bar = QFrame(self._root)
        bar.setProperty("ui2Role", "card")
        analyzer_product = self._context.initial_workspace_id == "analyzer"
        bar.setFixedHeight(40 if analyzer_product else 52)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 4 if analyzer_product else 6, 12, 4 if analyzer_product else 6)
        layout.setSpacing(8)
        title = QLabel(text("shell.title").removesuffix(" — UI V2"), bar)
        title.setProperty("ui2Role", "workspace-heading")
        layout.addWidget(title)
        self._discovery_chip = StatusChipV2(
            self.automatic_discovery_label,
            tone=StatusTone.INFO,
            detail=text("shell.auto_search_detail"),
            theme=self._theme,
            parent=bar,
        )
        layout.addWidget(self._discovery_chip)
        layout.addStretch(1)
        self._navigation_toggle = QPushButton(text("navigation.expand"), bar)
        self._navigation_toggle.setProperty("ui2Role", "utility-action")
        self._navigation_toggle.setAccessibleName(text("navigation.expand_name"))
        self._navigation_toggle.clicked.connect(self.toggle_navigation)
        layout.addWidget(self._navigation_toggle)
        self._appearance_toggle = QPushButton(text("shell.appearance.text"), bar)
        self._appearance_toggle.setProperty("ui2Role", "utility-action")
        self._appearance_toggle.setAccessibleName(text("shell.appearance.name"))
        self._appearance_toggle.setToolTip(text("shell.appearance.tooltip"))
        self._appearance_toggle.clicked.connect(self.toggle_appearance_popover)
        layout.addWidget(self._appearance_toggle)
        self._inspector_toggle = QPushButton(text("shell.inspector.hide"), bar)
        self._inspector_toggle.setProperty("ui2Role", "utility-action")
        self._inspector_toggle.setAccessibleName(text("shell.inspector.hide_name"))
        self._inspector_toggle.clicked.connect(self.toggle_inspector)
        layout.addWidget(self._inspector_toggle)
        self._top_bar = bar
        return bar

    def _build_navigation(self) -> QFrame:
        rail = QFrame(self._root)
        rail.setProperty("ui2Role", "panel")
        rail.setAccessibleName(text("shell.navigation"))
        rail.setFixedWidth(64)
        self._navigation_layout = QVBoxLayout(rail)
        self._navigation_layout.setContentsMargins(4, 8, 4, 8)
        self._navigation_layout.setSpacing(4)
        self._navigation_group = QButtonGroup(rail)
        self._navigation_group.setExclusive(True)
        for definition in self._definitions.values():
            self._add_navigation_item(definition)
        self._navigation_layout.addStretch(1)
        self._navigation_has_bottom_stretch = True
        return rail

    def _add_navigation_item(self, definition: WorkspaceDefinition) -> None:
        item = NavigationItem(
            definition.resolved_label(),
            icon=definition.icon,
            description=definition.resolved_description(),
            active_name=text(
                "navigation.current_name",
                label=definition.resolved_label(),
            ),
            active_description=text(
                "navigation.current_description",
                detail=definition.resolved_description(),
            ),
            theme=self._theme,
            parent=self._rail if hasattr(self, "_rail") else self._root,
        )
        item.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        item.clicked.connect(lambda _checked=False, identifier=definition.workspace_id: self.select_workspace(identifier))
        self._navigation_group.addButton(item)
        if self._navigation_has_bottom_stretch:
            self._navigation_layout.insertWidget(self._navigation_layout.count() - 1, item)
        else:
            self._navigation_layout.addWidget(item)
        self._nav_buttons[definition.workspace_id] = item

    def _build_inspector(self) -> QFrame:
        inspector = QFrame(self._root)
        inspector.setProperty("ui2Role", "panel")
        inspector.setAccessibleName(text("shell.context_inspector"))
        inspector.setFixedWidth(320)
        self._inspector_layout = QVBoxLayout(inspector)
        self._inspector_layout.setContentsMargins(8, 8, 8, 8)
        self._inspector_layout.setSpacing(0)
        return inspector

    def _build_status_bar(self) -> QWidget:
        bar = QFrame(self._root)
        bar.setProperty("ui2Role", "card")
        bar.setFixedHeight(32)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 2, 12, 2)
        self._status_message = MeasurementStripItem(
            text("shell.status.label"),
            text("shell.status.boundary"),
            detail=text("shell.status.detail"),
            parent=bar,
        )
        layout.addWidget(self._status_message)
        layout.addStretch(1)
        return bar

    def toggle_appearance_popover(self) -> None:
        """Show V2-only appearance controls without changing application state."""

        if self._appearance_popover is not None and self._appearance_popover.isVisible():
            self._appearance_popover.hide()
            return
        if self._appearance_popover is None:
            self._appearance_popover = AppearancePopover(parent=self, locale=self._locale)
            self._appearance_popover.theme_requested.connect(self.select_appearance_theme)
            self._appearance_popover.locale_requested.connect(self.select_appearance_locale)
            self._appearance_popover.layout_reset_requested.connect(self.reset_shell_layout)
            self._appearance_popover.visuals_reset_requested.connect(self.reset_shell_visuals)
            self._appearance_popover.shell_reset_requested.connect(self.reset_shell_settings)
        self._appearance_popover.set_theme(self._theme)
        self._appearance_popover.open_next_to(self._appearance_toggle)

    def select_appearance_theme(self, theme: ThemeId) -> None:
        """Persist a validated V2 presentation theme without touching Legacy settings."""

        self.set_theme(theme)
        self._settings.setValue(_SETTINGS_SCHEMA_KEY, "1")
        self._settings.setValue(_SETTINGS_THEME_KEY, theme.value)
        self._settings.sync()
        self._status_message.set_value(
            text(
                "shell.status.theme",
                self._locale,
                theme=theme_label(theme, self._locale),
            )
        )

    def select_appearance_locale(self, locale: object) -> None:
        """Persist and rebuild only the Qt presentation for an explicit language choice."""

        if not isinstance(locale, UiLocale):
            raise TypeError("UI V2 locale selection requires UiLocale")
        if locale is self._locale:
            return
        self._settings.setValue(_SETTINGS_SCHEMA_KEY, "1")
        self._settings.setValue(_SETTINGS_LOCALE_KEY, locale.value)
        self._settings.sync()
        self._rebuild_for_locale(locale)

    def reset_shell_layout(self) -> None:
        """Reset only geometry/navigation/inspector settings owned by this shell."""

        for key in ("geometry", "navigation_expanded", "inspector_visible"):
            self._settings.remove(f"{_SETTINGS_PREFIX}/{key}")
        self._hide_narrow_inspector_drawer()
        self._set_navigation_expanded(False)
        self._inspector_requested = True
        self.resize(*_DEFAULT_SHELL_SIZE)
        self._apply_responsive_layout()
        self._settings.setValue(_SETTINGS_SCHEMA_KEY, "1")
        self._settings.sync()
        self._status_message.set_value(text("shell.status.layout_reset"))

    def reset_shell_visuals(self) -> None:
        """Reset only the V2 theme; no device or measurement state is stored here."""

        self._settings.remove(_SETTINGS_THEME_KEY)
        self.select_appearance_theme(ThemeId.DARK)
        self._status_message.set_value(text("shell.status.visuals_reset"))

    def reset_shell_settings(self) -> None:
        """Reset the V2 shell namespace while preserving all Legacy and backend data."""

        self.reset_shell_layout()
        self.reset_shell_visuals()
        self._settings.remove(_SETTINGS_LOCALE_KEY)
        if self._locale is not UiLocale.RU:
            self._rebuild_for_locale(UiLocale.RU)
        self._status_message.set_value(text("shell.status.settings_reset"))

    def _rebuild_for_locale(self, locale: UiLocale) -> None:
        """Replace V2 widgets only; no presenter or receiver command is issued here."""

        active_workspace_id = self._active_workspace_id
        navigation_expanded = self._navigation_expanded
        inspector_requested = self._inspector_requested
        retained_pages = {}
        for identifier, page in self._workspace_pages.items():
            if identifier == "analyzer" and callable(getattr(page, "set_locale", None)):
                # Stateful Analyzer translates in place: do not destroy its
                # draft, viewport, markers or bounded history with shell text.
                self._stack.removeWidget(page)
                page.setParent(None)
                retained_pages[identifier] = page
                continue
            page.close()
            page.setParent(None)
            page.deleteLater()
        self._workspace_pages.clear()
        self._clear_inspector_layout()
        self._narrow_inspector_drawer.clear_content()
        if self._appearance_popover is not None:
            self._appearance_popover.hide()
            self._appearance_popover.deleteLater()
            self._appearance_popover = None
        old_root = self.takeCentralWidget()
        if old_root is not None:
            old_root.setParent(None)
            old_root.deleteLater()
        self._locale = locale
        set_active_locale(locale)
        self._nav_buttons.clear()
        self._inspector_workspace_id = None
        self._navigation_has_bottom_stretch = False
        self._build_shell()
        for identifier, page in retained_pages.items():
            page.set_locale()
            self._workspace_pages[identifier] = page
            self._stack.addWidget(page)
        self._set_navigation_expanded(navigation_expanded)
        self._inspector_requested = inspector_requested
        self.set_theme(self._theme)
        self.select_workspace(active_workspace_id, force=True)
        self._apply_responsive_layout()

    def _replace_inspector(self, workspace_id: str, definition: WorkspaceDefinition) -> None:
        self._clear_inspector_layout()
        # Analyzer always uses the explicit overlay, even on a wide monitor.
        # Its permanently hidden dock must not construct a second inspector
        # (and model subscription) on every return to the running spectrum.
        # The drawer factory below still creates fresh, current-state content
        # when explicitly opened, including after navigation/locale rebuild.
        if workspace_id != "analyzer":
            inspector = definition.inspector_factory()
            if not isinstance(inspector, QWidget):
                raise TypeError("inspector factory must return QWidget")
            self._apply_theme_to_widget(inspector)
            self._inspector_layout.addWidget(inspector)
        self._inspector_workspace_id = workspace_id
        if self._narrow_inspector_drawer_open:
            self._show_narrow_inspector_drawer()

    def _clear_inspector_layout(self) -> None:
        while self._inspector_layout.count():
            item = self._inspector_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _apply_theme_to_widget(self, widget: QWidget) -> None:
        """Theme an optional V2 widget through its presentation-only hook."""

        set_theme = getattr(widget, "set_theme", None)
        if callable(set_theme):
            set_theme(self._theme)

    def _apply_responsive_layout(self) -> None:
        # Analyzer reserves its width for measurement, at every display scale.
        # The inspector stays explicitly accessible as an overlay; this must
        # not rewrite the user's pinned-inspector preference for other pages.
        narrow = self.width() < _NARROW_WIDTH or self._active_workspace_id == "analyzer"
        self._inspector_hidden_for_narrow_width = narrow
        if narrow:
            self._inspector.setVisible(False)
            self._position_narrow_inspector_drawer()
            self._inspector_toggle.setText(
                text("shell.inspector.hide") if self._narrow_inspector_drawer_open else text("shell.inspector.show")
            )
            self._inspector_toggle.setAccessibleName(
                text("shell.inspector.hide_name") if self._narrow_inspector_drawer_open else text("shell.inspector.show_name")
            )
            self._inspector_toggle.setEnabled(True)
            self._inspector_toggle.setToolTip(
                text("shell.inspector.drawer_tooltip") if self._active_workspace_id == "analyzer"
                else text("shell.inspector.narrow_tooltip")
            )
            return
        if self._narrow_inspector_drawer_open:
            self._hide_narrow_inspector_drawer()
        visible = self._inspector_requested
        self._inspector.setVisible(visible)
        self._inspector_toggle.setText(text("shell.inspector.show") if not visible else text("shell.inspector.hide"))
        self._inspector_toggle.setEnabled(True)
        self._inspector_toggle.setToolTip(text("shell.inspector.tooltip"))

    def _show_narrow_inspector_drawer(self) -> None:
        if not self._inspector_hidden_for_narrow_width:
            return
        definition = self._definitions[self._active_workspace_id]
        inspector = definition.inspector_factory()
        if not isinstance(inspector, QWidget):
            raise TypeError("inspector factory must return QWidget")
        self._apply_theme_to_widget(inspector)
        self._narrow_inspector_drawer.set_content(inspector)
        self._narrow_inspector_drawer_open = True
        self._position_narrow_inspector_drawer()
        self._narrow_inspector_drawer.show()
        self._narrow_inspector_drawer.raise_()
        self._narrow_inspector_drawer.setFocus(Qt.FocusReason.OtherFocusReason)
        self._apply_responsive_layout()

    def _hide_narrow_inspector_drawer(self) -> None:
        self._narrow_inspector_drawer.clear_content()
        self._narrow_inspector_drawer.hide()
        self._narrow_inspector_drawer_open = False
        self._inspector_toggle.setFocus(Qt.FocusReason.OtherFocusReason)
        self._apply_responsive_layout()

    def _position_narrow_inspector_drawer(self) -> None:
        if not self._inspector_hidden_for_narrow_width:
            return
        top = self._top_bar.height()
        if self._active_workspace_id == "analyzer":
            page = self._workspace_pages.get("analyzer")
            primary = getattr(page, "primary", None)
            if isinstance(primary, QWidget):
                top = max(top, primary.mapTo(self._root, primary.rect().bottomRight()).y() + 8)
        status_height = self._status_bar.height() if self._status_bar.isVisible() else 0
        height = max(0, self._root.height() - top - status_height)
        self._narrow_inspector_drawer.setGeometry(
            max(0, self._root.width() - _INSPECTOR_DRAWER_WIDTH),
            top,
            _INSPECTOR_DRAWER_WIDTH,
            height,
        )

    def _restore_settings(self) -> None:
        version = str(self._settings.value(f"{_SETTINGS_PREFIX}/version", ""))
        if version != "1":
            return
        geometry = self._settings.value(f"{_SETTINGS_PREFIX}/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        navigation = self._setting_bool(f"{_SETTINGS_PREFIX}/navigation_expanded", False)
        inspector = self._setting_bool(f"{_SETTINGS_PREFIX}/inspector_visible", True)
        self._set_navigation_expanded(navigation)
        self._inspector_requested = inspector
        raw_theme = self._settings.value(_SETTINGS_THEME_KEY)
        if raw_theme is not None:
            try:
                self._theme = ThemeId(str(raw_theme))
            except ValueError:
                self._theme = ThemeId.DARK

    def _setting_bool(self, key: str, default: bool) -> bool:
        raw = self._settings.value(key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().casefold() in {"1", "true", "yes"}

    def _save_settings(self) -> None:
        self._settings.setValue(_SETTINGS_SCHEMA_KEY, "1")
        self._settings.setValue(f"{_SETTINGS_PREFIX}/version", "1")
        self._settings.setValue(f"{_SETTINGS_PREFIX}/geometry", self.saveGeometry())
        self._settings.setValue(f"{_SETTINGS_PREFIX}/navigation_expanded", self._navigation_expanded)
        self._settings.setValue(f"{_SETTINGS_PREFIX}/inspector_visible", self._inspector_requested)
        self._settings.setValue(_SETTINGS_THEME_KEY, self._theme.value)
        self._settings.setValue(_SETTINGS_LOCALE_KEY, self._locale.value)
        self._settings.sync()

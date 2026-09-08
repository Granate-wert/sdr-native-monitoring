"""Safe product Home workspace for UI V2 with explicit navigation only."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..components import SectionHeader, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import text
from ..shell.contracts import WorkspaceDefinition


class HomeWorkspaceV2(QWidget):
    """A no-device V2 landing page; navigation is explicit and remains in the shell."""

    workspace_requested = Signal(str)

    def __init__(
        self,
        *,
        sweep_available: bool = False,
        calibration_available: bool = False,
        diagnostics_available: bool = False,
        tinysa_available: bool = False,
        replay_available: bool = False,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._sweep_available = sweep_available
        self._calibration_available = calibration_available
        self._diagnostics_available = diagnostics_available
        self._tinysa_available = tinysa_available
        self._replay_available = replay_available
        self._build_ui()
        self.set_theme(theme)

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._policy_chip.set_theme(theme)

    def _build_ui(self) -> None:
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("home.accessible.name"))
        self.setAccessibleDescription(text("home.accessible.description"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(
            SectionHeader(
                text("home.header.title"),
                text("home.header.detail"),
                parent=self,
            )
        )
        policy = QFrame(self)
        policy.setProperty("ui2Role", "card")
        policy_layout = QHBoxLayout(policy)
        policy_layout.setContentsMargins(12, 10, 12, 10)
        self._policy_chip = StatusChipV2(
            text("home.auto_search"),
            tone=StatusTone.INFO,
            detail=text("home.auto_search_detail"),
            parent=policy,
        )
        policy_layout.addWidget(self._policy_chip)
        policy_layout.addStretch(1)
        layout.addWidget(policy)

        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        self._live_card = _route_card(
            text("home.live.title"),
            text("home.live.detail"),
            text("home.live.action"),
            enabled=True,
            parent=self,
        )
        self._live_card.button.clicked.connect(lambda: self.workspace_requested.emit("live"))
        cards.addWidget(self._live_card, 0, 0)
        self._sweep_card = _route_card(
            text("home.sweep.title"),
            (
                text("home.sweep.detail.available")
                if self._sweep_available
                else text("home.sweep.detail.unavailable")
            ),
            text("home.sweep.action") if self._sweep_available else text("home.unavailable"),
            enabled=self._sweep_available,
            parent=self,
        )
        if self._sweep_available:
            self._sweep_card.button.clicked.connect(lambda: self.workspace_requested.emit("sweep"))
        cards.addWidget(self._sweep_card, 0, 1)
        self._calibration_card = _route_card(
            text("home.calibration.title"),
            (
                text("home.calibration.detail.available")
                if self._calibration_available
                else text("home.calibration.detail.unavailable")
            ),
            text("home.calibration.action") if self._calibration_available else text("home.unavailable"),
            enabled=self._calibration_available,
            parent=self,
        )
        if self._calibration_available:
            self._calibration_card.button.clicked.connect(lambda: self.workspace_requested.emit("calibration"))
        cards.addWidget(self._calibration_card, 1, 0)
        self._diagnostics_card = _route_card(
            text("home.diagnostics.title"),
            (
                text("home.diagnostics.detail.available")
                if self._diagnostics_available
                else text("home.diagnostics.detail.unavailable")
            ),
            text("home.diagnostics.action") if self._diagnostics_available else text("home.unavailable"),
            enabled=self._diagnostics_available,
            parent=self,
        )
        if self._diagnostics_available:
            self._diagnostics_card.button.clicked.connect(lambda: self.workspace_requested.emit("diagnostics"))
        cards.addWidget(self._diagnostics_card, 1, 1)
        self._tinysa_card = _route_card(
            text("home.tinysa.title"),
            (
                text("home.tinysa.detail.available")
                if self._tinysa_available
                else text("home.tinysa.detail.unavailable")
            ),
            text("home.tinysa.action") if self._tinysa_available else text("home.unavailable"),
            enabled=self._tinysa_available,
            parent=self,
        )
        if self._tinysa_available:
            self._tinysa_card.button.clicked.connect(lambda: self.workspace_requested.emit("tinysa"))
        cards.addWidget(self._tinysa_card, 2, 0)
        self._replay_card = _route_card(
            text("home.replay.title"),
            (
                text("home.replay.detail.available")
                if self._replay_available
                else text("home.replay.detail.unavailable")
            ),
            text("home.replay.action") if self._replay_available else text("home.unavailable"),
            enabled=self._replay_available,
            parent=self,
        )
        if self._replay_available:
            self._replay_card.button.clicked.connect(lambda: self.workspace_requested.emit("replay"))
        cards.addWidget(self._replay_card, 2, 1)
        layout.addLayout(cards)
        boundary = QLabel(
            text("home.boundary"),
            self,
        )
        boundary.setProperty("ui2Role", "secondary")
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addStretch(1)


class _RouteCard(QFrame):
    def __init__(self, title: str, detail: str, action: str, *, enabled: bool, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("ui2Role", "card")
        self.setAccessibleName(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        heading = QLabel(title, self)
        heading.setProperty("ui2Role", "section-heading")
        layout.addWidget(heading)
        description = QLabel(detail, self)
        description.setProperty("ui2Role", "secondary")
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addStretch(1)
        self.button = QPushButton(action, self)
        self.button.setProperty("ui2Role", "utility-action")
        self.button.setAccessibleName(text("home.card.action_name", action=action, title=title))
        self.button.setEnabled(enabled)
        layout.addWidget(self.button)


def _route_card(
    title: str,
    detail: str,
    action: str,
    *,
    enabled: bool,
    parent: QWidget,
) -> _RouteCard:
    return _RouteCard(title, detail, action, enabled=enabled, parent=parent)


def home_workspace_definition(
    *,
    sweep_available: bool = False,
    calibration_available: bool = False,
    diagnostics_available: bool = False,
    tinysa_available: bool = False,
    replay_available: bool = False,
    theme: ThemeId = ThemeId.DARK,
) -> WorkspaceDefinition:
    """Define Home without an application/service/presenter dependency."""

    return WorkspaceDefinition(
        workspace_id="home",
        label=text("home.workspace.label"),
        description=text("home.workspace.description"),
        icon=V2IconId.INFO,
        workspace_factory=lambda: HomeWorkspaceV2(
            sweep_available=sweep_available,
            calibration_available=calibration_available,
            diagnostics_available=diagnostics_available,
            tinysa_available=tinysa_available,
            replay_available=replay_available,
            theme=theme,
        ),
        inspector_factory=lambda: _home_inspector(theme),
        label_key="home.workspace.label",
        description_key="home.workspace.description",
    )


def _home_inspector(theme: ThemeId) -> QWidget:
    inspector = QFrame()
    inspector.setProperty("ui2Root", True)
    inspector.setProperty("ui2Role", "panel")
    inspector.setStyleSheet(stylesheet_for_theme(theme))
    layout = QVBoxLayout(inspector)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)
    layout.addWidget(SectionHeader(text("home.context.title"), text("home.context.subtitle"), parent=inspector))
    detail = QLabel(
        text("home.context.detail"),
        inspector,
    )
    detail.setProperty("ui2Role", "secondary")
    detail.setWordWrap(True)
    layout.addWidget(detail)
    layout.addStretch(1)
    return inspector

"""V2-only tinySA source activation workspace.

Opening the page constructs neither the source presenter nor a serial backend.
The first factory call is coupled to the visible ``Discover`` command.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from sdr_monitor.services.tinysa_source_composition import TinySaSourcePhase, TinySaSourceSnapshot

from ..components import ErrorBanner, SectionHeader, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import enum_text, text
from ..shell.contracts import WorkspaceDefinition
from ..view_models.tinysa_view_model import (
    DeferredTinySaSourceActivationViewModel,
    TinySaAnalyzerBinding,
    TinySaSourceActivationViewState,
)


class TinySaActivationWorkspaceV2(QWidget):
    """Present source phases only; the widget never talks to USB/serial directly."""

    workspace_definition_ready = Signal(object)

    def __init__(
        self,
        view_model: DeferredTinySaSourceActivationViewModel,
        analyzer_workspace_factory: Callable[[TinySaAnalyzerBinding], WorkspaceDefinition],
        *,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not callable(analyzer_workspace_factory):
            raise TypeError("tinySA V2 activation requires an analyzer workspace factory")
        self._view_model = view_model
        self._analyzer_workspace_factory = analyzer_workspace_factory
        self._theme = theme
        self._emitted_binding: TinySaAnalyzerBinding | None = None
        self._build_ui()
        self.set_theme(theme)
        self._unsubscribe = view_model.subscribe(self._render_state)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        self._unsubscribe()
        super().closeEvent(event)

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._state_chip.set_theme(theme)

    def _build_ui(self) -> None:
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("tinysa_activation.accessible.name"))
        self.setAccessibleDescription(text("tinysa_activation.accessible.description"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(
            SectionHeader(
                text("tinysa_activation.header.title"), text("tinysa_activation.header.detail"),
                parent=self,
            )
        )
        boundary = QLabel(
            text("tinysa_activation.boundary"),
            self,
        )
        boundary.setProperty("ui2Role", "secondary")
        boundary.setWordWrap(True)
        layout.addWidget(boundary)

        source_card = QFrame(self)
        source_card.setProperty("ui2Role", "card")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(12, 12, 12, 12)
        source_layout.setSpacing(8)
        row = QHBoxLayout()
        self._state_chip = StatusChipV2(text("tinysa_activation.state.inactive"), tone=StatusTone.NEUTRAL, parent=source_card)
        row.addWidget(self._state_chip)
        row.addStretch(1)
        source_layout.addLayout(row)
        source_layout.addWidget(QLabel(text("tinysa_activation.source.label"), source_card))
        self._candidates = QComboBox(source_card)
        self._candidates.setProperty("ui2Role", "utility-select")
        self._candidates.setAccessibleName(text("tinysa_activation.source.name"))
        self._candidates.setAccessibleDescription(text("tinysa_activation.source.description"))
        source_layout.addWidget(self._candidates)
        actions = QHBoxLayout()
        self._discover = QPushButton(text("tinysa_activation.discover"), source_card)
        self._discover.setProperty("ui2Role", "primary-action")
        self._discover.setAccessibleName(text("tinysa_activation.discover.name"))
        self._discover.setAccessibleDescription(text("tinysa_activation.discover.description"))
        self._discover.clicked.connect(self._view_model.discover)
        self._select = QPushButton(text("tinysa_activation.select"), source_card)
        self._select.setProperty("ui2Role", "utility-action")
        self._select.setAccessibleName(text("tinysa_activation.select.name"))
        self._select.setAccessibleDescription(text("tinysa_activation.select.description"))
        self._select.clicked.connect(self._select_source)
        self._verify = QPushButton(text("tinysa_activation.verify"), source_card)
        self._verify.setProperty("ui2Role", "utility-action")
        self._verify.setAccessibleName(text("tinysa_activation.verify.name"))
        self._verify.setAccessibleDescription(text("tinysa_activation.verify.description"))
        self._verify.clicked.connect(self._view_model.verify_selected)
        self._compose = QPushButton(text("tinysa_activation.compose"), source_card)
        self._compose.setProperty("ui2Role", "utility-action")
        self._compose.setAccessibleName(text("tinysa_activation.compose.name"))
        self._compose.setAccessibleDescription(text("tinysa_activation.compose.description"))
        self._compose.clicked.connect(self._view_model.compose_selected)
        for button in (self._discover, self._select, self._verify, self._compose):
            actions.addWidget(button)
        actions.addStretch(1)
        source_layout.addLayout(actions)
        layout.addWidget(source_card)

        self._identity = QLabel(text("tinysa_activation.identity.unverified"), self)
        self._identity.setProperty("ui2Role", "secondary")
        self._identity.setWordWrap(True)
        self._identity.setAccessibleName(text("tinysa_activation.identity.name"))
        layout.addWidget(self._identity)
        self._busy = QLabel(text("tinysa_activation.busy"), self)
        self._busy.setProperty("ui2Role", "secondary")
        self._busy.setAccessibleName(text("tinysa_activation.busy.name"))
        self._busy.setVisible(False)
        layout.addWidget(self._busy)
        self._error = ErrorBanner(text("tinysa_activation.error.title"), "", parent=self)
        self._error.setVisible(False)
        layout.addWidget(self._error)
        layout.addStretch(1)

    def _select_source(self) -> None:
        source_id = self._candidates.currentData()
        if not isinstance(source_id, str) or not self._view_model.select(source_id):
            self._show_error(text("tinysa_activation.error.select"))

    def _render_state(self, state: TinySaSourceActivationViewState) -> None:
        snapshot = state.snapshot
        self._apply_snapshot(snapshot)
        self._busy.setVisible(state.busy)
        if state.busy:
            self._discover.setEnabled(False)
            self._select.setEnabled(False)
            self._verify.setEnabled(False)
            self._compose.setEnabled(False)
        if state.error:
            self._show_error(state.error)
        else:
            self._error.setVisible(False)
        binding = state.analyzer_binding
        if binding is not None and binding is not self._emitted_binding:
            try:
                definition = self._analyzer_workspace_factory(binding)
            except Exception:  # noqa: BLE001 - registration details must not leak a route or endpoint.
                self._show_error(text("tinysa_activation.error.registration_failed"))
                return
            if not isinstance(definition, WorkspaceDefinition) or not definition.optional:
                self._show_error(text("tinysa_activation.error.invalid_registration"))
                return
            self._emitted_binding = binding
            self.workspace_definition_ready.emit(definition)

    def _apply_snapshot(self, snapshot: TinySaSourceSnapshot | None) -> None:
        if snapshot is None:
            self._state_chip.set_text(text("tinysa_activation.state.inactive"))
            self._state_chip.set_tone(StatusTone.NEUTRAL)
            self._discover.setEnabled(True)
            self._select.setEnabled(False)
            self._verify.setEnabled(False)
            self._compose.setEnabled(False)
            self._candidates.clear()
            return
        tone = {
            TinySaSourcePhase.READY: StatusTone.INFO,
            TinySaSourcePhase.DISCOVERED: StatusTone.INFO,
            TinySaSourcePhase.SELECTED: StatusTone.WARNING,
            TinySaSourcePhase.VERIFIED: StatusTone.SUCCESS,
            TinySaSourcePhase.COMPOSED: StatusTone.SUCCESS,
            TinySaSourcePhase.FAULTED: StatusTone.ERROR,
        }[snapshot.phase]
        self._state_chip.set_text(enum_text("tinysa_activation.phase", snapshot.phase))
        self._state_chip.set_tone(tone)
        previous = self._candidates.currentData()
        self._candidates.clear()
        selected_index = -1
        for index, candidate in enumerate(snapshot.candidates):
            label = text(
                "tinysa_activation.candidate",
                label=candidate.label,
                assurance=enum_text("tinysa_identity.assurance", candidate.identity_assurance),
            )
            self._candidates.addItem(label, candidate.source_id)
            if candidate.source_id == (snapshot.selected_source_id or previous):
                selected_index = index
        if selected_index >= 0:
            self._candidates.setCurrentIndex(selected_index)
        self._discover.setEnabled(snapshot.can_discover)
        self._select.setEnabled(snapshot.can_select and bool(snapshot.candidates))
        self._verify.setEnabled(snapshot.can_verify)
        self._compose.setEnabled(snapshot.can_compose)
        if snapshot.verified is None:
            self._identity.setText(text("tinysa_activation.identity.unverified"))
        else:
            source = snapshot.verified
            assurance = enum_text("tinysa_identity.assurance", source.identity_assurance)
            self._identity.setText(text("tinysa_activation.identity.verified", label=source.label, assurance=assurance))

    def _show_error(self, message: str) -> None:
        self._error.set_content(text("tinysa_activation.error.title"), message)
        self._error.set_action("", enabled=False)
        self._error.setVisible(True)


def tinysa_activation_workspace_definition(
    view_model: DeferredTinySaSourceActivationViewModel,
    analyzer_workspace_factory: Callable[[TinySaAnalyzerBinding], WorkspaceDefinition],
    *,
    theme: ThemeId = ThemeId.DARK,
) -> WorkspaceDefinition:
    """Create the V2 activation route without constructing its presenter."""

    return WorkspaceDefinition(
        workspace_id="tinysa",
        label="tinySA",
        description=text("tinysa_activation.workspace.description"),
        icon=V2IconId.NAVIGATION,
        workspace_factory=lambda: TinySaActivationWorkspaceV2(
            view_model,
            analyzer_workspace_factory,
            theme=theme,
        ),
        inspector_factory=lambda: _inspector(theme),
        description_key="tinysa_activation.workspace.description",
    )


def _inspector(theme: ThemeId) -> QWidget:
    inspector = QFrame()
    inspector.setProperty("ui2Root", True)
    inspector.setProperty("ui2Role", "panel")
    inspector.setStyleSheet(stylesheet_for_theme(theme))
    layout = QVBoxLayout(inspector)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.addWidget(SectionHeader(text("tinysa_activation.inspector.context"), text("tinysa_activation.inspector.detail"), parent=inspector))
    detail = QLabel(
        text("tinysa_activation.inspector.identity"),
        inspector,
    )
    detail.setProperty("ui2Role", "secondary")
    detail.setWordWrap(True)
    layout.addWidget(detail)
    boundary = QLabel(
        text("tinysa_activation.inspector.boundary"),
        inspector,
    )
    boundary.setProperty("ui2Role", "secondary")
    boundary.setWordWrap(True)
    layout.addWidget(boundary)
    layout.addStretch(1)
    return inspector


__all__ = ["TinySaActivationWorkspaceV2", "tinysa_activation_workspace_definition"]

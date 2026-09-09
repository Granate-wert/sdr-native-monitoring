"""Deferred spectrum-recording replay workspace for UI V2."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..components import ErrorBanner, SectionHeader, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import text
from ..shell.contracts import WorkspaceDefinition
from ..spectrum import SpectrumScene
from ..view_models.replay_view_model import DeferredReplayViewModel, ReplayViewState


class ReplayWorkspaceV2(QWidget):
    """Show explicit spectrum replay and optional offline reprocess actions only."""

    def __init__(
        self,
        view_model: DeferredReplayViewModel,
        *,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._theme = theme
        self._rendered_frame: object | None = None
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
        self._scene.set_theme(theme)

    def _build_ui(self) -> None:
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("replay.accessible.name"))
        self.setAccessibleDescription(text("replay.accessible.description"))
        layout = QVBoxLayout(self)
        # Replay has five explicit vertical regions.  Keep every control and
        # the 240 px scene minimum, but use the V2 compact spacing tier so the
        # workspace still fits the 1280×720 shell after normal system-font
        # metrics are applied.
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        layout.addWidget(
            SectionHeader(
                text("replay.header.title"),
                text("replay.header.detail"),
                parent=self,
            )
        )
        layout.addWidget(self._build_open_card())
        self._error_banner = ErrorBanner(text("replay.error.title"), "", parent=self)
        self._error_banner.setVisible(False)
        layout.addWidget(self._error_banner)
        layout.addWidget(self._build_timeline_card())
        self._scene = SpectrumScene(theme=self._theme, parent=self)
        # Retain a usable graph at the 1280×720 desktop target without
        # forcing Replay to clip its explicit source/timeline/reprocess actions.
        self._scene.setMinimumHeight(240)
        self._scene.set_warning(text("replay.scene.warning"))
        layout.addWidget(self._scene, 1)
        layout.addWidget(self._build_reprocess_card())

    def _build_open_card(self) -> QFrame:
        card = _card(text("replay.card.source"), self)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel(text("replay.path.label"), card))
        self._path = QLineEdit(card)
        self._path.setPlaceholderText(text("replay.path.placeholder"))
        self._path.setAccessibleName(text("replay.path.name"))
        row.addWidget(self._path, 1)
        choose = QPushButton(text("replay.choose"), card)
        choose.setProperty("ui2Role", "utility-action")
        choose.setAccessibleName(text("replay.choose.name"))
        choose.clicked.connect(self._choose_recording)
        row.addWidget(choose)
        self._open = QPushButton(text("replay.open"), card)
        self._open.setProperty("ui2Role", "primary-action")
        self._open.setAccessibleName(text("replay.open.name"))
        self._open.clicked.connect(lambda: self._view_model.open_spectrum_recording(self._path.text()))
        row.addWidget(self._open)
        self._state_chip = StatusChipV2(
            text("replay.state.unopened"), tone=StatusTone.NEUTRAL, parent=card
        )
        row.addWidget(self._state_chip)
        _card_layout(card).addLayout(row)
        self._index_summary = QLabel(text("replay.index.unopened"), card)
        self._index_summary.setProperty("ui2Role", "secondary")
        self._index_summary.setWordWrap(True)
        _card_layout(card).addWidget(self._index_summary)
        return card

    def _build_timeline_card(self) -> QFrame:
        card = _card(text("replay.card.timeline"), self)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._position = QLabel(text("replay.position.empty"), card)
        self._position.setProperty("ui2Role", "secondary")
        row.addWidget(self._position)
        self._seek = QSlider(Qt.Orientation.Horizontal, card)
        self._seek.setRange(0, 1000)
        self._seek.setAccessibleName(text("replay.position.name"))
        self._seek.sliderReleased.connect(self._seek_replay)
        row.addWidget(self._seek, 1)
        self._next = QPushButton(text("replay.next"), card)
        self._next.setProperty("ui2Role", "utility-action")
        self._next.setAccessibleName(text("replay.next.name"))
        self._next.clicked.connect(self._view_model.read_next_spectrum)
        row.addWidget(self._next)
        _card_layout(card).addLayout(row)
        boundary = QLabel(
            text("replay.timeline.boundary"),
            card,
        )
        boundary.setProperty("ui2Role", "secondary")
        boundary.setWordWrap(True)
        _card_layout(card).addWidget(boundary)
        return card

    def _build_reprocess_card(self) -> QFrame:
        card = _card(text("replay.card.reprocess"), self)
        explanation = QLabel(
            text("replay.reprocess.explanation"),
            card,
        )
        explanation.setProperty("ui2Role", "secondary")
        explanation.setWordWrap(True)
        _card_layout(card).addWidget(explanation)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        self._backend = QComboBox(card)
        self._backend.setProperty("ui2Role", "utility-select")
        self._backend.setAccessibleName(text("replay.backend.name"))
        self._backend.addItem(text("replay.backend.cpu"), "cpu")
        self._backend.addItem(text("replay.backend.cuda"), "cuda")
        controls.addWidget(self._backend)
        self._reprocess = QPushButton(text("replay.reprocess.run"), card)
        self._reprocess.setProperty("ui2Role", "utility-action")
        self._reprocess.setAccessibleName(text("replay.reprocess.name"))
        self._reprocess.clicked.connect(self._confirm_reprocess)
        controls.addWidget(self._reprocess)
        self._cancel = QPushButton(text("replay.reprocess.cancel"), card)
        self._cancel.setProperty("ui2Role", "utility-action")
        self._cancel.setAccessibleName(text("replay.reprocess.cancel.name"))
        self._cancel.clicked.connect(self._view_model.cancel_reprocess)
        controls.addWidget(self._cancel)
        controls.addStretch(1)
        _card_layout(card).addLayout(controls)
        self._reprocess_summary = QLabel(text("replay.reprocess.not_started"), card)
        self._reprocess_summary.setProperty("ui2Role", "secondary")
        self._reprocess_summary.setWordWrap(True)
        _card_layout(card).addWidget(self._reprocess_summary)
        return card

    def _choose_recording(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            text("replay.dialog.open.title"),
            self._path.text(),
            text("replay.dialog.filter"),
        )
        if selected:
            self._path.setText(selected)

    def _seek_replay(self) -> None:
        self._view_model.seek(self._seek.value() / 1000.0)

    def _confirm_reprocess(self) -> None:
        choice = QMessageBox.question(
            self,
            text("replay.dialog.confirm.title"),
            text("replay.dialog.confirm.detail"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice is QMessageBox.StandardButton.Yes:
            self._view_model.reprocess_iq(str(self._backend.currentData()))

    def _render_state(self, state: ReplayViewState) -> None:
        index = state.index
        can_operate = index is not None and not state.busy
        self._open.setEnabled(not state.busy)
        self._next.setEnabled(can_operate)
        self._seek.setEnabled(can_operate)
        self._reprocess.setEnabled(can_operate)
        self._cancel.setEnabled(state.busy)
        self._seek.blockSignals(True)
        self._seek.setValue(round(state.position.fraction * 1000.0))
        self._seek.blockSignals(False)
        if state.busy:
            self._state_chip.set_text(text("replay.state.busy"))
            self._state_chip.set_tone(StatusTone.WARNING)
        elif index is None:
            self._state_chip.set_text(text("replay.state.unopened"))
            self._state_chip.set_tone(StatusTone.NEUTRAL)
        else:
            self._state_chip.set_text(text("replay.state.ready"))
            self._state_chip.set_tone(StatusTone.INFO)
        if index is None:
            self._index_summary.setText(text("replay.index.unopened"))
            self._position.setText(text("replay.position.empty"))
        else:
            self._index_summary.setText(
                text(
                    "replay.index.summary",
                    filename=index.filename,
                    frames=index.spectrum_frames,
                    duration=index.duration_ns / 1_000_000_000,
                    size=index.source_size,
                )
            )
            self._position.setText(
                text(
                    "replay.position.current",
                    ordinal=state.position.ordinal + 1,
                    total=index.spectrum_frames,
                    percent=state.position.fraction * 100,
                )
            )
        frame = state.spectrum_frame
        if frame is not None and frame is not self._rendered_frame:
            self._scene.set_frame(frame)
            self._rendered_frame = frame
        result = state.reprocess
        if result is not None:
            output = text("replay.reprocess.not_published") if result.output_filename is None else result.output_filename
            warning = f" · {result.warning}" if result.warning else ""
            self._reprocess_summary.setText(
                text(
                    "replay.reprocess.summary",
                    status=result.status,
                    frames=result.frames_processed,
                    backend_used=result.backend_used,
                    backend_requested=result.backend_requested,
                    output=output,
                    warning=warning,
                )
            )
        if state.error:
            self._error_banner.set_content(text("replay.error.title"), state.error)
            self._error_banner.set_action("", enabled=False)
            self._error_banner.setVisible(True)
        else:
            self._error_banner.setVisible(False)


def replay_workspace_definition(
    view_model: DeferredReplayViewModel,
    *,
    theme: ThemeId = ThemeId.DARK,
) -> WorkspaceDefinition:
    return WorkspaceDefinition(
        workspace_id="replay",
        label=text("replay.workspace.label"),
        description=text("replay.workspace.description"),
        icon=V2IconId.CHEVRON,
        workspace_factory=lambda: ReplayWorkspaceV2(view_model, theme=theme),
        inspector_factory=lambda: _inspector(view_model, theme),
        label_key="replay.workspace.label",
        description_key="replay.workspace.description",
    )


def _card(title: str, parent: QWidget) -> QFrame:
    card = QFrame(parent)
    card.setProperty("ui2Role", "card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(8)
    heading = QLabel(title, card)
    heading.setProperty("ui2Role", "section-heading")
    layout.addWidget(heading)
    return card


def _card_layout(card: QFrame) -> QVBoxLayout:
    layout = card.layout()
    if not isinstance(layout, QVBoxLayout):
        raise RuntimeError("replay V2 card has an invalid layout")
    return layout


def _inspector(view_model: DeferredReplayViewModel, theme: ThemeId) -> QWidget:
    inspector = QFrame()
    inspector.setProperty("ui2Root", True)
    inspector.setProperty("ui2Role", "panel")
    inspector.setStyleSheet(stylesheet_for_theme(theme))
    layout = QVBoxLayout(inspector)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.addWidget(
        SectionHeader(text("replay.inspector.context"), text("replay.inspector.detail"), parent=inspector)
    )
    summary = QLabel(inspector)
    summary.setProperty("ui2Role", "secondary")
    summary.setWordWrap(True)
    layout.addWidget(summary)
    boundary = QLabel(
        text("replay.inspector.boundary"),
        inspector,
    )
    boundary.setProperty("ui2Role", "secondary")
    boundary.setWordWrap(True)
    layout.addWidget(boundary)
    layout.addStretch(1)

    def render(state: ReplayViewState) -> None:
        if state.index is None:
            summary.setText(text("replay.inspector.unopened"))
        else:
            summary.setText(
                text(
                    "replay.inspector.summary",
                    filename=state.index.filename,
                    frames=state.index.spectrum_frames,
                    busy=text("replay.yes") if state.busy else text("replay.no"),
                )
            )

    unsubscribe = view_model.subscribe(render)
    inspector.destroyed.connect(lambda: unsubscribe())
    return inspector


__all__ = ["ReplayWorkspaceV2", "replay_workspace_definition"]

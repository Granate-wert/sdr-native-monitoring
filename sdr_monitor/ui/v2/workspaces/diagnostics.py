"""Deferred diagnostics workspace admitted by UI2-10A only."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.domain import DiagnosticStatus, DiagnosticsSnapshot

from ..components import ErrorBanner, SectionHeader, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import text
from ..shell.contracts import WorkspaceDefinition
from ..view_models.diagnostics_view_model import DeferredDiagnosticsViewModel, DiagnosticsViewState


class DiagnosticsWorkspaceV2(QWidget):
    """Present immutable diagnostics; no RX command is exposed by this workspace."""

    def __init__(
        self,
        view_model: DeferredDiagnosticsViewModel,
        *,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._theme = theme
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
        self.setAccessibleName(text("diagnostics.accessible.name"))
        self.setAccessibleDescription(text("diagnostics.accessible.description"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(
            SectionHeader(
                text("diagnostics.header.title"),
                text("diagnostics.header.detail"),
                parent=self,
            )
        )
        command = QFrame(self)
        command.setProperty("ui2Role", "card")
        command_row = QHBoxLayout(command)
        self._load_button = QPushButton(text("diagnostics.load"), command)
        self._load_button.setProperty("ui2Role", "primary-action")
        self._load_button.setAccessibleName(text("diagnostics.load.name"))
        self._load_button.clicked.connect(self._view_model.load)
        command_row.addWidget(self._load_button)
        self._self_test_button = QPushButton(text("diagnostics.self_test"), command)
        self._self_test_button.setProperty("ui2Role", "utility-action")
        self._self_test_button.setAccessibleName(text("diagnostics.self_test.name"))
        self._self_test_button.clicked.connect(self._view_model.run_self_tests)
        command_row.addWidget(self._self_test_button)
        self._cancel_button = QPushButton(text("diagnostics.cancel"), command)
        self._cancel_button.setProperty("ui2Role", "utility-action")
        self._cancel_button.setAccessibleName(text("diagnostics.cancel.name"))
        self._cancel_button.clicked.connect(self._view_model.cancel)
        command_row.addWidget(self._cancel_button)
        self._state_chip = StatusChipV2(
            text("diagnostics.state.not_loaded"), tone=StatusTone.NEUTRAL, parent=command
        )
        command_row.addWidget(self._state_chip)
        command_row.addStretch(1)
        layout.addWidget(command)
        self._error_banner = ErrorBanner(text("diagnostics.error.title"), "", parent=self)
        self._error_banner.setVisible(False)
        layout.addWidget(self._error_banner)
        content = QGridLayout()
        content.setHorizontalSpacing(8)
        content.setVerticalSpacing(8)
        self._cards_table = _table(_headers("diagnostics.table.headers.cards"), self)
        self._cards_table.setAccessibleName(text("diagnostics.table.cards.name"))
        content.addWidget(_card(text("diagnostics.card.summary"), self._cards_table, self), 0, 0)
        self._tests_table = _table(_headers("diagnostics.table.headers.tests"), self)
        self._tests_table.setAccessibleName(text("diagnostics.table.tests.name"))
        content.addWidget(_card(text("diagnostics.card.self_tests"), self._tests_table, self), 0, 1)
        self._errors_table = _table(_headers("diagnostics.table.headers.errors"), self)
        self._errors_table.setAccessibleName(text("diagnostics.table.errors.name"))
        content.addWidget(_card(text("diagnostics.card.errors"), self._errors_table, self), 1, 0)
        self._metrics_label = QLabel(text("diagnostics.metrics.not_loaded"), self)
        self._metrics_label.setProperty("ui2Role", "secondary")
        self._metrics_label.setWordWrap(True)
        content.addWidget(_card(text("diagnostics.card.metrics"), self._metrics_label, self), 1, 1)
        layout.addLayout(content, 1)
        bundle = QFrame(self)
        bundle.setProperty("ui2Role", "card")
        bundle_layout = QHBoxLayout(bundle)
        bundle_layout.addWidget(QLabel(text("diagnostics.bundle.directory"), bundle))
        self._bundle_path = QLineEdit(bundle)
        self._bundle_path.setPlaceholderText(text("diagnostics.bundle.placeholder"))
        self._bundle_path.setAccessibleName(text("diagnostics.bundle.directory.name"))
        bundle_layout.addWidget(self._bundle_path, 1)
        self._bundle_button = QPushButton(text("diagnostics.bundle.create"), bundle)
        self._bundle_button.setProperty("ui2Role", "utility-action")
        self._bundle_button.setAccessibleName(text("diagnostics.bundle.create.name"))
        self._bundle_button.clicked.connect(lambda: self._view_model.export_bundle(self._bundle_path.text()))
        bundle_layout.addWidget(self._bundle_button)
        self._bundle_status = QLabel(text("diagnostics.bundle.not_started"), bundle)
        self._bundle_status.setProperty("ui2Role", "secondary")
        self._bundle_status.setWordWrap(True)
        bundle_layout.addWidget(self._bundle_status, 1)
        layout.addWidget(bundle)

    def _render_state(self, state: DiagnosticsViewState) -> None:
        self._load_button.setText(
            text("diagnostics.refresh") if state.loaded else text("diagnostics.load")
        )
        self._load_button.setEnabled(not state.busy)
        self._self_test_button.setEnabled(state.loaded and not state.busy)
        self._cancel_button.setEnabled(state.busy)
        self._bundle_button.setEnabled(state.loaded and not state.busy)
        self._render_snapshot(state.snapshot)
        self._render_tests(state)
        if state.bundle is not None:
            self._bundle_status.setText(
                text(
                    "diagnostics.bundle.summary",
                    path=state.bundle.path,
                    files=len(state.bundle.files),
                    redacted=text("diagnostics.yes") if state.bundle.redacted else text("diagnostics.no"),
                )
            )
        if state.error:
            self._error_banner.set_content(text("diagnostics.error.title"), state.error)
            self._error_banner.set_action("", enabled=False)
            self._error_banner.setVisible(True)
        else:
            self._error_banner.setVisible(False)
        if state.busy:
            self._state_chip.set_text(text("diagnostics.state.busy"))
            self._state_chip.set_tone(StatusTone.WARNING)
        elif state.loaded:
            self._state_chip.set_text(text("diagnostics.state.loaded"))
            self._state_chip.set_tone(StatusTone.INFO)
        else:
            self._state_chip.set_text(text("diagnostics.state.not_loaded"))
            self._state_chip.set_tone(StatusTone.NEUTRAL)

    def _render_snapshot(self, snapshot: DiagnosticsSnapshot | None) -> None:
        cards = () if snapshot is None else snapshot.cards
        self._cards_table.setRowCount(len(cards))
        for row, card in enumerate(cards):
            for column, value in enumerate((card.title, _status_text(card.status), card.version, card.last_test, card.detail)):
                self._cards_table.setItem(row, column, QTableWidgetItem(value))
        errors = () if snapshot is None else snapshot.errors
        self._errors_table.setRowCount(len(errors))
        for row, error in enumerate(errors):
            for column, value in enumerate((error.summary, error.reason, error.recommendation, error.source)):
                self._errors_table.setItem(row, column, QTableWidgetItem(value))
        if snapshot is None:
            self._metrics_label.setText(text("diagnostics.metrics.not_loaded"))
        else:
            values = "; ".join(f"{key}: {value}" for key, value in sorted(snapshot.metrics.items()))
            self._metrics_label.setText(values or text("diagnostics.metrics.unpublished"))

    def _render_tests(self, state: DiagnosticsViewState) -> None:
        self._tests_table.setRowCount(len(state.self_tests))
        for row, result in enumerate(state.self_tests):
            for column, value in enumerate(
                (result.name, _status_text(result.status), f"{result.duration_ms:.3f} ms", result.detail)
            ):
                self._tests_table.setItem(row, column, QTableWidgetItem(value))


def diagnostics_workspace_definition(
    view_model: DeferredDiagnosticsViewModel,
    *,
    theme: ThemeId = ThemeId.DARK,
) -> WorkspaceDefinition:
    return WorkspaceDefinition(
        workspace_id="diagnostics",
        label=text("diagnostics.workspace.label"),
        description=text("diagnostics.workspace.description"),
        icon=V2IconId.INFO,
        workspace_factory=lambda: DiagnosticsWorkspaceV2(view_model, theme=theme),
        inspector_factory=lambda: _inspector(view_model, theme),
        label_key="diagnostics.workspace.label",
        description_key="diagnostics.workspace.description",
    )


def _inspector(view_model: DeferredDiagnosticsViewModel, theme: ThemeId) -> QWidget:
    inspector = QFrame()
    inspector.setProperty("ui2Root", True)
    inspector.setProperty("ui2Role", "panel")
    inspector.setStyleSheet(stylesheet_for_theme(theme))
    layout = QVBoxLayout(inspector)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.addWidget(
        SectionHeader(
            text("diagnostics.inspector.context"),
            text("diagnostics.inspector.detail"),
            parent=inspector,
        )
    )
    detail = QLabel(text("diagnostics.inspector.not_loaded"), inspector)
    detail.setProperty("ui2Role", "secondary")
    detail.setWordWrap(True)
    layout.addWidget(detail)
    boundary = QLabel(text("diagnostics.inspector.boundary"), inspector)
    boundary.setProperty("ui2Role", "secondary")
    boundary.setWordWrap(True)
    layout.addWidget(boundary)
    layout.addStretch(1)

    def render(state: DiagnosticsViewState) -> None:
        if state.snapshot is None:
            detail.setText(text("diagnostics.inspector.not_loaded"))
        else:
            detail.setText(
                text(
                    "diagnostics.inspector.summary",
                    cards=len(state.snapshot.cards),
                    errors=len(state.snapshot.errors),
                    tests=len(state.self_tests),
                    busy=text("diagnostics.yes") if state.busy else text("diagnostics.no"),
                )
            )

    unsubscribe = view_model.subscribe(render)
    inspector.destroyed.connect(lambda: unsubscribe())
    return inspector


def _table(headers: tuple[str, ...], parent: QWidget) -> QTableWidget:
    table = QTableWidget(0, len(headers), parent)
    table.setProperty("ui2Role", "data-table")
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    return table


def _headers(key: str) -> tuple[str, ...]:
    return tuple(text(key).split("|"))


def _card(title: str, body: QWidget, parent: QWidget) -> QFrame:
    card = QFrame(parent)
    card.setProperty("ui2Role", "card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(10, 10, 10, 10)
    heading = QLabel(title, card)
    heading.setProperty("ui2Role", "section-heading")
    layout.addWidget(heading)
    layout.addWidget(body, 1)
    return card


def _status_text(status: DiagnosticStatus) -> str:
    return {
        DiagnosticStatus.PASS: text("diagnostics.status.pass"),
        DiagnosticStatus.WARN: text("diagnostics.status.warn"),
        DiagnosticStatus.FAIL: text("diagnostics.status.fail"),
        DiagnosticStatus.UNAVAILABLE: text("diagnostics.status.unavailable"),
        DiagnosticStatus.CANCELLED: text("diagnostics.status.cancelled"),
    }[status]


__all__ = ["DiagnosticsWorkspaceV2", "diagnostics_workspace_definition"]

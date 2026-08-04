"""S10 diagnostics, error center and support bundle workspace."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ...domain import DiagnosticStatus, DiagnosticsSnapshot, SelfTestResult, SupportBundleResult
from ..components import ErrorState, SectionCard, StatusChip
from ..design_tokens import StatusTone
from ..presenters import DiagnosticsPresenter


class DiagnosticsWorkspace(QWidget):
    def __init__(self, presenter: DiagnosticsPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presenter = presenter
        self._build_ui()
        presenter.snapshot_changed.connect(self._show_snapshot)
        presenter.self_tests_changed.connect(self._show_tests)
        presenter.bundle_ready.connect(self._show_bundle)
        presenter.task_failed.connect(self._show_error)
        presenter.busy_changed.connect(self._set_busy)
        presenter.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("Диагностика и поддержка")
        title.setProperty("role", "heading")
        layout.addWidget(title)
        self.status = StatusChip("Self-tests не запускались", StatusTone.NEUTRAL)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.self_test_button = QPushButton("Запустить self-tests")
        self.self_test_button.clicked.connect(self._presenter.run_self_tests)
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.clicked.connect(self._presenter.cancel)
        self.rx_confirmation = QCheckBox("Подтверждаю RX-only test")
        self.rx_button = QPushButton("RX test")
        self.rx_button.clicked.connect(lambda: self._presenter.run_rx_test(self.rx_confirmation.isChecked()))
        export = QPushButton("Экспортировать support bundle…")
        export.clicked.connect(self._choose_bundle)
        for widget in (self.self_test_button, self.cancel_button, self.rx_confirmation, self.rx_button, export):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addLayout(actions)
        cards = SectionCard("Environment / backend cards")
        self.cards_table = QTableWidget(0, 6)
        self.cards_table.setHorizontalHeaderLabels(("Card", "Status", "Version", "Last test", "Detail", "Action"))
        cards.add_widget(self.cards_table)
        layout.addWidget(cards)
        tests = SectionCard("Last self-test")
        self.tests_table = QTableWidget(0, 4)
        self.tests_table.setHorizontalHeaderLabels(("Test", "Status", "Detail", "ms"))
        tests.add_widget(self.tests_table)
        layout.addWidget(tests)
        errors = SectionCard("Error center")
        self.error_list = QListWidget()
        self.error_list.setAccessibleName("Diagnostics error center")
        errors.add_widget(self.error_list)
        layout.addWidget(errors)
        self._bundle_label = QLabel("Support bundle: —")
        self._bundle_label.setWordWrap(True)
        layout.addWidget(self._bundle_label)
        self._error = ErrorState("Нет активной ошибки")
        self._error.setVisible(False)
        layout.addWidget(self._error)
        layout.addStretch(1)

    def _show_snapshot(self, snapshot: DiagnosticsSnapshot) -> None:
        self.cards_table.setRowCount(len(snapshot.cards))
        for row, card in enumerate(snapshot.cards):
            for column, value in enumerate((card.title, card.status.value, card.version, card.last_test, card.detail, card.primary_action)):
                self.cards_table.setItem(row, column, QTableWidgetItem(value))
        self.error_list.clear()
        for error in snapshot.errors:
            item = f"{error.summary} — {error.reason}; recommendation: {error.recommendation}"
            self.error_list.addItem(item)

    def _show_tests(self, tests: list[SelfTestResult]) -> None:
        self.tests_table.setRowCount(len(tests))
        for row, item in enumerate(tests):
            for column, value in enumerate((item.name, item.status.value, item.detail, f"{item.duration_ms:.2f}")):
                self.tests_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.status.set_status(f"Self-tests: {len(tests)}", StatusTone.SUCCESS if all(item.status is not DiagnosticStatus.FAIL for item in tests) else StatusTone.ERROR)

    def _choose_bundle(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Папка support bundle")
        if selected:
            self._presenter.export_bundle(Path(selected))

    def _show_bundle(self, result: SupportBundleResult) -> None:
        self._bundle_label.setText(f"Support bundle: {result.path}; redacted={result.redacted}")

    def _show_error(self, message: str) -> None:
        self._error.set_message(message)
        self._error.setVisible(True)
        self._presenter.report_error("Diagnostics task failed", message, "Review task details and rerun", message)

    def _set_busy(self, busy: bool) -> None:
        self.self_test_button.setEnabled(not busy)

    def shutdown(self) -> None:
        self._presenter.shutdown()


__all__ = ["DiagnosticsWorkspace"]

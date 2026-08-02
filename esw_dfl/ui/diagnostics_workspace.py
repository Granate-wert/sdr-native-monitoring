"""Diagnostics workspace: platform/native/backend state, P15 validation, support bundle.

Thin GUI layer.  :class:`DiagnosticsPresenter` owns collection, workers,
cancellation and privacy redaction; this widget renders immutable
:class:`DiagnosticsSnapshot` values from a QTimer.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .diagnostics_presenter import DiagnosticsPresenter
from .diagnostics_state import DiagnosticsSnapshot, ValidationRunState
from .i18n import LocaleId, Translator


class DiagnosticsWorkspace(QWidget):
    """Read-only diagnostics with the cancellable validation runner."""

    def __init__(
        self,
        *,
        presenter: DiagnosticsPresenter | None = None,
        locale: LocaleId = LocaleId.RU,
        poll_interval_ms: int = 250,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("p16DiagnosticsWorkspace")
        self._locale = locale
        self._tr = Translator(locale)
        self._presenter = presenter if presenter is not None else DiagnosticsPresenter()
        self._last_key: tuple[object, ...] | None = None

        root = QVBoxLayout(self)
        self._status = QLabel(self._tr.text("diagnostics.status.ready"), self)
        self._status.setObjectName("diagnosticsStatus")
        root.addWidget(self._status)

        buttons = QHBoxLayout()
        self._refresh_btn = QPushButton(self._tr.text("diagnostics.action.refresh"), self)
        self._refresh_btn.setObjectName("diagnosticsRefresh")
        self._refresh_btn.clicked.connect(self._on_refresh)
        self._selftest_btn = QPushButton(self._tr.text("diagnostics.action.selftest"), self)
        self._selftest_btn.setObjectName("diagnosticsSelfTest")
        self._selftest_btn.clicked.connect(self._on_selftest)
        self._validation_btn = QPushButton(self._tr.text("diagnostics.action.validate"), self)
        self._validation_btn.setObjectName("diagnosticsValidate")
        self._validation_btn.clicked.connect(self._on_validate)
        self._cancel_btn = QPushButton(self._tr.text("diagnostics.action.cancel"), self)
        self._cancel_btn.setObjectName("diagnosticsCancel")
        self._cancel_btn.clicked.connect(self._presenter.cancel_validation)
        self._bundle_btn = QPushButton(self._tr.text("diagnostics.action.bundle"), self)
        self._bundle_btn.setObjectName("diagnosticsBundle")
        self._bundle_btn.clicked.connect(self._on_bundle)
        for button in (
            self._refresh_btn,
            self._selftest_btn,
            self._validation_btn,
            self._cancel_btn,
            self._bundle_btn,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self._sections_box = QGroupBox(self._tr.text("diagnostics.sections.title"), self)
        self._sections_layout = QVBoxLayout(self._sections_box)
        root.addWidget(self._sections_box)

        table_box = QGroupBox(self._tr.text("diagnostics.validation.title"), self)
        table_layout = QVBoxLayout(table_box)
        self._validation_table = QTableWidget(0, 3, table_box)
        self._validation_table.setObjectName("diagnosticsValidationTable")
        self._validation_table.setHorizontalHeaderLabels(
            (
                self._tr.text("diagnostics.validation.col_name"),
                self._tr.text("diagnostics.validation.col_status"),
                self._tr.text("diagnostics.validation.col_detail"),
            )
        )
        self._validation_table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self._validation_table)
        root.addWidget(table_box)

        self._bundle_label = QLabel("—", self)
        self._bundle_label.setObjectName("diagnosticsBundleLabel")
        root.addWidget(self._bundle_label)
        root.addStretch(1)
        self._refresh_from_snapshot(self._presenter.snapshot)

        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll_presenter)
        self._timer.start()

    # -- actions ------------------------------------------------------------

    def _on_refresh(self) -> None:
        self._presenter.refresh()

    def _on_selftest(self) -> None:
        self._presenter.run_self_tests()
        self._refresh_from_snapshot(self._presenter.snapshot)

    def _on_validate(self) -> None:
        self._presenter.run_validation()

    def _on_bundle(self) -> None:
        self._presenter.export_support_bundle(Path(tempfile.gettempdir()))

    # -- polling ------------------------------------------------------------

    def _poll_presenter(self) -> None:
        self._refresh_from_snapshot(self._presenter.poll())

    def _refresh_from_snapshot(self, snapshot: DiagnosticsSnapshot) -> None:
        key = (
            snapshot.generation,
            snapshot.validation_state,
            len(snapshot.validation_rows),
            snapshot.support_bundle.size if snapshot.support_bundle else None,
            snapshot.error,
        )
        if key == self._last_key:
            return
        self._last_key = key
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: DiagnosticsSnapshot) -> None:
        running = snapshot.validation_state is ValidationRunState.RUNNING
        self._validation_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)

        while self._sections_layout.count():
            item = self._sections_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for section in snapshot.sections:
            box = QGroupBox(section.title, self)
            form = QFormLayout(box)
            for key, value in section.rows:
                label = QLabel(str(value), box)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                form.addRow(QLabel(key, box), label)
            self._sections_layout.addWidget(box)

        self._validation_table.setRowCount(len(snapshot.validation_rows))
        for row_index, row_data in enumerate(snapshot.validation_rows):
            self._validation_table.setItem(row_index, 0, QTableWidgetItem(row_data.name))
            self._validation_table.setItem(row_index, 1, QTableWidgetItem(row_data.status))
            self._validation_table.setItem(row_index, 2, QTableWidgetItem(row_data.detail))

        if snapshot.support_bundle is not None:
            bundle = snapshot.support_bundle
            if bundle.error:
                self._bundle_label.setText(
                    self._tr.text("diagnostics.bundle.error", detail=bundle.error)
                )
            else:
                self._bundle_label.setText(
                    self._tr.text(
                        "diagnostics.bundle.ok",
                        path=bundle.path_hint or "—",
                        size=bundle.size,
                    )
                )

        if snapshot.error:
            self._status.setText(
                self._tr.text("diagnostics.status.error", detail=snapshot.error)
            )
        else:
            self._status.setText(self._tr.text("diagnostics.status.ready"))

    # -- teardown -----------------------------------------------------------

    def request_shutdown(self) -> None:
        self._timer.stop()
        self._presenter.close()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._timer.stop()
        self._presenter.close()
        super().closeEvent(event)


__all__ = ["DiagnosticsWorkspace"]

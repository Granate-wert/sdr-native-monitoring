"""Measurement cards/table with explicit quality and provenance columns."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..sdr.measurements import LiveMeasurementResult
from .i18n import LocaleId, Translator
from .measurement_presenter import MeasurementPresenter
from .measurement_state import MeasurementWorkspaceSnapshot


class MeasurementPanel(QWidget):
    """Render only normalized measurement results; no frame-loop I/O."""

    def __init__(
        self,
        presenter: MeasurementPresenter | None = None,
        *,
        locale: LocaleId = LocaleId.RU,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = Translator(locale)
        self._presenter = presenter or MeasurementPresenter(locale=locale)
        self.setObjectName("measurementPanel")
        self._build_ui()
        self._refresh(self._presenter.snapshot)

    @property
    def presenter(self) -> MeasurementPresenter:
        return self._presenter

    @property
    def table(self) -> QTableWidget:
        return self._table

    def publish(self, result: LiveMeasurementResult[object], *, measurement_id: str | None = None) -> bool:
        accepted = self._presenter.publish(result, measurement_id=measurement_id)
        self._refresh(self._presenter.snapshot)
        return accepted

    def set_results(self, results: tuple[LiveMeasurementResult[object], ...]) -> bool:
        accepted = self._presenter.set_results(results)
        self._refresh(self._presenter.snapshot)
        return accepted

    def clear(self) -> None:
        self._refresh(self._presenter.clear())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self._summary = QLabel(self._tr.text("measurement.empty"), self)
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)
        self._table = QTableWidget(0, 9, self)
        self._table.setObjectName("measurementCardsTable")
        self._table.setAccessibleName(self._tr.text("shell.bottom_tools.measurements"))
        self._table.setHorizontalHeaderLabels(tuple(self._tr.text(key) for key in (
            "glossary.calibration", "measurement.value", "measurement.unit", "measurement.quality",
            "measurement.uncertainty", "measurement.frame", "measurement.timestamp", "measurement.warning",
            "measurement.calibration",
        )))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.itemSelectionChanged.connect(self._show_details)
        root.addWidget(self._table, 1)
        self._details = QPlainTextEdit(self)
        self._details.setObjectName("measurementWarningDrawer")
        self._details.setReadOnly(True)
        self._details.setPlaceholderText(self._tr.text("measurement.empty"))
        self._details.setMaximumHeight(100)
        root.addWidget(self._details)
        buttons = QHBoxLayout()
        copy_button = QPushButton(self._tr.text("measurement.copy"), self)
        copy_button.clicked.connect(self._copy)
        export_button = QPushButton(self._tr.text("measurement.export"), self)
        export_button.clicked.connect(self._export)
        clear_button = QPushButton(self._tr.text("measurement.clear"), self)
        clear_button.clicked.connect(self.clear)
        buttons.addWidget(copy_button)
        buttons.addWidget(export_button)
        buttons.addWidget(clear_button)
        buttons.addStretch(1)
        root.addLayout(buttons)

    def _refresh(self, snapshot: MeasurementWorkspaceSnapshot) -> None:
        self._table.setRowCount(len(snapshot.cards))
        for row, card in enumerate(snapshot.cards):
            values = (
                card.title, card.value, card.unit, card.quality.value, card.uncertainty,
                card.frame, card.timestamp, str(len(card.warnings)), card.calibration,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 7:
                    item.setToolTip("\n".join(card.warnings) or "—")
                self._table.setItem(row, column, item)
        frame = "—" if snapshot.frame_sequence is None else f"frame {snapshot.frame_sequence} / config {snapshot.config_generation}"
        self._summary.setText(
            snapshot.error
            or (f"{len(snapshot.cards)}; {frame}; {snapshot.warning_count} warning(s)" if snapshot.cards else self._tr.text("measurement.empty"))
        )
        self._show_details()

    def _show_details(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._presenter.snapshot.cards):
            self._details.clear()
            return
        card = self._presenter.snapshot.cards[row]
        lines = [card.detail, f"{self._tr.text('measurement.source')}: {card.source}", *card.warnings]
        self._details.setPlainText("\n".join(line for line in lines if line))

    def _copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._presenter.copy_text())

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self._tr.text("measurement.export"), "measurements.csv", "CSV (*.csv)")
        if path:
            self._presenter.export_csv(path)


__all__ = ["MeasurementPanel"]

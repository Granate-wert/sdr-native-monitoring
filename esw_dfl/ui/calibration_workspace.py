"""Qt presentation for calibration profiles, applicability and import safety."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..sdr.calibration_store import CalibrationSignature
from .calibration_presenter import CalibrationPresenter
from .calibration_state import CalibrationPlotSnapshot, CalibrationWorkspaceSnapshot
from .components import StatusBadge
from .design_tokens import StatusTone
from .i18n import LocaleId, Translator
from .units import format_frequency_hz


class CalibrationPlot(QWidget):
    """Compact correction/uncertainty plot with no scientific palette coupling."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plot = CalibrationPlotSnapshot()
        self.setObjectName("calibrationPlot")
        self.setAccessibleName("calibration_correction_uncertainty_plot")
        self.setMinimumHeight(180)

    @property
    def plot(self) -> CalibrationPlotSnapshot:
        return self._plot

    def set_plot(self, plot: CalibrationPlotSnapshot) -> None:
        self._plot = plot
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        area = self.rect().adjusted(42, 16, -16, -28)
        painter.setPen(QPen(self.palette().mid().color()))
        painter.drawRect(area)
        if len(self._plot.frequency_hz) < 2:
            painter.setPen(self.palette().text().color())
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "—")
            return
        x0, x1 = self._plot.frequency_hz[0], self._plot.frequency_hz[-1]
        span = max(x1 - x0, 1.0)
        values = self._plot.correction_db + self._plot.uncertainty_db
        lower = self._plot.correction_db + tuple(-item for item in self._plot.uncertainty_db)
        low = min(lower)
        high = max(values)
        if high <= low:
            high = low + 1.0
        for data, color in ((self._plot.correction_db, self.palette().highlight().color()), (values, self.palette().mid().color())):
            path = QPainterPath()
            for index, value in enumerate(data):
                x = area.left() + (self._plot.frequency_hz[index] - x0) / span * area.width()
                y = area.bottom() - (value - low) / (high - low) * area.height()
                if index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(color, 1.5))
            painter.drawPath(path)
        painter.setPen(self.palette().text().color())
        painter.drawText(4, area.top() + 12, "corr ± uncertainty")
        painter.drawText(area.left(), self.height() - 6, format_frequency_hz(x0, locale=LocaleId.RU))
        painter.drawText(area.right() - 90, self.height() - 6, format_frequency_hz(x1, locale=LocaleId.RU))


class CalibrationWorkspace(QWidget):
    """Profile browser and import workflow driven by :class:`CalibrationPresenter`."""

    def __init__(
        self,
        presenter: CalibrationPresenter | None = None,
        *,
        locale: LocaleId = LocaleId.RU,
        signature_provider: Callable[[], CalibrationSignature | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = Translator(locale)
        self._presenter = presenter or CalibrationPresenter(locale=locale)
        self._signature_provider = signature_provider or (lambda: None)
        self._selected_csv: Path | None = None
        self.setObjectName("calibrationWorkspace")
        self._build_ui()
        self._refresh(self._presenter.snapshot)

    @property
    def presenter(self) -> CalibrationPresenter:
        return self._presenter

    @property
    def profile_table(self) -> QTableWidget:
        return self._profile_table

    @property
    def plot(self) -> CalibrationPlot:
        return self._plot

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.addWidget(QLabel(self._tr.text("calibration.title")))
        self._status = StatusBadge(self._tr.text("calibration.no_profile"), StatusTone.NEUTRAL, self)
        root.addWidget(self._status)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        top = QWidget(splitter)
        top_layout = QVBoxLayout(top)
        top_layout.addWidget(QLabel(self._tr.text("calibration.profiles")))
        self._profile_table = QTableWidget(0, 7, top)
        self._profile_table.setObjectName("calibrationProfileTable")
        self._profile_table.setAccessibleName(self._tr.text("calibration.profiles"))
        self._profile_table.setHorizontalHeaderLabels(tuple(self._tr.text(key) for key in (
            "calibration.profile", "calibration.device", "calibration.settings", "calibration.range",
            "calibration.reference_plane", "calibration.uncertainty", "calibration.applicability",
        )))
        self._profile_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._profile_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._profile_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._profile_table.itemSelectionChanged.connect(self._profile_selected)
        top_layout.addWidget(self._profile_table)
        action_row = QHBoxLayout()
        self._activate_button = QPushButton(self._tr.text("calibration.activate"), top)
        self._activate_button.clicked.connect(self._activate)
        self._clear_button = QPushButton(self._tr.text("calibration.clear_active"), top)
        self._clear_button.clicked.connect(self._clear_active)
        action_row.addWidget(self._activate_button)
        action_row.addWidget(self._clear_button)
        action_row.addStretch(1)
        top_layout.addLayout(action_row)
        splitter.addWidget(top)

        bottom = QWidget(splitter)
        bottom_layout = QHBoxLayout(bottom)
        details = QGroupBox(self._tr.text("calibration.applicability"), bottom)
        details_layout = QVBoxLayout(details)
        self._applicability = QLabel("—", details)
        self._applicability.setWordWrap(True)
        details_layout.addWidget(self._applicability)
        self._comparison = QTableWidget(0, 4, details)
        self._comparison.setHorizontalHeaderLabels(tuple(self._tr.text(key) for key in (
            "calibration.field", "calibration.expected", "calibration.actual", "calibration.match",
        )))
        self._comparison.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        details_layout.addWidget(self._comparison)
        bottom_layout.addWidget(details, 2)
        right = QVBoxLayout()
        self._plot = CalibrationPlot(bottom)
        right.addWidget(self._plot)
        right.addWidget(self._build_import_group(bottom))
        bottom_layout.addLayout(right, 1)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)
        self._error = QLabel("", self)
        self._error.setWordWrap(True)
        root.addWidget(self._error)

    def _build_import_group(self, parent: QWidget) -> QGroupBox:
        group = QGroupBox(self._tr.text("calibration.import"), parent)
        form = QFormLayout(group)
        self._profile_id = QLineEdit(group)
        self._profile_id.setPlaceholderText("profile-id")
        self._profile_version = QSpinBox(group)
        self._profile_version.setRange(1, 2_147_483_647)
        self._profile_version.setValue(1)
        self._source_label = QLabel("—", group)
        select = QPushButton(self._tr.text("calibration.choose_csv"), group)
        select.clicked.connect(self._choose_csv)
        preview = QPushButton(self._tr.text("calibration.preview"), group)
        preview.clicked.connect(self._preview_csv)
        finalize = QPushButton(self._tr.text("calibration.finalize"), group)
        finalize.clicked.connect(self._finalize)
        form.addRow(self._tr.text("calibration.profile_id"), self._profile_id)
        form.addRow(self._tr.text("calibration.profile_version"), self._profile_version)
        form.addRow(self._tr.text("calibration.source"), self._source_label)
        form.addRow(select, preview)
        form.addRow(finalize)
        return group

    def _profile_selected(self) -> None:
        row = self._profile_table.currentRow()
        if row < 0:
            return
        profile_id = self._profile_table.item(row, 0)
        version = self._profile_table.item(row, 0)
        if profile_id is None or version is None:
            return
        raw = str(profile_id.data(Qt.ItemDataRole.UserRole) or "")
        if not raw:
            return
        profile_name, _, raw_version = raw.partition("@")
        self._presenter.select_profile(profile_name, int(raw_version))
        self._refresh(self._presenter.snapshot)

    def _activate(self) -> None:
        self._presenter.select_active_profile()
        self._refresh(self._presenter.snapshot)

    def _clear_active(self) -> None:
        self._refresh(self._presenter.clear_active_profile())

    def _choose_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self._tr.text("calibration.choose_csv"), "", "CSV (*.csv)")
        if path:
            self._selected_csv = Path(path)
            self._source_label.setText(self._selected_csv.name)

    def _preview_csv(self) -> None:
        if self._selected_csv is None:
            self._error.setText(self._tr.text("calibration.no_csv"))
            return
        self._presenter.preview_csv(
            self._selected_csv,
            profile_id=self._profile_id.text().strip(),
            profile_version=self._profile_version.value(),
            signature=self._signature_provider(),
        )
        self._refresh(self._presenter.snapshot)

    def _finalize(self) -> None:
        self._presenter.finalize_import()
        self._refresh(self._presenter.snapshot)

    def _refresh(self, snapshot: CalibrationWorkspaceSnapshot) -> None:
        self._profile_table.setRowCount(len(snapshot.profiles))
        for row, profile_item in enumerate(snapshot.profiles):
            values = (
                f"{profile_item.profile_id} v{profile_item.profile_version}", profile_item.device_serial,
                f"{profile_item.sample_rate} / {profile_item.bandwidth} / {profile_item.gain}", profile_item.valid_range,
                profile_item.reference_plane, profile_item.uncertainty, profile_item.applicability.value,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, f"{profile_item.profile_id}@{profile_item.profile_version}")
                self._profile_table.setItem(row, column, cell)
        self._applicability.setText(f"{snapshot.applicability.value}: {snapshot.applicability_reason}")
        self._comparison.setRowCount(len(snapshot.comparison))
        for row, item in enumerate(snapshot.comparison):
            comparison_values = (item.field, item.expected, item.actual, "✓" if item.matches else f"✗ {item.reason or ''}")
            for column, value in enumerate(comparison_values):
                self._comparison.setItem(row, column, QTableWidgetItem(value))
        self._plot.set_plot(snapshot.plot)
        self._error.setText(snapshot.error or "")
        tone = StatusTone.SUCCESS if snapshot.active_profile_id else StatusTone.WARNING if snapshot.profiles else StatusTone.NEUTRAL
        self._status.set_status(
            self._tr.text("calibration.active", profile=snapshot.active_profile_id or self._tr.text("calibration.none")),
            tone,
        )


__all__ = ["CalibrationPlot", "CalibrationWorkspace"]

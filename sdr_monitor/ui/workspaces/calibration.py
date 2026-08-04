"""S07 three-column calibration and measurement workspace."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...domain import CalibrationImportPreview, CalibrationProfile, MeasurementValue
from ..components import ErrorState, MeasurementCard, SectionCard, StatusChip
from ..design_tokens import StatusTone
from ..presenters import CalibrationPresenter


class CalibrationPlot(QWidget):
    """Small Qt-only correction/uncertainty plot; values remain inspectable in a table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profile: CalibrationProfile | None = None
        self.setMinimumHeight(190)
        self.setAccessibleName("График коррекции и неопределённости")

    def set_profile(self, profile: CalibrationProfile | None) -> None:
        self._profile = profile
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        margin = 20
        painter.setPen(QPen(self.palette().mid().color(), 1))
        painter.drawLine(margin, self.height() - margin, self.width() - margin, self.height() - margin)
        painter.drawLine(margin, margin, margin, self.height() - margin)
        profile = self._profile
        if profile is None:
            painter.drawText(margin + 8, margin + 20, "Нет выбранного профиля")
            return
        values = [point.correction_db for point in profile.points]
        low = min(values) - 1.0
        high = max(values) + 1.0
        span = max(high - low, 1e-9)
        left = profile.points[0].frequency_hz
        right = profile.points[-1].frequency_hz
        xspan = max(right - left, 1e-9)
        points: list[QPointF] = []
        for item in profile.points:
            x = margin + (item.frequency_hz - left) / xspan * (self.width() - 2 * margin)
            y = self.height() - margin - (item.correction_db - low) / span * (self.height() - 2 * margin)
            points.append(QPointF(x, y))
        painter.setPen(QPen(self.palette().highlight().color(), 2))
        for first, second in zip(points, points[1:]):
            painter.drawLine(first, second)
        painter.setPen(QPen(self.palette().text().color(), 1))
        painter.drawText(margin + 4, self.height() - 4, "Частота")
        painter.drawText(margin + 4, margin + 12, "Коррекция, dB")


class CalibrationWorkspace(QWidget):
    def __init__(self, presenter: CalibrationPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presenter = presenter
        self._profiles: tuple[CalibrationProfile, ...] = ()
        self._selected: CalibrationProfile | None = None
        self._preview: CalibrationImportPreview | None = None
        self._build_ui()
        presenter.profiles_changed.connect(self._show_profiles)
        presenter.preview_changed.connect(self._show_preview)
        presenter.applicability_changed.connect(self._show_applicability)
        presenter.active_changed.connect(self._show_active)
        presenter.busy_changed.connect(self._set_busy)
        presenter.task_failed.connect(self._show_error)
        presenter.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        heading = QLabel("Калибровка и измерения")
        heading.setProperty("role", "heading")
        root.addWidget(heading)
        self._status = StatusChip("Профиль не выбран", StatusTone.NEUTRAL)
        root.addWidget(self._status)
        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setChildrenCollapsible(False)
        left = SectionCard("Профили")
        self.profile_list = QListWidget()
        self.profile_list.setAccessibleName("Список calibration profiles")
        self.profile_list.currentItemChanged.connect(self._profile_changed)
        self.profile_metadata = QLabel("Профили ещё не загружены")
        self.profile_metadata.setWordWrap(True)
        left.add_widget(self.profile_list)
        left.add_widget(self.profile_metadata)
        columns.addWidget(left)

        center = SectionCard("Correction / uncertainty")
        self.plot = CalibrationPlot()
        center.add_widget(self.plot)
        self.correction_table = QTableWidget(0, 4)
        self.correction_table.setHorizontalHeaderLabels(("Frequency, Hz", "Correction, dB", "Uncertainty, dB", "Status"))
        self.correction_table.setAccessibleName("Calibration correction table")
        center.add_widget(self.correction_table)
        columns.addWidget(center)

        right = SectionCard("Применимость и импорт")
        self.applicability_table = QTableWidget(0, 4)
        self.applicability_table.setHorizontalHeaderLabels(("Параметр", "Ожидалось", "Текущее", "Статус"))
        self.applicability_table.setAccessibleName("Applicability matrix")
        right.add_widget(self.applicability_table)
        self.backend_consistency = QLabel("CPU/CUDA: одна и та же калибровочная математика")
        self.backend_consistency.setWordWrap(True)
        right.add_widget(self.backend_consistency)
        self.expert_override = QCheckBox("Разрешить expert override несовместимости")
        self.expert_override.setAccessibleName("Calibration expert override")
        right.add_widget(self.expert_override)
        self.activate_button = QPushButton("Сделать активным")
        self.activate_button.clicked.connect(self._activate)
        right.add_widget(self.activate_button)
        self.import_button = QPushButton("Импортировать CSV…")
        self.import_button.clicked.connect(self._import_csv)
        right.add_widget(self.import_button)
        self.preview_label = QLabel("CSV preview не загружен")
        self.preview_label.setWordWrap(True)
        right.add_widget(self.preview_label)
        self.finalize_button = QPushButton("Зафиксировать immutable version")
        self.finalize_button.setEnabled(False)
        self.finalize_button.clicked.connect(self._finalize)
        right.add_widget(self.finalize_button)
        self.warning_drawer = QLabel("")
        self.warning_drawer.setWordWrap(True)
        self.warning_drawer.setProperty("role", "warning")
        right.add_widget(self.warning_drawer)
        columns.addWidget(right)
        columns.setSizes((260, 560, 360))
        root.addWidget(columns, 1)

        measurements = SectionCard("Измерения")
        row = QHBoxLayout()
        self.measurement_cards: dict[str, MeasurementCard] = {}
        for identifier, title in (("peak", "Peak"), ("occupied_bandwidth", "Occupied bandwidth"), ("channel_power", "Channel power")):
            card = MeasurementCard()
            card.set_values(title, "—", "—", meta="Ожидание данных", quality="Quality: unavailable")
            self.measurement_cards[identifier] = card
            row.addWidget(card)
        measurements.content.addLayout(row)
        root.addWidget(measurements)
        self._error = ErrorState("Нет активной ошибки")
        self._error.setVisible(False)
        root.addWidget(self._error)

    def _show_profiles(self, profiles: tuple[CalibrationProfile, ...]) -> None:
        self._profiles = profiles
        self.profile_list.clear()
        for profile in profiles:
            item = QListWidgetItem(f"{profile.profile_id} · v{profile.profile_version}")
            item.setData(Qt.ItemDataRole.UserRole, profile)
            self.profile_list.addItem(item)
        if profiles:
            self.profile_list.setCurrentRow(0)
        else:
            self.profile_metadata.setText("Нет finalized профилей. Импортируйте CSV для preview.")

    def _profile_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        profile = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._selected = profile if isinstance(profile, CalibrationProfile) else None
        self.plot.set_profile(self._selected)
        self.correction_table.setRowCount(0)
        if self._selected is None:
            self.profile_metadata.setText("Профиль не выбран")
            self.applicability_table.setRowCount(0)
            return
        self.profile_metadata.setText(
            f"Версия: v{self._selected.profile_version}\n"
            f"Диапазон: {self._selected.points[0].frequency_hz:.0f}–{self._selected.points[-1].frequency_hz:.0f} Hz\n"
            f"Точек: {len(self._selected.points)}\n"
            f"Reference plane: {self._selected.reference_plane}\n"
            f"Оборудование: {self._selected.reference_equipment or 'не указано'}"
        )
        self.correction_table.setRowCount(len(self._selected.points))
        for row, point in enumerate(self._selected.points):
            for column, value in enumerate((f"{point.frequency_hz:.3f}", f"{point.correction_db:.3f}", f"±{point.uncertainty_db:.3f}", "exact point")):
                self.correction_table.setItem(row, column, QTableWidgetItem(value))
        self._presenter.compare(self._selected)

    def _show_applicability(self, result: object) -> None:
        rows = getattr(result, "rows", ())
        self.applicability_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = (item.label, item.expected, item.actual, "OK" if item.matches else "Mismatch")
            for column, value in enumerate(values):
                self.applicability_table.setItem(row, column, QTableWidgetItem(value))
        applicable = bool(getattr(result, "applicable", False))
        self._status.set_status("Профиль применим" if applicable else "Профиль несовместим", StatusTone.SUCCESS if applicable else StatusTone.WARNING)
        self.warning_drawer.setText("" if applicable else f"Предупреждение: {getattr(result, 'reason', 'несовместимые настройки')}. Без explicit expert override абсолютные dBm измерения заблокированы.")

    def _import_csv(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(self, "Импорт calibration CSV", "", "CSV (*.csv)")
        if selected:
            path = Path(selected)
            self._presenter.preview_path(path, path.stem, 1)

    def _show_preview(self, preview: CalibrationImportPreview) -> None:
        self._preview = preview
        if preview.valid:
            self.preview_label.setText(f"Preview: {preview.source_name}; точек: {len(preview.points)}; ошибок: 0")
            self.finalize_button.setEnabled(True)
            self.warning_drawer.setText("CSV preview валиден; запись появится только после явного finalize.")
        else:
            self.preview_label.setText("CSV preview содержит ошибки: " + "; ".join(preview.errors))
            self.finalize_button.setEnabled(False)
            self.warning_drawer.setText("Импорт заблокирован до исправления CSV.")

    def _finalize(self) -> None:
        if self._preview is not None and self._preview.valid:
            self._presenter.finalize_preview(self._preview)

    def _activate(self) -> None:
        if self._selected is not None:
            self._presenter.activate(self._selected, expert_override=self.expert_override.isChecked())

    def _show_active(self, value: object) -> None:
        if isinstance(value, CalibrationProfile):
            self._status.set_status(f"Активен {value.profile_id} v{value.profile_version}", StatusTone.SUCCESS)
        elif value is None:
            self._status.set_status("Активный профиль снят", StatusTone.NEUTRAL)

    def set_measurements(self, values: tuple[MeasurementValue, ...]) -> None:
        for item in values:
            card = self.measurement_cards.get(item.measurement_id)
            if card is None:
                continue
            display = "—" if item.value is None else f"{item.value:.3f}"
            uncertainty = "" if item.uncertainty_db is None else f"±{item.uncertainty_db:.3f} dB"
            card.set_values(item.title, display, item.unit, meta=uncertainty, quality=f"Quality: {item.quality.value}; {item.calibration_status.value}")

    def _show_error(self, message: str) -> None:
        self._error.set_message(message)
        self._error.setVisible(True)
        self.warning_drawer.setText(message)

    def _set_busy(self, busy: bool) -> None:
        self.import_button.setEnabled(not busy)
        self.activate_button.setEnabled(not busy and self._selected is not None)

    def shutdown(self) -> None:
        self._presenter.shutdown()


__all__ = ["CalibrationPlot", "CalibrationWorkspace"]

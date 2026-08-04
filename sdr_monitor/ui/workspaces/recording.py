"""S08 recording setup and health workspace."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QDoubleSpinBox, QVBoxLayout, QWidget

from ...domain import RecordingHealth, RecordingOptions, RecordingResult, RecordingState
from ..components import ErrorState, NumericReadout, SectionCard, StatusChip
from ..design_tokens import StatusTone
from ..presenters import RecordingPresenter


class RecordingWorkspace(QWidget):
    def __init__(self, presenter: RecordingPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presenter = presenter
        self._build_ui()
        presenter.health_changed.connect(self._show_health)
        presenter.result_ready.connect(self._show_result)
        presenter.task_failed.connect(self._show_error)
        presenter.busy_changed.connect(self._set_busy)
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(500)
        self._health_timer.timeout.connect(presenter.refresh_health)
        self._health_timer.start()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("Запись live IQ / Spectrum")
        title.setProperty("role", "heading")
        layout.addWidget(title)
        self._rec_indicator = StatusChip("REC: idle", StatusTone.NEUTRAL)
        self._warning = QLabel("Запись получает только опубликованные live frames; synthetic producer не запускается.")
        self._warning.setWordWrap(True)
        layout.addWidget(self._rec_indicator)
        layout.addWidget(self._warning)
        setup = SectionCard("Настройка записи")
        form = QFormLayout()
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(str(Path.cwd() / "recording.sdrrec"))
        self.path_edit.setAccessibleName("Recording output path")
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self._choose_path)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        form.addRow("Файл", path_row)
        self.iq_check = QCheckBox("IQ blocks")
        self.iq_check.setChecked(True)
        self.spectrum_check = QCheckBox("Spectrum frames")
        form.addRow("Потоки", self.iq_check)
        form.addRow("", self.spectrum_check)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(1.0, 1e9)
        self.rate.setValue(1e6)
        self.rate.setSuffix(" Hz")
        form.addRow("Sample rate", self.rate)
        self.queue_capacity = QSpinBox()
        self.queue_capacity.setRange(1, 4096)
        self.queue_capacity.setValue(64)
        form.addRow("Queue capacity", self.queue_capacity)
        setup.content.addLayout(form)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Начать запись")
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("Остановить и finalize")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._presenter.stop)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        setup.content.addLayout(buttons)
        layout.addWidget(setup)

        health = SectionCard("Recording health")
        readings = QHBoxLayout()
        self._queue = NumericReadout("Queue")
        self._blocks = NumericReadout("IQ blocks")
        self._frames = NumericReadout("Spectrum frames")
        self._drops = NumericReadout("Drops / gaps")
        for widget in (self._queue, self._blocks, self._frames, self._drops):
            readings.addWidget(widget)
        health.content.addLayout(readings)
        self._disk = QLabel("Disk: —")
        self._metadata = QLabel("Metadata: source/config events are persisted in header/records")
        self._metadata.setWordWrap(True)
        health.add_widget(self._disk)
        health.add_widget(self._metadata)
        layout.addWidget(health)
        self._error = ErrorState("Нет активной ошибки")
        self._error.setVisible(False)
        layout.addWidget(self._error)
        layout.addStretch(1)

    def _choose_path(self) -> None:
        selected, _filter = QFileDialog.getSaveFileName(self, "Файл записи", self.path_edit.text(), "SDR recording (*.sdrrec)")
        if selected:
            self.path_edit.setText(selected)

    def _start(self) -> None:
        try:
            options = RecordingOptions(self.path_edit.text(), self.iq_check.isChecked(), self.spectrum_check.isChecked(), self.rate.value(), queue_capacity=self.queue_capacity.value())
        except ValueError as error:
            self._show_error(str(error))
            return
        self._presenter.start(options)

    def _show_health(self, health: RecordingHealth) -> None:
        self._queue.set_value(float(health.queue_depth), "")
        self._blocks.set_value(float(health.iq_blocks), "")
        self._frames.set_value(float(health.spectrum_frames), "")
        self._drops.set_value(float(health.drops), f" / {health.gaps} gaps")
        self._disk.setText(f"Disk free: {health.disk_free_bytes if health.disk_free_bytes is not None else '—'} bytes; written: {health.bytes_written} bytes")
        if health.state is RecordingState.RECORDING:
            self._rec_indicator.set_status("REC: live", StatusTone.ERROR)
        elif health.state is RecordingState.FINALIZING:
            self._rec_indicator.set_status("REC: finalizing", StatusTone.WARNING)
        elif health.state is RecordingState.FAILED:
            self._rec_indicator.set_status("REC: failed", StatusTone.ERROR)
        else:
            self._rec_indicator.set_status(f"REC: {health.state.value}", StatusTone.NEUTRAL)
        self.stop_button.setEnabled(health.state is RecordingState.RECORDING)

    def _show_result(self, result: RecordingResult | object) -> None:
        if isinstance(result, RecordingResult):
            self._warning.setText(f"Запись завершена: {result.output_path}; IQ={result.iq_blocks}, Spectrum={result.spectrum_frames}, gaps={result.gaps}")
        self._presenter.refresh_health()

    def _show_error(self, message: str) -> None:
        self._error.set_message(message)
        self._error.setVisible(True)
        self._warning.setText(message)

    def _set_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(not busy)

    def shutdown(self) -> None:
        self._health_timer.stop()
        self._presenter.shutdown()


__all__ = ["RecordingWorkspace"]

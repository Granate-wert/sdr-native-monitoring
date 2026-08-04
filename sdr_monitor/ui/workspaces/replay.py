"""S09 replay timeline and reprocess workspace."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from ...domain import RecordingIndex, ReplayPosition, ReprocessResult
from ..components import ErrorState, SectionCard, StatusChip
from ..design_tokens import StatusTone
from ..presenters import ReplayPresenter


class ReplayWorkspace(QWidget):
    def __init__(self, presenter: ReplayPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presenter = presenter
        self._path: Path | None = None
        self._build_ui()
        presenter.index_ready.connect(self._show_index)
        presenter.position_changed.connect(self._show_position)
        presenter.frame_ready.connect(self._show_frame)
        presenter.reprocess_ready.connect(self._show_reprocess)
        presenter.task_failed.connect(self._show_error)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("Replay / Seek / Reprocess")
        title.setProperty("role", "heading")
        layout.addWidget(title)
        self.status = StatusChip("Replay не открыт", StatusTone.NEUTRAL)
        layout.addWidget(self.status)
        toolbar = QHBoxLayout()
        open_button = QPushButton("Открыть recording…")
        open_button.clicked.connect(self._open)
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._presenter.play)
        pause_button = QPushButton("Pause")
        pause_button.clicked.connect(self._presenter.pause)
        self.speed = QComboBox()
        for value in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
            self.speed.addItem(f"{value:g}x", value)
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(lambda _index: self._presenter.set_speed(float(self.speed.currentData())))
        self.next_button = QPushButton("Next frame")
        self.next_button.clicked.connect(self._presenter.read_next)
        for widget in (open_button, self.play_button, pause_button, self.speed, self.next_button):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        timeline = SectionCard("Timeline")
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 1000)
        self.timeline.setAccessibleName("Replay timeline")
        self.timeline.sliderReleased.connect(lambda: self._presenter.seek(self.timeline.value() / 1000.0))
        timeline.add_widget(self.timeline)
        self.position_label = QLabel("Position: —")
        timeline.add_widget(self.position_label)
        layout.addWidget(timeline)
        reprocess = SectionCard("Async reprocess")
        self.backend = QComboBox()
        self.backend.addItems(("CPU", "CUDA"))
        reprocess.add_widget(self.backend)
        row = QHBoxLayout()
        run = QPushButton("Reprocess IQ")
        run.clicked.connect(self._reprocess)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self._presenter.cancel_reprocess)
        row.addWidget(run)
        row.addWidget(cancel)
        reprocess.content.addLayout(row)
        self.reprocess_label = QLabel("Нет результата")
        reprocess.add_widget(self.reprocess_label)
        layout.addWidget(reprocess)
        self._error = ErrorState("Нет активной ошибки")
        self._error.setVisible(False)
        layout.addWidget(self._error)
        layout.addStretch(1)

    def _open(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(self, "Открыть recording", "", "SDR recording (*.sdrrec)")
        if selected:
            self._path = Path(selected)
            self._presenter.open(self._path)

    def _show_index(self, index: RecordingIndex) -> None:
        self.timeline.setEnabled(index.frame_count > 0)
        self.status.set_status(f"Открыт: {index.frame_count} indexed frames", StatusTone.SUCCESS)

    def _show_position(self, position: ReplayPosition) -> None:
        self.timeline.blockSignals(True)
        self.timeline.setValue(round(position.fraction * 1000))
        self.timeline.blockSignals(False)
        self.position_label.setText(f"Position: frame {position.ordinal}, timestamp {position.timestamp_ns} ns")

    def _show_frame(self, frame: object) -> None:
        self.status.set_status(f"Published replay frame: {type(frame).__name__}", StatusTone.INFO)

    def _reprocess(self) -> None:
        if self._path is not None:
            self._presenter.reprocess(self._path, str(self.backend.currentText()).lower())

    def _show_reprocess(self, result: ReprocessResult) -> None:
        self.reprocess_label.setText(f"{result.status}; backend={result.backend_used}; frames={result.frames_processed}; {result.warning}")

    def _show_error(self, message: str) -> None:
        self._error.set_message(message)
        self._error.setVisible(True)

    def shutdown(self) -> None:
        self._presenter.shutdown()


__all__ = ["ReplayWorkspace"]

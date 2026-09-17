"""Read-only contextual inspector; bounded scalars, no device commands."""
from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..i18n import text
from ..state.sweep_inspection import SweepInspection, inspect_sweep
from ..view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewModel, AnalyzerViewState


class AnalyzerInspector(QScrollArea):
    """Update an open inspector at most 4 Hz, keeping only the latest state.

    Hidden inspectors do no projection/formatting. One single-shot timer drains
    the latest pending state, including the final event when publications stop.
    Source/mode/epoch/lifecycle changes clear stale detail immediately.
    """
    def __init__(self, model: AnalyzerViewModel) -> None:
        super().__init__()
        self.setProperty("ui2Role", "panel-scroll")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setMinimumSize(0, 0)
        content = QWidget(self)
        content.setProperty("ui2Role", "panel-scroll-content")
        self.setWidget(content)
        self._state = model.state
        self._report: SweepInspection | None = None
        self._key: tuple[object, ...] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.refresh)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.heading = self._label("analyzer.inspection.title")
        self.summary = self._label("analyzer.unavailable")
        self.segment = QComboBox(self)
        self.segment.setProperty("ui2Role", "utility-select")
        self.segment.setMinimumWidth(0)
        self.segment.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.segment.setMinimumContentsLength(8)
        self.segment.setAccessibleName(text("analyzer.inspection.segment"))
        self.segment.currentIndexChanged.connect(self._selection)
        self.detail = self._label("analyzer.unavailable")
        self.scope = self._label("analyzer.inspection.scope")
        for widget in (self.heading, self.summary, self.segment, self.detail, self.scope):
            layout.addWidget(widget)
        layout.addStretch(1)
        self.setAccessibleName(text("analyzer.inspection.title"))
        unsubscribe = model.subscribe(self._offer)
        self.destroyed.connect(lambda _object=None: unsubscribe())

    def _label(self, key: str) -> QLabel:
        label = QLabel(text(key), self)
        label.setProperty("ui2Role", "secondary")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard
                                      | Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _offer(self, state: AnalyzerViewState) -> None:
        self._state = state
        frame = getattr(state.bundle, "spectrum", None)
        key = (state.mode, getattr(frame, "source_id", None), getattr(frame, "epoch", None),
               state.starting, state.stopping, state.running, state.error, state.bundle is None)
        urgent = key != self._key
        self._key = key
        if not self.isVisible():
            return
        if urgent:
            self.refresh()
        elif not self._timer.isActive():
            self._timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        self._timer.stop()
        state = self._state
        snapshot = state.sweep_snapshot if state.mode is AnalyzerMode.SWEEP else None
        try:
            self._report = inspect_sweep(snapshot)
        except ValueError:
            self._report = None
            self.summary.setText(text("analyzer.inspection.invalid"))
            self.segment.clear()
            self.segment.setEnabled(False)
            self.detail.clear()
            return
        report = self._report
        if report is None:
            self.summary.setText(text("analyzer.local") if state.mode is AnalyzerMode.RTBW
                                 else text("analyzer.unavailable"))
            self.segment.clear()
            self.segment.setEnabled(False)
            self.detail.clear()
            return
        rows = report.segments
        summary = text("analyzer.inspection.summary", source=report.source, epoch=report.epoch,
                       sequence=report.sequence, revision=report.revision if report.revision is not None else "—",
                       received=sum(row.state == "received" for row in rows), total=len(rows),
                       pending=sum(row.state == "pending" for row in rows),
                       missing=sum(row.state == "missing" for row in rows), unit=report.unit)
        summary += "\n" + text("analyzer.completed" if report.complete else "analyzer.gapped"
                                if report.terminal else "analyzer.partial")
        position = report.last_admitted_segment
        summary += "\n" + (text("analyzer.position.detail", index=position.segment_index,
                                 lower=f"{position.usable_start_hz / 1e6:g}",
                                 upper=f"{position.usable_stop_hz / 1e6:g}") if position is not None
                            else text("analyzer.position.unknown"))
        if not (state.running or state.starting or state.stopping):
            summary += "\n" + text("analyzer.stopped_last")
        if self.summary.text() != summary:
            self.summary.setText(summary)
        selected = self.segment.currentData()
        desired = [(text("analyzer.inspection.row", index=row.index,
                         state=text("analyzer.inspection." + row.state)), row.index) for row in rows]
        existing = [(self.segment.itemText(i), self.segment.itemData(i)) for i in range(self.segment.count())]
        if desired != existing:
            with QSignalBlocker(self.segment):
                self.segment.clear()
                for label, index in desired:
                    self.segment.addItem(label, index)
                selected_index = self.segment.findData(selected)
                self.segment.setCurrentIndex(max(0, selected_index))
        self.segment.setEnabled(bool(rows))
        self._selection()

    def _selection(self) -> None:
        report = self._report
        if report is None:
            return
        row = next((row for row in report.segments if row.index == self.segment.currentData()), None)
        if row is None:
            return
        value = text("analyzer.inspection.generation", generation=row.generation if row.generation is not None else "—")
        value += "\n" + self._record(row.acquisition)
        if row.previous_generation is not None:
            value += "\n\n" + text("analyzer.inspection.previous", sequence=report.previous_sequence,
                                      generation=row.previous_generation)
            value += "\n" + self._record(row.previous_acquisition)
        if self.detail.text() != value:
            self.detail.setText(value)

    @staticmethod
    def _record(record) -> str:
        if record is None:
            return text("analyzer.inspection.no_record")
        return text("analyzer.inspection.record", timestamp=record.timestamp_ns,
                    sample=record.first_sample_index, frame=record.frame_sequence,
                    rate=f"{record.sample_rate_hz:g}", fft=record.fft_size,
                    flags=f"0x{record.quality_flags:08X}")

"""Instrument-only draft for the common Analyzer; no SDK work on Qt."""

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QGridLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest

from ..i18n import text
from ..view_models.analyzer_view_model import AnalyzerViewModel, AnalyzerViewState


class TinySaConfigurationBar(QWidget):
    draft_changed = Signal()

    def __init__(self, model: AnalyzerViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._revision: int | None = None
        self.setObjectName("v2-tinysa-settings")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QGridLayout()
        self.start = QDoubleSpinBox(self)
        self.stop = QDoubleSpinBox(self)
        for frequency in (self.start, self.stop):
            frequency.setRange(0.1, 5300)
            frequency.setDecimals(6)
            frequency.setKeyboardTracking(False)
        self.points = QSpinBox(self)
        self.points.setRange(2, 10001)
        self.points.setKeyboardTracking(False)
        self.deadline = QSpinBox(self)
        self.deadline.setRange(1, 120)
        self.deadline.setKeyboardTracking(False)
        self._fields: tuple[QDoubleSpinBox | QSpinBox, ...] = (self.start, self.stop, self.points, self.deadline)
        self._labels: list[tuple[QLabel, str]] = []
        for index, (field, key) in enumerate(zip(self._fields, (
                "tinysa.common.start", "tinysa.common.stop", "tinysa.common.points", "tinysa.common.deadline"))):
            label = QLabel(self)
            self._labels.append((label, key))
            row.addWidget(label, index // 2, (index % 2) * 2)
            row.addWidget(field, index // 2, (index % 2) * 2 + 1)
        for control in (self.start, self.stop):
            control.valueChanged.connect(self._changed)
        for numeric in (self.points, self.deadline):
            numeric.valueChanged.connect(self._changed)
        layout.addLayout(row)
        self.repeat = QCheckBox(self)
        self.repeat.toggled.connect(self._changed)
        layout.addWidget(self.repeat)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.set_locale()
        self.hide()

    def _changed(self, _value: object = None) -> None:
        self.draft_changed.emit()

    def set_locale(self) -> None:
        for frequency in (self.start, self.stop):
            frequency.setSuffix(text("hackrf.unit.mhz"))
        self.deadline.setSuffix(text("tinysa.common.seconds"))
        for field, (label, key) in zip(self._fields, self._labels):
            label.setText(text(key))
            field.setAccessibleName(text(key))
        self.summary.setText(text("tinysa.common.scope"))
        self.repeat.setText(text("tinysa.common.repeat"))
        self.repeat.setAccessibleName(text("tinysa.common.repeat"))
        self.repeat.setToolTip(text("tinysa.common.repeat.help"))

    def apply_view_state(self, state: AnalyzerViewState) -> None:
        available = state.tinysa_controls_available
        self.setVisible(available)
        selection = state.source_selection
        revision = selection.revision if available and selection is not None else None
        if revision != self._revision:
            self._revision = revision
            for field, value in zip(self._fields, (87.5, 108.0, 3100, 60)):
                with QSignalBlocker(field):
                    if isinstance(field, QSpinBox):
                        field.setValue(int(value))
                    else:
                        field.setValue(float(value))
            with QSignalBlocker(self.repeat):
                self.repeat.setChecked(False)
        for field in self._fields:
            field.setEnabled(available and not state.controls_locked)
        self.repeat.setEnabled(available and not state.controls_locked)

    def request(self) -> TinySaSweepRequest:
        state = self._model.state
        selection = state.source_selection
        if not state.tinysa_controls_available or selection is None or selection.selected is None:
            raise ValueError("Instrument selection is not ready")
        return TinySaSweepRequest(selection.selected, selection.revision,
            round(self.start.value() * 1e6), round(self.stop.value() * 1e6), self.points.value(), self.deadline.value(),
            repeat_until_stop=self.repeat.isChecked())

    @property
    def valid(self) -> bool:
        try:
            self.request()
        except (ValueError, TypeError):
            return False
        return True

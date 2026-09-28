"""Compact HackRF Sweep draft in the shared Analyzer, without an RX owner."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest

from ..i18n import text
from ..view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewModel, AnalyzerViewState


class HackrfSweepConfigurationBar(QWidget):
    """Visible intent only; a single explicit Start applies the bounded plan."""

    draft_changed = Signal()

    def __init__(self, model: AnalyzerViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self.setObjectName("v2-hackrf-sweep-settings")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        self.start_mhz = QSpinBox(self)
        self.start_mhz.setRange(1, 6000)
        self.start_mhz.setValue(100)
        self.stop_mhz = QSpinBox(self)
        self.stop_mhz.setRange(1, 6000)
        self.stop_mhz.setValue(220)
        self.fft = QComboBox(self)
        for size in (1024, 2048, 4096):
            self.fft.addItem(str(size), size)
        self.fft.setCurrentIndex(self.fft.findData(4096))
        self.lna = QSpinBox(self)
        self.lna.setRange(0, 40)
        self.lna.setSingleStep(8)
        self.lna.setValue(16)
        self.vga = QSpinBox(self)
        self.vga.setRange(0, 62)
        self.vga.setSingleStep(2)
        self.vga.setValue(20)
        self.preview = QSpinBox(self)
        self.preview.setRange(1, 100)
        self.preview.setValue(30)
        self._fields: tuple[QSpinBox | QComboBox, ...] = (
            self.start_mhz, self.stop_mhz, self.fft, self.lna, self.vga, self.preview)
        keys = ("analyzer.start_frequency", "analyzer.stop_frequency", "hackrf.fft",
                "hackrf.lna", "hackrf.vga", "hackrf.sweep.preview")
        self._labels: list[tuple[QLabel, str]] = []
        for index, (field, key) in enumerate(zip(self._fields, keys)):
            label = QLabel(self)
            label.setProperty("ui2Role", "secondary")
            label.setBuddy(field)
            field.setProperty("ui2Role", "utility-select" if isinstance(field, QComboBox) else "range-control")
            grid.addWidget(label, index // 3, 2 * (index % 3))
            grid.addWidget(field, index // 3, 2 * (index % 3) + 1)
            self._labels.append((label, key))
            if isinstance(field, QComboBox):
                field.currentIndexChanged.connect(lambda _value: self.draft_changed.emit())
            else:
                assert isinstance(field, QSpinBox)
                field.valueChanged.connect(lambda _value: self.draft_changed.emit())
        layout.addLayout(grid)
        self.scope = QLabel(self)
        self.scope.setProperty("ui2Role", "secondary")
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        self.set_locale()
        self.hide()

    def set_locale(self) -> None:
        self.setAccessibleName(text("hackrf.sweep.name"))
        for label, key in self._labels:
            label.setText(text(key))
            label.setAccessibleName(text(key))
            if label.buddy() is not None:
                label.buddy().setAccessibleName(text(key))
        self.scope.setText(text("hackrf.sweep.scope"))
        self.scope.setToolTip(text("hackrf.sweep.scope"))

    def apply_view_state(self, state: AnalyzerViewState) -> None:
        available = state.mode is AnalyzerMode.SWEEP and state.hackrf_sweep_controls_available
        self.setVisible(available)
        for field in self._fields:
            field.setEnabled(available and not state.controls_locked)

    def request(self) -> HackrfSweepRequest:
        state = self._model.state
        selection = state.source_selection
        if (not state.hackrf_sweep_controls_available or selection is None
                or selection.selected is None):
            raise ValueError("HackRF Sweep source is unavailable")
        return HackrfSweepRequest(
            source=selection.selected,
            selection_revision=selection.revision,
            start_hz=self.start_mhz.value() * 1_000_000,
            stop_hz=self.stop_mhz.value() * 1_000_000,
            fft_size=self.fft.currentData(),
            lna_gain=self.lna.value(),
            vga_gain=self.vga.value(),
            preview_rate_hz=self.preview.value(),
        )

    @property
    def valid(self) -> bool:
        try:
            self.request()
        except (TypeError, ValueError):
            return False
        return True


__all__ = ["HackrfSweepConfigurationBar"]

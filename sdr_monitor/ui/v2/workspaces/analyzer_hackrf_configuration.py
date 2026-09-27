"""Bounded HackRF draft, explicit staging; no SDK calls or Pluto gain mapping."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from sdr_monitor.domain.identity import as_source_id

from ..i18n import text
from ..view_models.analyzer_view_model import AnalyzerViewModel, AnalyzerViewState


class HackrfConfigurationBar(QWidget):
    draft_changed = Signal()

    def __init__(self, model: AnalyzerViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._key: tuple[object, ...] | None = None
        self._base: HackrfLiveRequest | None = None
        self.dirty = False
        self.setObjectName("v2-hackrf-settings")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QGridLayout()
        self._labels: list[tuple[QLabel, str]] = []
        self.center = QDoubleSpinBox(self)
        self.center.setRange(1, 6000)
        self.center.setDecimals(4)
        self.rate = QComboBox(self)
        for rate in (20, 16, 10, 8):
            self.rate.addItem(f"{rate} MS/s", float(rate * 1e6))
        self.bandwidth = QComboBox(self)
        for hz in (1_750_000, 2_500_000, 3_500_000, 5_000_000, 5_500_000, 6_000_000, 7_000_000,
                   8_000_000, 9_000_000, 10_000_000, 12_000_000, 14_000_000, 15_000_000, 20_000_000):
            self.bandwidth.addItem(f"{hz / 1e6:g} MHz", hz)
        self.fft = QComboBox(self)
        for fft in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536):
            self.fft.addItem(str(fft), fft)
        self.lna = QSpinBox(self)
        self.lna.setRange(0, 40)
        self.lna.setSingleStep(8)
        self.vga = QSpinBox(self)
        self.vga.setRange(0, 62)
        self.vga.setSingleStep(2)
        self._fields: tuple[QComboBox | QDoubleSpinBox | QSpinBox, ...] = (
            self.center, self.rate, self.bandwidth, self.fft, self.lna, self.vga)
        for index, (field, key) in enumerate(zip(self._fields, ("hackrf.center", "hackrf.rate", "hackrf.filter", "hackrf.fft", "hackrf.lna", "hackrf.vga"))):
            label = QLabel(self)
            self._labels.append((label, key))
            row.addWidget(label, index // 3, (index % 3) * 2)
            row.addWidget(field, index // 3, (index % 3) * 2 + 1)
            if isinstance(field, QComboBox):
                field.currentIndexChanged.connect(self._changed)
            elif isinstance(field, (QDoubleSpinBox, QSpinBox)):
                field.valueChanged.connect(self._changed)
        layout.addLayout(row)
        actions = QHBoxLayout()
        self.stage = QPushButton(self)
        self.stage.clicked.connect(self._stage)
        self.discard = QPushButton(self)
        self.discard.clicked.connect(self._reset)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        actions.addWidget(self.stage)
        actions.addWidget(self.discard)
        actions.addWidget(self.summary, 1)
        layout.addLayout(actions)
        self.set_locale()
        self.hide()

    def set_locale(self) -> None:
        self.center.setSuffix(text("hackrf.unit.mhz"))
        self.lna.setSuffix(text("hackrf.unit.db"))
        self.vga.setSuffix(text("hackrf.unit.db"))
        for index in range(self.bandwidth.count()):
            self.bandwidth.setItemText(index, f"{self.bandwidth.itemData(index) / 1e6:g}{text('hackrf.unit.mhz')}")
        for label, key in self._labels:
            label.setText(text(key))
        for field, (_, key) in zip(self._fields, self._labels):
            field.setAccessibleName(text(key))
        self.stage.setText(text("hackrf.stage"))
        self.discard.setText(text("hackrf.discard"))
        self.summary.setText(text("hackrf.scope"))

    def apply_view_state(self, state: AnalyzerViewState) -> None:
        available = state.hackrf_controls_available
        self.setVisible(available)
        snapshot = state.live.snapshot
        key = (state.source_selection.revision if state.source_selection else None,
               getattr(snapshot, "generation", None), available)
        if key != self._key:
            self._key = key
            self._base = getattr(snapshot, "hackrf_request", None) if available else None
            self._reset()
        for field in self._fields:
            field.setEnabled(available and not state.controls_locked)
        self.stage.setEnabled(available and not state.controls_locked and (self.dirty or self._base is None))
        self.discard.setEnabled(available and not state.controls_locked and self.dirty)

    def _reset(self) -> None:
        request = self._base
        # These are visible defaults only. No automatic stage or Start.
        values = (request.center_frequency_hz / 1e6 if request else 100.0,
                  request.sample_rate_hz if request else 20e6,
                  request.baseband_filter_hz if request else 15_000_000,
                  request.fft_size if request else 4096,
                  request.lna_gain_db if request else 16, request.vga_gain_db if request else 20)
        for field, value in zip(self._fields, values):
            with QSignalBlocker(field):
                if isinstance(field, QComboBox):
                    field.setCurrentIndex(field.findData(value))
                elif isinstance(field, QDoubleSpinBox):
                    field.setValue(value)
                else:
                    field.setValue(int(value))
        self.dirty = False
        self.summary.setText(text("hackrf.scope"))
        self.stage.setEnabled(self._base is None and not self._model.state.controls_locked)
        self.discard.setEnabled(False)
        self.draft_changed.emit()

    def _changed(self, _value: object = None) -> None:
        self.dirty = True
        self.apply_view_state(self._model.state)
        self.draft_changed.emit()

    def _stage(self) -> None:
        state = self._model.state
        selection = state.source_selection
        if selection is None or selection.selected is None or state.controls_locked:
            return
        try:
            fft = self.fft.currentData()
            request = HackrfLiveRequest(center_frequency_hz=self.center.value() * 1e6,
                sample_rate_hz=self.rate.currentData(), baseband_filter_hz=self.bandwidth.currentData(),
                fft_size=fft, hop_size=fft // 2, lna_gain_db=self.lna.value(), vga_gain_db=self.vga.value(),
                source_id=as_source_id(selection.selected.device_id))
            patch = HackrfConfigurationPatch(request, selection.revision, getattr(state.live.snapshot, "generation", 0))
            self._model.stage_hackrf_configuration(patch)
        except (ValueError, TypeError):
            self.summary.setText(text("hackrf.invalid"))


__all__ = ["HackrfConfigurationBar"]

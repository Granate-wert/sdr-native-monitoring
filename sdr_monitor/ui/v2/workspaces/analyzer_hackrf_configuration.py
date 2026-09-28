"""Bounded HackRF draft, explicit staging; no SDK calls or Pluto gain mapping."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from sdr_monitor.domain.identity import as_source_id

from ..i18n import text
from ..view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewModel, AnalyzerViewState


class HackrfConfigurationBar(QWidget):
    draft_changed = Signal()

    def __init__(self, model: AnalyzerViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._key: tuple[object, ...] | None = None
        self._base: HackrfLiveRequest | None = None
        self.dirty = False
        self.setObjectName("v2-hackrf-settings")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
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
        for fft in (256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144):
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
            label.setProperty("ui2Role", "secondary")
            self._labels.append((label, key))
            row.addWidget(label, index // 3, (index % 3) * 2)
            row.addWidget(field, index // 3, (index % 3) * 2 + 1)
            if isinstance(field, QComboBox):
                field.currentIndexChanged.connect(self._changed)
            elif isinstance(field, (QDoubleSpinBox, QSpinBox)):
                field.valueChanged.connect(self._changed)
        layout.addLayout(row)
        self.dsp_toggle = QPushButton(self)
        self.dsp_toggle.setCheckable(True)
        self.dsp_toggle.setProperty("ui2Role", "utility-action")
        layout.addWidget(self.dsp_toggle)
        self.dsp_panel = QWidget(self)
        dsp_layout = QVBoxLayout(self.dsp_panel)
        dsp_layout.setContentsMargins(0, 0, 0, 0)
        dsp_grid = QGridLayout()
        self.fft_window = QComboBox(self)
        for token in ("rectangular", "hann", "blackman_harris_4term", "flat_top", "nuttall", "kaiser"):
            self.fft_window.addItem(token, token)
        self.detector = QComboBox(self)
        for token in ("sample", "peak", "negative_peak", "rms", "average_power"):
            self.detector.addItem(token, token)
        self.hop = QSpinBox(self)
        self.hop.setRange(1, 262144)
        self.averaging = QSpinBox(self)
        self.averaging.setRange(1, 256)
        self.persistence_mode = QComboBox(self)
        for token in ("disabled", "exponential-decay", "rolling-exact"):
            self.persistence_mode.addItem(token, token)
        self.persistence_bins = QSpinBox(self)
        self.persistence_bins.setRange(16, 4096)
        dsp_fields: tuple[QComboBox | QSpinBox, ...] = (
            self.fft_window, self.detector, self.hop, self.averaging,
            self.persistence_mode, self.persistence_bins)
        for index, (field, key) in enumerate(zip(dsp_fields, ("hackrf.window", "hackrf.detector", "hackrf.hop", "hackrf.group",
                                                            "hackrf.persistence", "hackrf.persistence.bins"))):
            label = QLabel(self)
            label.setProperty("ui2Role", "secondary")
            self._labels.append((label, key))
            dsp_grid.addWidget(label, index // 2, (index % 2) * 2)
            dsp_grid.addWidget(field, index // 2, (index % 2) * 2 + 1)
            if isinstance(field, QComboBox):
                field.currentIndexChanged.connect(self._changed)
            elif isinstance(field, QSpinBox):
                field.valueChanged.connect(self._changed)
        self._fields += dsp_fields
        for field in self._fields:
            field.setProperty("ui2Role", "utility-select" if isinstance(field, QComboBox) else "range-control")
        dsp_layout.addLayout(dsp_grid)
        self.dsp_help = QLabel(self)
        self.dsp_help.setProperty("ui2Role", "secondary")
        self.dsp_help.setWordWrap(True)
        dsp_layout.addWidget(self.dsp_help)
        layout.addWidget(self.dsp_panel)
        self.dsp_panel.hide()
        self.dsp_toggle.toggled.connect(self.dsp_panel.setVisible)
        actions = QHBoxLayout()
        self.stage = QPushButton(self)
        self.stage.setProperty("ui2Role", "utility-action")
        self.stage.clicked.connect(self._stage)
        self.discard = QPushButton(self)
        self.discard.setProperty("ui2Role", "utility-action")
        self.discard.clicked.connect(self._reset)
        self.summary = QLabel(self)
        self.summary.setProperty("ui2Role", "secondary")
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
        self.dsp_toggle.setText(text("hackrf.dsp"))
        self.dsp_toggle.setAccessibleName(text("hackrf.dsp"))
        self.dsp_help.setText(text("hackrf.dsp.help"))
        for combo, prefix in ((self.fft_window, "hackrf.window."), (self.detector, "hackrf.detector."),
                              (self.persistence_mode, "hackrf.persistence.")):
            with QSignalBlocker(combo):
                for index in range(combo.count()):
                    combo.setItemText(index, text(prefix + combo.itemData(index)))
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
        selected = state.hackrf_controls_available
        available = selected and getattr(state, "mode", AnalyzerMode.RTBW) is AnalyzerMode.RTBW
        self.setVisible(available)
        snapshot = state.live.snapshot
        key = (state.source_selection.revision if state.source_selection else None,
               getattr(snapshot, "generation", None), selected)
        if key != self._key:
            self._key = key
            self._base = getattr(snapshot, "hackrf_request", None) if selected else None
            self._reset()
        for field in self._fields:
            field.setEnabled(available and not state.controls_locked)
        self.averaging.setEnabled(available and not state.controls_locked
            and bool(getattr(snapshot, "hackrf_detector_groups_available", False)))
        self.averaging.setToolTip(text("hackrf.group.help") if getattr(snapshot, "hackrf_detector_groups_available", False)
                                 else text("hackrf.group.unavailable"))
        persistence_available = bool(getattr(snapshot, "hackrf_persistence_available", False))
        self.persistence_mode.setEnabled(available and not state.controls_locked and persistence_available)
        self.persistence_bins.setEnabled(available and not state.controls_locked and persistence_available
                                        and self.persistence_mode.currentData() != "disabled")
        self.persistence_mode.setToolTip(text("hackrf.persistence.help") if persistence_available
                                        else text("hackrf.persistence.unavailable"))
        self.stage.setEnabled(available and not state.controls_locked and (self.dirty or self._base is None))
        self.discard.setEnabled(available and not state.controls_locked and self.dirty)

    def _reset(self) -> None:
        request = self._base
        # These are visible defaults only. No automatic stage or Start.
        values: tuple[int | float | str, ...] = (request.center_frequency_hz / 1e6 if request else 100.0,
                  request.sample_rate_hz if request else 20e6,
                  request.baseband_filter_hz if request else 15_000_000,
                  request.fft_size if request else 4096,
                  request.lna_gain_db if request else 16, request.vga_gain_db if request else 20,
                  request.window if request else "hann", request.detector if request else "sample",
                  request.hop_size if request else 2048, request.averaging_frames if request else 1,
                  request.persistence_mode if request else "disabled",
                  request.persistence_power_bins if request else 256)
        with QSignalBlocker(self.hop):
            self.hop.setMaximum(request.fft_size if request else 4096)
        # Preserve a valid non-preset request; at most ONE extra Fs item exists.
        # Never infer a measured rate or silently substitute a nearby preset.
        with QSignalBlocker(self.rate):
            while self.rate.count() > 4:
                self.rate.removeItem(4)
            if self.rate.findData(values[1]) < 0:
                self.rate.addItem(f"{float(values[1]) / 1e6:g} MS/s", values[1])
        for field, value in zip(self._fields, values):
            with QSignalBlocker(field):
                if isinstance(field, QComboBox):
                    field.setCurrentIndex(field.findData(value))
                elif isinstance(field, QDoubleSpinBox):
                    field.setValue(float(value))
                else:
                    field.setValue(int(value))
        self.dirty = False
        self.summary.setText(text("hackrf.scope"))
        self.stage.setEnabled(self._base is None and not self._model.state.controls_locked)
        self.discard.setEnabled(False)
        self.draft_changed.emit()

    def _changed(self, _value: object = None) -> None:
        fft = self.fft.currentData()
        if type(fft) is int:
            with QSignalBlocker(self.hop):
                self.hop.setMaximum(fft)  # Visible draft clamp; never applies to an active owner.
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
            base = self._base or HackrfLiveRequest(100e6, 20e6, 15_000_000, 16, 20)
            request = replace(base, center_frequency_hz=self.center.value() * 1e6,
                sample_rate_hz=self.rate.currentData(), baseband_filter_hz=self.bandwidth.currentData(),
                fft_size=fft, hop_size=self.hop.value(), window=self.fft_window.currentData(),
                detector=self.detector.currentData(), averaging_frames=self.averaging.value(),
                persistence_enabled=self.persistence_mode.currentData() != "disabled",
                persistence_mode=self.persistence_mode.currentData(),
                persistence_power_bins=self.persistence_bins.value(),
                lna_gain_db=self.lna.value(), vga_gain_db=self.vga.value(),
                source_id=as_source_id(selection.selected.device_id))
            patch = HackrfConfigurationPatch(request, selection.revision, getattr(state.live.snapshot, "generation", 0))
            self._model.stage_hackrf_configuration(patch)
        except (ValueError, TypeError) as error:
            self.summary.setText(text("hackrf.persistence.budget")
                if "256 MiB" in str(error) else text("hackrf.invalid"))


__all__ = ["HackrfConfigurationBar"]

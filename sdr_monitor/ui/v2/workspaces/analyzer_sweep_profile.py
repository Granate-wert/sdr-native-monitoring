"""Local next-Start choice, never an immediate acquisition configuration command."""
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QVBoxLayout, QWidget

from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.sweep_speed import SweepSpeedProfile
from ..i18n import text


class AnalyzerSweepProfileControl(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.label = QLabel(self)
        self.label.setProperty("ui2Role", "secondary")
        self.choice = QComboBox(self)
        self.choice.setProperty("ui2Role", "utility-select")
        for profile in SweepSpeedProfile:
            self.choice.addItem("", profile.value)
        self.label.setBuddy(self.choice)
        self.hint = QLabel(self)
        self.hint.setProperty("ui2Role", "secondary")
        self.hint.setWordWrap(True)
        defaults = ContinuousSweepPlanRequest(100e6, 1000e6)
        self.usable_window = QDoubleSpinBox(self)
        self.usable_window.setProperty("ui2Role", "range-control")
        self.usable_window.setRange(0.001, 100.0)
        self.usable_window.setDecimals(3)
        self.usable_window.setValue(defaults.usable_window_hz / 1e6)
        self.overlap = QDoubleSpinBox(self)
        self.overlap.setProperty("ui2Role", "range-control")
        self.overlap.setRange(0.0, 100.0)
        self.overlap.setDecimals(3)
        self.overlap.setValue(defaults.overlap_hz / 1e6)
        self.window_label = QLabel(self)
        self.window_label.setProperty("ui2Role", "secondary")
        self.window_label.setBuddy(self.usable_window)
        self.overlap_label = QLabel(self)
        self.overlap_label.setProperty("ui2Role", "secondary")
        self.overlap_label.setBuddy(self.overlap)
        geometry = QFormLayout()
        geometry.addRow(self.window_label, self.usable_window)
        geometry.addRow(self.overlap_label, self.overlap)
        self.geometry_hint = QLabel(self)
        self.geometry_hint.setProperty("ui2Role", "secondary")
        self.geometry_hint.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addWidget(self.choice)
        layout.addWidget(self.hint)
        layout.addLayout(geometry)
        layout.addWidget(self.geometry_hint)
        self.set_locale()

    @property
    def profile(self) -> SweepSpeedProfile:
        return SweepSpeedProfile(self.choice.currentData())

    def set_locale(self) -> None:
        self.label.setText(text("analyzer.speed.title"))
        self.choice.setAccessibleName(text("analyzer.speed.title"))
        self.choice.setAccessibleDescription(text("analyzer.speed.help"))
        self.choice.setToolTip(text("analyzer.speed.help"))
        with QSignalBlocker(self.choice):
            for index, profile in enumerate(SweepSpeedProfile):
                self.choice.setItemText(index, text("analyzer.speed." + profile.value))
        self.hint.setText(text("analyzer.speed.help"))
        self.window_label.setText(text("analyzer.sweep.window"))
        self.overlap_label.setText(text("analyzer.sweep.overlap"))
        for field, key in ((self.usable_window, "analyzer.sweep.window"),
                           (self.overlap, "analyzer.sweep.overlap")):
            field.setAccessibleName(text(key))
            field.setAccessibleDescription(text("analyzer.sweep.geometry_help"))
            field.setToolTip(text("analyzer.sweep.geometry_help"))
        self.geometry_hint.setText(text("analyzer.sweep.geometry_help"))

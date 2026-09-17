"""Local next-Start choice, never an immediate acquisition configuration command."""
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

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
        layout.addWidget(self.label)
        layout.addWidget(self.choice)
        layout.addWidget(self.hint)
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

"""Reusable standalone controls.  Styling comes exclusively from ThemeProvider."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget

from .design_tokens import StatusTone
from .formatters import format_frequency_hz, format_power, parse_frequency_hz
from .i18n import DEFAULT_TRANSLATOR, LocaleId
from .icons import IconId, IconRegistry


def _card(widget: QFrame, name: str) -> None:
    widget.setObjectName(name)
    widget.setProperty("card", True)


class FrequencyInput(QLineEdit):
    frequency_accepted = Signal(float)
    validation_failed = Signal(str)

    def __init__(self, *, locale: LocaleId = LocaleId.RU, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._locale = locale
        self.setAccessibleName("frequency_input")
        self.setPlaceholderText("915 MHz")
        self.editingFinished.connect(self._accept)

    def frequency_hz(self) -> float:
        return parse_frequency_hz(self.text(), self._locale)

    def set_frequency_hz(self, value_hz: float, *, decimals: int = 3) -> None:
        self.setText(format_frequency_hz(value_hz, decimals=decimals, locale=self._locale))

    def _accept(self) -> None:
        try:
            self.frequency_accepted.emit(self.frequency_hz())
        except ValueError:
            self.validation_failed.emit(DEFAULT_TRANSLATOR.text("unit.invalid_frequency"))


class StatusChip(QFrame):
    _ICONS = {StatusTone.NEUTRAL: IconId.INFO, StatusTone.INFO: IconId.INFO, StatusTone.SUCCESS: IconId.SUCCESS, StatusTone.WARNING: IconId.WARNING, StatusTone.ERROR: IconId.ERROR}

    def __init__(self, text: str = "", tone: StatusTone = StatusTone.NEUTRAL, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _card(self, "sdrStatusChip")
        row = QHBoxLayout(self)
        self._icon = QLabel()
        self._text = QLabel()
        row.addWidget(self._icon)
        row.addWidget(self._text)
        self.set_status(text, tone)

    def set_status(self, text: str, tone: StatusTone) -> None:
        self._icon.setPixmap(IconRegistry.icon(self._ICONS[tone], size=14).pixmap(14, 14))
        self._text.setText(text)
        self.setProperty("statusTone", tone.value)
        self.setAccessibleName(text)
        self.setAccessibleDescription(f"{tone.value}: {text}")


class MeasurementCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _card(self, "sdrMeasurementCard")
        layout = QVBoxLayout(self)
        self._name, self._value, self._meta, self._quality = QLabel(), QLabel(), QLabel(), QLabel()
        self._value.setProperty("numeric", True)
        self._meta.setProperty("role", "secondary")
        for label in (self._name, self._value, self._meta, self._quality):
            layout.addWidget(label)

    def set_values(self, name: str, value: str, unit: str, *, meta: str = "", quality: str = "") -> None:
        self._name.setText(name)
        self._value.setText(f"{value} {unit}")
        self._meta.setText(meta)
        self._quality.setText(quality)
        self.setAccessibleName(name)
        self.setAccessibleDescription(f"{value} {unit}; {meta}; {quality}".strip("; "))


class AppliedValueRow(QFrame):
    """Shows requested and actually applied values without concealing adjustment."""
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _card(self, "sdrAppliedValueRow")
        layout = QVBoxLayout(self)
        self._label, self._requested, self._applied = QLabel(label), QLabel(), QLabel()
        self._requested.setProperty("role", "secondary")
        self._applied.setProperty("numeric", True)
        for child in (self._label, self._requested, self._applied):
            layout.addWidget(child)

    def set_values(self, requested: str, applied: str) -> None:
        self._requested.setText(f"{DEFAULT_TRANSLATOR.text('field.requested')}: {requested}")
        self._applied.setText(f"{DEFAULT_TRANSLATOR.text('field.applied')}: {applied}")
        self.setAccessibleDescription(f"{self._label.text()}. {self._requested.text()}. {self._applied.text()}")


class SectionCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _card(self, "sdrSectionCard")
        layout = QVBoxLayout(self)
        self._title = QLabel(title)
        self._title.setProperty("role", "heading")
        layout.addWidget(self._title)
        self.content = QVBoxLayout()
        layout.addLayout(self.content)

    def add_widget(self, widget: QWidget) -> None:
        self.content.addWidget(widget)


class EmptyState(QFrame):
    def __init__(self, title: str, detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _card(self, "sdrEmptyState")
        layout = QVBoxLayout(self)
        heading, body = QLabel(title), QLabel(detail)
        body.setWordWrap(True)
        body.setProperty("role", "secondary")
        layout.addWidget(heading)
        layout.addWidget(body)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)


class ErrorState(EmptyState):
    def __init__(self, message: str, *, retry: callable | None = None, parent: QWidget | None = None) -> None:
        super().__init__(DEFAULT_TRANSLATOR.text("error.title"), message, parent)
        self.setObjectName("sdrErrorState")
        if retry is not None:
            button = QPushButton(DEFAULT_TRANSLATOR.text("action.retry"), self)
            button.clicked.connect(retry)
            self.layout().addWidget(button)


class TaskProgress(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _card(self, "sdrTaskProgress")
        layout = QVBoxLayout(self)
        self._label, self._bar = QLabel(), QProgressBar()
        self._bar.setRange(0, 100)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)

    def set_progress(self, percent: float, message: str) -> None:
        self._bar.setValue(max(0, min(100, round(percent))))
        self._label.setText(message)


class NumericReadout(QFrame):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _card(self, "sdrNumericReadout")
        layout = QHBoxLayout(self)
        self._label, self._value = QLabel(label), QLabel("—")
        self._value.setProperty("numeric", True)
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._label)
        layout.addStretch(1)
        layout.addWidget(self._value)

    def set_value(self, value: float | None, unit: str, *, decimals: int = 2) -> None:
        self._value.setText(format_power(value, unit, decimals=decimals))
        self.setAccessibleDescription(f"{self._label.text()}: {self._value.text()}")
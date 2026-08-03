"""Small reusable controls that consume design, units and icon contracts."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .design_tokens import StatusTone
from .icons import IconId, IconRegistry
from .i18n import DEFAULT_TRANSLATOR, LocaleId
from .units import format_frequency_hz, format_level, parse_frequency_hz


class FrequencyInput(QLineEdit):
    """Text input accepting SI suffixes while exposing only Hz to callers."""

    frequency_accepted = Signal(float)
    validation_failed = Signal(str)

    def __init__(self, *, locale: LocaleId = LocaleId.RU, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._locale = locale
        self.setAccessibleName("frequency_input")
        self.setPlaceholderText("915 MHz")
        self.editingFinished.connect(self._accept_text)

    def frequency_hz(self) -> float:
        return parse_frequency_hz(self.text(), self._locale)

    def set_frequency_hz(self, value_hz: float, *, decimals: int = 3) -> None:
        self.setText(format_frequency_hz(value_hz, decimals=decimals, locale=self._locale))

    def _accept_text(self) -> None:
        try:
            value_hz = self.frequency_hz()
        except ValueError:
            self.validation_failed.emit(DEFAULT_TRANSLATOR.text("unit.invalid_frequency"))
            return
        self.frequency_accepted.emit(value_hz)


class ReadOnlyValue(QFrame):
    """Accessible text value and unit display without unit conversion."""

    def __init__(self, label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self._label = QLabel(label)
        self._value = QLabel("—")
        self._value.setTextInteractionFlags(self._value.textInteractionFlags())
        layout.addWidget(self._label)
        layout.addWidget(self._value, 1)
        self.setAccessibleName(label)

    def set_value(self, value: float | None, unit: str, *, decimals: int = 2, locale: LocaleId = LocaleId.RU) -> None:
        self._value.setText(format_level(value, unit, decimals=decimals, locale=locale))
        self.setAccessibleDescription(f"{self._label.text()}: {self._value.text()}")

    @property
    def value_text(self) -> str:
        return self._value.text()


class StatusBadge(QFrame):
    """Status indicator that always provides an icon and text, never colour alone."""

    _ICONS = {
        StatusTone.NEUTRAL: IconId.INFO,
        StatusTone.INFO: IconId.INFO,
        StatusTone.SUCCESS: IconId.SUCCESS,
        StatusTone.WARNING: IconId.WARNING,
        StatusTone.ERROR: IconId.ERROR,
    }

    def __init__(self, text: str = "", tone: StatusTone = StatusTone.NEUTRAL, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self._icon = QLabel()
        self._text = QLabel()
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        self.set_status(text, tone)

    def set_status(self, text: str, tone: StatusTone) -> None:
        icon_id = self._ICONS[tone]
        self._icon.setPixmap(IconRegistry.icon(icon_id, size=16).pixmap(16, 16))
        self._text.setText(text)
        self.setProperty("statusTone", tone.value)
        self.setAccessibleName(text)
        self.setAccessibleDescription(f"{tone.value}: {text}")

    @property
    def text(self) -> str:
        return self._text.text()


class StatusChip(QFrame):
    """Compact status chip with icon and text (never color alone)."""

    def __init__(self, text: str = "", tone: StatusTone = StatusTone.NEUTRAL, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self._icon = QLabel()
        self._text = QLabel()
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        self.set_status(text, tone)
        self.setObjectName("p16StatusChip")

    def set_status(self, text: str, tone: StatusTone) -> None:
        icon_id = StatusBadge._ICONS[tone]
        self._icon.setPixmap(IconRegistry.icon(icon_id, size=14).pixmap(14, 14))
        self._text.setText(text)
        self.setProperty("statusTone", tone.value)
        self.setAccessibleName(text)
        self.setAccessibleDescription(f"{tone.value}: {text}")


class MeasurementCard(QFrame):
    """Single metric display (name, value with unit, quality, source, warnings)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._name = QLabel()
        self._value = QLabel()
        self._value.setStyleSheet("font-size: 14px; font-weight: 600")
        self._meta = QLabel()
        self._warn = QLabel()
        layout.addWidget(self._name)
        layout.addWidget(self._value)
        layout.addWidget(self._meta)
        layout.addWidget(self._warn)
        self.setObjectName("p16MeasurementCard")

    def set_values(self, name: str, value: str, unit: str, meta: str = "", warn: str = "") -> None:
        self._name.setText(name)
        self._value.setText(f"{value} {unit}")
        self._meta.setText(meta)
        self._warn.setText(warn)
        self.setAccessibleName(name)
        self.setAccessibleDescription(f"{value} {unit}")


class SectionCard(QFrame):
    """Titled box containing arbitrary child content."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._title = QLabel(title)
        self._title.setStyleSheet("font-weight: 600")
        layout.addWidget(self._title)
        self._content_layout = QVBoxLayout()
        layout.addLayout(self._content_layout)
        self.setObjectName("p16SectionCard")

    def add_widget(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)


class EmptyState(QFrame):
    """Placeholder shown when a list/sidebar is empty."""

    def __init__(self, message: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._text = QLabel(message)
        layout.addWidget(self._text)
        self.setObjectName("p16EmptyState")
        self.setAccessibleName("empty_state")


class ErrorState(QFrame):
    """User-visible error panel with retry hint."""

    def __init__(self, message: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._title = QLabel("Error")
        self._text = QLabel(message)
        layout.addWidget(self._title)
        layout.addWidget(self._text)
        self.setObjectName("p16ErrorState")
        self.setAccessibleName("error_state")


class TaskProgress(QFrame):
    """Bounded progress display with status label."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._label = QLabel()
        self._progress = QProgressBar()
        layout.addWidget(self._label)
        layout.addWidget(self._progress)
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self.setObjectName("p16TaskProgress")

    def set_progress(self, percent: float, message: str = "") -> None:
        self._progress.setValue(int(percent))
        if message:
            self._label.setText(message)


class NumericReadout(QFrame):
    """Right-aligned numeric value with a fixed-width unit."""

    def __init__(self, label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self._label = QLabel(label)
        self._value = QLabel("—")
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._label)
        layout.addStretch(1)
        layout.addWidget(self._value)
        self.setObjectName("p16NumericReadout")

    def set_value(self, value: float | None, unit: str, *, decimals: int = 2) -> None:
        if value is None:
            self._value.setText("—")
        else:
            self._value.setText(f"{value:.{decimals}f} {unit}")

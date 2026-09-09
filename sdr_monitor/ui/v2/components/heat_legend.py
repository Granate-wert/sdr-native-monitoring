"""Theme-aware custom-painted heat legend for later waterfall/persistence layers."""

from __future__ import annotations

from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from ..design.tokens import ThemeId, density_lookup_table, tokens_for_theme
from ..i18n import UiLocale, text


class HeatLegend(QWidget):
    """A paint-only legend; palette selection never changes analytical density."""

    def __init__(
        self,
        minimum_label: str = "-120 dB",
        maximum_label: str = "0 dB",
        *,
        theme: ThemeId = ThemeId.DARK,
        locale: UiLocale = UiLocale.RU,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._locale = locale
        self._minimum_label = minimum_label
        self._maximum_label = maximum_label
        self.setMinimumHeight(28)
        self.setAccessibleName(text("component.heat_legend.name", locale))
        self._update_description()

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.update()

    def set_labels(self, minimum_label: str, maximum_label: str) -> None:
        self._minimum_label = minimum_label
        self._maximum_label = maximum_label
        self._update_description()
        self.update()

    def set_locale(self, locale: UiLocale) -> None:
        self._locale = UiLocale(locale)
        self.setAccessibleName(text("component.heat_legend.name", self._locale))
        self._update_description()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tokens = tokens_for_theme(self._theme)
        margin = 4
        bar_width = max(24, self.width() - 116)
        bar_rect = self.rect().adjusted(margin, margin, -self.width() + bar_width, -margin)
        gradient = QLinearGradient(bar_rect.topLeft(), bar_rect.topRight())
        lookup = density_lookup_table()
        for index in range(0, 256, 16):
            gradient.setColorAt(index / 255.0, QColor(*lookup[index].tolist()))
        gradient.setColorAt(1.0, QColor(*lookup[-1].tolist()))
        painter.fillRect(bar_rect, gradient)
        painter.setPen(QColor(tokens.colors.border))
        painter.drawRect(bar_rect)
        painter.setPen(QColor(tokens.colors.primary_text))
        painter.drawText(bar_rect.right() + 8, self.height() // 2 - 1, self._minimum_label)
        painter.drawText(bar_rect.right() + 8, self.height() - 4, self._maximum_label)
        painter.end()

    def _update_description(self) -> None:
        self.setAccessibleDescription(
            text(
                "component.heat_legend.description",
                self._locale, minimum=self._minimum_label,
                maximum=self._maximum_label,
            )
        )

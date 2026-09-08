"""V2-owned appearance/layout popover with no presenter or settings ownership."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QWidget

from ..components import ContextPopover
from ..design import ThemeId, stylesheet_for_theme
from ..i18n import UiLocale, text

_THEME_KEYS: dict[ThemeId, str] = {
    ThemeId.DARK: "theme.dark",
    ThemeId.LIGHT: "theme.light",
    ThemeId.HIGH_CONTRAST: "theme.high_contrast",
}
_LOCALE_KEYS: dict[UiLocale, str] = {
    UiLocale.RU: "appearance.locale.ru",
    UiLocale.EN: "appearance.locale.en",
}


class AppearancePopover(ContextPopover):
    """Emit explicit V2 presentation intents; shell owns all settings writes."""

    theme_requested = Signal(object)
    locale_requested = Signal(object)
    layout_reset_requested = Signal()
    visuals_reset_requested = Signal()
    shell_reset_requested = Signal()

    def __init__(self, *, parent: QWidget, locale: UiLocale = UiLocale.RU) -> None:
        self._locale = locale
        super().__init__(text("appearance.title", locale), parent=parent)
        self.setAccessibleDescription(text("appearance.description", locale))
        self._theme_buttons: dict[ThemeId, QPushButton] = {}
        self._locale_buttons: dict[UiLocale, QPushButton] = {}
        for theme, key in _THEME_KEYS.items():
            label = text(key, locale)
            button = QPushButton(label, self)
            button.setProperty("ui2Role", "utility-action")
            button.setAccessibleName(text("appearance.theme.name", locale, theme=label))
            button.setAccessibleDescription(text("appearance.theme.description", locale))
            button.clicked.connect(lambda _checked=False, selected=theme: self.theme_requested.emit(selected))
            self.add_content(button)
            self._theme_buttons[theme] = button
        for candidate, key in _LOCALE_KEYS.items():
            label = text(key, locale)
            button = QPushButton(f"✓ {label}" if candidate is locale else label, self)
            button.setProperty("ui2Role", "utility-action")
            button.setAccessibleName(text("appearance.locale.name", locale, locale_name=label))
            button.setAccessibleDescription(text("appearance.locale.description", locale))
            button.clicked.connect(lambda _checked=False, selected=candidate: self.locale_requested.emit(selected))
            self.add_content(button)
            self._locale_buttons[candidate] = button
        self._add_reset_button(
            text("appearance.reset_layout.text", locale),
            text("appearance.reset_layout.name", locale),
            text("appearance.reset_layout.description", locale),
            self.layout_reset_requested.emit,
        )
        self._add_reset_button(
            text("appearance.reset_visuals.text", locale),
            text("appearance.reset_visuals.name", locale),
            text("appearance.reset_visuals.description", locale),
            self.visuals_reset_requested.emit,
        )
        self._add_reset_button(
            text("appearance.reset_shell.text", locale),
            text("appearance.reset_shell.name", locale),
            text("appearance.reset_shell.description", locale),
            self.shell_reset_requested.emit,
        )

    @property
    def theme_buttons(self) -> dict[ThemeId, QPushButton]:
        """Expose controls for offscreen regression without mutable settings state."""

        return self._theme_buttons

    @property
    def locale_buttons(self) -> dict[UiLocale, QPushButton]:
        """Expose locale controls for offscreen regression only."""

        return self._locale_buttons

    def set_theme(self, theme: ThemeId) -> None:
        """Reflect the shell's resolved theme without persisting or changing it."""

        self.setStyleSheet(stylesheet_for_theme(theme))
        for candidate, button in self._theme_buttons.items():
            label = text(_THEME_KEYS[candidate], self._locale)
            button.setText(f"✓ {label}" if candidate == theme else label)

    def set_locale(self, locale: UiLocale) -> None:
        """Refresh locale-choice labels only; the shell rebuilds all other widgets."""

        self._locale = locale
        for candidate, button in self._locale_buttons.items():
            label = text(_LOCALE_KEYS[candidate], locale)
            button.setText(f"✓ {label}" if candidate is locale else label)
            button.setAccessibleName(text("appearance.locale.name", locale, locale_name=label))
            button.setAccessibleDescription(text("appearance.locale.description", locale))

    def _add_reset_button(self, text: str, name: str, description: str, callback: Callable[[], None]) -> None:
        button = QPushButton(text, self)
        button.setProperty("ui2Role", "utility-action")
        button.setAccessibleName(name)
        button.setAccessibleDescription(description)
        button.clicked.connect(callback)
        self.add_content(button)


def theme_label(theme: ThemeId, locale: UiLocale = UiLocale.RU) -> str:
    """Return the localized V2 label for an already validated theme identifier."""

    return text(_THEME_KEYS[theme], locale)

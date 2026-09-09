"""Axis primitives owned exclusively by the V2 measurement scene."""

from __future__ import annotations

import pyqtgraph as pg

from ..i18n import UiLocale
from .contracts import format_frequency_hz


class FrequencyAxis(pg.AxisItem):
    """Frequency axis that never guesses a display unit from signal amplitude."""

    def __init__(self, *args, locale: UiLocale = UiLocale.RU, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._locale = locale

    def set_locale(self, locale: UiLocale) -> None:
        self._locale = UiLocale(locale)
        self.picture = None
        self.update()

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:  # noqa: N802 - Qt API.
        del scale, spacing
        return [format_frequency_hz(value, locale=self._locale) for value in values]

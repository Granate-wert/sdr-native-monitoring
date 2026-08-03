"""Locale-aware engineering formatters used by standalone UI controls."""

from __future__ import annotations

import re

from .i18n import LocaleId

_FREQUENCY_FACTORS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


def parse_frequency_hz(value: str, locale: LocaleId = LocaleId.RU) -> float:
    text = value.strip().casefold().replace(" ", "")
    if locale is LocaleId.RU:
        text = text.replace(",", ".")
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(hz|khz|mhz|ghz)?", text)
    if match is None:
        raise ValueError("invalid frequency")
    number, suffix = match.groups()
    parsed = float(number) * _FREQUENCY_FACTORS.get(suffix or "hz", 1.0)
    if parsed < 0:
        raise ValueError("frequency must be non-negative")
    return parsed


def format_frequency_hz(value_hz: float, *, decimals: int = 3, locale: LocaleId = LocaleId.RU) -> str:
    absolute = abs(value_hz)
    unit, factor = ("GHz", 1e9) if absolute >= 1e9 else ("MHz", 1e6) if absolute >= 1e6 else ("kHz", 1e3) if absolute >= 1e3 else ("Hz", 1.0)
    number = f"{value_hz / factor:.{decimals}f}"
    if locale is LocaleId.RU:
        number = number.replace(".", ",")
    return f"{number} {unit}"


def format_power(value: float | None, unit: str, *, decimals: int = 2) -> str:
    return "—" if value is None else f"{value:.{decimals}f} {unit}"

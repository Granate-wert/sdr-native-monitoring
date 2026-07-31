"""SI-preserving presentation formatting and parsing for UI controls."""

from __future__ import annotations

import math
import re

from .i18n import LocaleId


_FREQUENCY_MULTIPLIERS = {
    "": 1.0,
    "hz": 1.0,
    "k": 1_000.0,
    "khz": 1_000.0,
    "m": 1_000_000.0,
    "mhz": 1_000_000.0,
    "g": 1_000_000_000.0,
    "ghz": 1_000_000_000.0,
}
_FREQUENCY_RE = re.compile(
    r"^\s*(?P<value>[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?)\s*(?P<unit>[A-Za-z]+)?\s*$"
)


def parse_localized_number(text: str, locale: LocaleId = LocaleId.RU) -> float:
    normalized = text.strip().replace("\u00a0", "").replace(" ", "")
    if locale is LocaleId.RU:
        normalized = normalized.replace(",", ".")
    elif "," in normalized and "." not in normalized:
        normalized = normalized.replace(",", ".")
    value = float(normalized)
    if not math.isfinite(value):
        raise ValueError("value must be finite")
    return value


def parse_frequency_hz(text: str, locale: LocaleId = LocaleId.RU) -> float:
    match = _FREQUENCY_RE.match(text)
    if match is None:
        raise ValueError(f"invalid frequency: {text!r}")
    unit = (match.group("unit") or "").casefold()
    if unit not in _FREQUENCY_MULTIPLIERS:
        raise ValueError(f"unsupported frequency unit: {unit!r}")
    value = parse_localized_number(match.group("value"), locale)
    result = value * _FREQUENCY_MULTIPLIERS[unit]
    if result < 0:
        raise ValueError("frequency must be non-negative")
    return result


def _decimal_separator(locale: LocaleId) -> str:
    return "," if locale is LocaleId.RU else "."


def format_frequency_hz(
    value_hz: float,
    *,
    decimals: int = 3,
    locale: LocaleId = LocaleId.RU,
) -> str:
    if not math.isfinite(value_hz):
        return "—"
    absolute = abs(value_hz)
    if absolute >= 1_000_000_000.0:
        divisor, suffix = 1_000_000_000.0, "GHz"
    elif absolute >= 1_000_000.0:
        divisor, suffix = 1_000_000.0, "MHz"
    elif absolute >= 1_000.0:
        divisor, suffix = 1_000.0, "kHz"
    else:
        divisor, suffix = 1.0, "Hz"
    rendered = f"{value_hz / divisor:.{decimals}f}"
    if locale is LocaleId.RU:
        rendered = rendered.replace(".", _decimal_separator(locale))
    return f"{rendered} {suffix}"


def format_level(value: float | None, unit: str, *, decimals: int = 2, locale: LocaleId = LocaleId.RU) -> str:
    """Format a level with its supplied unit; never convert calibration semantics."""

    if value is None or not math.isfinite(value):
        return "—"
    rendered = f"{value:.{decimals}f}"
    if locale is LocaleId.RU:
        rendered = rendered.replace(".", _decimal_separator(locale))
    return f"{rendered} {unit}".rstrip()

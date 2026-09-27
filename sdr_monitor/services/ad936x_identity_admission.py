"""Small, Qt-free identity boundary for receiver-owned AD936x contexts."""

from __future__ import annotations


def normalized_pluto_serial(value: str | None) -> str | None:
    """Normalize observed ASCII serials, never infer one from a route/model.

    Keep this normalization identical to the native receiver admission guard.
    Empty, placeholder, non-ASCII or control-containing values are unknown.
    """

    if not isinstance(value, str):
        return None
    serial = value.strip(" \t\n\r\v\f").lower()
    if not serial or serial in {"-", "unknown", "n/a", "none"}:
        return None
    if any(not 33 <= ord(character) <= 126 for character in serial):
        return None
    return serial

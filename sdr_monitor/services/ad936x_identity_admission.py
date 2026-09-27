"""Small, Qt-free identity boundary for receiver-owned AD936x contexts."""

from __future__ import annotations

from typing import Any

_IDENTITY_PROTOCOL_VERSION = 1


def normalized_pluto_serial(value: str | None) -> str | None:
    """Normalize observed ASCII serials, never infer one from a route/model.

    Keep this normalization identical to the native receiver admission guard.
    Empty, placeholder, non-ASCII or control-containing values are unknown.
    """

    if not isinstance(value, str):
        return None
    serial = value.strip(" \t\n\r\v\f")
    if any(not 33 <= ord(character) <= 126 for character in serial):
        return None
    serial = serial.lower()
    if not serial or serial in {"-", "unknown", "n/a", "none"}:
        return None
    return serial


def create_identity_bound_owner(
    native_module: Any, factory_name: str, uri: str, timeout_ms: int,
    *, expected_serial: str | None = None,
) -> Any:
    """Create one native owner, with same-context admission when identity is known.

    Unknown identity retains the legacy single-route call, not a stable-device
    claim. A supplied invalid serial or old protocol must never downgrade to it.
    Constructors must clean a rejected context before throwing; no second probe
    or Python-side Configure is used to verify identity.
    """

    if expected_serial is not None:
        serial = normalized_pluto_serial(expected_serial)
        if serial is None:
            raise ValueError("expected Pluto identity must be a known serial")
        protocol = getattr(native_module, "PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION", None)
        if type(protocol) is not int or protocol != _IDENTITY_PROTOCOL_VERSION:
            raise RuntimeError("native runtime lacks receiver-owned Pluto identity admission")
    else:
        serial = None
    factory = getattr(native_module, factory_name, None)
    if factory is None:
        raise RuntimeError("requested native Pluto owner is unavailable")
    if not callable(factory):
        raise TypeError("requested native Pluto owner factory is invalid")
    if serial is None:
        return factory(uri, timeout_ms)
    return factory(uri, timeout_ms, expected_serial=serial)

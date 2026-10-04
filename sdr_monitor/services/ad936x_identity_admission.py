"""Small, Qt-free identity boundary for receiver-owned AD936x contexts."""

from __future__ import annotations

from typing import Any

from ..domain.pluto_connection import PlutoUsbConnectionExpectation, normalized_pluto_serial

_IDENTITY_PROTOCOL_VERSION = 1
_USB_CONNECTION_PROTOCOL_VERSION = 1


def create_identity_bound_owner(
    native_module: Any, factory_name: str, uri: str, timeout_ms: int,
    *, expected_serial: str | None = None,
    expected_usb_connection: PlutoUsbConnectionExpectation | None = None,
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
    native_usb = None
    if expected_usb_connection is not None:
        if not isinstance(expected_usb_connection, PlutoUsbConnectionExpectation):
            raise TypeError("expected Pluto USB connection must be typed")
        if not isinstance(uri, str) or not uri.startswith("usb:"):
            raise ValueError("expected Pluto USB connection requires a USB route")
        if serial is not None and serial != normalized_pluto_serial(expected_usb_connection.usb_serial):
            raise ValueError("expected Pluto USB and hardware identities conflict")
        protocol = getattr(native_module, "PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION", None)
        bridge = getattr(native_module, "PlutoExpectedUsbConnection", None)
        if type(protocol) is not int or protocol != _USB_CONNECTION_PROTOCOL_VERSION or not callable(bridge):
            raise RuntimeError("native runtime lacks receiver-owned Pluto USB connection admission")
        native_usb = bridge()
        for name in ("bus", "device_address", "interface_number", "vendor_id", "product_id", "usb_serial"):
            setattr(native_usb, name, getattr(expected_usb_connection, name))
    factory = getattr(native_module, factory_name, None)
    if factory is None:
        raise RuntimeError("requested native Pluto owner is unavailable")
    if not callable(factory):
        raise TypeError("requested native Pluto owner factory is invalid")
    if native_usb is not None:
        if serial is None:
            return factory(uri, timeout_ms, expected_usb_connection=native_usb)
        return factory(uri, timeout_ms, expected_serial=serial, expected_usb_connection=native_usb)
    if serial is None:
        return factory(uri, timeout_ms)
    return factory(uri, timeout_ms, expected_serial=serial)

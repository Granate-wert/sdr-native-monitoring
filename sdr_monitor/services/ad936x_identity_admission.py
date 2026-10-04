"""Small, Qt-free identity boundary for receiver-owned AD936x contexts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

_IDENTITY_PROTOCOL_VERSION = 1
_USB_CONNECTION_PROTOCOL_VERSION = 1


@dataclass(frozen=True, slots=True)
class PlutoUsbConnectionExpectation:
    """Bounded context observation; never a stable calibration identity.

    A later owner must recheck it against its SAME fresh context before RF.
    This does not prove liveness or distinguish same-port identical hot-swap.
    """

    bus: int
    device_address: int
    interface_number: int
    vendor_id: int
    product_id: int
    usb_serial: str

    def __post_init__(self) -> None:
        for value, minimum, maximum in (
            (self.bus, 0, 255), (self.device_address, 1, 127),
            (self.interface_number, 0, 255),
            (self.vendor_id, 1, 65535), (self.product_id, 1, 65535),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError("expected Pluto USB connection is invalid")
        if (not isinstance(self.usb_serial, str) or len(self.usb_serial) > 256
                or (self.usb_serial != "" and normalized_pluto_serial(self.usb_serial) is None)):
            raise ValueError("expected Pluto USB serial must be known or explicitly empty")

    @classmethod
    def from_probe(cls, probe: Any) -> PlutoUsbConnectionExpectation:
        """Use raw observed attributes, never caller URI/description fallback."""
        route = getattr(probe, "backend_uri", None)
        if getattr(probe, "context_name", None) != "usb" or not isinstance(route, str) or len(route) > 32:
            raise ValueError("observed Pluto USB connection is incomplete")
        match = re.fullmatch(r"usb:(0|[1-9][0-9]{0,2})\.([1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})", route)
        if match is None:
            raise ValueError("observed Pluto USB route is invalid")
        descriptors: list[int] = []
        for name in ("usb_vendor_id", "usb_product_id"):
            text = getattr(probe, name, None)
            if not isinstance(text, str) or re.fullmatch(r"[0-9a-fA-F]{4}", text) is None:
                raise ValueError("observed Pluto USB descriptor is incomplete")
            descriptors.append(int(text, 16))
        usb_serial = getattr(probe, "usb_serial", None)
        hardware_serial = getattr(probe, "serial", None)
        if (not isinstance(usb_serial, str) or not isinstance(hardware_serial, str) or len(hardware_serial) > 256
                or (hardware_serial != "" and normalized_pluto_serial(hardware_serial) is None)
                or normalized_pluto_serial(usb_serial) != normalized_pluto_serial(hardware_serial)):
            raise ValueError("observed Pluto USB and hardware identities do not agree")
        bus, address, interface = (int(value) for value in match.groups())
        return cls(bus, address, interface, descriptors[0], descriptors[1], usb_serial)


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

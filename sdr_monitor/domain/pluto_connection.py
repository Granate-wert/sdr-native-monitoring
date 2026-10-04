"""Typed bounded Pluto USB observations; no hardware, Qt or calibration identity."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

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

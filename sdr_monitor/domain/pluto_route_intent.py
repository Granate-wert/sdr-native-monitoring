"""Explicit operational route intent; neither discovery nor device identity."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class PlutoOperationalRouteIntent:
    """Pin one user-selected USB/IP URI, with no implicit transport failover.

    The URI is not evidence of a serial, chip, link speed, or independent SDR.
    A fresh owned observation must still match the selected logical source.
    """

    uri: str

    def __post_init__(self) -> None:
        if (not isinstance(self.uri, str) or not 4 <= len(self.uri) <= 320
                or any(not 33 <= ord(char) <= 126 for char in self.uri)):
            raise ValueError("explicit Pluto route must be bounded ASCII without whitespace")
        if self.uri.startswith("usb:"):
            match = re.fullmatch(r"usb:(0|[1-9][0-9]{0,2})\.([1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})", self.uri)
            if match is None:
                raise ValueError("explicit Pluto USB route requires an exact bus/address/interface")
            bus, address, interface = (int(item) for item in match.groups())
            if bus > 255 or address > 127 or interface > 255:
                raise ValueError("explicit Pluto USB route exceeds context bounds")
        elif not self.uri.startswith("ip:") or not self.uri[3:]:
            raise ValueError("explicit Pluto route must use USB or IP")

"""Copied bounds of one observed route, NEVER a stable/calibration identity."""

from __future__ import annotations

from dataclasses import dataclass

from .device_capabilities import CapabilityRange


@dataclass(frozen=True, slots=True)
class Ad936xRouteCapabilities:
    """Control-plane facts for a coherent owner that reported an EMPTY serial.

    The descriptor retains this exact object. It is not catalog evidence, a
    reusable Start permit, USB/IP equivalence or a calibration/profile key.
    A new route selection must produce new owned facts; equal values are not
    sufficient to reuse an older RF proposal.
    """

    uri: str
    firmware: str
    tuning_ranges_hz: tuple[CapabilityRange, ...]
    sample_rate_ranges_hz: tuple[CapabilityRange, ...]
    analog_bandwidth_ranges_hz: tuple[CapabilityRange, ...]
    gain_ranges_db: tuple[CapabilityRange, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.uri, str) or self.uri != self.uri.strip()
                or not self.uri.startswith(("usb:", "ip:")) or not self.uri.split(":", 1)[1]
                or len(self.uri) > 1024):
            raise ValueError("route facts require an explicit observed IIO route")
        if (not isinstance(self.firmware, str) or not self.firmware.strip()
                or self.firmware.strip().casefold() in {"unknown", "none", "n/a", "-"}
                or len(self.firmware) > 256):
            raise ValueError("route facts require observed firmware")
        for name, unit in (("tuning_ranges_hz", "Hz"), ("sample_rate_ranges_hz", "Hz"),
                           ("analog_bandwidth_ranges_hz", "Hz"), ("gain_ranges_db", "dB")):
            ranges = getattr(self, name)
            if type(ranges) is not tuple or not 1 <= len(ranges) <= 16:
                raise ValueError("route facts require finite observed ranges")
            for item in ranges:
                if not isinstance(item, CapabilityRange) or item.unit != unit:
                    raise ValueError("route range unit/type is invalid")
                item.__post_init__()
                if unit == "Hz" and item.minimum <= 0:
                    raise ValueError("route frequency bounds must be positive")


__all__ = ["Ad936xRouteCapabilities"]

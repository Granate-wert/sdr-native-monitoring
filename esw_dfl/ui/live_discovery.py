"""Bounded device discovery for the Live Monitor workspace.

The module wraps the native Pluto scan in a thin, GUI-friendly facade:

* :func:`discover_devices` never blocks the GUI thread for longer than the
  native scan itself and never leaks device internals — it returns small
  immutable :class:`DiscoveredDevice` records;
* :func:`parse_manual_uri` normalizes user-entered Pluto URIs
  (``usb:``, ``ip:``, ``local:``) without touching the native layer;
* a scan failure surfaces as :class:`DiscoveryError` with a human-readable
  message, it is never silently swallowed.

Discovery results contain no credentials and no measurement data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Callable

from ..sdr.pluto import PlutoContextSummary, discover_pluto


class DeviceKind(StrEnum):
    USB = "usb"
    IP = "ip"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    """One discovered Pluto context, safe for GUI consumption."""

    uri: str
    description: str
    kind: DeviceKind

    def __post_init__(self) -> None:
        if not self.uri.strip():
            raise ValueError("uri must not be empty")


class DiscoveryError(RuntimeError):
    """Raised when the native scan cannot produce a result."""


_MANUAL_URI_PATTERN = re.compile(r"^(usb|ip|local):\S+$")


def parse_manual_uri(text: str) -> str | None:
    """Normalize a manually entered Pluto URI.

    Accepts the canonical libiio forms ``usb:...``, ``ip:...`` and
    ``local:...``.  Returns the trimmed URI when it is valid, ``None``
    otherwise.  Whitespace-only and empty input yield ``None``.
    """

    candidate = text.strip()
    if not candidate:
        return None
    if not _MANUAL_URI_PATTERN.match(candidate):
        return None
    return candidate


def _classify(uri: str) -> DeviceKind:
    if uri.startswith("ip:"):
        return DeviceKind.IP
    if uri.startswith("local:"):
        return DeviceKind.MANUAL
    return DeviceKind.USB


def discover_devices(
    *,
    filter: str = "usb,ip",
    scanner: Callable[[str], tuple[PlutoContextSummary, ...]] | None = None,
) -> tuple[DiscoveredDevice, ...]:
    """Scan for Pluto contexts and return GUI-safe device records.

    The optional ``scanner`` is injected in tests; the default is the
    native :func:`discover_pluto` scan.  A missing native backend raises
    :class:`DiscoveryError` with the underlying reason.
    """

    scan = scanner if scanner is not None else discover_pluto
    try:
        summaries = tuple(scan(filter))
    except Exception as error:
        raise DiscoveryError(f"device scan failed: {type(error).__name__}: {error}") from error
    return tuple(
        DiscoveredDevice(
            uri=summary.uri,
            description=summary.description,
            kind=_classify(summary.uri),
        )
        for summary in summaries
    )


__all__ = [
    "DeviceKind",
    "DiscoveredDevice",
    "DiscoveryError",
    "discover_devices",
    "parse_manual_uri",
]

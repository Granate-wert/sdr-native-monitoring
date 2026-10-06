"""Bounded observed USB alias of a known IP source, not its RX transport."""
from __future__ import annotations

from dataclasses import dataclass

from .device_capabilities import (CapabilityTransport, DeviceCalibrationIdentity,
                                 DeviceCapabilitySnapshot, DeviceFamily, stable_identity_key)
from .pluto_connection import PlutoUsbConnectionExpectation, normalized_pluto_serial
from .pluto_route_intent import PlutoOperationalRouteIntent
from .receiver_topology import ReceiverTopologySnapshot


@dataclass(frozen=True, slots=True)
class PlutoUsbAliasWitness:
    """Copied same-observer facts. Construction alone is not owner admission.

    The native control retains this exact object and selected route lifetime;
    before RF it re-observes the USB alias using its existing temporary observer.
    An IP owner never receives this connection as its acquisition expectation.
    Identical serial/firmware clones and same-port swaps remain unprovable.
    """

    source_id: str
    route: PlutoOperationalRouteIntent
    connection: PlutoUsbConnectionExpectation
    calibration_identity: DeviceCalibrationIdentity
    usb_capabilities: DeviceCapabilitySnapshot
    usb_topology: ReceiverTopologySnapshot

    def __post_init__(self) -> None:
        if (not isinstance(self.source_id, str) or not self.source_id
                or self.source_id != self.source_id.strip()
                or not isinstance(self.route, PlutoOperationalRouteIntent)
                or not self.route.uri.startswith("ip:")
                or not isinstance(self.connection, PlutoUsbConnectionExpectation)
                or not isinstance(self.calibration_identity, DeviceCalibrationIdentity)
                or self.calibration_identity.family is not DeviceFamily.AD936X
                or not isinstance(self.usb_capabilities, DeviceCapabilitySnapshot)
                or self.usb_capabilities.family is not DeviceFamily.AD936X
                or self.usb_capabilities.transports != (CapabilityTransport.USB,)
                or self.usb_capabilities.identity_key != self.calibration_identity.device_identity_key
                or self.usb_capabilities.adapter_id != self.calibration_identity.adapter_id
                or not isinstance(self.usb_topology, ReceiverTopologySnapshot)
                or self.usb_topology.verified_rf_paths):
            raise ValueError("USB alias requires a known typed AD936x IP source")
        self.connection.__post_init__()
        serial = normalized_pluto_serial(self.connection.usb_serial)
        if (serial is None or self.calibration_identity.device_identity_key !=
                stable_identity_key(f"ad936x-serial|{serial}")):
            raise ValueError("USB alias serial must match the observed canonical IP identity")

    @property
    def usb_uri(self) -> str:
        value = self.connection
        return f"usb:{value.bus}.{value.device_address}.{value.interface_number}"

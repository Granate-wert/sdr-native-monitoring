"""Read-only R10-E0 topology inventory service.

This module deliberately does not import libiio, native bindings or Qt.  A
future adapter may populate ``DeviceCapabilities.receiver_topology`` from a
read-only native snapshot; E0 simply preserves the facts and never turns scan
elements into a physical RX2 or throughput claim.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.live import DeviceDescriptor
from ..domain.receiver_topology import ReceiverTopologyInventory


def inventory_receiver_topology(devices: Iterable[DeviceDescriptor]) -> tuple[ReceiverTopologyInventory, ...]:
    """Create a stable, bounded inventory without device I/O or mutable state."""

    result: list[ReceiverTopologyInventory] = []
    device_ids: set[str] = set()
    resources: set[str] = set()
    for device in devices:
        if device.device_id in device_ids:
            raise ValueError("receiver topology inventory requires unique device ids")
        device_ids.add(device.device_id)
        topology = device.capabilities.receiver_topology
        if topology is not None:
            if topology.physical_stream_resource_id in resources:
                raise ValueError("receiver topology inventory requires unique physical stream resources")
            resources.add(topology.physical_stream_resource_id)
        result.append(ReceiverTopologyInventory(device.device_id, device.device_id, topology))
    return tuple(result)


__all__ = ["inventory_receiver_topology"]

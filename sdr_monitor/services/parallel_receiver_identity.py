"""Pure parallel-source guard; bounded USB observations are not stable keys."""
from __future__ import annotations

from collections.abc import Mapping, Set

from ..domain.device_capabilities import DeviceFamily
from ..domain.pluto_connection import PlutoUsbConnectionExpectation, normalized_pluto_serial


def validate_parallel_receiver_identity(
    source_ids: Set[str], identities: Mapping[str, str | None],
    families: Mapping[str, DeviceFamily],
    usb_connections: Mapping[str, PlutoUsbConnectionExpectation | None] | None = None,
) -> None:
    """Reject aliases/unresolved same-family sources without touching hardware.

    Callers retain the selected source/session while staged and after uncertain
    close. Each accepted USB owner must also confirm its expectation before RF.
    This validator cannot certify native capability, liveness or cross-process
    ownership, and cannot establish USB/IP equivalence for an unknown serial.
    """
    if identities.keys() != source_ids or any(
        key is not None and (not isinstance(key, str) or not key.startswith("sha256:")
        or len(key) != 71 or any(character not in "0123456789abcdef" for character in key[7:]))
        for key in identities.values()
    ):
        raise ValueError("parallel receivers require exact canonical source identities")
    known = tuple(key for key in identities.values() if key is not None)
    if len(set(known)) != len(known):
        raise ValueError("two operational sources alias one physical receiver")
    connections = dict(usb_connections or {})
    if not connections.keys() <= source_ids:
        raise ValueError("USB observations have an unknown parallel source")
    if connections or any(key is None for key in identities.values()):
        if families.keys() != source_ids or any(not isinstance(family, DeviceFamily) for family in families.values()):
            raise ValueError("parallel receiver families must match every selected source")
    for source_id, connection in connections.items():
        if connection is None:
            continue
        if families[source_id] is not DeviceFamily.AD936X or not isinstance(connection, PlutoUsbConnectionExpectation):
            raise ValueError("parallel Pluto USB observations must be typed AD936x facts")
        connection.__post_init__()
    sources = sorted(source_ids)
    for index, left in enumerate(sources):
        for right in sources[index + 1:]:
            a, b = connections.get(left), connections.get(right)
            if a is not None and b is not None:
                if ((a.bus, a.device_address) == (b.bus, b.device_address)
                        or (normalized_pluto_serial(a.usb_serial) is not None
                            and normalized_pluto_serial(a.usb_serial) == normalized_pluto_serial(b.usb_serial))):
                    raise ValueError("two USB connections alias one physical receiver")
            if (identities[left] is None or identities[right] is None) and families[left] is families[right]:
                if families[left] is not DeviceFamily.AD936X or a is None or b is None:
                    raise ValueError("unidentified parallel receiver needs a unique selected family or distinct asserted USB connections")

"""Only observed non-placeholder serials can establish Pluto route aliases."""

from __future__ import annotations

import unittest

from sdr_monitor.domain import DeviceCapabilities, DeviceDescriptor, DeviceTransport
from sdr_monitor.services.native_live import (
    _merge_duplicate_pluto_routes,
    _physical_identity_key,
)


def _route(uri: str, serial: str | None, *, key: str | None = None) -> DeviceDescriptor:
    return DeviceDescriptor(
        device_id=f"candidate:{uri}",
        label="Analog Devices PlutoSDR Rev.B (Z7010-AD9364)",
        uri=uri,
        transport=DeviceTransport.USB if uri.startswith("usb:") else DeviceTransport.IP,
        capabilities=DeviceCapabilities(sample_rates_hz=(61_440_000.0,), gain_range_db=(0.0, 73.0)),
        serial=serial,
        identity_key=key,
    )


class PlutoRouteIdentityTests(unittest.TestCase):
    def test_unknown_usb_and_default_ip_are_not_physical_alias_proof(self) -> None:
        for serial in (None, "", " ", "UNKNOWN", "None", "N/A", "—", "-"):
            with self.subTest(serial=serial):
                routes = (_route("usb:1.2.3", serial), _route("ip:pluto.local", serial))
                self.assertEqual(_merge_duplicate_pluto_routes(routes), routes)

    def test_matching_normalized_serial_merges_despite_stale_route_keys(self) -> None:
        routes = (
            _route("ip:192.168.3.1", " AbC123 ", key="route-a"),
            _route("usb:1.2.3", "abc123", key="route-b"),
        )
        merged = _merge_duplicate_pluto_routes(routes)
        self.assertEqual(len(merged), 1)
        device = merged[0]
        self.assertEqual(device.uri, "usb:1.2.3")
        self.assertEqual(device.alternate_uris, ("ip:192.168.3.1",))
        self.assertTrue(device.identity_key.startswith("serial-"))
        self.assertNotIn("abc123", device.device_id.casefold())

    def test_shared_stale_key_cannot_merge_different_observed_serials(self) -> None:
        routes = (
            _route("usb:1.2.3", "radio-a", key="serial-stale"),
            _route("ip:192.168.3.1", "radio-b", key="serial-stale"),
        )
        merged = _merge_duplicate_pluto_routes(routes)
        self.assertEqual(len(merged), 2)
        self.assertNotEqual(merged[0].device_id, merged[1].device_id)
        self.assertFalse(any(device.alternate_uris for device in merged))

    def test_unknown_routes_never_join_known_serial_group(self) -> None:
        routes = (
            _route("usb:1.2.3", "radio-a"),
            _route("ip:pluto.local", None),
            _route("usb:2.3.4", None),
        )
        self.assertEqual(len(_merge_duplicate_pluto_routes(routes)), 3)

    def test_identity_hash_uses_same_serial_normalization_as_alias_admission(self) -> None:
        def key(serial: str) -> str:
            return _physical_identity_key(
                uri="usb:1.2.3", model="PlutoSDR", serial=serial,
                firmware="test", device_ids=("ad9361-phy",),
            )

        self.assertEqual(key(" AbC123 "), key("abc123"))
        for placeholder in ("UNKNOWN", "None", "N/A", "—", "-"):
            with self.subTest(placeholder=placeholder):
                self.assertTrue(key(placeholder).startswith("route-"))


if __name__ == "__main__":
    unittest.main()

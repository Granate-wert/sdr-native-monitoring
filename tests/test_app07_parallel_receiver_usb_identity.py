"""Pure connection admission; no SDK, UI, liveness or calibration claims."""
from dataclasses import replace
import unittest

from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pluto_connection import PlutoUsbConnectionExpectation
from sdr_monitor.services.parallel_receiver_identity import validate_parallel_receiver_identity


class ParallelReceiverUsbIdentityTests(unittest.TestCase):
    def test_known_and_empty_usb_distinct_devices_admit_without_stable_key_synthesis(self):
        known = PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, "KNOWN")
        unknown = PlutoUsbConnectionExpectation(2, 19, 5, 0x0456, 0xb673, "")
        keys = {"a": "sha256:" + "a" * 64, "b": None}
        validate_parallel_receiver_identity(set(keys), keys, dict.fromkeys(keys, DeviceFamily.AD936X),
                                            {"a": known, "b": unknown})
        self.assertIsNone(keys["b"])
        validate_parallel_receiver_identity(set(keys), dict.fromkeys(keys, None),
                                            dict.fromkeys(keys, DeviceFamily.AD936X),
                                            {"a": replace(known, usb_serial=""), "b": unknown})

    def test_same_bus_device_different_interfaces_refuse_even_distinct_stable_keys(self):
        a = PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, "")
        keys = {"a": "sha256:" + "a" * 64, "b": "sha256:" + "b" * 64}
        with self.assertRaisesRegex(ValueError, "alias"):
            validate_parallel_receiver_identity(set(keys), keys, dict.fromkeys(keys, DeviceFamily.AD936X),
                                                {"a": a, "b": replace(a, interface_number=6)})

    def test_known_usb_serial_alias_across_addresses_refuses(self):
        a = PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, "KNOWN")
        keys = dict.fromkeys(("a", "b"), None)
        with self.assertRaisesRegex(ValueError, "alias"):
            validate_parallel_receiver_identity(set(keys), keys, dict.fromkeys(keys, DeviceFamily.AD936X),
                                                {"a": a, "b": replace(a, device_address=19, usb_serial="known")})

    def test_unresolved_usb_ip_relation_unknown_same_family_refuses_both_orders(self):
        a = PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, "")
        for ids in (("a", "b"), ("b", "a")):
            keys = {ids[0]: None, ids[1]: "sha256:" + "b" * 64}
            with self.assertRaisesRegex(ValueError, "distinct asserted USB"):
                validate_parallel_receiver_identity(set(keys), keys, dict.fromkeys(keys, DeviceFamily.AD936X),
                                                    {ids[0]: a})

    def test_duplicate_stable_alias_still_refuses_across_transport(self):
        keys = dict.fromkeys(("a", "b"), "sha256:" + "a" * 64)
        with self.assertRaisesRegex(ValueError, "alias"):
            validate_parallel_receiver_identity(set(keys), keys, dict.fromkeys(keys, DeviceFamily.AD936X))

    def test_other_family_unknown_identity_and_known_legacy_preserve_rules(self):
        keys = dict.fromkeys(("a", "b"), None)
        validate_parallel_receiver_identity(set(keys), keys, {"a": DeviceFamily.AD936X, "b": DeviceFamily.HACKRF})
        with self.assertRaises(ValueError):
            validate_parallel_receiver_identity(set(keys), keys, dict.fromkeys(keys, DeviceFamily.HACKRF))
        keys = {"a": "sha256:" + "a" * 64, "b": "sha256:" + "b" * 64}
        validate_parallel_receiver_identity(set(keys), keys, {})

    def test_unknown_source_wrong_type_and_foreign_family_usb_refuse(self):
        a = PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, "")
        keys = dict.fromkeys(("a", "b"), None)
        for connections, families in (
            ({"foreign": a}, dict.fromkeys(keys, DeviceFamily.AD936X)),
            ({"a": "usb:2.18.5"}, dict.fromkeys(keys, DeviceFamily.AD936X)),
            ({"a": a}, {"a": DeviceFamily.HACKRF, "b": DeviceFamily.AD936X}),
        ):
            with self.assertRaises(ValueError):
                validate_parallel_receiver_identity(set(keys), keys, families, connections)


if __name__ == "__main__":
    unittest.main()

"""Qt-free typed USB assertion boundary; fake runtime, no hardware."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sdr_monitor.services.ad936x_identity_admission import (
    PlutoUsbConnectionExpectation,
    create_identity_bound_owner,
)


def _probe(**changes: object) -> SimpleNamespace:
    values = dict(context_name="usb", backend_uri="usb:2.42.5", uri="usb:caller-alias",
                  usb_vendor_id="0456", usb_product_id="B673", usb_serial="MOCK", serial="mock")
    values.update(changes)
    return SimpleNamespace(**values)


class PlutoUsbConnectionAdmissionTests(unittest.TestCase):
    def test_observed_route_and_explicit_empty_serial_not_caller_route(self) -> None:
        assertion = PlutoUsbConnectionExpectation.from_probe(_probe())
        self.assertEqual(assertion, PlutoUsbConnectionExpectation(2, 42, 5, 0x0456, 0xb673, "MOCK"))
        empty = PlutoUsbConnectionExpectation.from_probe(_probe(usb_serial="", serial=""))
        self.assertEqual(empty.usb_serial, "")
        with self.assertRaises(AttributeError):
            empty.bus = 3  # type: ignore[misc]

    def test_bad_observations_do_not_synthesize_or_downgrade(self) -> None:
        for changes in (
            dict(context_name="ip"), dict(backend_uri=None), dict(backend_uri="usb:mock"),
            dict(backend_uri="usb:02.42.5"), dict(backend_uri="usb:2.42.5tail"), dict(backend_uri="usb:2x42x5"),
            dict(backend_uri="usb:2.0.5"), dict(backend_uri="usb:2.128.5"),
            dict(backend_uri="usb:256.42.5"), dict(backend_uri="usb:2.42.256"),
            dict(usb_vendor_id=None), dict(usb_vendor_id="0000"), dict(usb_product_id="b673\x00"),
            dict(usb_serial=None), dict(usb_serial="OTHER"), dict(usb_serial="", serial="MOCK"),
            dict(usb_serial="unknown", serial=""), dict(usb_serial="bad serial", serial="bad serial"),
            dict(usb_serial="", serial="unknown"), dict(serial=None),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                PlutoUsbConnectionExpectation.from_probe(_probe(**changes))

    def test_invalid_host_fields_refuse(self) -> None:
        assertion = PlutoUsbConnectionExpectation.from_probe(_probe())
        for field, value in (("bus", True), ("bus", -1), ("bus", 256),
                             ("device_address", 0), ("device_address", 128),
                             ("interface_number", 256), ("vendor_id", 0), ("product_id", 65536),
                             ("usb_serial", None), ("usb_serial", "UNKNOWN"), ("usb_serial", "\x00"),
                             ("usb_serial", "a" * 257)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                replace(assertion, **{field: value})

    def test_bridge_receives_typed_assertion_once_for_each_owner(self) -> None:
        assertion = PlutoUsbConnectionExpectation.from_probe(_probe())
        for name in ("PlutoDevice", "PlutoFixedBandEngine", "NativeContinuousSweepCoordinator"):
            factory = Mock()
            bridge = Mock(side_effect=SimpleNamespace)
            native = SimpleNamespace(**{name: factory}, PlutoExpectedUsbConnection=bridge,
                                     PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION=1,
                                     PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION=1)
            result = create_identity_bound_owner(native, name, "usb:caller-alias", 1234,
                                                 expected_serial="MOCK", expected_usb_connection=assertion)
            self.assertIs(result, factory.return_value)
            bridge.assert_called_once_with()
            factory.assert_called_once()
            self.assertEqual(factory.call_args.kwargs["expected_serial"], "mock")
            usb = factory.call_args.kwargs["expected_usb_connection"]
            self.assertEqual((usb.bus, usb.device_address, usb.interface_number), (2, 42, 5))
            self.assertEqual(usb.usb_serial, "MOCK")

    def test_missing_wrong_protocol_and_bad_type_never_call_hardware_factory(self) -> None:
        assertion = PlutoUsbConnectionExpectation.from_probe(_probe())
        for protocol in (None, True, 0, 2, "1"):
            factory = Mock()
            native = SimpleNamespace(PlutoDevice=factory, PlutoExpectedUsbConnection=SimpleNamespace,
                                     PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION=protocol)
            with self.subTest(protocol=protocol), self.assertRaises(RuntimeError):
                create_identity_bound_owner(native, "PlutoDevice", "usb:alias", 3000,
                                            expected_usb_connection=assertion)
            factory.assert_not_called()
        with self.assertRaises(TypeError):
            create_identity_bound_owner(SimpleNamespace(), "PlutoDevice", "usb:alias", 3000,
                                        expected_usb_connection=SimpleNamespace())  # type: ignore[arg-type]

    def test_cross_transport_and_conflicting_serial_refuse_before_bridge_or_factory(self) -> None:
        assertion = PlutoUsbConnectionExpectation.from_probe(_probe())
        bridge = Mock()
        factory = Mock()
        native = SimpleNamespace(PlutoDevice=factory, PlutoExpectedUsbConnection=bridge,
                                 PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION=1,
                                 PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION=1)
        for uri, serial in (("ip:192.0.2.1", None), ("usb:alias", "OTHER")):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                create_identity_bound_owner(native, "PlutoDevice", uri, 3000,
                                            expected_serial=serial, expected_usb_connection=assertion)
        bridge.assert_not_called()
        factory.assert_not_called()

    def test_empty_usb_serial_keeps_stable_identity_unknown(self) -> None:
        factory = Mock()
        native = SimpleNamespace(PlutoDevice=factory, PlutoExpectedUsbConnection=SimpleNamespace,
                                 PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION=1)
        assertion = PlutoUsbConnectionExpectation.from_probe(_probe(usb_serial="", serial=""))
        create_identity_bound_owner(native, "PlutoDevice", "usb:alias", 3000,
                                    expected_usb_connection=assertion)
        self.assertNotIn("expected_serial", factory.call_args.kwargs)
        self.assertEqual(factory.call_args.kwargs["expected_usb_connection"].usb_serial, "")


if __name__ == "__main__":
    unittest.main()

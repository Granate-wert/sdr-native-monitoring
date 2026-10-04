"""Actual Live/Sweep service propagation with a deterministic fake SDK."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sdr_monitor.domain import BackendKind, DeviceTransport, LiveConfiguration, LiveSessionState
from sdr_monitor.domain.pluto_connection import PlutoUsbConnectionExpectation
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.native_sweep import NativeSweepLease, NativeSweepService, NativeSweepSource
from sdr_monitor.domain import SweepConfiguration, SweepExecutionMode
from tests.test_native_live_discovery import _FakeNative


class _UsbNative(_FakeNative):
    PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION = 1
    PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION = 1
    PlutoExpectedUsbConnection = SimpleNamespace

    def __init__(self, *, serial: str = "RADIO-A") -> None:
        super().__init__()
        self.capabilities.serial = serial
        self.actual_serial = serial
        self.actual_uri = "usb:2.42.5"
        self.actual_usb_serial = serial
        self.opens: list[tuple[str, str, object]] = []
        self.fail_disconnect = False

    def scan_pluto_contexts(self, _filter: str):
        return (SimpleNamespace(uri="usb:2.42.5", description="test USB"),)

    def facts(self, uri: str):
        return SimpleNamespace(uri=uri, context_name="usb", backend_uri=self.actual_uri,
                               usb_vendor_id="0456", usb_product_id="b673",
                               usb_serial=self.actual_usb_serial, serial=self.actual_serial)

    def check(self, kind: str, uri: str, serial, connection):
        self.opens.append((kind, uri, connection))
        if serial is not None and serial != self.actual_serial.lower():
            raise RuntimeError("same owned hardware serial mismatch")
        if connection is not None:
            expected = PlutoUsbConnectionExpectation(connection.bus, connection.device_address,
                connection.interface_number, connection.vendor_id, connection.product_id, connection.usb_serial)
            if PlutoUsbConnectionExpectation.from_probe(self.facts(uri)) != expected:
                raise RuntimeError("same owned USB connection mismatch")

    def PlutoDevice(self, uri, timeout_ms, *, expected_serial=None, expected_usb_connection=None):
        self.check("device", uri, expected_serial, expected_usb_connection)
        owner = super().PlutoDevice(uri, timeout_ms)
        owner.probe = lambda: self.facts(uri)
        disconnect = owner.disconnect
        def close():
            if self.fail_disconnect:
                raise RuntimeError("test SDK close uncertain")
            disconnect()
        owner.disconnect = close
        return owner

    def PlutoFixedBandEngine(self, uri, timeout_ms, *, expected_serial=None, expected_usb_connection=None):
        self.check("engine", uri, expected_serial, expected_usb_connection)
        return super().PlutoFixedBandEngine(uri, timeout_ms)


def _selected(*, serial: str = "RADIO-A"):
    native = _UsbNative(serial=serial)
    service = NativeLiveSessionService(native)
    descriptor = service.discover_devices()[0]
    result = service.select_device(descriptor.device_id)
    if result.state is not LiveSessionState.CONNECTED:
        raise AssertionError(result.error)
    return service, native, result.device


class PlutoUsbProductPathTests(unittest.TestCase):
    def test_selected_descriptor_rechecks_owned_connection_without_rf(self):
        service, native, descriptor = _selected()
        try:
            self.assertIsInstance(descriptor.usb_connection, PlutoUsbConnectionExpectation)
            self.assertIsNotNone(native.opens[-1][2])
            self.assertFalse(native.engines)
            self.assertNotIn("usb_connection", repr(descriptor))
        finally:
            service.close_live()

    def test_usb_move_after_discovery_refuses_selection_without_ip_fallback(self):
        native = _UsbNative()
        service = NativeLiveSessionService(native)
        descriptor = service.discover_devices()[0]
        service._devices = (replace(descriptor, alternate_uris=("ip:radio.local",)),)
        native.actual_uri = "usb:2.43.5"
        start = len(native.opens)
        result = service.select_device(descriptor.device_id)
        self.assertEqual(result.state, LiveSessionState.ERROR)
        self.assertEqual([(kind, uri) for kind, uri, _ in native.opens[start:]], [("device", descriptor.uri)])
        self.assertFalse(native.engines)

    def test_usb_move_after_apply_refuses_before_engine_configure_start(self):
        service, native, descriptor = _selected()
        try:
            service.apply_configuration(LiveConfiguration(sample_rate_hz=20e6, backend=BackendKind.CPU))
            service._devices = (replace(descriptor, alternate_uris=("ip:radio.local",)),)
            native.actual_uri = "usb:2.43.5"
            start = len(native.opens)
            result = service.start_admitted()
            self.assertEqual(result.state, LiveSessionState.ERROR)
            self.assertEqual([(kind, uri) for kind, uri, _ in native.opens[start:]], [("engine", descriptor.uri)])
            self.assertFalse(native.engines)
            self.assertIsNone(service._engine)
        finally:
            service.close_live()

    def test_valid_rtbw_owner_gets_assertion_then_configure_start(self):
        service, native, descriptor = _selected()
        try:
            service.apply_configuration(LiveConfiguration(sample_rate_hz=20e6, backend=BackendKind.CPU))
            result = service.start_admitted()
            self.assertEqual(result.state, LiveSessionState.RUNNING, result.error)
            self.assertEqual(native.opens[-1][0], "engine")
            self.assertEqual(native.opens[-1][2].device_address, descriptor.usb_connection.device_address)
            self.assertEqual(native.engines[-1].configure_calls, 1)
            self.assertEqual(native.engines[-1].start_calls, 1)
        finally:
            service.close_live()

    def test_missing_contradictory_new_protocol_facts_are_not_legacy_fallback(self):
        for change in ("missing", "contradictory", "wrong_protocol"):
            native = _UsbNative()
            if change == "missing":
                native.actual_uri = ""
            elif change == "contradictory":
                native.actual_usb_serial = "OTHER"
            else:
                native.PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION = True
            service = NativeLiveSessionService(native)
            # The discovery transaction aborts; it must not publish a
            # downgraded descriptor with a missing connection assertion.
            with self.assertRaises((ValueError, RuntimeError)):
                service.discover_devices()
            self.assertFalse(native.engines)

    def test_explicit_empty_serial_stays_unknown_but_has_usb_assertion(self):
        service, native, descriptor = _selected(serial="")
        try:
            self.assertIsNone(descriptor.serial)
            self.assertIsNone(descriptor.capability_snapshot)
            self.assertIsNone(descriptor.calibration_identity)
            self.assertEqual(descriptor.usb_connection.usb_serial, "")
            self.assertIsNotNone(native.opens[-1][2])
        finally:
            service.close_live()

    def test_bad_descriptor_and_sweep_cross_transport_refuse(self):
        service, _native, descriptor = _selected()
        try:
            with self.assertRaises(ValueError):
                replace(descriptor, transport=DeviceTransport.IP, uri="ip:radio.local")
            with self.assertRaises(ValueError):
                replace(descriptor, serial="OTHER")
            source = NativeSweepSource(descriptor.uri, "source", LiveConfiguration(backend=BackendKind.CPU),
                                       expected_serial="RADIO-A", expected_usb_connection=descriptor.usb_connection)
            self.assertNotIn("expected_usb_connection", repr(source))
            with self.assertRaises(ValueError):
                replace(source, context_uri="ip:radio.local")
        finally:
            service.close_live()

    def test_sweep_lease_carries_selected_connection_and_invalidation_is_explicit(self):
        service, _native, descriptor = _selected()
        service.apply_configuration(LiveConfiguration(sample_rate_hz=20e6, backend=BackendKind.CPU))
        lease = service.acquire_native_sweep_lease()
        try:
            self.assertIs(lease.source.expected_usb_connection, descriptor.usb_connection)
            lease.assert_active()
            lease.release()
            with self.assertRaises(RuntimeError):
                lease.assert_active()
        finally:
            service.close_live()

    def test_direct_factory_and_sequential_sweep_forward_the_same_assertion(self):
        assertion = PlutoUsbConnectionExpectation(2, 42, 5, 0x0456, 0xb673, "RADIO-A")
        coordinator = Mock()
        reject = Mock(side_effect=RuntimeError("owned USB mismatch"))
        native = SimpleNamespace(PlutoFixedBandEngine=reject, NativeContinuousSweepCoordinator=coordinator,
                                 PlutoExpectedUsbConnection=SimpleNamespace,
                                 PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION=1,
                                 PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION=1)
        source = NativeSweepSource("usb:alias", "source", LiveConfiguration(backend=BackendKind.CPU),
                                   expected_serial="RADIO-A", expected_usb_connection=assertion)
        direct = NativeContinuousSweepDisplayService(native, source.context_uri, expected_serial=source.expected_serial,
                                                     expected_usb_connection=source.expected_usb_connection)
        direct.close()
        lease = NativeSweepLease(native, source, lambda: None, Mock())
        factory = NativeContinuousSweepPlanFactory(lease)
        try:
            display = factory.create_display_service()
            display.close()
            factory.create_coordinator()
            for call in coordinator.call_args_list:
                self.assertEqual(call.kwargs["expected_usb_connection"].device_address, 42)
        finally:
            factory.close()
        release = Mock()
        sequential = NativeSweepService(native, source, assert_exclusive=lambda: None, release_lease=release)
        try:
            with self.assertRaisesRegex(RuntimeError, "owned USB mismatch"):
                sequential.execute(SweepConfiguration(start_hz=100e6, stop_hz=110e6,
                    execution_mode=SweepExecutionMode.NATIVE), lambda _: None)
            self.assertEqual(reject.call_args.kwargs["expected_usb_connection"].device_address, 42)
            release.assert_called_once()
        finally:
            sequential.close()

    def test_uncertain_observation_close_blocks_new_selection_until_explicit_close(self):
        service, native, descriptor = _selected()
        native.fail_disconnect = True
        result = service.select_device(descriptor.device_id)
        self.assertEqual(result.state, LiveSessionState.ERROR)
        self.assertTrue(service._observation_owner.cleanup_pending)
        count = len(native.opens)
        with self.assertRaises(Exception):
            service.select_device(descriptor.device_id)
        self.assertEqual(len(native.opens), count)
        native.fail_disconnect = False
        service.close_capability_observation()
        self.assertFalse(service._observation_owner.cleanup_pending)
        service.close_live()


if __name__ == "__main__":
    unittest.main()

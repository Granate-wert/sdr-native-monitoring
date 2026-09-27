"""Known Pluto identity follows all V2 native owners, never a separate probe."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from sdr_monitor.domain import BackendKind, LiveConfiguration, LiveSessionState, SweepConfiguration, SweepExecutionMode
from sdr_monitor.services.ad936x_identity_admission import (
    create_identity_bound_owner,
    normalized_pluto_serial,
)
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.native_sweep import NativeSweepLease, NativeSweepService, NativeSweepSource
from tests.test_native_live_discovery import _FakeNative


class _IdentityNative(_FakeNative):
    PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self.capabilities.serial = "radio-a"
        self.observed_serial = "radio-a"
        self.bound_calls: list[tuple[str, str | None]] = []

    def _check(self, kind: str, expected: str | None) -> None:
        self.bound_calls.append((kind, expected))
        if expected is not None and expected != self.observed_serial:
            raise RuntimeError("Pluto receiver identity was not confirmed on the opened context")

    def PlutoDevice(self, uri, timeout_ms, *, expected_serial=None):
        self._check("device", expected_serial)
        return super().PlutoDevice(uri, timeout_ms)

    def PlutoFixedBandEngine(self, uri, timeout_ms, *, expected_serial=None):
        self._check("engine", expected_serial)
        return super().PlutoFixedBandEngine(uri, timeout_ms)


def _selected():
    native = _IdentityNative()
    service = NativeLiveSessionService(native)
    device = service.discover_devices()[0]
    result = service.select_device(device.device_id)
    if result.state is not LiveSessionState.CONNECTED:
        raise AssertionError(result.error)
    service.apply_configuration(LiveConfiguration(sample_rate_hz=20e6, backend=BackendKind.CPU))
    return service, native


class PlutoIdentityAdmissionTests(unittest.TestCase):
    def test_normalization_is_explicit_and_rejects_placeholder_or_control(self) -> None:
        self.assertEqual(normalized_pluto_serial(" \tABC-123\r\n"), "abc-123")
        for invalid in (None, "", " UNKNOWN ", "None", "N/A", "—", "-", "a b", "a\nb", "é", "K", 123):
            with self.subTest(invalid=invalid):
                self.assertIsNone(normalized_pluto_serial(invalid))

    def test_known_identity_protocol_rejects_before_factory_io(self) -> None:
        for version in (None, True, False, 0, 2, "1", 1.0):
            with self.subTest(version=version):
                factory = Mock()
                native = SimpleNamespace(PlutoDevice=factory)
                if version is not None:
                    native.PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION = version
                with self.assertRaisesRegex(RuntimeError, "identity admission"):
                    create_identity_bound_owner(native, "PlutoDevice", "usb:test", 3000,
                                                expected_serial="PRIVATE-SERIAL")
                factory.assert_not_called()

    def test_supplied_invalid_serial_never_downgrades_to_unknown(self) -> None:
        factory = Mock()
        native = SimpleNamespace(PlutoDevice=factory, PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION=1)
        for serial in ("", "UNKNOWN", "—", "a b"):
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                create_identity_bound_owner(native, "PlutoDevice", "usb:test", 3000, expected_serial=serial)
        factory.assert_not_called()

    def test_known_factory_gets_expected_identity_on_single_open(self) -> None:
        factory = Mock()
        native = SimpleNamespace(PlutoDevice=factory, PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION=1)
        owner = create_identity_bound_owner(native, "PlutoDevice", "usb:test", 3000,
                                            expected_serial=" ABC ")
        self.assertIs(owner, factory.return_value)
        factory.assert_called_once_with("usb:test", 3000, expected_serial="abc")

    def test_unknown_route_retains_old_call_without_claiming_stable_identity(self) -> None:
        factory = Mock()
        owner = create_identity_bound_owner(SimpleNamespace(PlutoDevice=factory), "PlutoDevice", "usb:test", 3000)
        self.assertIs(owner, factory.return_value)
        factory.assert_called_once_with("usb:test", 3000)

    def test_missing_and_noncallable_factory_are_rejected_without_io(self) -> None:
        with self.assertRaises(RuntimeError):
            create_identity_bound_owner(SimpleNamespace(), "PlutoDevice", "usb:test", 3000)
        with self.assertRaises(TypeError):
            create_identity_bound_owner(SimpleNamespace(PlutoDevice=123), "PlutoDevice", "usb:test", 3000)

    def test_selection_is_identity_bound_and_changed_device_cannot_configure_start(self) -> None:
        service, native = _selected()
        try:
            self.assertEqual(native.bound_calls[-1], ("device", "radio-a"))
            native.observed_serial = "radio-b"
            snapshot = service.start_admitted()
            self.assertEqual(snapshot.state, LiveSessionState.ERROR)
            self.assertEqual(native.bound_calls[-1], ("engine", "radio-a"))
            self.assertEqual(native.engines, [])
            self.assertIsNone(service._engine)
        finally:
            service.close_live()

    def test_old_runtime_known_route_is_rejected_at_selection_without_new_open(self) -> None:
        native = _IdentityNative()
        native.PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION = 0
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        count = len(native.created)
        result = service.select_device(device.device_id)
        self.assertEqual(result.state, LiveSessionState.ERROR)
        self.assertEqual(len(native.created), count)
        self.assertEqual(native.engines, [])

    def test_live_lease_carries_selected_identity_not_route_guess(self) -> None:
        service, _native = _selected()
        lease = service.acquire_native_sweep_lease()
        try:
            self.assertEqual(lease.source.expected_serial, "radio-a")
            self.assertNotIn("expected_serial", repr(lease.source))
        finally:
            lease.release()
            service.close_live()

    def test_direct_and_factory_continuous_sweep_forward_identity(self) -> None:
        coordinator = Mock()
        native = SimpleNamespace(
            NativeContinuousSweepCoordinator=coordinator,
            PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION=1,
        )
        source = NativeSweepSource("usb:test", "source", LiveConfiguration(backend=BackendKind.CPU),
                                   expected_serial="RADIO-A")
        lease = NativeSweepLease(native, source, lambda: None, Mock())
        factory = NativeContinuousSweepPlanFactory(lease)
        try:
            direct = NativeContinuousSweepDisplayService(native, "usb:test", expected_serial="RADIO-A")
            direct.close()
            display = factory.create_display_service()
            display.close()
            factory.create_coordinator(timeout_ms=1250)
            self.assertEqual(coordinator.call_count, 3)
            self.assertEqual(coordinator.call_args_list[0].kwargs, {"expected_serial": "radio-a"})
            self.assertEqual(coordinator.call_args_list[1].kwargs, {"expected_serial": "radio-a"})
            self.assertEqual(coordinator.call_args_list[2].args, ("usb:test", 1250))
            self.assertEqual(coordinator.call_args_list[2].kwargs, {"expected_serial": "radio-a"})
        finally:
            factory.close()

    def test_sequential_sweep_guards_before_configure_and_releases_lease(self) -> None:
        reject = Mock(side_effect=RuntimeError("Pluto receiver identity was not confirmed"))
        native = SimpleNamespace(PlutoFixedBandEngine=reject, PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION=1)
        release = Mock()
        source = NativeSweepSource("usb:test", "source", LiveConfiguration(backend=BackendKind.CPU),
                                   expected_serial="RADIO-A")
        service = NativeSweepService(native, source, assert_exclusive=lambda: None, release_lease=release)
        try:
            with self.assertRaisesRegex(RuntimeError, "identity"):
                service.execute(SweepConfiguration(start_hz=100e6, stop_hz=110e6,
                                                  execution_mode=SweepExecutionMode.NATIVE), lambda _: None)
            reject.assert_called_once_with("usb:test", 3000, expected_serial="radio-a")
            release.assert_called_once_with()
            self.assertFalse(service.cleanup_pending)
        finally:
            service.close()

    def test_sweep_source_rejects_invalid_expected_serial_without_native_io(self) -> None:
        source = NativeSweepSource("usb:test", "source", LiveConfiguration(backend=BackendKind.CPU))
        with self.assertRaises(ValueError):
            replace(source, expected_serial="UNKNOWN")


if __name__ == "__main__":
    unittest.main()

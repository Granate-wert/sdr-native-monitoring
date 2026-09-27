"""AD936x observation is inadmissible until its temporary owner closes."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from sdr_monitor.domain.device_capabilities import CapabilityTransport
from sdr_monitor.services.ad936x_capability_adapter import (
    Ad936xCapabilityObservationError,
    Ad936xLibiioCapabilityAdapter,
)


def _range(lower: float, upper: float) -> SimpleNamespace:
    return SimpleNamespace(minimum=lower, maximum=upper)


class _Device:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.probe_error = False
        self.capabilities_error = False
        self.disconnect_error = False
        self.serial: str | None = "fixture-serial"

    def probe(self) -> SimpleNamespace:
        self.calls.append("probe")
        if self.probe_error:
            raise RuntimeError("probe private route")
        return SimpleNamespace(serial=self.serial, firmware="fixture-fw")

    def capabilities(self) -> SimpleNamespace:
        self.calls.append("capabilities")
        if self.capabilities_error:
            raise RuntimeError("capabilities private route")
        return SimpleNamespace(
            supports_continuous_iq=True,
            supports_hardware_timestamps=False,
            supports_overflow_counter=False,
            tuning_range_hz=_range(70e6, 6e9),
            sample_rate_ranges_hz=(_range(2.083334e6, 61.44e6),),
            analog_bandwidth_ranges_hz=(_range(0.2e6, 56e6),),
            gain_range_db=_range(-3.0, 71.0),
        )

    def disconnect(self) -> None:
        self.calls.append("disconnect")
        if self.disconnect_error:
            raise RuntimeError("disconnect private route")


class _Native:
    def __init__(self, device: _Device) -> None:
        self.device = device
        self.open_calls: list[tuple[str, int]] = []

    def PlutoDevice(self, route: str, timeout_ms: int) -> _Device:
        self.open_calls.append((route, timeout_ms))
        return self.device


class App06Ad936xCapabilityCleanupTests(unittest.TestCase):
    def test_success_closes_before_publishing_and_reuses_serial_identity_for_aliases(self) -> None:
        device = _Device()
        native = _Native(device)
        adapter = Ad936xLibiioCapabilityAdapter(native)
        usb = adapter.observe("usb:1.2.3")
        self.assertEqual(device.calls, ["probe", "capabilities", "disconnect"])
        ip = adapter.observe("ip:fixture.local")
        self.assertEqual(usb.snapshot.identity_key, ip.snapshot.identity_key)
        self.assertEqual(usb.snapshot.transports, (CapabilityTransport.USB,))
        self.assertEqual(ip.snapshot.transports, (CapabilityTransport.ETHERNET,))
        self.assertEqual(usb.snapshot.sample_rate_ranges_hz[0].maximum, 61.44e6)
        self.assertIsNone(usb.snapshot.rx_channel_count)
        self.assertIsNone(usb.snapshot.shared_rx_lo)
        self.assertEqual(len(native.open_calls), 2)

    def test_probe_and_capability_failure_still_attempt_close_and_redact_error(self) -> None:
        for failure in ("probe_error", "capabilities_error"):
            with self.subTest(failure=failure):
                device = _Device()
                setattr(device, failure, True)
                adapter = Ad936xLibiioCapabilityAdapter(_Native(device))
                with self.assertRaises(Ad936xCapabilityObservationError) as error:
                    adapter.observe("usb:1.2.3")
                self.assertNotIn("private", str(error.exception))
                self.assertEqual(device.calls[-1], "disconnect")
                setattr(device, failure, False)
                adapter.observe("usb:1.2.3")

    def test_close_failure_rejects_valid_facts_and_blocks_new_context_until_explicit_release(self) -> None:
        device = _Device()
        device.disconnect_error = True
        native = _Native(device)
        adapter = Ad936xLibiioCapabilityAdapter(native)
        with self.assertRaises(Ad936xCapabilityObservationError) as error:
            adapter.observe("usb:1.2.3")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(device.calls, ["probe", "capabilities", "disconnect"])
        with self.assertRaises(Ad936xCapabilityObservationError):
            adapter.observe("ip:fixture.local")
        self.assertEqual(len(native.open_calls), 1)
        self.assertEqual(device.calls.count("disconnect"), 1)
        with self.assertRaises(Ad936xCapabilityObservationError):
            adapter.close()
        self.assertEqual(device.calls.count("disconnect"), 2)
        device.disconnect_error = False
        adapter.close()
        adapter.close()  # Successful release is idempotent, with no extra device call.
        self.assertEqual(device.calls.count("disconnect"), 3)
        adapter.observe("ip:fixture.local")
        self.assertEqual(len(native.open_calls), 2)

    def test_failed_probe_and_failed_close_retain_owner_and_prevent_reopen(self) -> None:
        device = _Device()
        device.probe_error = device.disconnect_error = True
        native = _Native(device)
        adapter = Ad936xLibiioCapabilityAdapter(native)
        for _ in range(2):
            with self.assertRaises(Ad936xCapabilityObservationError):
                adapter.observe("usb:1.2.3")
        self.assertEqual(len(native.open_calls), 1)
        self.assertEqual(device.calls, ["probe", "disconnect"])
        device.disconnect_error = False
        adapter.close()

    def test_invalid_facts_are_rejected_after_successful_cleanup(self) -> None:
        device = _Device()
        device.serial = None
        native = _Native(device)
        adapter = Ad936xLibiioCapabilityAdapter(native)
        with self.assertRaises(Ad936xCapabilityObservationError):
            adapter.observe("usb:1.2.3")
        self.assertEqual(device.calls[-1], "disconnect")
        device.serial = "fixture-serial"
        adapter.observe("usb:1.2.3")

    def test_invalid_route_is_rejected_without_open(self) -> None:
        native = _Native(_Device())
        adapter = Ad936xLibiioCapabilityAdapter(native)
        for route in ("", " usb:1.2.3", "pcie:fixture"):
            with self.subTest(route=route), self.assertRaises(Ad936xCapabilityObservationError):
                adapter.observe(route)
        self.assertEqual(native.open_calls, [])

    def test_close_before_observation_does_not_touch_device(self) -> None:
        device = _Device()
        native = _Native(device)
        adapter = Ad936xLibiioCapabilityAdapter(native)
        adapter.close()
        self.assertEqual(native.open_calls, [])
        self.assertEqual(device.calls, [])

    def test_constructor_failure_does_not_poison_next_explicit_observation(self) -> None:
        device = _Device()
        native = _Native(device)
        original = native.PlutoDevice
        native.PlutoDevice = lambda *_args: (_ for _ in ()).throw(RuntimeError("private"))
        adapter = Ad936xLibiioCapabilityAdapter(native)
        with self.assertRaises(Ad936xCapabilityObservationError) as error:
            adapter.observe("usb:1.2.3")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(device.calls, [])
        adapter.close()
        native.PlutoDevice = original
        adapter.observe("usb:1.2.3")


if __name__ == "__main__":
    unittest.main()

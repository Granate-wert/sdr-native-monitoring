"""One owned observation, explicit release and the existing capability inventory."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from sdr_monitor.domain import LiveSessionState
from sdr_monitor.domain.device_capabilities import CapabilityTransport, DeviceFamily
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.services.ad936x_capability_adapter import (
    Ad936xCapabilityObservationError,
    Ad936xLibiioCapabilityAdapter,
)
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.pluto_readonly_observation import PlutoObservationError, PlutoReadOnlyObserver
from tests.test_native_live_discovery import _FakeNative


def _range(minimum, maximum):
    return SimpleNamespace(minimum=minimum, maximum=maximum, step=0.0)


class _Device:
    def __init__(self, native, uri):
        self.native, self.uri = native, uri
        self.disconnect_error = native.disconnect_error
        self.calls = []

    def probe(self):
        self.calls.append("probe")
        if self.native.read_failure == "probe":
            raise RuntimeError("PRIVATE SDK serial/route")
        return SimpleNamespace(
            uri=self.uri, model="PlutoSDR", serial=self.native.serial,
            firmware="fixture-fw", rx_stream_device_id="iio:rx", device_ids=("iio:phy", "iio:rx"),
        )

    def capabilities(self):
        self.calls.append("capabilities")
        if self.native.read_failure == "capabilities":
            raise RuntimeError("PRIVATE SDK serial/route")
        maximum = 20e6 if self.uri in self.native.conflicting_routes else 61.44e6
        return SimpleNamespace(
            model="PlutoSDR", serial=self.native.serial, firmware="fixture-fw",
            supports_continuous_iq=True, supports_hardware_timestamps=False,
            supports_overflow_counter=False, tuning_range_hz=_range(70e6, 6e9),
            sample_rate_ranges_hz=(_range(2.083333e6, maximum),),
            analog_bandwidth_ranges_hz=(_range(0.2e6, 56e6),), gain_range_db=_range(-3.0, 71.0),
        )

    def receiver_topology(self):
        self.calls.append("topology")
        if self.native.read_failure == "topology":
            raise RuntimeError("PRIVATE SDK serial/route")
        return SimpleNamespace(
            context=SimpleNamespace(serial=self.native.topology_serial, firmware="fixture-fw",
                                    model="PlutoSDR", rx_stream_device_id="iio:rx"),
            phy_rx_channel_ids=("voltage0",),
            input_scan_elements=tuple(SimpleNamespace(
                id=f"voltage{index}", device_channel_index=index, storage_bits=16,
                significant_bits=12, shift=0, is_signed=True, is_big_endian=False, repeat=1,
            ) for index in range(2)),
        )

    def disconnect(self):
        self.calls.append("disconnect")
        if self.disconnect_error:
            raise RuntimeError("PRIVATE disconnect details")


class _Native(_FakeNative):
    PLUTO_OBSERVATION_PROTOCOL_VERSION = 1
    PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION = 1

    def __init__(self, *, serial="fixture-a"):
        super().__init__()
        self.serial = self.topology_serial = serial
        self.disconnect_error = False
        self.read_failure = None
        self.conflicting_routes = set()
        self.scans = []
        self.routes = ("usb:fixture", "ip:fixture.local")

    def scan_pluto_contexts(self, filter_value):
        self.scans.append(filter_value)
        return tuple(SimpleNamespace(uri=uri, description="fixture") for uri in self.routes)

    def PlutoDevice(self, uri, timeout_ms, *, expected_serial=None):
        if expected_serial is not None and expected_serial != self.serial:
            raise RuntimeError("identity was not confirmed")
        device = _Device(self, uri)
        self.created.append(device)
        return device

    def probe_pluto_context(self, *_args):
        raise AssertionError("separate context probe must not be called")

    def probe_pluto_receiver_topology(self, *_args):
        raise AssertionError("separate topology context must not be called")


class PlutoObservationCatalogTests(unittest.TestCase):
    def test_owned_reader_closes_before_publishing_all_facts(self):
        native = _Native()
        reader = PlutoReadOnlyObserver(native)
        observed = reader.observe("usb:fixture")
        self.assertEqual(native.created[0].calls, ["probe", "capabilities", "topology", "disconnect"])
        self.assertFalse(reader.cleanup_pending)
        self.assertTrue(observed.coherent_context)
        self.assertNotIn("fixture-a", repr(observed))
        self.assertEqual(observed.topology.context.serial, observed.probe.serial)

    def test_read_errors_close_owner_and_redact_details(self):
        for failure in ("probe", "capabilities", "topology"):
            with self.subTest(failure=failure):
                native = _Native()
                native.read_failure = failure
                reader = PlutoReadOnlyObserver(native)
                with self.assertRaises(PlutoObservationError) as error:
                    reader.observe("usb:fixture")
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertFalse(reader.cleanup_pending)
                self.assertEqual(native.created[0].calls[-1], "disconnect")

    def test_incoherent_topology_cannot_publish_capabilities(self):
        native = _Native()
        native.topology_serial = "other-radio"
        reader = PlutoReadOnlyObserver(native)
        with self.assertRaises(PlutoObservationError):
            reader.observe("usb:fixture")
        self.assertFalse(reader.cleanup_pending)
        self.assertEqual(native.created[0].calls[-1], "disconnect")

    def test_wrong_owned_uri_cannot_publish_successful_observation(self):
        native = _Native()
        create = native.PlutoDevice

        def wrong_uri(*args, **kwargs):
            device = create(*args, **kwargs)
            probe = device.probe
            def mismatched_probe():
                result = probe()
                result.uri = "usb:other"
                return result
            device.probe = mismatched_probe
            return device

        native.PlutoDevice = wrong_uri
        reader = PlutoReadOnlyObserver(native)
        with self.assertRaises(PlutoObservationError):
            reader.observe("usb:fixture")
        self.assertFalse(reader.cleanup_pending)
        self.assertEqual(native.created[0].calls[-1], "disconnect")

    def test_reader_retains_failed_close_until_explicit_retry(self):
        native = _Native()
        native.disconnect_error = True
        reader = PlutoReadOnlyObserver(native)
        with self.assertRaises(PlutoObservationError):
            reader.observe("usb:fixture")
        self.assertTrue(reader.cleanup_pending)
        with self.assertRaises(PlutoObservationError):
            reader.observe("ip:fixture.local")
        self.assertEqual(len(native.created), 1)
        self.assertEqual(native.created[0].calls.count("disconnect"), 1)
        native.created[0].disconnect_error = False
        reader.close()
        reader.close()
        self.assertFalse(reader.cleanup_pending)
        self.assertEqual(native.created[0].calls.count("disconnect"), 2)

    def test_live_discovery_failed_close_blocks_all_new_owners_until_stop(self):
        native = _Native()
        native.disconnect_error = True
        service = NativeLiveSessionService(native)
        with self.assertRaises(PlutoObservationError):
            service.discover_devices()
        self.assertEqual(len(native.created), 1)  # no second alias probe.
        with self.assertRaises(LiveAdmissionRejected):
            service.discover_devices()
        with self.assertRaises(LiveAdmissionRejected):
            service.select_manual_uri("ip:fixture.local")
        with self.assertRaises(LiveAdmissionRejected):
            service.start_admitted()
        with self.assertRaises(LiveAdmissionRejected):
            service.capability_inventory()
        self.assertEqual(service.start().state, LiveSessionState.ERROR)
        self.assertEqual(native.created[0].calls.count("disconnect"), 1)
        self.assertEqual(native.scans, ["usb,ip"])
        self.assertEqual(native.engines, [])
        with self.assertRaises(RuntimeError):
            service.stop()
        native.created[0].disconnect_error = False
        native.disconnect_error = False
        service.stop()
        self.assertFalse(service._observation_owner.cleanup_pending)
        self.assertFalse(service._stream_release_failed)
        self.assertEqual(len(service.discover_devices()), 1)
        service.close_live()

    def test_selection_failed_close_does_not_fall_back_or_implicitly_retry(self):
        native = _Native()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        native.disconnect_error = True
        opened = len(native.created)
        snapshot = service.select_device(device.device_id)
        self.assertEqual(snapshot.state, LiveSessionState.ERROR)
        self.assertEqual(len(native.created), opened + 1)
        self.assertTrue(service._observation_owner.cleanup_pending)
        with self.assertRaises(RuntimeError):
            service.acquire_native_sweep_lease()
        with self.assertRaises(LiveAdmissionRejected):
            service.select_device(device.device_id)
        native.created[-1].disconnect_error = False
        service.stop()
        self.assertFalse(service._observation_owner.cleanup_pending)

    def test_known_aliases_use_existing_inventory_without_additional_sdk_calls(self):
        native = _Native()
        service = NativeLiveSessionService(native)
        devices = service.discover_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(len(native.created), 2)
        inventory = service.capability_inventory()
        self.assertEqual(len(native.created), 2)
        self.assertEqual(len(inventory.snapshots), 1)
        snapshot = inventory.snapshots[0]
        self.assertIs(snapshot, devices[0].capability_snapshot)
        self.assertEqual(snapshot.family, DeviceFamily.AD936X)
        self.assertEqual(snapshot.transports, (CapabilityTransport.USB, CapabilityTransport.ETHERNET))
        self.assertEqual(snapshot.sample_rate_ranges_hz[0].maximum, 61.44e6)
        self.assertEqual(snapshot.rx_channel_count, 1)
        self.assertIsNone(snapshot.shared_rx_lo)

    def test_unknown_serial_routes_stay_operational_without_stable_inventory_claim(self):
        native = _Native(serial=None)
        service = NativeLiveSessionService(native)
        devices = service.discover_devices()
        self.assertEqual(len(devices), 2)
        self.assertEqual(service.capability_inventory().snapshots, ())
        self.assertTrue(all(device.capability_snapshot is None for device in devices))
        self.assertTrue(all(61.44e6 in device.capabilities.sample_rates_hz for device in devices))

    def test_old_or_invalid_observation_protocol_cannot_publish_stable_evidence(self):
        for protocol in (None, 0, True, "1", 1.0, 2):
            with self.subTest(protocol=protocol):
                native = _Native()
                native.PLUTO_OBSERVATION_PROTOCOL_VERSION = protocol
                service = NativeLiveSessionService(native)
                self.assertIsNone(service.discover_devices()[0].capability_snapshot)
                self.assertEqual(service.capability_inventory().snapshots, ())
                with self.assertRaises(Ad936xCapabilityObservationError):
                    Ad936xLibiioCapabilityAdapter(native).observe("usb:fixture")

    def test_conflicting_alias_capabilities_do_not_create_admitted_snapshot(self):
        native = _Native()
        native.conflicting_routes.add("ip:fixture.local")
        service = NativeLiveSessionService(native)
        devices = service.discover_devices()
        self.assertEqual(len(devices), 1)  # observed serial still proves route grouping.
        self.assertIsNone(devices[0].capability_snapshot)
        self.assertEqual(service.capability_inventory().snapshots, ())

    def test_duplicate_uri_is_observed_once_even_with_unknown_serial(self):
        native = _Native(serial=None)
        native.routes = ("usb:fixture", "usb:fixture")
        service = NativeLiveSessionService(native)
        self.assertEqual(len(service.discover_devices()), 1)
        self.assertEqual(len(native.created), 1)

    def test_oversized_scan_rejects_before_any_context_open(self):
        native = _Native()
        native.routes = tuple(f"usb:fixture{index}" for index in range(65))
        service = NativeLiveSessionService(native)
        with self.assertRaisesRegex(RuntimeError, "bounded route"):
            service.discover_devices()
        self.assertEqual(native.created, [])

    def test_oversized_logical_inventory_closes_all_probes_without_publication(self):
        native = _Native(serial=None)
        native.routes = tuple(f"usb:fixture{index}" for index in range(33))
        service = NativeLiveSessionService(native)
        with self.assertRaisesRegex(RuntimeError, "bounded logical device"):
            service.discover_devices()
        self.assertEqual(len(native.created), 33)
        self.assertTrue(all(device.calls[-1] == "disconnect" for device in native.created))
        self.assertFalse(service._observation_owner.cleanup_pending)
        self.assertEqual(service.capability_inventory().snapshots, ())
        self.assertEqual(service._devices, ())

    def test_discovery_while_engine_owned_rejects_before_scan(self):
        native = _Native()
        service = NativeLiveSessionService(native)
        service._engine = object()
        try:
            with self.assertRaises(LiveAdmissionRejected):
                service.discover_devices()
            self.assertEqual(native.scans, [])
            self.assertEqual(native.created, [])
        finally:
            service._engine = None

    def test_selection_refreshes_actual_ranges_instead_of_reusing_discovery_profile(self):
        native = _Native()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        native.conflicting_routes.add("usb:fixture")
        selected = service.select_device(device.device_id)
        self.assertEqual(selected.state, LiveSessionState.CONNECTED)
        self.assertEqual(selected.device.capability_snapshot.sample_rate_ranges_hz[0].maximum, 20e6)
        self.assertNotIn(61.44e6, selected.device.capabilities.sample_rates_hz)
        service.close_live()


if __name__ == "__main__":
    unittest.main()

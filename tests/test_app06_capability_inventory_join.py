"""Common inventory references existing facts; no SDR, native load or Qt."""

from __future__ import annotations

import unittest
from dataclasses import replace

from sdr_monitor.domain.device_capabilities import (
    AdapterRuntimeAvailability,
    AdapterRuntimeSnapshot,
    DeviceCalibrationIdentity,
    DeviceCapabilityBinding,
    DeviceFamily,
    build_device_capability_inventory,
    merge_device_capability_inventories,
    stable_identity_key,
)
from sdr_monitor.services.hackrf_capability_adapter import (
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfReadOnlyProbe,
)
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.tinysa_capability_adapter import TinySaCapabilityAdapter, TinySaModel, TinySaReadOnlyProbe
from tests.test_app06_pluto_observation_catalog import _Native
from tests.test_device_capability_contracts import _snapshot


def _identity(snapshot, firmware="fixture-fw"):
    return DeviceCalibrationIdentity(snapshot.family, snapshot.adapter_id, snapshot.identity_key,
                                     stable_identity_key(firmware))


def _binding(snapshot, source="source-one"):
    return DeviceCapabilityBinding(source, snapshot.family, snapshot.adapter_id, snapshot, _identity(snapshot))


class _ProbePort:
    def __init__(self, probe):
        self.value = probe
        self.calls = []

    def probe(self):
        self.calls.append("probe")
        return self.value

    def close(self):
        self.calls.append("close")


class CapabilityInventoryJoinTests(unittest.TestCase):
    def test_known_pluto_binds_unchanged_source_id_to_same_observed_canonical_facts(self):
        native = _Native()
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        opened = len(native.created)
        inventory = service.capability_inventory()
        binding = inventory.binding_for_source(device.device_id)
        self.assertIsNotNone(binding)
        self.assertIs(binding.snapshot, device.capability_snapshot)
        self.assertIs(binding.calibration_identity, device.calibration_identity)
        self.assertEqual(binding.identity_key, device.capability_snapshot.identity_key)
        self.assertNotEqual(device.identity_key, binding.identity_key)  # explicit join, not namespace rewriting
        self.assertEqual(binding.source_id, device.device_id)
        self.assertEqual(len(native.created), opened)
        self.assertEqual(native.engines, [])
        self.assertNotIn("fixture-a", repr(binding))
        self.assertNotIn("usb:fixture", repr(binding))

    def test_unknown_serial_is_visible_but_has_no_canonical_or_calibration_claim(self):
        native = _Native(serial=None)
        service = NativeLiveSessionService(native)
        devices = service.discover_devices()
        inventory = service.capability_inventory()
        self.assertEqual(len(inventory.bindings), 2)
        self.assertEqual(inventory.snapshots, ())
        for device in devices:
            binding = inventory.binding_for_source(device.device_id)
            self.assertIsNone(binding.snapshot)
            self.assertIsNone(binding.identity_key)
            self.assertIsNone(binding.calibration_identity)
        self.assertIsNone(inventory.binding_for_source("not-present"))

    def test_runtime_contract_availability_does_not_imply_stable_hardware_identity(self):
        native = _Native(serial=None)
        service = NativeLiveSessionService(native)
        service.discover_devices()
        inventory = service.capability_inventory()
        self.assertEqual(len(inventory.runtimes), 1)
        runtime = inventory.runtimes[0]
        self.assertIs(runtime.availability, AdapterRuntimeAvailability.AVAILABLE)
        self.assertEqual(runtime.family, DeviceFamily.AD936X)
        self.assertEqual(inventory.snapshots, ())
        self.assertIs(inventory.runtime_for_adapter(runtime.adapter_id), runtime)

    def test_missing_or_invalid_runtime_contract_is_not_hidden_by_valid_capability_facts(self):
        for name, value in (("PlutoFixedBandEngine", None), ("PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION", None),
                            ("PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION", True),
                            ("PLUTO_OBSERVATION_PROTOCOL_VERSION", 1.0)):
            with self.subTest(name=name, value=value):
                native = _Native()
                setattr(native, name, value)
                service = NativeLiveSessionService(native)
                service.discover_devices()
                opened = len(native.created)
                inventory = service.capability_inventory()
                self.assertIs(inventory.runtimes[0].availability, AdapterRuntimeAvailability.UNAVAILABLE)
                self.assertEqual(len(native.created), opened)
                self.assertEqual(native.engines, [])

    def test_cross_family_inventory_merge_preserves_typed_facts_and_units_without_probe(self):
        native = _Native()
        service = NativeLiveSessionService(native)
        service.discover_devices()
        hackrf_port = _ProbePort(HackrfReadOnlyProbe(HackrfBoardKind.HACKRF_ONE, (0, 0, 1, 2), "fw", 0x0107))
        tiny_port = _ProbePort(TinySaReadOnlyProbe(TinySaModel.ULTRA, "unit-one", "fw"))
        hackrf = HackrfCapabilityAdapter(lambda: hackrf_port).observe()
        tiny = TinySaCapabilityAdapter(lambda: tiny_port).observe()
        inventories = [service.capability_inventory()]
        for observed in (hackrf, tiny):
            snapshot = observed.snapshot
            identity = observed.external_correction_identity if observed is tiny else observed.calibration_identity
            binding = DeviceCapabilityBinding(snapshot.device_id, snapshot.family, snapshot.adapter_id,
                                              snapshot, identity)
            inventories.append(build_device_capability_inventory((snapshot,), bindings=(binding,)))
        opened = len(native.created)
        result = merge_device_capability_inventories(inventories)
        self.assertEqual(len(result.snapshots), 3)
        self.assertEqual(len(result.bindings), 3)
        self.assertIs(result.snapshots[1], hackrf.snapshot)
        self.assertIs(result.snapshots[2], tiny.snapshot)
        self.assertFalse(tiny.snapshot.raw_iq_available)
        self.assertEqual(tiny.analyzer_semantics.reported_unit, "dBm")
        self.assertIsNone(result.runtime_for_adapter(hackrf.snapshot.adapter_id))
        self.assertIsNone(result.runtime_for_adapter(tiny.snapshot.adapter_id))
        self.assertEqual(hackrf_port.calls, ["probe", "close"])
        self.assertEqual(tiny_port.calls, ["probe", "close"])
        self.assertEqual(len(native.created), opened)

    def test_identity_and_adapter_mismatch_cannot_create_a_binding(self):
        snapshot = _snapshot()
        for identity in (replace(_identity(snapshot), device_identity_key=stable_identity_key("other")),
                         replace(_identity(snapshot), adapter_id="other.adapter"),
                         replace(_identity(snapshot), family=DeviceFamily.TINYSA)):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                DeviceCapabilityBinding("source-one", snapshot.family, snapshot.adapter_id, snapshot, identity)
        with self.assertRaises(ValueError):
            DeviceCapabilityBinding("source-one", DeviceFamily.TINYSA, snapshot.adapter_id, snapshot, _identity(snapshot))
        with self.assertRaises(ValueError):
            DeviceCapabilityBinding("source-one", snapshot.family, snapshot.adapter_id, snapshot, None)
        with self.assertRaises(ValueError):
            DeviceCapabilityBinding("source-one", snapshot.family, snapshot.adapter_id, None, _identity(snapshot))

    def test_routes_controls_and_whitespace_cannot_become_catalog_source_ids(self):
        for value in ("usb:1.2", "ip:192.0.2.1", "manual:route", "source\n", " source", "C:\\private", "a/b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _binding(_snapshot(), value)

    def test_orphan_and_conflicting_capability_facts_are_rejected(self):
        snapshot = _snapshot()
        with self.assertRaises(ValueError):
            build_device_capability_inventory((), bindings=(_binding(snapshot),))
        with self.assertRaises(ValueError):
            build_device_capability_inventory((snapshot,), bindings=(_binding(replace(snapshot, label="different")),))
        with self.assertRaises(ValueError):
            build_device_capability_inventory((snapshot,), bindings=(_binding(snapshot), _binding(snapshot)))

    def test_same_physical_key_cannot_hide_conflicting_firmware_across_alias_bindings(self):
        snapshot = _snapshot()
        first = _binding(snapshot)
        other = replace(first, source_id="source-two", calibration_identity=_identity(snapshot, "other-fw"))
        with self.assertRaises(ValueError):
            build_device_capability_inventory((snapshot,), bindings=(first, other))

    def test_runtime_observation_is_independent_bounded_and_never_hardware_proof(self):
        snapshot = _snapshot()
        runtime = AdapterRuntimeSnapshot(snapshot.adapter_id, snapshot.family,
                                         AdapterRuntimeAvailability.UNKNOWN)
        inventory = build_device_capability_inventory((snapshot,), runtimes=(runtime,))
        self.assertIs(inventory.runtime_for_adapter(snapshot.adapter_id).availability,
                      AdapterRuntimeAvailability.UNKNOWN)
        with self.assertRaises(ValueError):
            replace(runtime, availability=AdapterRuntimeAvailability.AVAILABLE)
        with self.assertRaises(ValueError):
            build_device_capability_inventory((snapshot,), runtimes=(runtime, runtime))
        with self.assertRaises(ValueError):
            build_device_capability_inventory((snapshot,), runtimes=(replace(runtime, family=DeviceFamily.TINYSA),))

    def test_merge_is_exact_idempotent_and_rejects_conflicts_instead_of_last_wins(self):
        snapshot = _snapshot()
        inventory = build_device_capability_inventory((snapshot,), bindings=(_binding(snapshot),))
        self.assertEqual(merge_device_capability_inventories((inventory, inventory)), inventory)
        different = build_device_capability_inventory((replace(snapshot, label="changed"),))
        with self.assertRaises(ValueError):
            merge_device_capability_inventories((inventory, different))
        runtime = AdapterRuntimeSnapshot(snapshot.adapter_id, snapshot.family,
                                         AdapterRuntimeAvailability.AVAILABLE, "fixture-contract")
        before = build_device_capability_inventory((snapshot,), runtimes=(runtime,))
        after = build_device_capability_inventory((snapshot,), runtimes=(replace(runtime, reference="other-contract"),))
        with self.assertRaises(ValueError):
            merge_device_capability_inventories((before, after))

    def test_existing_inventory_constructor_stays_source_only_and_finite(self):
        snapshot = _snapshot()
        inventory = build_device_capability_inventory((snapshot,))
        self.assertEqual(inventory.bindings, ())
        self.assertEqual(inventory.runtimes, ())
        with self.assertRaises(ValueError):
            build_device_capability_inventory((snapshot,), bindings=(_binding(snapshot, "first"),
                                                                     _binding(snapshot, "second")), maximum_devices=1)

    def test_alias_firmware_conflict_is_not_hidden_by_equal_range_snapshots(self):
        native = _Native()
        _with_firmware(native, {"usb:fixture": "fw-one", "ip:fixture.local": "fw-two"})
        service = NativeLiveSessionService(native)
        devices = service.discover_devices()
        self.assertEqual(len(devices), 1)  # observed serial can group routes, not their firmware facts
        self.assertIsNone(devices[0].capability_snapshot)
        self.assertIsNone(devices[0].calibration_identity)
        inventory = service.capability_inventory()
        self.assertEqual(inventory.snapshots, ())
        self.assertIsNone(inventory.bindings[0].identity_key)

    def test_selection_refreshes_firmware_join_without_mutating_prior_inventory(self):
        native = _Native()
        native.routes = ("usb:fixture",)
        firmware = {"usb:fixture": "fw-one"}
        _with_firmware(native, firmware)
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        before = service.capability_inventory().binding_for_source(device.device_id)
        firmware["usb:fixture"] = "fw-two"
        selected = service.select_device(device.device_id)
        after = service.capability_inventory().binding_for_source(device.device_id)
        self.assertEqual(before.source_id, after.source_id)
        self.assertEqual(before.identity_key, after.identity_key)
        self.assertNotEqual(before.calibration_identity.firmware_fingerprint,
                            after.calibration_identity.firmware_fingerprint)
        self.assertIs(after.calibration_identity, selected.device.calibration_identity)
        service.close_live()

    def test_inventory_consumption_is_bounded_even_for_repeating_generators(self):
        observed = []
        def values():
            while True:
                observed.append(1)
                yield _snapshot()
        with self.assertRaises(ValueError):
            build_device_capability_inventory(values(), maximum_devices=2)
        self.assertEqual(len(observed), 3)
        observed.clear()
        with self.assertRaises(TypeError):
            build_device_capability_inventory(values(), maximum_devices=True)
        self.assertEqual(observed, [])


def _with_firmware(native, firmware):
    create = native.PlutoDevice
    def factory(*args, **kwargs):
        port = create(*args, **kwargs)
        for name in ("probe", "capabilities", "receiver_topology"):
            original = getattr(port, name)
            def read(original=original, name=name):
                result = original()
                (result.context if name == "receiver_topology" else result).firmware = firmware[port.uri]
                return result
            setattr(port, name, read)
        return port
    native.PlutoDevice = factory


if __name__ == "__main__":
    unittest.main()

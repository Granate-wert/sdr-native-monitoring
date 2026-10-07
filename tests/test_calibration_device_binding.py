"""Admitted source/canonical joins and strict refusal; no physical hardware."""
from dataclasses import replace
from pathlib import Path
from enum import Enum
from types import SimpleNamespace
import tempfile
import unittest

from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.domain.device_capabilities import DeviceCapabilityBinding
from sdr_monitor.services.calibration_device_binding import confirmed_calibration_device_binding
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.services.receiver_calibration import ReceiverCalibrationRegistry
from sdr_monitor.services.live_calibration_signature import build_live_calibration_signature
from sdr_monitor.services.native_live import _observed_single_receiver
from tests import test_live_calibration_signature as fixtures


class CalibrationDeviceBindingTests(unittest.TestCase):
    def setUp(self):
        self.facts = fixtures.LiveCalibrationSignatureTests()
        self.facts.setUp()
        self.original = self.facts.device
        self.receipt = DeviceCapabilityBinding('explicit-operational-source',
            self.original.calibration_identity.family, self.original.calibration_identity.adapter_id,
            self.original.capability_snapshot, self.original.calibration_identity)
        self.device = replace(self.original, device_id=self.receipt.source_id, capability_binding=self.receipt)
        self.endpoint = replace(self.facts.endpoint, source_id=self.receipt.source_id)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.registry = ReceiverCalibrationRegistry(CalibrationProfileStore(Path(folder.name)))

    def test_exact_receipt_preserves_both_ids_signature_and_scope(self):
        self.assertIs(confirmed_calibration_device_binding(self.device), self.receipt)
        self.assertNotEqual(self.device.device_id, self.device.capability_snapshot.device_id)
        signature = build_live_calibration_signature(self.device, self.endpoint, self.facts.applied,
            self.facts.provenance, self.facts.frontend, unit='dBFS/bin')
        self.assertEqual(signature, self.facts.build())
        service = self.registry.for_device(self.device, self.endpoint)
        self.assertIs(service, self.registry.for_device(self.device, replace(self.endpoint, endpoint_id='other-pane')))
        self.assertIsNone(service.active_profile())

    def test_distinct_ids_without_receipt_and_foreign_endpoint_refuse(self):
        missing = replace(self.device, capability_binding=None)
        for device, endpoint in ((missing, self.endpoint),
                                 (self.device, replace(self.endpoint, source_id=self.original.device_id))):
            with self.subTest(device=device.device_id, source=endpoint.source_id):
                with self.assertRaises(CalibrationProfileError):
                    self.registry.for_device(device, endpoint)
                with self.assertRaises(CalibrationProfileError):
                    build_live_calibration_signature(device, endpoint, self.facts.applied,
                        self.facts.provenance, self.facts.frontend, unit='dBFS/bin')

    def test_foreign_or_stale_receipt_rejected_before_registry(self):
        for changes in (
            dict(device_id='another-source'),
            dict(capability_snapshot=replace(self.device.capability_snapshot, device_id='another-canonical')),
            dict(calibration_identity=replace(self.device.calibration_identity,
                firmware_fingerprint='sha256:' + '3' * 64)),
            dict(capability_binding=replace(self.receipt, source_id='another-source')),
            dict(capability_binding=replace(self.receipt,
                snapshot=replace(self.receipt.snapshot, device_id='another-canonical'))),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.device, **changes)

    def test_changed_source_or_firmware_requires_new_scope_without_selection(self):
        first = self.registry.for_device(self.device, self.endpoint)
        identity = replace(self.device.calibration_identity, firmware_fingerprint='sha256:' + '4' * 64)
        receipt = replace(self.receipt, calibration_identity=identity)
        updated = replace(self.device, calibration_identity=identity, capability_binding=receipt)
        self.assertIsNot(first, self.registry.for_device(updated, self.endpoint))
        receipt = replace(self.receipt, source_id='other-operational-source')
        updated = replace(self.device, device_id=receipt.source_id, capability_binding=receipt)
        self.assertIsNot(first, self.registry.for_device(updated, replace(self.endpoint, source_id=receipt.source_id)))

    def test_receiver_metadata_uses_exact_applied_enum_not_name_or_default(self):
        class Selection(Enum):
            RX1 = 1
            RX2 = 2
            BOTH = 3

        native = SimpleNamespace(PlutoReceiverSelection=Selection)
        for selected in (Selection.RX1, Selection.RX2):
            self.assertEqual(_observed_single_receiver(native, SimpleNamespace(receiver_selection=selected)),
                             selected.name)
        for value in (None, 'RX1', 1, Selection.BOTH, SimpleNamespace(name='RX1')):
            with self.subTest(value=value):
                self.assertIsNone(_observed_single_receiver(native, SimpleNamespace(receiver_selection=value)))
        self.assertIsNone(_observed_single_receiver(SimpleNamespace(), SimpleNamespace(receiver_selection=Selection.RX1)))


if __name__ == '__main__':
    unittest.main()

"""Typed RX selections and real service-root assembly; synthetic identity only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
from sdr_monitor.services.calibration_service import CalibrationService
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.services.receiver_calibration import ReceiverCalibrationRegistry
from sdr_monitor.services.sdr_application_services import SdrApplicationServices, UnavailableLiveService
from tests import test_current_frame_calibration as fixtures
from tests.test_live_calibration_owner import SnapshotPort
from unittest.mock import patch


class ReceiverCalibrationRegistryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CurrentFrameCalibrationTests()
        self.fixture.setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = CalibrationProfileStore(Path(folder.name))
        self.registry = ReceiverCalibrationRegistry(self.store)
        self.device, self.endpoint = self.fixture.facts.device, self.fixture.facts.endpoint

    def test_rx1_rx2_independent_and_same_rx_pane_labels_share(self):
        one = self.registry.for_device(self.device, self.endpoint)
        two = self.registry.for_device(self.device, replace(self.endpoint, selection=ReceiverChainSelection.RX2))
        self.assertIsNot(one, two)
        self.assertIs(one.store, two.store)
        self.assertIs(one, self.registry.for_device(self.device, replace(self.endpoint, endpoint_id='another-pane')))
        profile = self.fixture.profile(self.fixture.signature())
        one.set_current_settings(profile.signature)
        one.select_active_profile(profile)
        self.assertIsNone(two.active_profile())
        two.clear_active_profile()
        self.assertIs(one.active_profile(), profile)

    def test_changed_firmware_or_admitted_device_identity_never_inherits_activation(self):
        one = self.registry.for_device(self.device, self.endpoint)
        profile = self.fixture.profile(self.fixture.signature())
        one.set_current_settings(profile.signature)
        one.select_active_profile(profile)
        device = replace(self.device, calibration_identity=replace(
            self.device.calibration_identity, firmware_fingerprint='sha256:' + '3' * 64))
        newer = self.registry.for_device(device, self.endpoint)
        self.assertIsNot(one, newer)
        self.assertIsNone(newer.active_profile())
        identity = 'sha256:' + '4' * 64
        device = replace(self.device, calibration_identity=replace(self.device.calibration_identity,
            device_identity_key=identity), capability_snapshot=replace(self.device.capability_snapshot, identity_key=identity))
        self.assertIsNone(self.registry.for_device(device, self.endpoint).active_profile())

    def test_wrong_identity_resource_unknown_and_both_refuse(self):
        for endpoint in (replace(self.endpoint, selection=ReceiverChainSelection.BOTH),
                         replace(self.endpoint, source_id='other'), replace(self.endpoint, physical_stream_resource_id='other')):
            with self.assertRaises(CalibrationProfileError):
                self.registry.for_device(self.device, endpoint)
        with self.assertRaises(CalibrationProfileError):
            self.registry.for_device(replace(self.device, calibration_identity=None), self.endpoint)
        # The descriptor itself rejects inconsistent identity before registry lookup.
        with self.assertRaises(ValueError):
            replace(self.device, calibration_identity=replace(self.device.calibration_identity,
                device_identity_key='sha256:' + '4' * 64))

    def test_release_invalidates_old_receipt_and_reopen_is_unselected(self):
        one = self.registry.for_device(self.device, self.endpoint)
        result = one.correct_current_spectrum(self.fixture.current, self.endpoint, self.fixture.facts.frontend)
        self.registry.release(self.device, self.endpoint)
        self.assertFalse(one.is_current_selection(result))
        newer = self.registry.for_device(self.device, self.endpoint)
        self.assertIsNot(newer, one)
        self.assertIsNone(newer.active_profile())

    def test_concurrent_lookup_has_one_selection_owner(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: self.registry.for_device(self.device, self.endpoint), range(16)))
        self.assertTrue(all(service is results[0] for service in results))

    def test_capacity_refuses_without_silent_eviction(self):
        registry = ReceiverCalibrationRegistry(self.store, max_scopes=1)
        one = registry.for_device(self.device, self.endpoint)
        with self.assertRaises(CalibrationProfileError):
            registry.for_device(self.device, replace(self.endpoint, selection=ReceiverChainSelection.RX2))
        self.assertIs(one, registry.for_device(self.device, self.endpoint))
        registry.clear()
        self.assertIsNot(one, registry.for_device(self.device, self.endpoint))

    def test_real_service_root_shares_storage_not_legacy_activation(self):
        calibration = CalibrationService(self.store)
        root = SdrApplicationServices(live_sdr=UnavailableLiveService(), calibration=calibration)
        self.assertIsNotNone(root.receiver_calibration)
        scoped = root.receiver_calibration.for_device(self.device, self.endpoint)
        self.assertIs(scoped.store, calibration.store)
        self.assertIsNot(scoped, calibration)
        self.assertFalse(root.live_sdr.is_running())

    def test_application_registry_pull_and_scope_release_during_calculation(self):
        from sdr_monitor.services import calibration_service as module
        owner = LiveSessionApplicationService(SnapshotPort(self.fixture.current))
        service = self.registry.for_device(self.device, self.endpoint)
        profile = self.fixture.profile(self.fixture.signature())
        service.set_current_settings(profile.signature)
        service.select_active_profile(profile)
        result = owner.calibrated_receiver_spectrum(self.registry, self.endpoint, self.fixture.facts.frontend)
        self.assertEqual(result.result.unit, 'dBm/bin')
        original = module.apply_calibration

        def retired(*args, **kwargs):
            self.registry.release(self.device, self.endpoint)
            # Even reactivating a retained old service does not resurrect registry membership.
            service.select_active_profile(profile)
            return original(*args, **kwargs)

        with patch.object(module, 'apply_calibration', side_effect=retired):
            with self.assertRaises(CalibrationProfileError):
                owner.calibrated_receiver_spectrum(self.registry, self.endpoint, self.fixture.facts.frontend)
        self.assertFalse(self.registry.is_current_service(self.device, self.endpoint, service))

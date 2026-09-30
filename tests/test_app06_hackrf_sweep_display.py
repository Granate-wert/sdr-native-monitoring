"""Pure lifecycle tests for the optional native HackRF Sweep display owner."""

import unittest
from copy import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import (
    AcquisitionKind,
    AdapterRuntimeAvailability,
    AdapterRuntimeSnapshot,
    CapabilityEvidence,
    CapabilityEvidenceOrigin,
    CapabilityField,
    CapabilityRange,
    CapabilityTransport,
    DeviceCalibrationIdentity,
    DeviceCapabilityBinding,
    DeviceCapabilityInventory,
    DeviceCapabilitySnapshot,
    DeviceFamily,
    stable_identity_key,
)
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.services.hackrf_activation_preflight import HackrfRuntimeIdentityProbe, _identity_key
from sdr_monitor.services.hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID, HackrfBoardKind
from sdr_monitor.services.hackrf_sweep_display import HackrfSweepDisplayService
from sdr_monitor.services.source_capability_admission import SourceRequestAdmissionReason, admit_source_request

SERIAL = (1, 2, 3, 4)
IDENTITY = _identity_key(HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, SERIAL))


def setup_owner():
    runtime = AdapterRuntimeSnapshot(HACKRF_LIBHACKRF_ADAPTER_ID, DeviceFamily.HACKRF,
                                     AdapterRuntimeAvailability.AVAILABLE, "fixture")
    snapshot = DeviceCapabilitySnapshot(
        "radio-1", IDENTITY, "HackRF fixture", DeviceFamily.HACKRF,
        HACKRF_LIBHACKRF_ADAPTER_ID, (CapabilityTransport.USB,),
        (AcquisitionKind.COMPLEX_IQ,), (CapabilityRange(1e6, 6e9, "Hz"),),
        raw_iq_available=True, model_id="hackrf_one",
        evidence=(
            CapabilityEvidence(CapabilityField.MODEL, CapabilityEvidenceOrigin.VENDOR_DECLARATION, "fixture"),
            CapabilityEvidence(CapabilityField.TRANSPORT, CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY, "fixture"),
            CapabilityEvidence(CapabilityField.ACQUISITION_KIND, CapabilityEvidenceOrigin.VENDOR_DECLARATION, "fixture"),
            CapabilityEvidence(CapabilityField.TUNING_RANGE, CapabilityEvidenceOrigin.VENDOR_DECLARATION, "fixture"),
            CapabilityEvidence(CapabilityField.RAW_IQ, CapabilityEvidenceOrigin.VENDOR_DECLARATION, "fixture"),
        ))
    calibration = DeviceCalibrationIdentity(DeviceFamily.HACKRF, HACKRF_LIBHACKRF_ADAPTER_ID,
                                             IDENTITY, stable_identity_key("firmware"))
    binding = DeviceCapabilityBinding("radio-1", DeviceFamily.HACKRF,
                                      HACKRF_LIBHACKRF_ADAPTER_ID, snapshot, calibration)
    inventory = DeviceCapabilityInventory((snapshot,), bindings=(binding,), runtimes=(runtime,))
    choice = AnalyzerSourceChoice(binding, runtime, "HackRF fixture", "USB")
    selection = AnalyzerSourceSelection(9, (choice,), choice.device_id)
    request = HackrfSweepRequest(choice, 9, 100_000_000, 120_000_000, 2048, 16, 20, 10, epoch=1)
    control = SimpleNamespace(poll_next_publication=Mock(return_value=None), metrics=Mock(return_value={
        "worker_failed": False, "terminal_superseded": 0, "progress_superseded": 0,
        "progress_pending": 0, "terminal_pending": 0,
        "completed_lines": 0, "gapped_lines": 0}),
        stop=Mock(return_value={"complete": True}))
    native = SimpleNamespace(HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION=1,
        HACKRF_SWEEP_FACTORY_CONTRACT_VERSION=1,
        create_hackrf_sweep_runtime_control=Mock(return_value=control))
    manifest = {"hackrf_sweep_bridge_contract_version": 1,
                "hackrf_sweep_factory_contract_version": 1}
    class Identity:
        def probe(self):
            return HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, SERIAL)
        def close(self):
            pass
    class Catalog:
        def snapshot(self):
            return inventory
    class Exclusion:
        def __init__(self): self.events = []
        def claim_external_analyzer_rx(self, owner): self.events.append(("claim", owner))
        def release_external_analyzer_rx(self, owner): self.events.append(("release", owner))
    exclusion = Exclusion()
    service = HackrfSweepDisplayService(native, Catalog(), exclusion, Identity, manifest)
    return service, native, control, exclusion, request, selection


class HackrfSweepDisplayTests(unittest.TestCase):
    def test_completed_pass_rate_is_retained_between_native_progress_polls(self):
        service, _, control, _, request, selection = setup_owner()
        service.start(request, selection)
        counter = control.metrics.return_value
        with patch("sdr_monitor.services.hackrf_sweep_display.time.monotonic",
                   side_effect=(0.0, 0.2, 0.3, 1.3)):
            self.assertEqual(service.poll_latest().metrics.completed_line_lps, 0.0)
            counter["completed_lines"] = 4
            self.assertEqual(service.poll_latest().metrics.completed_line_lps, 20.0)
            self.assertEqual(service.poll_latest().metrics.completed_line_lps, 20.0)
            self.assertEqual(service.poll_latest().metrics.completed_line_lps, 0.0)
        service.stop()

    def test_duplicate_progress_still_refreshes_counter_and_stale_rate(self):
        service, _, control, _, request, selection = setup_owner()
        service.start(request, selection)
        counter = control.metrics.return_value
        service._last_progress = (1, 1)
        control.poll_next_publication.return_value = SimpleNamespace(
            source_id=request.source.device_id, epoch=request.epoch,
            unit="dBFS/bin", line_sequence=1, revision=1)
        with patch("sdr_monitor.services.hackrf_sweep_display.time.monotonic",
                   side_effect=(0.0, 0.2, 0.3, 1.3)):
            self.assertEqual(service.poll_latest().metrics.completed_line_lps, 0.0)
            counter["completed_lines"] = 4
            self.assertEqual(service.poll_latest().metrics.completed_line_lps, 20.0)
            self.assertEqual(service.poll_latest().metrics.completed_line_lps, 20.0)
            self.assertEqual(service.poll_latest().metrics.completed_line_lps, 0.0)
        service.stop()

    def test_start_poll_stop_and_retained_terminal_poll_contract(self):
        service, native, control, exclusion, request, selection = setup_owner()
        service.start(request, selection)
        native.create_hackrf_sweep_runtime_control.assert_called_once_with(
            SERIAL, "radio-1", request.epoch, request.fft_size, 100, 120, 16, 20)
        self.assertEqual([x[0] for x in exclusion.events], ["claim"])
        self.assertIsNotNone(service.poll_latest())
        service.stop()
        self.assertEqual([x[0] for x in exclusion.events], ["claim", "release"])
        control.stop.assert_called_once_with(5000)
        service.poll_latest()  # stopped control remains available for terminal drain

    def test_stale_selection_refuses_before_identity_or_factory(self):
        service, native, _, exclusion, request, _ = setup_owner()
        stale = AnalyzerSourceSelection(10)
        with self.assertRaises(LiveAdmissionRejected):
            service.start(request, stale)
        native.create_hackrf_sweep_runtime_control.assert_not_called()
        self.assertEqual(exclusion.events, [])

    def test_failed_stop_keeps_external_rx_claim(self):
        service, _, control, exclusion, request, selection = setup_owner()
        service.start(request, selection)
        control.stop.return_value = {"complete": False}
        with self.assertRaises(RuntimeError):
            service.stop()
        self.assertEqual([x[0] for x in exclusion.events], ["claim"])

    def test_missing_paired_contract_refuses_before_observation_or_claim(self):
        service, native, _, exclusion, request, selection = setup_owner()
        service._manifest["hackrf_sweep_factory_contract_version"] = 0
        with self.assertRaises(LiveAdmissionRejected):
            service.start(request, selection)
        native.create_hackrf_sweep_runtime_control.assert_not_called()
        self.assertEqual(exclusion.events, [])

    def test_unassigned_epoch_refuses_before_identity_or_rx_claim(self):
        service, native, _, exclusion, request, selection = setup_owner()
        with self.assertRaisesRegex(LiveAdmissionRejected, "positive epoch"):
            service.start(replace(request, epoch=0), selection)
        native.create_hackrf_sweep_runtime_control.assert_not_called()
        self.assertEqual(exclusion.events, [])

    def test_pure_capability_admission_needs_explicit_runtime_and_exact_range(self):
        service, _, _, _, request, _ = setup_owner()
        inventory = service._catalog.snapshot()
        self.assertIs(admit_source_request(inventory, request.source.device_id, "sweep", request).reason,
                      SourceRequestAdmissionReason.MODE_RUNTIME_UNAVAILABLE)
        self.assertTrue(admit_source_request(inventory, request.source.device_id, "sweep", request,
                                             hackrf_sweep_runtime_available=True).accepted)
        # A foreign caller can forge a frozen payload; capability admission
        # must still reject it even though normal construction now refuses.
        forged = copy(request)
        object.__setattr__(forged, "start_hz", 6_000_000_000)
        object.__setattr__(forged, "stop_hz", 6_020_000_000)
        self.assertIs(admit_source_request(inventory, request.source.device_id, "sweep", forged,
                       hackrf_sweep_runtime_available=True).reason,
                      SourceRequestAdmissionReason.REQUEST_RANGE)

    def test_new_factory_failure_cannot_stop_old_control_or_release_new_claim(self):
        service, native, old_control, exclusion, request, selection = setup_owner()
        service.start(request, selection)
        service.stop()
        native.create_hackrf_sweep_runtime_control.side_effect = RuntimeError("private SDK failure")
        with self.assertRaisesRegex(RuntimeError, "explicit Stop required"):
            service.start(replace(request, epoch=2), selection)
        with self.assertRaisesRegex(RuntimeError, "unresolved"):
            service.stop()
        old_control.stop.assert_called_once_with(5000)
        self.assertEqual([event for event, _ in exclusion.events], ["claim", "release", "claim"])


if __name__ == "__main__":
    unittest.main()

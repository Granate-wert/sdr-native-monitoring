"""HOST composition/negative tests; separately labelled actual native MOCK RTL.

No physical devices, Qt, SDK fallback or RF-accuracy qualification.
"""

import ctypes
from dataclasses import fields, replace
import hashlib
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analytical_journal import JournalCounters, JournalState, OwnerJournalScope, OwnerJournalSnapshot
from sdr_monitor.domain.analytical_ready import DetectorReadyReceipt, ReadyClockMapping
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceCapabilityBinding, DeviceFamily
from sdr_monitor.domain.live import LiveAdmissionRejected, LiveSessionState, LiveSnapshot, LiveSpectrumFrame
from sdr_monitor.domain.processing_policy import DC_REMOVED_MASK, DspProcessingRecipeObservationV1, HostDcMode, SdrProcessingPolicyV1
from sdr_monitor.domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
from sdr_monitor.domain.source_processing import RfProcessingValue, RfValueAuthority
from sdr_monitor.domain.spectrum_provenance import SpectrumProvenance
from sdr_monitor.services.hackrf_native_factory import HackrfNativeFactoryError, HackrfNativeRuntimeFactory
from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
from sdr_monitor.services.rtl_capability_provider import RTL_ADAPTER_ID, RTL_SOURCE_ID, RtlCapabilityProvider, RtlRuntimeProvision
from sdr_monitor.services.source_processing import SourceProcessingJoin, admit_source_processing
from tests.test_app06_hackrf_burst_budget import request as hf_request
from tests.test_app06_hackrf_dsp_profile import permit
from tests import test_app07_rtl_product_route as rtl_fixture
from tests.test_app07_rtl_product_route import _Exclusion
from tests.test_r11n_hackrf_native_factory import _NativeFactory


BLOCK = SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN)


def factory_contract(family):
    return {"schema_version": 1, "family": "hackrf" if family == "hackrf" else "rtl-sdr",
        "scope": "native_rtbw_factory_only", "dc_modes": ("off", "block_mean_v1"),
        "argument": "dc_removal_block_mean", "default_off": True, "full_owner_context": False}


def composition(family="hackrf", policy=BLOCK):
    request = (hf_request(processing_policy=policy) if family == "hackrf"
               else RtlLiveRequest(100_000_000, 2_400_000, processing_policy=policy))
    choice = AnalyzerSourceChoice(DeviceCapabilityBinding(str(request.source_id), DeviceFamily(family),
        "mock-contract-only"), None, "MOCK-only", "MOCK-only")
    current = LiveSnapshot(1, 0, LiveSessionState.RUNNING, source_choice=choice, selection_revision=1,
        hackrf_request=request if family == "hackrf" else None,
        rtl_request=request if family == "rtl_sdr" else None,
        session_id="mock-session", acquisition_epoch=9, active_source_id=request.source_id,
        active_config_generation=1, clock_domain="host_steady_ns")
    ready = DetectorReadyReceipt("mock-clock", 1, 4, 1, 1, 1, request.source_id, None, 9,
        "mock-session", ReadyClockMapping.OUTSIDE_SAMPLES, owner_run_id="mock-run")
    scope = OwnerJournalScope("mock-clock", 1, "mock-run", request.source_id, None, "mock-session", 1, 9)
    counts = {f.name: 0 for f in fields(JournalCounters)}
    counts.update(producer_instance_id=4, offered=1, handed_off=1, events_generated=2, events_drained=2,
        event_capacity=4096, event_storage_bytes=4096 * 48, event_contract_version=1)
    journal = OwnerJournalSnapshot(scope, JournalState.ACTIVE, JournalCounters(**counts))
    frame = SimpleNamespace(source=SimpleNamespace(source_id=request.source_id,
        backend_id="native.libhackrf.rx.v1" if family == "hackrf" else "native.librtlsdr.unbundled.cpu.v1"),
        config_generation=1, center_frequency_hz=request.center_frequency_hz, sample_rate_hz=request.sample_rate_hz,
        fft_size=request.fft_size, hop_size=request.hop_size, quality_flags=DC_REMOVED_MASK | (1 << 31),
        frequencies_hz=request.center_frequency_hz - request.sample_rate_hz / 2
            + np.arange(request.fft_size) * request.sample_rate_hz / request.fft_size,
        values=np.zeros(request.fft_size, dtype=np.float32))
    provenance = SpectrumProvenance(window=request.window, detector=request.detector,
        precision_mode="reference_f64", calibration_status="uncalibrated",
        averaging_frames=getattr(request, "averaging_frames", 1), window_normalization_version="power-norm-v1",
        processing_recipe=DspProcessingRecipeObservationV1(policy.dc_mode))
    return frame, current, provenance, ready, journal


class SourceProcessingTests(unittest.TestCase):
    def test_requests_default_off_immutable_and_replacement_retains_policy(self):
        for request in (hf_request(), RtlLiveRequest(100_000_000, 2_400_000)):
            self.assertTrue(request.processing_policy.is_off)
            self.assertEqual(replace(replace(request, processing_policy=BLOCK),
                configuration_generation=8).processing_policy, BLOCK)
            for bad in (None, "block_mean_v1", {}, True):
                with self.subTest(value=bad), self.assertRaises(ValueError):
                    replace(request, processing_policy=bad)

    def test_exact_family_handshake_and_legacy_off_no_call(self):
        for family, name in (("hackrf", "hackrf_processing_factory_contract"),
                             ("rtl_sdr", "rtl_processing_factory_contract")):
            native = SimpleNamespace()
            self.assertFalse(admit_source_processing(native, family, SdrProcessingPolicyV1()))
            with self.assertRaises(LiveAdmissionRejected):
                admit_source_processing(native, family, BLOCK)
            expected = factory_contract(family)
            for key, bad in (("schema_version", True), ("family", "ad936x"), ("dc_modes", ["off", "block_mean_v1"]),
                             ("full_owner_context", True), ("default_off", 1), ("argument", "dc")):
                setattr(native, name, lambda key=key, bad=bad: {**expected, key: bad})
                with self.subTest(family=family, key=key), self.assertRaises(LiveAdmissionRejected):
                    admit_source_processing(native, family, BLOCK)
            setattr(native, name, lambda: expected)
            self.assertTrue(admit_source_processing(native, family, BLOCK))
            with self.assertRaises(LiveAdmissionRejected):
                admit_source_processing(native, family, SdrProcessingPolicyV1(compare_raw=True))

    def test_hackrf_gate_precedes_permit_consume_and_optional_keyword_only_for_blockmean(self):
        native = _NativeFactory()
        current = permit(hf_request(processing_policy=BLOCK))
        factory = HackrfNativeRuntimeFactory(lambda: native)
        with self.assertRaises(HackrfNativeFactoryError):
            factory.create(current)
        self.assertEqual(native.calls, [])
        native.hackrf_processing_factory_contract = lambda: factory_contract("hackrf")
        factory.create(current)
        self.assertIs(native.calls[0]["dc_removal_block_mean"], True)
        with self.assertRaises(HackrfNativeFactoryError):
            factory.create(current)
        factory.create(permit(hf_request()))
        self.assertNotIn("dc_removal_block_mean", native.calls[1])

    def test_rtl_actual_provision_not_catalog_gate_refuses_before_claim_or_open(self):
        native, provider, inventory, selection = rtl_fixture.RtlProductRouteTests()._selected()
        exclusion = _Exclusion()
        native.rtl_processing_factory_contract = lambda: factory_contract("rtl_sdr")
        provision = replace(provider.provision_for(selection.selected.binding), native=SimpleNamespace())
        service = RtlAnalyzerService(native, exclusion, lambda: inventory, lambda *_args: provision)
        service.bind_selection(selection)
        with self.assertRaises(LiveAdmissionRejected):
            service.stage(RtlConfigurationPatch(RtlLiveRequest(100_000_000, 2_400_000,
                source_id=RTL_SOURCE_ID, processing_policy=BLOCK), selection.revision, 0))
        self.assertFalse(exclusion.claimed)
        self.assertEqual(native.create_calls, 0)
        self.assertEqual(service.current_snapshot().generation, 0)

    def test_unknown_bandwidth_no_fabrication_and_family_authority(self):
        for bad in (0., 2.4e6, True):
            with self.assertRaises(ValueError):
                RfProcessingValue(bad, RfValueAuthority.UNKNOWN)
        for family in ("hackrf", "rtl_sdr"):
            frame, current, provenance, ready, journal = composition(family)
            context = SourceProcessingJoin().receipt(family, frame, current, provenance, "dBFS/bin", ready, journal)
            expected = RfValueAuthority.SDK_APPLIED if family == "hackrf" else RfValueAuthority.READBACK
            self.assertIs(context.frame_key.center.authority, expected)
            self.assertIs(context.frame_key.sample_rate.authority, expected)
            self.assertIsNone(context.frame_key.native_receiver_id)
            self.assertIsNone(context.hardware_dc_tracking)
            if family == "rtl_sdr":
                self.assertIsNone(context.frame_key.analog_bandwidth.value_hz)
            self.assertEqual(context.native_quality_flags, frame.quality_flags)

    def test_actual_hackrf_converter_attaches_same_journal_composition_receipt(self):
        # Production converter, but fabricated reduced metadata/journal. This
        # is not a native HackRF SDK acquisition or hardware proof.
        from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
        from tests.test_processing_recipe_bridge import recipe
        from tests.test_s15_live_rx_bridge import _make_frame

        frame, current, provenance, ready, journal = composition()
        converted_input = _make_frame(1, source_id=current.active_source_id, config_generation=1,
            quality_flags=frame.quality_flags)
        for field in ("source", "center_frequency_hz", "sample_rate_hz", "fft_size", "hop_size",
                      "frequencies_hz", "values"):
            setattr(converted_input, field, getattr(frame, field))
        converted_input.window = "hann"
        converted_input.detector = "sample"
        converted_input.averaging_frames = 1
        converted_input.precision_mode = "reference_f64"
        converted_input.calibration_status = "uncalibrated"
        converted_input.window_normalization_version = provenance.window_normalization_version
        converted_input.dsp_processing_recipe = recipe(HostDcMode.BLOCK_MEAN)
        service = HackrfAnalyzerService(SimpleNamespace(), object(), lambda: None, object(), object())
        with patch.object(service._ready_bridge, "convert", return_value=ready), \
                patch.object(service._journal, "current", return_value=journal):
            converted = service._convert(converted_input, current)
        self.assertIs(converted.processing_context.dc_mode, HostDcMode.BLOCK_MEAN)
        self.assertEqual(converted.processing_context.frame_key.owner_run_id, ready.owner_run_id)
        self.assertTrue(np.shares_memory(converted.values, frame.values))
        self.assertEqual(converted.native_quality_flags, frame.quality_flags)

    def test_processed_density_without_contract_and_raw_calibration_refuse(self):
        from sdr_monitor.domain.calibration import CalibrationProfileError
        from sdr_monitor.domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint
        from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
        from sdr_monitor.services.live_calibration_signature import CalibrationFrontendContext, build_live_calibration_signature
        from tests.test_live_processing import configuration, context

        native = SimpleNamespace(hackrf_processing_factory_contract=lambda: factory_contract("hackrf"))
        service = HackrfAnalyzerService(native, object(), lambda: None, object(), object())
        with patch("sdr_monitor.services.hackrf_analyzer.owner_journal_capacity", return_value=4096), \
                self.assertRaisesRegex(LiveAdmissionRejected,
                    "processed density requires exact metadata and SAME layer journal contracts"):
            service._admit(hf_request(processing_policy=BLOCK, persistence_enabled=True,
                persistence_mode="rolling-exact"))
        frame, current, provenance, ready, journal = composition()
        processed = SourceProcessingJoin().receipt("hackrf", frame, current, provenance, "dBFS/bin", ready, journal)
        self.assertTrue(processed.whole_frame_modified)
        ad = context(configuration())
        endpoint = ReceiverEndpoint("test-endpoint", ad.device.device_id, "observed-test-resource", ReceiverChainSelection.RX1)
        with self.assertRaisesRegex(CalibrationProfileError, "non-OFF"):
            build_live_calibration_signature(ad.device, endpoint, ad.applied, provenance,
                CalibrationFrontendContext("test-port", "test-chain", "test-plane"), unit="dBFS/bin")

    def test_stale_owner_producer_epoch_session_and_receiver_refuse(self):
        for family in ("hackrf", "rtl_sdr"):
            frame, current, provenance, ready, journal = composition(family)
            join = SourceProcessingJoin()
            for field, value in (("owner_run_id", "previous-run"), ("producer_instance_id", 99),
                                 ("acquisition_epoch", 8), ("session_id", "old-session"),
                                 ("config_generation", 2), ("receiver_id", "RX1"),
                                 ("adapter_clock_scope_id", "foreign-clock"), ("host_process_id", 2)):
                with self.subTest(family=family, field=field), self.assertRaises(ValueError):
                    join.receipt(family, frame, current, provenance, "dBFS/bin", replace(ready, **{field: value}), journal)
            for bad in (None, replace(ready, owner_run_id=None)):
                with self.assertRaises(ValueError):
                    join.receipt(family, frame, current, provenance, "dBFS/bin", bad, journal)
            with self.assertRaises(ValueError):
                join.receipt(family, frame, current, provenance, "dBFS/bin", ready,
                    replace(journal, state=JournalState.FINAL))

    def test_mismatched_policy_grid_backend_and_unit_refuse(self):
        frame, current, provenance, ready, journal = composition()
        join = SourceProcessingJoin()
        with self.assertRaises(ValueError):
            join.receipt("hackrf", frame, current, replace(provenance,
                processing_recipe=DspProcessingRecipeObservationV1(HostDcMode.OFF)), "dBFS/bin", ready, journal)
        for field, value in (("sample_rate_hz", 1.), ("fft_size", 256), ("config_generation", 2)):
            invalid = SimpleNamespace(**{**vars(frame), field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                join.receipt("hackrf", invalid, current, provenance, "dBFS/bin", ready, journal)
        invalid = SimpleNamespace(**{**vars(frame), "source": SimpleNamespace(source_id=frame.source.source_id, backend_id="foreign")})
        with self.assertRaises(ValueError):
            join.receipt("hackrf", invalid, current, provenance, "dBFS/bin", ready, journal)
        with self.assertRaises(ValueError):
            join.receipt("hackrf", frame, current, provenance, "dBm", ready, journal)
        for invalid in (replace(provenance, precision_mode="fast_f32"),
                        replace(provenance, calibration_status="applied")):
            with self.assertRaises(ValueError):
                join.receipt("hackrf", frame, current, invalid, "dBFS/bin", ready, journal)

    def test_live_frame_v2_validation_and_no_per_frame_policy_hash_or_json(self):
        frame, current, provenance, ready, journal = composition()
        join = SourceProcessingJoin()
        receipt = join.receipt("hackrf", frame, current, provenance, "dBFS/bin", ready, journal)
        args = dict(sequence=1, timestamp_ns=1, center_frequency_hz=frame.center_frequency_hz,
            sample_rate_hz=frame.sample_rate_hz, fft_size=frame.fft_size, hop_size=frame.hop_size,
            frequencies_hz=frame.frequencies_hz, values=frame.values, source_id=current.active_source_id,
            config_generation=1, acquisition_epoch=9, native_quality_flags=frame.quality_flags,
            numerical_provenance=provenance, detector_ready=ready, processing_context=receipt)
        valid = LiveSpectrumFrame(**args)
        self.assertIs(valid.processing_context, receipt)
        for field, value in (("owner_run_id", "different"), ("producer_instance_id", 99)):
            with self.assertRaises(ValueError):
                replace(valid, detector_ready=replace(ready, **{field: value}))
        with patch("sdr_monitor.domain.processing_policy.json.dumps", side_effect=AssertionError("hot JSON")), \
                patch("sdr_monitor.domain.processing_policy.hashlib.sha256", side_effect=AssertionError("hot policy hash")):
            for _ in range(20):
                self.assertEqual(join.receipt("hackrf", frame, current, provenance, "dBFS/bin", ready, journal), receipt)
        self.assertLessEqual(len(join._grids), 4)
        join.clear()
        self.assertEqual(len(join._grids), 0)


class NativeSourceProcessingTests(unittest.TestCase):
    def test_actual_rtl_mock_service_blockmean_restart_and_confirmed_cleanup(self):
        location = os.environ.get("SDR_APP07_TEST_MOCK_RTL")
        if not location:
            self.skipTest("explicit synthetic RTL DLL required; no SDK fallback")
        n = importlib.import_module("sdr_monitor._sdr_native")
        mock = Path(location).resolve(strict=True)
        digest = hashlib.sha256(mock.read_bytes()).hexdigest()
        if digest != "30d2939e0cb77faf66e77d550dffe55938f5a1d85066ae6c25d70e785fbb27cd":
            raise RuntimeError("unqualified mock DLL: no device open permitted")
        sdk = ctypes.CDLL(str(mock))
        sdk.mock_rtl_set_scenario.argtypes = (ctypes.c_int,)
        sdk.mock_rtl_counter.restype = ctypes.c_int
        sdk.mock_rtl_set_scenario(3)
        runtime = n.RtlExternalRuntime(n.RtlExternalFile(str(mock), digest), [])
        provider = RtlCapabilityProvider(RtlRuntimeProvision(n, runtime, "b" * 64, "c" * 64))
        provider.discover(startup_only=False)
        inventory = provider.observe_source(RTL_SOURCE_ID)
        binding = inventory.binding_for_source(RTL_SOURCE_ID)
        choice = AnalyzerSourceChoice(binding, inventory.runtime_for_adapter(RTL_ADAPTER_ID), "MOCK RTL", "MOCK")
        selection = AnalyzerSourceSelection(3, (choice,), RTL_SOURCE_ID)
        exclusion = _Exclusion()
        service = RtlAnalyzerService(n, exclusion, lambda: inventory,
            lambda binding, _runtime: provider.provision_for(binding))
        service.bind_selection(selection)
        receipts = []
        try:
            for policy in (SdrProcessingPolicyV1(), BLOCK, BLOCK):
                request = RtlLiveRequest(100_000_000, 2_400_000, source_id=RTL_SOURCE_ID, processing_policy=policy)
                before = service.current_snapshot()
                service.stage(RtlConfigurationPatch(request, selection.revision, int(before.generation)))
                sdk.mock_rtl_reset_counters()
                started = service.start()
                self.assertIs(started.state, LiveSessionState.RUNNING, started.error)
                with self.assertRaises(LiveAdmissionRejected):
                    service.stage(RtlConfigurationPatch(replace(request, processing_policy=BLOCK),
                        selection.revision, int(started.generation)))
                self.assertTrue(service.is_running())
                self.assertEqual(service.current_snapshot().rtl_request.processing_policy, policy)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    snapshot = service.current_snapshot()
                    if snapshot.spectrum is not None or snapshot.error is not None:
                        break
                    time.sleep(.01)
                self.assertIsNone(snapshot.error, service.first_fault_diagnostic())
                self.assertIsNotNone(snapshot.spectrum)
                context = snapshot.spectrum.processing_context
                self.assertIsNotNone(context)
                self.assertEqual(context.policy_digest, policy.digest)
                self.assertIs(context.dc_mode, policy.dc_mode)
                self.assertIsNone(context.frame_key.analog_bandwidth.value_hz)
                self.assertIsNone(snapshot.spectrum.receiver_id)
                receipts.append(context)
                stopped = service.stop()
                self.assertFalse(stopped.stop_required, stopped.error)
                self.assertFalse(exclusion.claimed)
                self.assertTrue(service.analytical_journal_snapshot().native_stop_confirmed)
                self.assertFalse(n.rtl_process_is_quarantined())
                self.assertEqual((sdk.mock_rtl_counter(0), sdk.mock_rtl_counter(1), sdk.mock_rtl_counter(2)), (1, 1, 1))
        finally:
            service.stop()
        self.assertEqual(len({receipt.frame_key.owner_run_id for receipt in receipts}), 3)
        self.assertEqual(len({receipt.frame_key.producer_instance_id for receipt in receipts}), 3)

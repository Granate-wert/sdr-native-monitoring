"""Inert RTL profile/compiler contracts; no vendor DLL or receiver is opened."""

from __future__ import annotations

import unittest
from dataclasses import replace

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.device_capabilities import (
    AcquisitionKind, AdapterRuntimeAvailability, AdapterRuntimeSnapshot,
    CapabilityEvidence, CapabilityEvidenceOrigin, CapabilityField, CapabilityRange,
    CapabilityTransport, DeviceCalibrationIdentity,
    DeviceCapabilityBinding, DeviceCapabilitySnapshot, DeviceFamily,
    RtlSessionRouteAssurance, build_device_capability_inventory,
    stable_identity_key,
)
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, RtlRtbwPaneProfile
from sdr_monitor.domain.rtl_live import RtlLiveRequest
from sdr_monitor.ui.v2_pane_user_plan import (
    PaneSlotDraft, PaneUserPlanError, RtbwBandPolicy, compile_user_pane_plan,
)


def _choice(*, canonical: bool, available: bool = True) -> AnalyzerSourceChoice:
    family = DeviceFamily.RTL_SDR
    adapter = "rtl.mock"
    snapshot = None
    identity = None
    if canonical:
        snapshot = DeviceCapabilitySnapshot(
            device_id="rtl-mock", identity_key=stable_identity_key("synthetic-unique-rtl"),
            label="Mock RTL tuner", family=family, adapter_id=adapter,
            transports=(CapabilityTransport.USB,),
            acquisition_kinds=(AcquisitionKind.COMPLEX_IQ,),
            tuning_ranges_hz=(CapabilityRange(100_000_000, 200_000_000, "Hz"),),
            sample_rate_ranges_hz=(CapabilityRange(2_048_000, 2_400_000, "Hz"),),
            runtime_control_contract="rtl.librtlsdr.rx.v1",
            evidence=tuple(CapabilityEvidence(field, CapabilityEvidenceOrigin.RUNTIME_READBACK,
                                               f"rtl-mock-{field.value}") for field in (
                CapabilityField.TRANSPORT, CapabilityField.ACQUISITION_KIND,
                CapabilityField.TUNING_RANGE, CapabilityField.SAMPLE_RATE_RANGE,
                CapabilityField.RUNTIME_CONTROL_CONTRACT)),
        )
        identity = DeviceCalibrationIdentity(
            family, adapter, snapshot.identity_key, stable_identity_key("mock-rtl-firmware"))
    binding = DeviceCapabilityBinding("rtl-mock-source", family, adapter, snapshot, identity)
    runtime = AdapterRuntimeSnapshot(
        adapter, family,
        AdapterRuntimeAvailability.AVAILABLE if available else AdapterRuntimeAvailability.UNAVAILABLE,
        "mock-rtl-runtime")
    return AnalyzerSourceChoice(binding, runtime, "Mock RTL tuner", "USB")


class RtlPureContractTests(unittest.TestCase):
    def test_selected_generic_serial_is_session_only_not_calibration(self) -> None:
        from sdr_monitor.services.rtl_capability_provider import RTL_ADAPTER_ID
        from sdr_monitor.services.source_capability_admission import admit_source_request

        route = RtlSessionRouteAssurance("Generic", "RTL tuner", "00000001", 5, 7,
                                        True, "a" * 64)
        binding = DeviceCapabilityBinding("rtl-sdr-session", DeviceFamily.RTL_SDR,
                                          RTL_ADAPTER_ID, rtl_session_route=route)
        self.assertIsNone(binding.identity_key)
        self.assertIsNone(binding.calibration_identity)
        self.assertIsNone(binding.snapshot)
        runtime = AdapterRuntimeSnapshot(RTL_ADAPTER_ID, DeviceFamily.RTL_SDR,
            AdapterRuntimeAvailability.AVAILABLE, "mock-unbundled-runtime")
        inventory = build_device_capability_inventory((), bindings=(binding,), runtimes=(runtime,))
        request = RtlLiveRequest(150_000_000, 2_400_000, source_id="rtl-sdr-session")
        self.assertTrue(admit_source_request(inventory, binding.source_id, "rtbw", request).accepted)
        self.assertFalse(admit_source_request(inventory, binding.source_id, "sweep", request).accepted)
        unknown = replace(binding, rtl_session_route=None)
        self.assertFalse(admit_source_request(build_device_capability_inventory((), bindings=(unknown,),
            runtimes=(runtime,)), unknown.source_id, "rtbw", request).accepted)
        selected = AnalyzerSourceChoice(binding, runtime, "RTL selected USB session", "USB SESSION")
        draft = PaneSlotDraft(1, binding.source_id, 149_500_000, 150_500_000,
                              sample_rate_hz=2_400_000, fft_size=4096)
        plan = compile_user_pane_plan((draft,), {binding.source_id: selected},
                                      {binding.source_id: 4})
        assert plan.layout.schedule is not None
        self.assertIsInstance(plan.layout.schedule.resources[0].jobs[0].profile,
                              RtlRtbwPaneProfile)

    def test_four_distinct_mock_families_put_rtl_only_in_pane_four(self) -> None:
        # Reuse the existing no-hardware AD/HackRF/tinySA graph fixtures;
        # this proves a compiler layout, not four working receiver owners.
        from tests.ui_v2.test_app07_pane_user_plan import PaneUserPlanTests

        fixture = PaneUserPlanTests(
            "test_three_distinct_families_and_empty_compile_parallel_without_io")
        fixture.setUp()
        try:
            rtl = _choice(canonical=True)
            fixture.selected[rtl.device_id] = rtl
            fixture.revisions[rtl.device_id] = 3
            source_ids = (fixture.ad_id, fixture.hf_id, fixture.ts_id, rtl.device_id)
            self.assertEqual(len(set(source_ids)), 4)
            plan = fixture._compile((
                PaneSlotDraft(1, fixture.ad_id, 100_000_000, 108_000_000),
                PaneSlotDraft(2, fixture.hf_id, 140_000_000, 148_000_000),
                PaneSlotDraft(3, fixture.ts_id, 200_000_000, 210_000_000, points=101),
                PaneSlotDraft(4, rtl.device_id, 149_500_000, 150_500_000,
                              sample_rate_hz=2_400_000, fft_size=4096),
            ))
            self.assertEqual(plan.layout.empty_slots, ())
            self.assertEqual(len(plan.groups), 4)
            assert plan.layout.schedule is not None
            resources = plan.layout.schedule.resources
            self.assertEqual(len(resources), 4)
            self.assertEqual(len({item.physical_stream_resource_id for item in resources}), 4)
            by_pane = {job.crops[0].pane_id: job.profile
                       for resource in resources for job in resource.jobs}
            self.assertEqual(set(by_pane), {"pane-1", "pane-2", "pane-3", "pane-4"})
            self.assertEqual({pane for pane, profile in by_pane.items()
                              if isinstance(profile, RtlRtbwPaneProfile)}, {"pane-4"})
            assert isinstance(by_pane["pane-4"], RtlRtbwPaneProfile)
            self.assertEqual(by_pane["pane-4"].request_template.source_id, rtl.device_id)
        finally:
            fixture.tearDown()

    def test_request_burst_and_restrictions(self) -> None:
        for fft, expected_capacity in ((1024, 34), (2048, 18), (4096, 10)):
            with self.subTest(fft=fft):
                request = RtlLiveRequest(150_000_000, 2_400_000, fft_size=fft, hop_size=fft // 2)
                self.assertEqual(request.resolved_dsp_output_capacity, expected_capacity)
                with self.assertRaisesRegex(ValueError, "analytical queue"):
                    RtlLiveRequest(150_000_000, 2_400_000, fft_size=fft, hop_size=fft // 2,
                                   dsp_output_capacity=expected_capacity - 1)
        for overrides in ({"sample_rate_hz": 3_200_000}, {"backend": "cuda"},
                          {"center_frequency_hz": 150_000_000.5}, {"window": "blackman"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                RtlLiveRequest(**({"center_frequency_hz": 150_000_000,
                                   "sample_rate_hz": 2_400_000} | overrides))

    def test_compiler_mock_canonical_rtbw_and_real_unknown_refusal(self) -> None:
        draft = PaneSlotDraft(1, "rtl-mock-source", 149_500_000, 150_500_000,
                              sample_rate_hz=2_400_000, fft_size=4096)
        choice = _choice(canonical=True)
        plan = compile_user_pane_plan((draft,), {choice.device_id: choice}, {choice.device_id: 3})
        assert plan.layout.schedule is not None
        profile = plan.layout.schedule.resources[0].jobs[0].profile
        assert isinstance(profile, RtlRtbwPaneProfile)
        self.assertEqual((profile.request_template.center_frequency_hz,
                          profile.request_template.sample_rate_hz, profile.unit),
                         (150_000_000, 2_400_000, "dBFS/bin"))
        self.assertEqual(profile.usable_capture_span_hz, 1_200_000)
        unknown = _choice(canonical=False)
        with self.assertRaisesRegex(PaneUserPlanError, "selected session or tuner capability"):
            compile_user_pane_plan((draft,), {unknown.device_id: unknown}, {unknown.device_id: 3})
        unavailable = _choice(canonical=True, available=False)
        with self.assertRaisesRegex(PaneUserPlanError, "selected session or tuner capability"):
            compile_user_pane_plan((draft,), {unavailable.device_id: unavailable},
                                   {unavailable.device_id: 3})
        assert choice.binding.snapshot is not None
        for changed_snapshot in (
                replace(choice.binding.snapshot, acquisition_kinds=(AcquisitionKind.SPECTRUM_TRACE,)),
                replace(choice.binding.snapshot, runtime_control_contract="rtl.other.v1")):
            changed = replace(choice, binding=replace(choice.binding, snapshot=changed_snapshot))
            with self.subTest(contract=changed_snapshot.runtime_control_contract,
                              kind=changed_snapshot.acquisition_kinds), self.assertRaisesRegex(
                                  PaneUserPlanError, "selected session or tuner capability"):
                compile_user_pane_plan((draft,), {changed.device_id: changed},
                                       {changed.device_id: 3})

    def test_compiler_rejects_unqualified_sweep_wide_fractional_and_rate(self) -> None:
        choice = _choice(canonical=True)
        base = PaneSlotDraft(1, choice.device_id, 149_500_000, 150_500_000,
                             sample_rate_hz=2_400_000)
        cases = (
            (replace(base, measurement_mode=CaptureMeasurementMode.SWEEP), "RTL Sweep"),
            (replace(base, rtbw_band=RtbwBandPolicy.FULL_RECEIVE), "wide receive band"),
            (replace(base, start_hz=149_500_000.5), "exact whole hertz"),
            (replace(base, sample_rate_hz=20_000_000), "bounded edge-trimmed"),
        )
        for draft, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(PaneUserPlanError, reason):
                compile_user_pane_plan((draft,), {choice.device_id: choice}, {choice.device_id: 3})
        with self.assertRaisesRegex(PaneUserPlanError, "qualified draft choices"):
            replace(base, sample_rate_hz=3_200_000)


if __name__ == "__main__":
    unittest.main()

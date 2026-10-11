"""Explicit RTL digital Fs coverage, without tuner/SDK or RF acceptance."""

from __future__ import annotations

from dataclasses import replace
import unittest

import numpy as np

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, RtbwFrameMetadata
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.device_capabilities import (
    AcquisitionKind, AdapterRuntimeAvailability, AdapterRuntimeSnapshot, CapabilityEvidence,
    CapabilityEvidenceOrigin, CapabilityField, CapabilityRange,
    CapabilityTransport, DeviceCalibrationIdentity, DeviceCapabilityBinding,
    DeviceCapabilitySnapshot, DeviceFamily, RtlSessionRouteAssurance,
)
from sdr_monitor.domain.identity import (
    ConfigurationGeneration, FrameSequence, LossReason, SourceId, TimestampNs, TimestampQuality,
)
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, RtlRtbwPaneProfile
from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
from sdr_monitor.domain.rtl_live import RTL_FFT_CHOICES, RTL_RATE_CHOICES_HZ
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
from sdr_monitor.ui.v2_pane_user_plan import (
    PaneSlotDraft, PaneUserPlan, PaneUserPlanError, RtbwBandPolicy, compile_user_pane_plan,
)


def _choice(*, selected: bool = True,
            availability: AdapterRuntimeAvailability = AdapterRuntimeAvailability.AVAILABLE
            ) -> AnalyzerSourceChoice:
    adapter = "rtl.librtlsdr.rx.v1"
    route = RtlSessionRouteAssurance(
        "RTL", "SDR", "00000001", 5, 1, True, "a" * 64,
        manual_gain_contract_version=1, tuner_gains_tenth_db=(-42, 0, 127)) if selected else None
    return AnalyzerSourceChoice(
        DeviceCapabilityBinding("rtl-source", DeviceFamily.RTL_SDR, adapter, rtl_session_route=route),
        AdapterRuntimeSnapshot(adapter, DeviceFamily.RTL_SDR, availability, "rtl-fixture"),
        "Synthetic RTL selected session", "USB")


def _compile(draft: PaneSlotDraft, choice: AnalyzerSourceChoice | None = None) -> PaneUserPlan:
    choice = choice or _choice()
    return compile_user_pane_plan((draft,), {choice.device_id: choice}, {choice.device_id: 17})


def _draft(*, rate: int = 2_400_000, fft: int = 4096) -> PaneSlotDraft:
    return PaneSlotDraft(
        1, "rtl-source", 100_000_000, 100_000_000 + rate,
        sample_rate_hz=float(rate), fft_size=fft, measurement_mode=CaptureMeasurementMode.RTBW,
        rtbw_band=RtbwBandPolicy.FULL_RECEIVE, manual_tuner_gain_tenth_db=-42)


def _bundle(draft: PaneSlotDraft, *, center_offset_hz: float = 0,
            source: str = "rtl-source", unit: str = "dBFS/bin") -> AnalyzerFrameBundle:
    assert draft.start_hz is not None and draft.stop_hz is not None
    center = (draft.start_hz + draft.stop_hz) / 2 + center_offset_hz
    frequencies = center + (np.arange(draft.fft_size, dtype=np.float64) - draft.fft_size // 2) * (
        draft.sample_rate_hz / draft.fft_size)
    values = np.linspace(-100, -10, draft.fft_size, dtype=np.float32)
    frame = LiveSpectrumFrame(
        FrameSequence(7), TimestampNs(0), center, draft.sample_rate_hz, draft.fft_size,
        draft.fft_size // 2, frequencies, values, unit=unit, source_id=SourceId(source),
        config_generation=ConfigurationGeneration(5), acquisition_epoch=3,
        timestamp_quality=TimestampQuality.UNKNOWN, dropped_fft_frames_before=2,
        loss_reasons=(LossReason.SNAPSHOT,), native_quality_flags=8193)
    return AnalyzerFrameBundle(
        frame, "synthetic-session", None, 3,
        RtbwFrameMetadata(center, draft.sample_rate_hz, draft.fft_size, draft.fft_size // 2))


class RtlFullReceiveTests(unittest.TestCase):
    def test_full_span_keeps_qualified_fs_fft_gain_and_one_rx(self) -> None:
        for rate in sorted(RTL_RATE_CHOICES_HZ):
            for fft in sorted(RTL_FFT_CHOICES):
                with self.subTest(rate=rate, fft=fft):
                    plan = _compile(_draft(rate=rate, fft=fft))
                    self.assertIsNotNone(plan.layout.schedule)
                    assert plan.layout.schedule is not None
                    self.assertEqual(len(plan.groups), 1)
                    self.assertEqual(len(plan.groups[0].endpoints), 1)
                    self.assertIs(plan.groups[0].endpoints[0].selection, ReceiverChainSelection.RX1)
                    self.assertEqual(len(plan.layout.schedule.resources), 1)
                    jobs = plan.layout.schedule.resources[0].jobs
                    self.assertEqual(len(jobs), 1)
                    profile = jobs[0].profile
                    self.assertIsInstance(profile, RtlRtbwPaneProfile)
                    assert isinstance(profile, RtlRtbwPaneProfile)
                    request = profile.request_template
                    self.assertEqual(profile.usable_capture_span_hz, rate)
                    self.assertEqual((request.sample_rate_hz, request.fft_size, request.hop_size),
                                     (rate, fft, fft // 2))
                    self.assertEqual(request.manual_tuner_gain_tenth_db, -42)
                    self.assertEqual(request.source_id, "rtl-source")
                    self.assertEqual(request.detector, "peak")
                    self.assertEqual(plan.resource_sources, ((plan.groups[0].physical_stream_resource_id,
                                                              "rtl-source"),))

    def test_edge_trimmed_default_remains_half_fs_and_rejects_wider_crop(self) -> None:
        draft = PaneSlotDraft(1, "rtl-source", 100_000_000, 101_200_000,
                              sample_rate_hz=2_400_000.0)
        self.assertIs(draft.rtbw_band, RtbwBandPolicy.EDGE_TRIMMED)
        plan = _compile(draft)
        assert plan.layout.schedule is not None
        self.assertEqual(plan.layout.schedule.resources[0].jobs[0].profile.usable_capture_span_hz,
                         1_200_000)
        with self.assertRaises(PaneUserPlanError) as rejected:
            _compile(replace(draft, stop_hz=101_200_002))
        self.assertIs(rejected.exception.reason, PaneUserRefusal.CAPTURE_SPAN_EXCEEDED)

    def test_full_overflow_unsupported_fs_fft_mode_family_and_rx_refuse(self) -> None:
        draft = _draft()
        with self.assertRaises(PaneUserPlanError) as rejected:
            _compile(replace(draft, stop_hz=draft.stop_hz + 2))
        self.assertIs(rejected.exception.reason, PaneUserRefusal.CAPTURE_SPAN_EXCEEDED)
        invalid = (
            replace(draft, sample_rate_hz=20_000_000.0),
            replace(draft, fft_size=16384),
            replace(draft, measurement_mode=CaptureMeasurementMode.SWEEP),
            replace(draft, measurement_mode=CaptureMeasurementMode.INSTRUMENT_TRACE),
            replace(draft, receiver_selection=ReceiverChainSelection.RX2),
        )
        for item in invalid:
            with self.subTest(draft=item), self.assertRaises(PaneUserPlanError):
                _compile(item)
        tiny = AnalyzerSourceChoice(DeviceCapabilityBinding(
            "tiny-source", DeviceFamily.TINYSA, "tiny.fixture"), None, "tiny fixture", "serial")
        with self.assertRaises(PaneUserPlanError):
            _compile(replace(draft, source_id=tiny.device_id, manual_tuner_gain_tenth_db=None,
                             measurement_mode=CaptureMeasurementMode.INSTRUMENT_TRACE), tiny)

    def test_canonical_full_still_requires_observed_tuning_and_fs_ranges(self) -> None:
        selected = _choice()
        family, adapter = selected.family, selected.binding.adapter_id
        key = "sha256:" + "b" * 64
        snapshot = DeviceCapabilitySnapshot(
            "rtl-source", key, "Synthetic RTL capability", family, adapter,
            (CapabilityTransport.USB,), (AcquisitionKind.COMPLEX_IQ,),
            tuning_ranges_hz=(CapabilityRange(100_000_000, 104_000_000, "Hz"),),
            sample_rate_ranges_hz=(CapabilityRange(2_048_000, 2_400_000, "Hz"),),
            rx_channel_count=1, runtime_control_contract=adapter,
            evidence=tuple(CapabilityEvidence(field, CapabilityEvidenceOrigin.RUNTIME_READBACK,
                                              "synthetic-rtl-fixture") for field in (
                CapabilityField.TRANSPORT, CapabilityField.ACQUISITION_KIND, CapabilityField.TUNING_RANGE,
                CapabilityField.SAMPLE_RATE_RANGE, CapabilityField.RX_CHANNEL_COUNT,
                CapabilityField.RUNTIME_CONTROL_CONTRACT)))
        identity = DeviceCalibrationIdentity(family, adapter, key, "sha256:" + "c" * 64)
        binding = DeviceCapabilityBinding("rtl-source", family, adapter, snapshot, identity)
        canonical = replace(selected, binding=binding)
        draft = replace(_draft(), measurement_mode=None, manual_tuner_gain_tenth_db=None)
        plan = _compile(draft, canonical)
        assert plan.layout.schedule is not None
        self.assertEqual(plan.layout.schedule.resources[0].jobs[0].profile.usable_capture_span_hz,
                         draft.sample_rate_hz)
        self.assertIs(canonical.binding.calibration_identity, identity)
        self.assertIs(canonical.binding.snapshot, snapshot)
        with self.assertRaises(PaneUserPlanError):
            _compile(replace(draft, start_hz=99_000_000, stop_hz=101_400_000), canonical)
        narrow_rate = replace(snapshot, sample_rate_ranges_hz=(CapabilityRange(2_048_000, 2_048_000, "Hz"),))
        narrow = replace(canonical, binding=replace(binding, snapshot=narrow_rate))
        with self.assertRaises(PaneUserPlanError):
            _compile(draft, narrow)

    def test_full_does_not_bypass_selected_route_runtime_or_manual_gain(self) -> None:
        for choice in (_choice(selected=False),
                       _choice(availability=AdapterRuntimeAvailability.UNAVAILABLE)):
            with self.subTest(choice=choice), self.assertRaises(PaneUserPlanError):
                _compile(_draft(), choice)
        with self.assertRaises(PaneUserPlanError):
            _compile(replace(_draft(), manual_tuner_gain_tenth_db=-41))
        with self.assertRaises(PaneUserPlanError):
            compile_user_pane_plan((_draft(),), {_choice().device_id: _choice()}, {})

    def test_full_delivery_accepts_exclusive_right_edge_without_extra_bin_or_metadata_loss(self) -> None:
        for rate in sorted(RTL_RATE_CHOICES_HZ):
            for fft in sorted(RTL_FFT_CHOICES):
                with self.subTest(rate=rate, fft=fft):
                    draft = _draft(rate=rate, fft=fft)
                    plan = _compile(draft)
                    budget = PresentationAllocationBudget()
                    preparer = PaneDeliveryPreparer(plan.layout, plan.groups, budget)
                    try:
                        binding = preparer.bindings["pane-1"]
                        bundle = _bundle(draft)
                        delivery = PaneDelivery(
                            binding.physical_stream_resource_id, binding.capture_id,
                            binding.receiver_endpoint_id, 2, binding.crop, bundle, 1.25, host_run_serial=4)
                        prepared = preparer.prepare(delivery)
                        self.assertIs(prepared.delivery, delivery)
                        self.assertIs(prepared.bundle, bundle)
                        self.assertIs(prepared.spectrum.view.source_frame, bundle)
                        self.assertEqual(bundle.rtbw_frequency_bounds_hz, (draft.start_hz, draft.stop_hz))
                        self.assertLess(bundle.frequencies_hz[-1], draft.stop_hz)
                        self.assertEqual(bundle.frequencies_hz[-1], draft.stop_hz - rate / fft)
                        np.testing.assert_array_equal(prepared.spectrum.view.frequencies_hz,
                                                      bundle.frequencies_hz)
                        np.testing.assert_array_equal(prepared.spectrum.view.values, bundle.values)
                        self.assertEqual(prepared.spectrum.view.point_count, fft)
                        self.assertFalse(prepared.spectrum.view.values.flags.writeable)
                        self.assertEqual((bundle.spectrum.sequence, bundle.spectrum.timestamp_ns,
                                          bundle.spectrum.config_generation, bundle.acquisition_epoch),
                                         (7, 0, 5, 3))
                        self.assertIs(bundle.spectrum.timestamp_quality, TimestampQuality.UNKNOWN)
                        self.assertEqual(bundle.spectrum.native_quality_flags, 8193)
                        self.assertEqual(bundle.spectrum.dropped_fft_frames_before, 2)
                        self.assertEqual(bundle.spectrum.loss_reasons, (LossReason.SNAPSHOT,))
                        self.assertEqual((delivery.host_activation_serial, delivery.host_run_serial,
                                          delivery.host_received_monotonic_s), (2, 4, 1.25))
                        self.assertIsNone(bundle.receiver_id)
                        self.assertIsNone(bundle.paired_capture)
                        self.assertIsNotNone(prepared.waterfall)
                    finally:
                        preparer.clear()
                    self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_full_delivery_rejects_short_coverage_wrong_source_unit_and_malformed_grid(self) -> None:
        draft = _draft()
        plan = _compile(draft)
        preparer = PaneDeliveryPreparer(plan.layout, plan.groups, PresentationAllocationBudget())
        try:
            binding = preparer.bindings["pane-1"]
            for bundle in (_bundle(draft, center_offset_hz=-1), _bundle(draft, source="other-source"),
                           _bundle(draft, unit="dBm")):
                delivery = PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
                                        binding.receiver_endpoint_id, 2, binding.crop, bundle, 1.25)
                with self.subTest(bundle=bundle), self.assertRaises(ValueError):
                    preparer.prepare(delivery)
            bundle = _bundle(draft)
            malformed = bundle.frequencies_hz.copy()
            malformed[-1] = draft.stop_hz
            frame = replace(bundle.spectrum, frequencies_hz=malformed)
            with self.assertRaises(ValueError):
                replace(bundle, spectrum=frame)
        finally:
            preparer.clear()


if __name__ == "__main__":
    unittest.main()

"""User-editable APP-07 slots compile to the SAME bounded pane contracts."""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from collections import Counter

from sdr_monitor.domain.receiver_topology import ReceiverBindingMode, ReceiverChainSelection, SchedulerPolicyKind
from sdr_monitor.domain.pane_scheduler import Ad936xSweepPaneProfile, CaptureMeasurementMode, HackrfSweepPaneProfile
from sdr_monitor.domain.device_capabilities import DeviceFamily, stable_identity_key
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, PairedCaptureMetadata
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2_pane_user_plan import (
    PaneSlotDraft, PaneUserPlanError, RtbwBandPolicy, compile_user_pane_plan,
)

from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_fixture
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from types import SimpleNamespace
from tests.test_app07_pane_resource_session import FakeOwner, live_frame, readonly


class PaneUserPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        _, self.ad = _ad_graph(serial="")
        hf = hackrf_fixture()
        ts = tinysa_fixture()
        self.hf = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog, analyzer_hackrf=hf.hackrf))
        self.ts = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=ts.live, device_catalog=ts.catalog, analyzer_tinysa=ts.instrument))
        choices = [next(choice for choice in graph.live.discover(startup=True)
                        if choice.family is family)
                   for graph, family in ((self.ad, DeviceFamily.AD936X),
                                         (self.hf, DeviceFamily.HACKRF),
                                         (self.ts, DeviceFamily.TINYSA))]
        for graph, choice in zip((self.ad, self.hf, self.ts), choices, strict=True):
            graph.live.select_device(choice.device_id)
        ad_choice, hf_choice, ts_choice = (
            graph.live.current_source_selection().selected for graph in (self.ad, self.hf, self.ts))
        self.selected = {choice.device_id: choice for choice in (ad_choice, hf_choice, ts_choice)}
        self.revisions = {
            choice.device_id: graph.live.current_source_selection().revision
            for choice, graph in ((ad_choice, self.ad), (hf_choice, self.hf), (ts_choice, self.ts))
        }
        self.ad_id, self.hf_id, self.ts_id = (item.device_id for item in (ad_choice, hf_choice, ts_choice))

    def tearDown(self) -> None:
        for graph in (self.ad, self.hf, self.ts):
            graph.live.shutdown()

    def _compile(self, drafts):
        used = {item.source_id for item in drafts if item.source_id is not None}
        return compile_user_pane_plan(tuple(drafts),
            {source: self.selected[source] for source in used},
            {source: self.revisions[source] for source in used})

    def test_three_distinct_families_and_empty_compile_parallel_without_io(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.ad_id, 100e6, 108e6),
            PaneSlotDraft(2, self.hf_id, 140e6, 148e6),
            PaneSlotDraft(3, self.ts_id, 200e6, 210e6, points=101),
            PaneSlotDraft(4),
        ))
        self.assertEqual(plan.layout.empty_slots, (4,))
        self.assertEqual(len(plan.layout.schedule.resources), 3)
        self.assertEqual(len(plan.groups), 3)
        self.assertEqual(len(plan.initial_ad_configurations), 1)
        self.assertEqual(tuple(resource.jobs[0].mode for resource in plan.layout.schedule.resources),
                         (ReceiverBindingMode.DEDICATED_PARALLEL,) * 3)

    def test_same_source_nearby_panes_share_one_capture(self) -> None:
        plan = self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 104e6),
                              PaneSlotDraft(2, self.ad_id, 105e6, 108e6)))
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(len(plan.layout.schedule.resources[0].jobs), 1)
        self.assertEqual(plan.layout.schedule.resources[0].jobs[0].mode,
                         ReceiverBindingMode.SHARED_CAPTURE)

    def test_presentation_selection_is_typed_and_rf_preview_cannot_replace_chain(self) -> None:
        plan = self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 108e6),))
        preparer = PaneDeliveryPreparer(plan.layout, plan.groups, PresentationAllocationBudget())
        try:
            binding = preparer.bindings["pane-1"]
            self.assertIs(binding.receiver_selection, ReceiverChainSelection.RX1)
            self.assertEqual(preparer.paired_resource_ids, frozenset())
            for invalid in ("rx1", "RX2", True, 1):
                with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                    replace(binding, receiver_selection=invalid)
            group = plan.groups[0]
            switched = replace(group, endpoints=(replace(group.endpoints[0], selection=ReceiverChainSelection.RX2),))
            with self.assertRaisesRegex(ValueError, "RF change"):
                preparer.preview_resource_layout(plan.layout, (switched,), group.physical_stream_resource_id)
            self.assertIs(preparer.bindings["pane-1"], binding)
        finally:
            preparer.clear()

    def test_explicit_wide_rtbw_56_and_20_mhz_keep_exact_native_intent(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.ad_id, 100e6, 156e6, sample_rate_hz=61.44e6,
                          rtbw_band=RtbwBandPolicy.FULL_RECEIVE),
            PaneSlotDraft(2, self.hf_id, 140e6, 160e6, rtbw_band=RtbwBandPolicy.FULL_RECEIVE),
            PaneSlotDraft(3, self.ts_id, 100e6, 300e6, points=1001), PaneSlotDraft(4)))
        ad, hf, tiny = (resource.jobs[0].profile for resource in plan.layout.schedule.resources)
        self.assertEqual((ad.sample_rate_hz, ad.analog_bandwidth_hz, ad.usable_capture_span_hz),
                         (61.44e6, 56e6, 56e6))
        self.assertEqual((hf.sample_rate_hz, hf.request_template.baseband_filter_hz,
                          hf.usable_capture_span_hz), (20e6, 20_000_000, 20e6))
        self.assertEqual((ad.fft_size, hf.fft_size, ad.hop_size, hf.hop_size),
                         (4096, 4096, 2048, 2048))
        self.assertEqual(hf.request_template.averaging_frames, 1)
        self.assertFalse(hf.request_template.rf_amplifier_enabled)
        self.assertFalse(hf.request_template.bias_tee_enabled)
        self.assertEqual(plan.initial_ad_configurations[0][1].analog_bandwidth_hz, 56e6)
        self.assertEqual((tiny.points, tiny.unit, plan.layout.empty_slots), (1001, "dBm", (4,)))
        for source, rate, span in ((self.ad_id, 61.44e6, 56e6), (self.hf_id, 20e6, 20e6)):
            for stop in (100e6 + span, 100e6 + span + 1):
                with self.subTest(source=source, stop=stop), self.assertRaises(PaneUserPlanError):
                    self._compile((PaneSlotDraft(1, source, 100e6, stop, sample_rate_hz=rate,
                        **({} if stop == 100e6 + span else {"rtbw_band": RtbwBandPolicy.FULL_RECEIVE})),))

    def test_lower_rate_wide_profiles_never_exceed_fs_or_discrete_filter(self) -> None:
        for source, rate, span in ((self.ad_id, 20e6, 20e6), (self.hf_id, 16e6, 14e6)):
            with self.subTest(source=source):
                plan = self._compile((PaneSlotDraft(1, source, 100e6, 100e6 + span,
                    sample_rate_hz=rate, rtbw_band=RtbwBandPolicy.FULL_RECEIVE),))
                profile = plan.layout.schedule.resources[0].jobs[0].profile
                self.assertEqual(profile.usable_capture_span_hz, span)
                self.assertLessEqual(profile.usable_capture_span_hz, rate)
                with self.assertRaises(PaneUserPlanError):
                    self._compile((PaneSlotDraft(1, source, 100e6, 100e6 + span + 1,
                        sample_rate_hz=rate, rtbw_band=RtbwBandPolicy.FULL_RECEIVE),))

    def test_wide_and_trimmed_same_rx_are_not_silently_merged(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.ad_id, 100e6, 108e6, sample_rate_hz=61.44e6),
            PaneSlotDraft(2, self.ad_id, 100e6, 108e6, sample_rate_hz=61.44e6,
                          rtbw_band=RtbwBandPolicy.FULL_RECEIVE)))
        resource = plan.layout.schedule.resources[0]
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(len(resource.jobs), 2)
        self.assertTrue(all(job.mode is ReceiverBindingMode.TIME_SLICED for job in resource.jobs))
        self.assertEqual({job.profile.analog_bandwidth_hz for job in resource.jobs}, {40e6, 56e6})
        shared = self._compile(tuple(PaneSlotDraft(number, self.hf_id, start, stop,
            rtbw_band=RtbwBandPolicy.FULL_RECEIVE)
            for number, start, stop in ((1, 140e6, 148e6), (2, 150e6, 160e6))))
        self.assertEqual(len(shared.layout.schedule.resources[0].jobs), 1)
        self.assertIs(shared.layout.schedule.resources[0].jobs[0].mode, ReceiverBindingMode.SHARED_CAPTURE)

    def test_wide_policy_is_typed_rtbw_only_and_empty_does_not_retain_it(self) -> None:
        for invalid in (True, 1, "full_receive", None):
            with self.subTest(invalid=invalid), self.assertRaises(PaneUserPlanError):
                PaneSlotDraft(1, self.ad_id, 100e6, 108e6, rtbw_band=invalid)
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, rtbw_band=RtbwBandPolicy.FULL_RECEIVE)
        for source, rate, mode in ((self.ad_id, 61.44e6, CaptureMeasurementMode.SWEEP),
                                   (self.hf_id, 20e6, CaptureMeasurementMode.SWEEP),
                                   (self.ts_id, 20e6, CaptureMeasurementMode.INSTRUMENT_TRACE)):
            with self.subTest(source=source), self.assertRaisesRegex(PaneUserPlanError, "only.*RTBW"):
                self._compile((PaneSlotDraft(1, source, 100e6, 220e6,
                    sample_rate_hz=rate, measurement_mode=mode,
                    rtbw_band=RtbwBandPolicy.FULL_RECEIVE),))

    def test_full_hackrf_fft_right_edge_routes_and_prepares_without_extra_bin(self) -> None:
        plan = self._compile((PaneSlotDraft(1, self.hf_id, 140e6, 160e6,
                                          rtbw_band=RtbwBandPolicy.FULL_RECEIVE),))
        resource = plan.layout.schedule.resources[0].physical_stream_resource_id
        endpoint = plan.groups[0].endpoints[0].endpoint_id
        owner = FakeOwner(resource)
        owner.admission_source_id = self.hf_id
        owner.admission_mode = "rtbw"
        owner.admission_generation = 5
        session = PaneResourceSession(plan.layout.schedule, plan.groups, {resource: owner},
            ReceiverLeaseManager(max_active_resources=4),
            source_identity_keys={self.hf_id: stable_identity_key(self.hf_id)})
        preparer = PaneDeliveryPreparer(plan.layout, plan.groups, PresentationAllocationBudget())
        session.apply()
        activation = session.start_resource(resource)
        try:
            valid = live_frame(self.hf_id, "fake-live-session", 7,
                               center_hz=150e6, sample_rate_hz=20e6)
            valid = replace(valid, spectrum=replace(valid.spectrum, native_quality_flags=0x2001))
            self.assertLess(valid.frequencies_hz[-1], 160e6)
            self.assertEqual(valid.rtbw_frequency_bounds_hz, (140e6, 160e6))
            deliveries = session.accept_frame(activation, endpoint, valid)
            self.assertEqual(len(deliveries), 1)
            prepared = preparer.prepare(deliveries[0])
            self.assertIs(prepared.bundle, valid)
            self.assertIs(prepared.spectrum.view.source_frame, valid)
            self.assertEqual(prepared.bundle.frequencies_hz.size, 4096)
            self.assertEqual(prepared.bundle.spectrum.native_quality_flags, 0x2001)
            self.assertFalse(prepared.bundle.frequencies_hz.flags.writeable)
            self.assertIsNotNone(prepared.waterfall)
            for invalid in (live_frame(self.hf_id, "fake-live-session", 7,
                            center_hz=150e6 - 1, sample_rate_hz=20e6),
                            live_frame(self.hf_id, "other-session", 7,
                            center_hz=150e6, sample_rate_hz=20e6)):
                self.assertEqual(session.accept_frame(activation, endpoint, invalid), ())
                if invalid.session_id == valid.session_id:
                    with self.assertRaisesRegex(ValueError, "outside"):
                        preparer.prepare(replace(deliveries[0], bundle=invalid))
            for grid in (valid.frequencies_hz[:-1], valid.frequencies_hz + 1000):
                forged = replace(valid.spectrum, frequencies_hz=readonly(grid, np.float64),
                                 values=readonly(valid.spectrum.values[:len(grid)], np.float32))
                with self.assertRaisesRegex(ValueError, "grid"):
                    AnalyzerFrameBundle(forged, valid.session_id, valid.receiver_id,
                                        valid.acquisition_epoch, valid.rtbw)
        finally:
            self.assertEqual(session.stop_all(), ())
            preparer.clear()

    def test_presentation_uses_current_admitted_producer_not_operational_route(self) -> None:
        plan = self._compile((PaneSlotDraft(1, self.hf_id, 140e6, 160e6,
                                          rtbw_band=RtbwBandPolicy.FULL_RECEIVE),))
        resource = plan.layout.schedule.resources[0].physical_stream_resource_id
        endpoint = plan.groups[0].endpoints[0].endpoint_id

        class EndpointProducerOwner(FakeOwner):
            def start_capture(self, job):
                admitted = super().start_capture(job)
                return replace(admitted, endpoint_source_ids=tuple(
                    (source_endpoint, source_endpoint) for source_endpoint in job.receiver_endpoint_ids))

        owner = EndpointProducerOwner(resource)
        owner.admission_source_id = self.hf_id
        owner.admission_mode = "rtbw"
        owner.admission_generation = 5
        owner.receiver_ids[endpoint] = "RX1"
        session = PaneResourceSession(plan.layout.schedule, plan.groups, {resource: owner},
            ReceiverLeaseManager(max_active_resources=4),
            source_identity_keys={self.hf_id: stable_identity_key(self.hf_id)})
        preparer = PaneDeliveryPreparer(plan.layout, plan.groups, PresentationAllocationBudget(),
            admitted_producer_source_id=session.admitted_producer_source_id)
        self.assertIsNone(session.admitted_producer_source_id(resource, endpoint))
        session.apply()
        activation = session.start_resource(resource)
        try:
            self.assertEqual(session.admitted_producer_source_id(resource, endpoint), endpoint)
            frame = live_frame(endpoint, "fake-live-session", 7, center_hz=150e6,
                               sample_rate_hz=20e6, receiver_id="RX1")
            first = frame
            deliveries = session.accept_frame(activation, endpoint, first)
            self.assertEqual(len(deliveries), 1)
            with patch.object(preparer, "clear_resource", wraps=preparer.clear_resource) as clear:
                prepared = preparer.prepare(deliveries[0])
                self.assertEqual(prepared.binding.source_id, self.hf_id)
                self.assertEqual(prepared.producer_source_id, endpoint)
                self.assertIs(prepared.bundle, first)
                self.assertEqual(clear.call_count, 0)
                preparer.prepare(deliveries[0])
                self.assertEqual(clear.call_count, 0)
                second = replace(first, paired_capture=PairedCaptureMetadata(2, 4096, 1))
                with self.assertRaisesRegex(ValueError, "typed acquisition group"):
                    preparer.prepare(replace(deliveries[0], bundle=second))
                self.assertEqual(clear.call_count, 0)
            with self.assertRaisesRegex(ValueError, "source"):
                preparer.prepare(replace(deliveries[0], bundle=replace(
                    first, spectrum=replace(first.spectrum, source_id=self.hf_id), identity=None)))
        finally:
            self.assertEqual(session.stop_all(), ())
            self.assertIsNone(session.admitted_producer_source_id(resource, endpoint))
            with self.assertRaisesRegex(ValueError, "source"):
                preparer.prepare(deliveries[0])
            preparer.clear()

    def test_high_fs_rf_filter_is_distinct_from_36mhz_usable_capture(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.ad_id, 100e6, 116e6, sample_rate_hz=61.44e6),
            PaneSlotDraft(2, self.ad_id, 120e6, 136e6, sample_rate_hz=61.44e6),
            PaneSlotDraft(3), PaneSlotDraft(4),
        ))
        jobs = plan.layout.schedule.resources[0].jobs
        self.assertEqual(len(jobs), 1)
        self.assertIs(jobs[0].mode, ReceiverBindingMode.SHARED_CAPTURE)
        self.assertEqual(jobs[0].profile.sample_rate_hz, 61.44e6)
        self.assertEqual(jobs[0].profile.analog_bandwidth_hz, 40e6)
        self.assertEqual(jobs[0].profile.usable_capture_span_hz, 36e6)
        self.assertEqual(plan.initial_ad_configurations[0][1].analog_bandwidth_hz, 40e6)
        self.assertEqual((jobs[0].start_hz, jobs[0].stop_hz), (100e6, 136e6))
        with self.assertRaisesRegex(PaneUserPlanError, "usable capture span"):
            self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 137e6, sample_rate_hz=61.44e6),))

    def test_same_source_distant_panes_are_one_time_sliced_owner(self) -> None:
        plan = self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 108e6),
                              PaneSlotDraft(2, self.ad_id, 200e6, 208e6)))
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(len(plan.layout.schedule.resources[0].jobs), 2)
        self.assertEqual({job.mode for job in plan.layout.schedule.resources[0].jobs},
                         {ReceiverBindingMode.TIME_SLICED})

    def test_priority_uses_existing_weighted_slots_and_stages_actual_first_ad_job(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.ad_id, 100e6, 108e6),
            PaneSlotDraft(2, self.ad_id, 200e6, 208e6, priority=3),
            PaneSlotDraft(3, self.hf_id, 140e6, 148e6), PaneSlotDraft(4)))
        ad, hf = plan.layout.schedule.resources
        counts = Counter(slot.capture_id for slot in ad.slots)
        by_pane = {job.crops[0].pane_id: job for job in ad.jobs}
        self.assertEqual(counts[by_pane["pane-1"].capture_id], 1)
        self.assertEqual(counts[by_pane["pane-2"].capture_id], 3)
        self.assertEqual(ad.slots[0].capture_id, by_pane["pane-2"].capture_id)
        self.assertEqual(plan.initial_ad_configurations[0][1].center_hz, 204e6)
        self.assertEqual(len(hf.jobs), 1)
        self.assertEqual(len(hf.slots), 1)
        self.assertEqual(len(plan.groups), 2)
        self.assertIs(by_pane["pane-2"].scheduler_policy.kind, SchedulerPolicyKind.WEIGHTED)

    def test_shared_capture_merges_strictest_target_and_max_weight_without_second_rx(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.ad_id, 100e6, 104e6, maximum_revisit_s=0.09),
            PaneSlotDraft(2, self.ad_id, 105e6, 108e6, priority=4, maximum_revisit_s=0.08)))
        resource = plan.layout.schedule.resources[0]
        self.assertEqual(len(resource.jobs), 1)
        self.assertEqual(len(plan.groups), 1)
        job = resource.jobs[0]
        self.assertIs(job.mode, ReceiverBindingMode.SHARED_CAPTURE)
        self.assertEqual((job.scheduler_policy.weight, job.scheduler_policy.minimum_revisit_s),
                         (4, 0.08))
        self.assertEqual(tuple((item.requested.weight, item.requested.minimum_revisit_s)
                              for item in plan.scheduler_intents), ((1, 0.09), (4, 0.08)))
        self.assertTrue(all(item.effective == job.scheduler_policy for item in plan.scheduler_intents))
        self.assertTrue(all(slot.control_gap_before is None for slot in resource.slots))
        self.assertIsNone(resource.cycle_transition_gap)
        self.assertTrue(all(item.visits_per_cycle == 4 for item in plan.layout.schedule.pane_revisits))

    def test_both_native_sweep_families_keep_geometry_while_shared_priority_changes(self) -> None:
        for source, rate, fft in ((self.ad_id, 61.44e6, 1024), (self.hf_id, 20e6, 2048)):
            with self.subTest(source=source):
                plan = self._compile(tuple(PaneSlotDraft(number, source, 100e6, 220e6,
                    sample_rate_hz=rate, fft_size=fft, measurement_mode=CaptureMeasurementMode.SWEEP,
                    priority=weight, maximum_revisit_s=1.1)
                    for number, weight in ((1, 1), (2, 9))))
                job = plan.layout.schedule.resources[0].jobs[0]
                self.assertEqual(len(plan.layout.schedule.resources[0].jobs), 1)
                self.assertEqual(job.scheduler_policy.weight, 9)
                self.assertEqual(job.profile.sample_rate_hz, rate)
                self.assertEqual(job.profile.fft_size, 2048)
                self.assertEqual(tuple(item.requested.weight for item in plan.scheduler_intents), (1, 9))
                self.assertEqual(len(plan.ad_sweep_geometry) + len(plan.hackrf_sweep_geometry), 2)

    def test_model_deadline_refusal_retains_every_missed_pane_across_resources(self) -> None:
        with self.assertRaises(PaneUserPlanError) as caught:
            self._compile((
                PaneSlotDraft(1, self.ad_id, 100e6, 108e6, maximum_revisit_s=0.01),
                PaneSlotDraft(2, self.ad_id, 200e6, 208e6, maximum_revisit_s=0.01),
                PaneSlotDraft(3, self.hf_id, 140e6, 148e6, maximum_revisit_s=0.01),
                PaneSlotDraft(4)))
        violations = caught.exception.revisit_violations
        self.assertEqual(tuple(item.pane_id for item in violations), ("pane-1", "pane-2", "pane-3"))
        self.assertTrue(all(item.maximum_revisit_s > item.requested_maximum_revisit_s
                            for item in violations))
        self.assertEqual(len({item.physical_stream_resource_id for item in violations}), 2)

    def test_feasible_target_does_not_change_fs_fft_unit_or_profile(self) -> None:
        plain = self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 108e6),
                               PaneSlotDraft(2, self.ad_id, 200e6, 208e6)))
        planned = self._compile((
            PaneSlotDraft(1, self.ad_id, 100e6, 108e6, maximum_revisit_s=0.2),
            PaneSlotDraft(2, self.ad_id, 200e6, 208e6, maximum_revisit_s=0.2)))
        self.assertEqual(tuple(job.configuration_key for job in plain.layout.schedule.resources[0].jobs),
                         tuple(job.configuration_key for job in planned.layout.schedule.resources[0].jobs))
        self.assertTrue(all(item.requested_maximum_revisit_s == 0.2
                            for item in planned.layout.schedule.pane_revisits))
        self.assertEqual(planned.layout.schedule.resources[0].cycle_duration_s,
                         plain.layout.schedule.resources[0].cycle_duration_s)

    def test_priority_and_target_inputs_are_strict_and_empty_has_no_schedule(self) -> None:
        for priority in (0, 101, True, 1.0, "2"):
            with self.subTest(priority=priority), self.assertRaises(PaneUserPlanError):
                PaneSlotDraft(1, self.ad_id, 100e6, 108e6, priority=priority)
        for target in (0, -1, True, float("nan"), float("inf"), 3601, 10 ** 1000, "0.2"):
            with self.subTest(target=target), self.assertRaises(PaneUserPlanError):
                PaneSlotDraft(1, self.ad_id, 100e6, 108e6, maximum_revisit_s=target)
        for intent in ({"priority": 2}, {"maximum_revisit_s": 1}):
            with self.subTest(intent=intent), self.assertRaises(PaneUserPlanError):
                PaneSlotDraft(1, **intent)

    def test_maximum_user_weights_remain_inside_the_400_slot_budget(self) -> None:
        plan = self._compile(tuple(PaneSlotDraft(number, self.ad_id,
            number * 100e6, number * 100e6 + 8e6, priority=100, maximum_revisit_s=3600)
            for number in range(1, 5)))
        resource = plan.layout.schedule.resources[0]
        self.assertEqual(len(resource.slots), 400)
        self.assertEqual(set(Counter(slot.capture_id for slot in resource.slots).values()), {100})
        self.assertEqual(len(plan.groups), 1)

    def test_tinysa_schedule_keeps_device_dbm_trace_not_an_fft(self) -> None:
        plan = self._compile((PaneSlotDraft(1, self.ts_id, 100e6, 300e6,
            points=1001, priority=7, maximum_revisit_s=9), PaneSlotDraft(2)))
        job = plan.layout.schedule.resources[0].jobs[0]
        self.assertEqual((job.profile.unit, job.profile.points), ("dBm", 1001))
        self.assertIs(job.profile.measurement_mode, CaptureMeasurementMode.INSTRUMENT_TRACE)
        self.assertEqual(job.scheduler_policy.weight, 7)
        self.assertEqual(plan.layout.schedule.pane_revisits[0].requested_maximum_revisit_s, 9)

    def test_exact_decimal_model_boundary_does_not_false_refuse_long_weighted_cycle(self) -> None:
        plan = self._compile(tuple(PaneSlotDraft(number, self.ad_id,
            number * 100e6, number * 100e6 + 8e6, priority=100, maximum_revisit_s=0.32)
            for number in range(1, 5)))
        self.assertTrue(all(abs(item.maximum_revisit_s - 0.32) < 1e-10
                            for item in plan.layout.schedule.pane_revisits))
        with self.assertRaises(PaneUserPlanError):
            self._compile(tuple(PaneSlotDraft(number, self.ad_id,
                number * 100e6, number * 100e6 + 8e6, priority=100, maximum_revisit_s=0.319)
                for number in range(1, 5)))

    def test_hackrf_host_sweep_and_rtbw_share_one_selected_owner(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.hf_id, 100e6, 108e6),
            PaneSlotDraft(2, self.hf_id, 100e6, 220e6,
                          measurement_mode=CaptureMeasurementMode.SWEEP),
            PaneSlotDraft(3), PaneSlotDraft(4),
        ))
        self.assertEqual(len(plan.groups), 1)
        jobs = plan.layout.schedule.resources[0].jobs
        self.assertEqual(len(jobs), 2)
        self.assertEqual({job.profile.measurement_mode for job in jobs},
                         {CaptureMeasurementMode.RTBW, CaptureMeasurementMode.SWEEP})
        self.assertEqual({job.mode for job in jobs}, {ReceiverBindingMode.TIME_SLICED})
        sweep = next(job.profile for job in jobs if isinstance(job.profile, HackrfSweepPaneProfile))
        self.assertIs(sweep.request_template.source, self.selected[self.hf_id])
        self.assertEqual((sweep.request_template.start_hz, sweep.request_template.stop_hz),
                         (100_000_000, 220_000_000))
        self.assertIsNone(sweep.hop_size)

    def test_identical_hackrf_sweep_panes_share_one_capture(self) -> None:
        drafts = tuple(PaneSlotDraft(number, self.hf_id, 100e6, 220e6,
                                     measurement_mode=CaptureMeasurementMode.SWEEP)
                       for number in (1, 2))
        plan = self._compile(drafts)
        jobs = plan.layout.schedule.resources[0].jobs
        self.assertEqual(len(jobs), 1)
        self.assertIs(jobs[0].mode, ReceiverBindingMode.SHARED_CAPTURE)
        self.assertEqual({crop.pane_id for crop in jobs[0].crops}, {"pane-1", "pane-2"})

    def test_unsupported_sweep_geometry_and_family_refuse_before_rx(self) -> None:
        for start, stop, fft, rate in ((100e6, 108e6, 4096, 20e6),
                                        (100e6, 225.5e6, 4096, 20e6),
                                        (100e6, 220e6, 16384, 20e6),
                                        (100e6, 220e6, 4096, 16e6)):
            with self.subTest(start=start, stop=stop, fft=fft, rate=rate):
                with self.assertRaises(PaneUserPlanError):
                    self._compile((PaneSlotDraft(1, self.hf_id, start, stop,
                         sample_rate_hz=rate, fft_size=fft,
                         measurement_mode=CaptureMeasurementMode.SWEEP),))
        with self.assertRaisesRegex(PaneUserPlanError, "61.44 MS/s"):
            self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 220e6,
                         measurement_mode=CaptureMeasurementMode.SWEEP),))
        with self.assertRaisesRegex(PaneUserPlanError, "device trace"):
            self._compile((PaneSlotDraft(1, self.ts_id, 200e6, 210e6,
                         measurement_mode=CaptureMeasurementMode.SWEEP),))
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, measurement_mode=CaptureMeasurementMode.SWEEP)

    def test_ad_sweep_n_is_inside_36mhz_not_the_physical_fft(self) -> None:
        for n, physical_f in ((1024, 2048), (4096, 8192), (16384, 32768)):
            with self.subTest(n=n):
                plan = self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 220e6,
                    sample_rate_hz=61.44e6, fft_size=n,
                    measurement_mode=CaptureMeasurementMode.SWEEP),))
                job = plan.layout.schedule.resources[0].jobs[0]
                self.assertIsInstance(job.profile, Ad936xSweepPaneProfile)
                profile = job.profile
                self.assertIs(profile.source, self.selected[self.ad_id])
                self.assertEqual(profile.request_template.analysis_bins_per_usable_window, n)
                self.assertEqual((profile.fft_size, profile.sample_rate_hz), (physical_f, 61.44e6))
                self.assertIsNone(profile.hop_size)
                geometry = plan.ad_sweep_geometry[0][1]
                self.assertEqual((geometry.segment_count, geometry.segment_stride_hz), (4, 34e6))
                self.assertEqual(geometry.output_spacing_hz, 36e6 / n)
                self.assertGreaterEqual(geometry.output_spacing_hz, geometry.physical_bin_spacing_hz)
                self.assertEqual(plan.initial_ad_configurations[0][1], profile.configuration)
                self.assertLess(job.stop_hz, profile.request_template.stop_hz)

    def test_identical_ad_sweep_shares_but_different_plans_time_slice(self) -> None:
        for stop, mode, count in ((220e6, ReceiverBindingMode.SHARED_CAPTURE, 1),
                                  (254e6, ReceiverBindingMode.TIME_SLICED, 2)):
            with self.subTest(stop=stop):
                plan = self._compile(tuple(PaneSlotDraft(number, self.ad_id, 100e6, upper,
                    sample_rate_hz=61.44e6, measurement_mode=CaptureMeasurementMode.SWEEP)
                    for number, upper in ((1, 220e6), (2, stop))))
                jobs = plan.layout.schedule.resources[0].jobs
                self.assertEqual(len(jobs), count)
                self.assertEqual({job.mode for job in jobs}, {mode})
                self.assertEqual(len(plan.groups), 1)

    def test_ad_sweep_and_rtbw_remain_explicit_jobs_on_one_rx(self) -> None:
        plan = self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 108e6),
            PaneSlotDraft(2, self.ad_id, 100e6, 220e6, sample_rate_hz=61.44e6,
                          measurement_mode=CaptureMeasurementMode.SWEEP)))
        jobs = plan.layout.schedule.resources[0].jobs
        self.assertEqual(len(jobs), 2)
        self.assertEqual({job.profile.measurement_mode for job in jobs},
                         {CaptureMeasurementMode.RTBW, CaptureMeasurementMode.SWEEP})
        self.assertEqual({job.mode for job in jobs}, {ReceiverBindingMode.TIME_SLICED})

    def test_ad_sweep_segment_and_memory_bounds_are_not_bypassed(self) -> None:
        for start, stop, n in ((70e6, 100e9, 1024), (100e6, 2e9, 16384)):
            with self.subTest(stop=stop, n=n), self.assertRaisesRegex(PaneUserPlanError, "native geometry"):
                self._compile((PaneSlotDraft(1, self.ad_id, start, stop,
                    sample_rate_hz=61.44e6, fft_size=n,
                    measurement_mode=CaptureMeasurementMode.SWEEP),))

    def test_full_sdr_ranges_three_sweep_panes_keep_exact_geometry_and_memory(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.ad_id, 70e6, 6e9, sample_rate_hz=61.44e6, fft_size=1024,
                          measurement_mode=CaptureMeasurementMode.SWEEP),
            PaneSlotDraft(2, self.hf_id, 1e6, 6e9, fft_size=1024,
                          measurement_mode=CaptureMeasurementMode.SWEEP),
            PaneSlotDraft(3, self.ts_id, 100e6, 300e6, points=1001), PaneSlotDraft(4)))
        self.assertEqual(len(plan.layout.schedule.resources), 3)
        ad = plan.ad_sweep_geometry[0][1]
        hf = plan.hackrf_sweep_geometry[0][1]
        self.assertEqual((ad.segment_count, ad.physical_fft_size), (175, 2048))
        self.assertEqual((hf.segment_count, hf.physical_fft_size), (1200, 1024))
        self.assertEqual(plan.hackrf_hardware_ranges, (("pane-2", 1_000_000, 6_001_000_000),))
        self.assertLess(ad.reduced.total_bytes, 64 * 1024 * 1024)
        self.assertLess(hf.reduced.total_bytes, 64 * 1024 * 1024)
        for source, rate in ((self.ad_id, 61.44e6), (self.hf_id, 20e6)):
            with self.subTest(source=source), self.assertRaises(PaneUserPlanError):
                self._compile((PaneSlotDraft(1, source, 70e6 if source == self.ad_id else 1e6,
                    6e9, sample_rate_hz=rate, fft_size=16384 if source == self.ad_id else 4096,
                    measurement_mode=CaptureMeasurementMode.SWEEP),))

    def test_three_family_sweep_2x2_compiles_without_fabricating_empty_rx(self) -> None:
        plan = self._compile((
            PaneSlotDraft(1, self.ad_id, 100e6, 220e6, sample_rate_hz=61.44e6,
                          measurement_mode=CaptureMeasurementMode.SWEEP),
            PaneSlotDraft(2, self.hf_id, 100e6, 220e6,
                          measurement_mode=CaptureMeasurementMode.SWEEP),
            PaneSlotDraft(3, self.ts_id, 100e6, 300e6, points=1001), PaneSlotDraft(4)))
        self.assertEqual(plan.layout.empty_slots, (4,))
        self.assertEqual(len(plan.groups), 3)
        self.assertEqual([resource.jobs[0].profile.measurement_mode for resource in
                          plan.layout.schedule.resources],
                         [CaptureMeasurementMode.SWEEP, CaptureMeasurementMode.SWEEP,
                          CaptureMeasurementMode.INSTRUMENT_TRACE])

    def test_hackrf_full_range_physical_fft2048_is_explicit_and_budgeted(self) -> None:
        plan = self._compile((PaneSlotDraft(1, self.hf_id, 1e6, 6e9, fft_size=2048,
                              measurement_mode=CaptureMeasurementMode.SWEEP),))
        profile = plan.layout.schedule.resources[0].jobs[0].profile
        self.assertIsInstance(profile, HackrfSweepPaneProfile)
        self.assertEqual(profile.request_template.fft_size, 2048)
        geometry = plan.hackrf_sweep_geometry[0][1]
        self.assertEqual(geometry.physical_fft_size, 2048)
        self.assertEqual(geometry.segment_count, 1200)
        self.assertEqual(geometry.output_spacing_hz, 20e6 / 2048)
        self.assertGreater(geometry.reduced.total_bytes, 64 * 1024 * 1024)
        self.assertLess(geometry.reduced.total_bytes, 128 * 1024 * 1024)
        self.assertEqual(plan.hackrf_hardware_ranges, (("pane-1", 1_000_000, 6_001_000_000),))

    def test_new_physical_fft_choice_does_not_expand_other_pane_profiles(self) -> None:
        for source, mode, rate, stop in (
                (self.ad_id, CaptureMeasurementMode.SWEEP, 61.44e6, 220e6),
                (self.ad_id, CaptureMeasurementMode.RTBW, 20e6, 108e6),
                (self.hf_id, CaptureMeasurementMode.RTBW, 20e6, 108e6)):
            with self.subTest(source=source, mode=mode), self.assertRaisesRegex(
                    PaneUserPlanError, "qualified HackRF Sweep"):
                self._compile((PaneSlotDraft(1, source, 100e6, stop, fft_size=2048,
                            sample_rate_hz=rate, measurement_mode=mode),))

    def test_excess_span_and_all_empty_fail_before_device_operation(self) -> None:
        with self.assertRaises(PaneUserPlanError):
            self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 120e6),))
        with self.assertRaises(PaneUserPlanError):
            self._compile((PaneSlotDraft(1), PaneSlotDraft(2)))


if __name__ == "__main__":
    unittest.main()

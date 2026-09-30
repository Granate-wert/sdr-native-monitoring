"""User-editable APP-07 slots compile to the SAME bounded pane contracts."""

from __future__ import annotations

import unittest

from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.domain.pane_scheduler import Ad936xSweepPaneProfile, CaptureMeasurementMode, HackrfSweepPaneProfile
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.ui.v2_pane_user_plan import (
    PaneSlotDraft, PaneUserPlanError, compile_user_pane_plan,
)

from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_fixture
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from types import SimpleNamespace


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

"""User-editable APP-07 slots compile to the SAME bounded pane contracts."""

from __future__ import annotations

import unittest

from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, HackrfSweepPaneProfile
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
                                        (100e6, 225e6, 4096, 20e6),
                                        (100e6, 220e6, 16384, 20e6),
                                        (100e6, 220e6, 4096, 16e6)):
            with self.subTest(start=start, stop=stop, fft=fft, rate=rate):
                with self.assertRaises(PaneUserPlanError):
                    self._compile((PaneSlotDraft(1, self.hf_id, start, stop,
                         sample_rate_hz=rate, fft_size=fft,
                         measurement_mode=CaptureMeasurementMode.SWEEP),))
        with self.assertRaisesRegex(PaneUserPlanError, "AD936x wide Sweep"):
            self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 220e6,
                         measurement_mode=CaptureMeasurementMode.SWEEP),))
        with self.assertRaisesRegex(PaneUserPlanError, "device trace"):
            self._compile((PaneSlotDraft(1, self.ts_id, 200e6, 210e6,
                         measurement_mode=CaptureMeasurementMode.SWEEP),))
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, measurement_mode=CaptureMeasurementMode.SWEEP)

    def test_excess_span_and_all_empty_fail_before_device_operation(self) -> None:
        with self.assertRaises(PaneUserPlanError):
            self._compile((PaneSlotDraft(1, self.ad_id, 100e6, 120e6),))
        with self.assertRaises(PaneUserPlanError):
            self._compile((PaneSlotDraft(1), PaneSlotDraft(2)))


if __name__ == "__main__":
    unittest.main()

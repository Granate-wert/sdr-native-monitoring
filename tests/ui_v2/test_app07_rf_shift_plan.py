"""Pure RF shifts preserve original all-family and shared scheduling intent."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.ui.v2_pane_rf_plan import PaneRfPlanContext, compile_pane_rf_shift
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, PaneUserPlanError, RtbwBandPolicy, compile_user_pane_plan

from tests.ui_v2 import test_app07_pane_user_plan as plan_fixture
from tests.ui_v2.test_app06_tinysa_runtime_settings import settings_graph
from tests.ui_v2.test_app07_tinysa_pane_settings import _rbw, _v2


class RfShiftPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = plan_fixture.PaneUserPlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def context(self, drafts) -> PaneRfPlanContext:
        plan = self.fixture._compile(drafts)
        return PaneRfPlanContext(plan, drafts, tuple(
            (source, self.fixture.selected[source], self.fixture.revisions[source])
            for _resource, source in plan.resource_sources))

    def test_wide_ad_and_hackrf_preserve_fs_filter_fft_hop_group_band_and_peer(self) -> None:
        drafts = (PaneSlotDraft(1, self.fixture.ad_id, 100e6, 156e6, sample_rate_hz=61.44e6,
                               rtbw_band=RtbwBandPolicy.FULL_RECEIVE),
                  PaneSlotDraft(2, self.fixture.hf_id, 140e6, 160e6, rtbw_band=RtbwBandPolicy.FULL_RECEIVE))
        context = self.context(drafts)
        for slot in (1, 2):
            with self.subTest(slot=slot):
                proposal = compile_pane_rf_shift(context, slot, 20_000_000.4)
                self.assertEqual(proposal.effective_shift_hz, 20e6)
                new = proposal.proposed_context
                self.assertEqual(new.drafts[slot - 1], replace(drafts[slot - 1],
                    start_hz=drafts[slot - 1].start_hz + 20e6, stop_hz=drafts[slot - 1].stop_hz + 20e6))
                self.assertIs(new.drafts[2 - slot], drafts[2 - slot])
                before = context.plan.layout.schedule.resources[slot - 1].jobs[0].profile
                after = new.plan.layout.schedule.resources[slot - 1].jobs[0].profile
                self.assertEqual(after.compatibility_key, before.compatibility_key)

    def test_shared_to_timeslice_keeps_original_weights_and_unchanged_neighbor_range(self) -> None:
        drafts = (PaneSlotDraft(1, self.fixture.ad_id, 100e6, 104e6, priority=5, maximum_revisit_s=1.0),
                  PaneSlotDraft(2, self.fixture.ad_id, 105e6, 108e6, priority=2, maximum_revisit_s=0.7))
        context = self.context(drafts)
        self.assertIs(context.plan.layout.schedule.resources[0].jobs[0].mode, ReceiverBindingMode.SHARED_CAPTURE)
        proposal = compile_pane_rf_shift(context, 1, 30e6)
        following = proposal.proposed_context
        self.assertIs(following.drafts[1], drafts[1])
        self.assertEqual(tuple(item.requested.weight for item in following.plan.scheduler_intents), (5, 2))
        self.assertEqual(tuple(item.effective.weight for item in following.plan.scheduler_intents), (5, 2))
        self.assertEqual(tuple(item.requested.minimum_revisit_s for item in following.plan.scheduler_intents), (1.0, 0.7))
        self.assertEqual(len(following.plan.layout.schedule.resources[0].jobs), 2)

    def test_infeasible_split_refuses_all_deadlines_before_stop(self) -> None:
        context = self.context((PaneSlotDraft(1, self.fixture.ad_id, 100e6, 104e6, maximum_revisit_s=0.07),
                                PaneSlotDraft(2, self.fixture.ad_id, 105e6, 108e6, maximum_revisit_s=0.07)))
        with self.assertRaises(PaneUserPlanError) as caught:
            compile_pane_rf_shift(context, 1, 30e6)
        self.assertEqual({item.pane_id for item in caught.exception.revisit_violations}, {"pane-1", "pane-2"})

    def test_hackrf_sweep_explicit_mhz_rounding_preserves_full_request_settings(self) -> None:
        context = self.context((PaneSlotDraft(1, self.fixture.hf_id, 100e6, 300e6, fft_size=2048,
                                              measurement_mode=CaptureMeasurementMode.SWEEP),))
        proposal = compile_pane_rf_shift(context, 1, -1_500_000.0)
        self.assertEqual((proposal.quantum_hz, proposal.effective_shift_hz), (1e6, -2e6))
        old = context.plan.layout.schedule.resources[0].jobs[0].profile.request_template
        new = proposal.proposed_context.plan.layout.schedule.resources[0].jobs[0].profile.request_template
        self.assertEqual(new, replace(old, start_hz=98_000_000, stop_hz=298_000_000))

    def test_ad_sweep_and_tinysa_preserve_n_window_overlap_points_input_and_units(self) -> None:
        tiny = _v2(settings_graph())
        self.addCleanup(tiny.live.shutdown)
        source = tiny.live.discover(startup=True)[0]
        tiny.live.select_device(source.device_id)
        selection = tiny.live.current_source_selection()
        for draft in (PaneSlotDraft(1, self.fixture.ad_id, 100e6, 300e6, sample_rate_hz=61.44e6,
                                   fft_size=1024, measurement_mode=CaptureMeasurementMode.SWEEP),
                      PaneSlotDraft(1, source.device_id, 100e6, 300e6, points=1001, tinysa=_rbw())):
            with self.subTest(source=draft.source_id):
                if draft.tinysa is None:
                    context = self.context((draft,))
                else:
                    plan = compile_user_pane_plan((draft,), {source.device_id: selection.selected},
                                                  {source.device_id: selection.revision})
                    context = PaneRfPlanContext(plan, (draft,), ((source.device_id, selection.selected, selection.revision),))
                new = compile_pane_rf_shift(context, 1, 10e6).proposed_context
                self.assertEqual(new.drafts[0], replace(draft, start_hz=110e6, stop_hz=310e6))
                before = context.plan.layout.schedule.resources[0].jobs[0].profile
                after = new.plan.layout.schedule.resources[0].jobs[0].profile
                self.assertEqual(after.unit, before.unit)
                self.assertEqual(after.request_template, replace(before.request_template,
                    start_hz=110_000_000, stop_hz=310_000_000))

    def test_empty_nonfinite_bool_subquantum_and_out_of_capability_refuse_without_clamping(self) -> None:
        context = self.context((PaneSlotDraft(1, self.fixture.ad_id, 100e6, 108e6), PaneSlotDraft(2)))
        for slot, delta in ((2, 1e6), (1, float("nan")), (1, True), (1, 0.1), (1, -1e9)):
            with self.subTest(slot=slot, delta=delta), self.assertRaises(PaneUserPlanError):
                compile_pane_rf_shift(context, slot, delta)

    def test_context_copies_mutable_containers_and_refuses_malformed_selection(self) -> None:
        original = self.context((PaneSlotDraft(1, self.fixture.ad_id, 100e6, 108e6),))
        drafts = list(original.drafts)
        selections = [list(entry) for entry in original.selections]
        frozen = PaneRfPlanContext(original.plan, drafts, selections)
        drafts.clear()
        selections[0].clear()
        self.assertEqual(frozen.drafts, original.drafts)
        self.assertEqual(frozen.selections, original.selections)
        self.assertIs(frozen.selections[0][1], original.selections[0][1])
        for entries in (((),), ((self.fixture.ad_id, object(), 1),),
                        ((self.fixture.ad_id, original.selections[0][1], True),)):
            with self.subTest(entries=entries), self.assertRaises(PaneUserPlanError):
                PaneRfPlanContext(original.plan, original.drafts, entries)


if __name__ == "__main__":
    unittest.main()

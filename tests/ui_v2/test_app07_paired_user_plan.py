"""Typed user paired intent with current selected MOCK topology, no RF I/O."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.identity import SessionId
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.receiver_topology import (
    IqComponent, ReceiverBindingMode, ReceiverChain, ReceiverChainSelection,
    ReceiverTopologySnapshot, StreamScanElement,
)
from sdr_monitor.ui.v2_pane_rf_plan import PaneRfPlanContext, compile_pane_rf_shift
from sdr_monitor.ui.v2_pane_user_plan import (
    PanePairedSelectionReceipt, PaneSlotDraft, PaneUserPlanError, compile_user_pane_plan,
)
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


class PairedUserPlanTests(unittest.TestCase):
    def setUp(self):
        self.native, self.graph = _ad_graph()
        self.source = self.graph.live.discover(startup=True)[0]
        self.graph.live.select_device(self.source.device_id)
        selection = self.graph.live.current_source_selection()
        self.source, self.revision = selection.selected, selection.revision
        before = self.graph.live.current_snapshot()
        topology = ReceiverTopologySnapshot("mock:physical", "ad9361", "mock-board", ("voltage0", "voltage1"),
            tuple(StreamScanElement(f"voltage{index}", chain, component, index, 16, 12, 0, True, False)
                  for index, (chain, component) in enumerate((
                      (ReceiverChain.RX1, IqComponent.IN_PHASE), (ReceiverChain.RX1, IqComponent.QUADRATURE),
                      (ReceiverChain.RX2, IqComponent.IN_PHASE), (ReceiverChain.RX2, IqComponent.QUADRATURE)))))
        device = replace(before.device, capabilities=replace(before.device.capabilities, receiver_topology=topology))
        self.snapshot = replace(before, device=device)
        self.receipt = PanePairedSelectionReceipt(self.source, self.revision, self.snapshot)
        self.drafts = (PaneSlotDraft(1, self.source.device_id, 2_440e6, 2_450e6, sample_rate_hz=61_440_000.),
                       PaneSlotDraft(2, self.source.device_id, 2_450e6, 2_460e6, sample_rate_hz=61_440_000.,
                                     receiver_selection=ReceiverChainSelection.RX2), PaneSlotDraft(3), PaneSlotDraft(4))

    def tearDown(self):
        self.graph.live.shutdown()

    def compile(self, drafts=None, receipts=None):
        return compile_user_pane_plan(self.drafts if drafts is None else drafts,
            {self.source.device_id: self.source}, {self.source.device_id: self.revision},
            paired_selections={self.source.device_id: self.receipt} if receipts is None else receipts)

    def test_pair_is_one_shared_job_and_explicit_chains_with_no_rf_or_staged_profile(self):
        self.assertIsNone(self.snapshot.applied)
        plan = self.compile()
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(tuple(item.selection for item in plan.groups[0].endpoints),
                         (ReceiverChainSelection.RX1, ReceiverChainSelection.RX2))
        resource = plan.layout.schedule.resources[0]
        self.assertEqual(len(resource.jobs), 1)
        self.assertIs(resource.jobs[0].mode, ReceiverBindingMode.SHARED_CAPTURE)
        self.assertEqual(len(resource.jobs[0].receiver_endpoint_ids), 2)
        self.assertEqual(plan.initial_ad_configurations[0][1].center_hz, 2_450e6)
        self.assertEqual(plan.paired_selections, (self.receipt,))
        self.assertEqual(self.native.engines, [])

    def test_missing_extra_or_wrong_revision_receipt_refuses(self):
        with self.assertRaises(PaneUserPlanError):
            self.compile(receipts={})
        with self.assertRaises(PaneUserPlanError):
            self.compile(receipts={"foreign": self.receipt})
        with self.assertRaises(PaneUserPlanError):
            self.compile(receipts={self.source.device_id: replace(self.receipt, selection_revision=self.revision + 1)})

    def test_current_receipt_checks_session_topology_identity_and_state_without_configuration_claim(self):
        self.receipt.validate_current(self.source, self.revision, replace(
            self.snapshot, state=LiveSessionState.RUNNING, stop_required=True))
        invalid = (replace(self.snapshot, session_id=SessionId("foreign")),
                   replace(self.snapshot, error="failed"), replace(self.snapshot, stop_required=True),
                   replace(self.snapshot, device=replace(self.snapshot.device, serial=None)),
                   replace(self.snapshot, device=replace(self.snapshot.device,
                       capabilities=replace(self.snapshot.device.capabilities, receiver_topology=None))))
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot.state), self.assertRaises(PaneUserPlanError):
                self.receipt.validate_current(self.source, self.revision, snapshot)
        with self.assertRaises(PaneUserPlanError):
            self.receipt.validate_current(replace(self.source), self.revision, self.snapshot)

    def test_lone_rx2_and_untyped_or_both_pane_selections_refuse(self):
        with self.assertRaises(PaneUserPlanError):
            self.compile((replace(self.drafts[1], number=1),))
        for value in ("rx1", "rx2", True, ReceiverChainSelection.BOTH):
            with self.subTest(value=value), self.assertRaises(PaneUserPlanError):
                replace(self.drafts[0], receiver_selection=value)
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, receiver_selection=ReceiverChainSelection.RX2)

    def test_incompatible_profile_or_outside_common_window_refuses_no_time_slice(self):
        for changed in (replace(self.drafts[1], fft_size=16384),
                        replace(self.drafts[1], sample_rate_hz=20_000_000.),
                        replace(self.drafts[1], start_hz=2_490e6, stop_hz=2_500e6)):
            with self.subTest(changed=changed), self.assertRaisesRegex(PaneUserPlanError, "common profile"):
                self.compile((self.drafts[0], changed))

    def test_paired_sweep_refuses_before_implicit_fallback(self):
        with self.assertRaisesRegex(PaneUserPlanError, "paired Sweep"):
            self.compile((replace(self.drafts[0], measurement_mode=CaptureMeasurementMode.SWEEP), self.drafts[1]))

    def test_rf_shift_preserves_pair_receipt_common_profile_and_other_crop(self):
        plan = self.compile()
        context = PaneRfPlanContext(plan, self.drafts, ((self.source.device_id, self.source, self.revision),))
        proposal = compile_pane_rf_shift(context, 1, 1_000_000.)
        changed = proposal.proposed_context
        self.assertEqual(changed.plan.groups, plan.groups)
        self.assertEqual(changed.plan.paired_selections, plan.paired_selections)
        self.assertEqual(changed.drafts[1], self.drafts[1])
        self.assertEqual(len(changed.plan.layout.schedule.resources[0].jobs), 1)
        with self.assertRaises(PaneUserPlanError):
            compile_pane_rf_shift(context, 1, -50_000_000.)

    def test_shared_priorities_merge_without_relaxing_original_intent(self):
        plan = self.compile((replace(self.drafts[0], priority=2), replace(self.drafts[1], priority=3)))
        self.assertEqual(tuple(item.requested.weight for item in plan.scheduler_intents), (2, 3))
        self.assertEqual(tuple(item.effective.weight for item in plan.scheduler_intents), (3, 3))


if __name__ == "__main__":
    unittest.main()

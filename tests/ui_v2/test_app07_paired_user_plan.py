"""Typed user paired intent with current selected MOCK topology, no RF I/O."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.device_capabilities import CapabilityRange
from sdr_monitor.domain.identity import SessionId
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.pane_scheduler import Ad936xPairedSweepPaneProfile, CaptureMeasurementMode
from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
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
        with self.assertRaises(PaneUserPlanError) as caught:
            self.compile(receipts={})
        self.assertIs(caught.exception.reason, PaneUserRefusal.PAIRED_SELECTION_REQUIRED)
        with self.assertRaises(PaneUserPlanError):
            self.compile(receipts={"foreign": self.receipt})
        with self.assertRaises(PaneUserPlanError) as caught:
            self.compile(receipts={self.source.device_id: replace(self.receipt, selection_revision=self.revision + 1)})
        self.assertIs(caught.exception.reason, PaneUserRefusal.SELECTION_CHANGED)

    def test_extra_receipt_has_distinct_exact_set_refusal(self):
        with self.assertRaises(PaneUserPlanError) as caught:
            self.compile(receipts={self.source.device_id: self.receipt, "foreign": self.receipt})
        self.assertIs(caught.exception.reason, PaneUserRefusal.PAIRED_RECEIPTS_MISMATCH)

    def test_initial_single_rx_missing_topology_or_identity_have_typed_refusals(self):
        device = self.snapshot.device
        topology = device.capabilities.receiver_topology
        single = replace(topology, phy_rx_channel_ids=("voltage0",), scan_elements=topology.scan_elements[:2])
        cases = (
            (replace(device, serial=None), PaneUserRefusal.PAIRED_STABLE_IDENTITY_REQUIRED),
            (replace(device, identity_key=None), PaneUserRefusal.PAIRED_STABLE_IDENTITY_REQUIRED),
            (replace(device, capabilities=replace(device.capabilities, receiver_topology=None)),
             PaneUserRefusal.PAIRED_TOPOLOGY_UNAVAILABLE),
            (replace(device, capabilities=replace(device.capabilities, receiver_topology=single)),
             PaneUserRefusal.PAIRED_TOPOLOGY_UNAVAILABLE),
        )
        for changed, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(PaneUserPlanError) as caught:
                PanePairedSelectionReceipt(self.source, self.revision, replace(self.snapshot, device=changed))
            self.assertIs(caught.exception.reason, reason)
        self.assertEqual(self.native.engines, [])

    def test_changed_session_identity_or_topology_are_stale_not_initial_capabilities(self):
        device = self.snapshot.device
        topology = device.capabilities.receiver_topology
        single = replace(topology, phy_rx_channel_ids=("voltage0",), scan_elements=topology.scan_elements[:2])
        for snapshot in (
            replace(self.snapshot, session_id=SessionId("foreign")),
            replace(self.snapshot, device=replace(device, serial="foreign")),
            replace(self.snapshot, device=replace(device, identity_key="foreign")),
            replace(self.snapshot, device=replace(device, capabilities=replace(device.capabilities,
                                                                             receiver_topology=single))),
        ):
            with self.subTest(snapshot=snapshot), self.assertRaises(PaneUserPlanError) as caught:
                self.receipt.validate_current(self.source, self.revision, snapshot)
            self.assertIs(caught.exception.reason, PaneUserRefusal.SELECTION_CHANGED)

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
        with self.assertRaises(PaneUserPlanError) as caught:
            self.compile((replace(self.drafts[1], number=1),))
        self.assertIs(caught.exception.reason, PaneUserRefusal.PAIRED_ASSIGNMENT_UNSUPPORTED)
        for value in ("rx1", "rx2", True, ReceiverChainSelection.BOTH):
            with self.subTest(value=value), self.assertRaises(PaneUserPlanError) as caught:
                replace(self.drafts[0], receiver_selection=value)
            self.assertIs(caught.exception.reason, PaneUserRefusal.INVALID_RECEIVER_SELECTION)
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, receiver_selection=ReceiverChainSelection.RX2)

    def test_incompatible_profile_or_outside_common_window_refuses_no_time_slice(self):
        for changed, reason in (
            (replace(self.drafts[1], fft_size=16384), PaneUserRefusal.PAIRED_PROFILE_CONFLICT),
            (replace(self.drafts[1], sample_rate_hz=20_000_000.), PaneUserRefusal.PAIRED_PROFILE_CONFLICT),
            (replace(self.drafts[1], start_hz=2_490e6, stop_hz=2_500e6), PaneUserRefusal.PAIRED_WINDOW_CONFLICT),
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(PaneUserPlanError, "common profile") as caught:
                self.compile((self.drafts[0], changed))
            self.assertIs(caught.exception.reason, reason)

    def test_mixed_sweep_and_rtbw_refuse_before_implicit_fallback(self):
        with self.assertRaisesRegex(PaneUserPlanError, "cannot mix") as caught:
            self.compile((replace(self.drafts[0], measurement_mode=CaptureMeasurementMode.SWEEP), self.drafts[1]))
        self.assertIs(caught.exception.reason, PaneUserRefusal.PAIRED_MODE_UNSUPPORTED)

    def test_distinct_disjoint_sweep_crops_use_one_exact_common_plan(self):
        left = replace(self.drafts[0], start_hz=2_400e6, stop_hz=2_410e6,
                       measurement_mode=CaptureMeasurementMode.SWEEP)
        right = replace(self.drafts[1], start_hz=2_470e6, stop_hz=2_480e6,
                        measurement_mode=CaptureMeasurementMode.SWEEP)
        plan = self.compile((left, right))
        resource = plan.layout.schedule.resources[0]
        self.assertEqual(len(resource.jobs), 1)
        job = resource.jobs[0]
        self.assertIsInstance(job.profile, Ad936xPairedSweepPaneProfile)
        intent = job.profile.paired_request
        self.assertEqual((job.start_hz, job.stop_hz), (2_400e6, 2_480e6))
        self.assertEqual((intent.sweep.start_hz, intent.sweep.stop_hz), (2_400e6, 2_480e6))
        self.assertEqual((intent.pair.primary_source_id, intent.pair.secondary_source_id),
                         job.receiver_endpoint_ids)
        self.assertIs(intent.selected_snapshot, self.snapshot)
        self.assertEqual(plan.paired_selections, (self.receipt,))
        self.assertEqual(len(plan.paired_sweep_geometry), 1)
        self.assertEqual(plan.ad_sweep_geometry, ())
        self.assertEqual(tuple(item[:2] for item in plan.paired_sweep_requested_crops),
                         (("pane-resource-1", "pane-1"), ("pane-resource-1", "pane-2")))
        self.assertEqual(tuple(crop.start_hz for crop in job.crops), (2_400e6, 2_470e6))
        self.assertTrue(all(crop.stop_hz < draft.stop_hz for crop, draft in zip(job.crops, (left, right))))
        self.assertEqual(self.native.engines, [])

    def test_offset_crop_uses_common_grid_and_excludes_requested_stop(self):
        left = replace(self.drafts[0], start_hz=2_400e6, stop_hz=2_410_001_000.,
                       measurement_mode=CaptureMeasurementMode.SWEEP)
        right = replace(self.drafts[1], start_hz=2_470_001_000., stop_hz=2_480_003_000.,
                        measurement_mode=CaptureMeasurementMode.SWEEP)
        plan = self.compile((left, right))
        job = plan.layout.schedule.resources[0].jobs[0]
        spacing = plan.paired_sweep_geometry[0][1].output_spacing_hz
        for crop, draft in zip(job.crops, (left, right)):
            self.assertLess(crop.stop_hz, draft.stop_hz)
            self.assertGreaterEqual(crop.stop_hz + spacing, draft.stop_hz)
            self.assertGreater(crop.stop_hz, crop.start_hz)

    def test_paired_sweep_rejects_mismatched_analysis_or_rate(self):
        left = replace(self.drafts[0], measurement_mode=CaptureMeasurementMode.SWEEP)
        right = replace(self.drafts[1], measurement_mode=CaptureMeasurementMode.SWEEP)
        for changed in (replace(right, fft_size=16384), replace(right, sample_rate_hz=20e6)):
            with self.subTest(changed=changed), self.assertRaises(PaneUserPlanError) as caught:
                self.compile((left, changed))
            self.assertIs(caught.exception.reason, PaneUserRefusal.PAIRED_PROFILE_CONFLICT)

    def test_paired_sweep_common_envelope_cannot_cross_observed_tuning_gap(self):
        capability = replace(self.source.binding.snapshot, tuning_ranges_hz=(
            CapabilityRange(2_390e6, 2_420e6, "Hz"),
            CapabilityRange(2_460e6, 2_490e6, "Hz")))
        source = replace(self.source, binding=replace(self.source.binding, snapshot=capability))
        snapshot = replace(self.snapshot, device=replace(self.snapshot.device,
                                                           capability_snapshot=capability))
        receipt = PanePairedSelectionReceipt(source, self.revision, snapshot)
        drafts = (replace(self.drafts[0], start_hz=2_400e6, stop_hz=2_410e6,
                          measurement_mode=CaptureMeasurementMode.SWEEP),
                  replace(self.drafts[1], start_hz=2_470e6, stop_hz=2_480e6,
                          measurement_mode=CaptureMeasurementMode.SWEEP))
        with self.assertRaises(PaneUserPlanError) as caught:
            compile_user_pane_plan(drafts, {source.device_id: source},
                                   {source.device_id: self.revision},
                                   paired_selections={source.device_id: receipt})
        self.assertIs(caught.exception.reason, PaneUserRefusal.OBSERVED_RANGE_EXCEEDED)
        self.assertEqual(self.native.engines, [])

    def test_paired_sweep_stale_receipt_refuses_before_common_plan(self):
        drafts = tuple(replace(draft, measurement_mode=CaptureMeasurementMode.SWEEP)
                       if draft.source_id is not None else draft for draft in self.drafts)
        with self.assertRaises(PaneUserPlanError) as caught:
            self.compile(drafts, {self.source.device_id: replace(self.receipt,
                                                                  selection_revision=self.revision + 1)})
        self.assertIs(caught.exception.reason, PaneUserRefusal.SELECTION_CHANGED)
        self.assertEqual(self.native.engines, [])

    def test_paired_sweep_placeholder_serial_refuses_before_common_plan(self):
        snapshot = replace(self.snapshot, device=replace(self.snapshot.device, serial="unknown"))
        receipt = PanePairedSelectionReceipt(self.source, self.revision, snapshot)
        drafts = tuple(replace(draft, measurement_mode=CaptureMeasurementMode.SWEEP)
                       if draft.source_id is not None else draft for draft in self.drafts)
        with self.assertRaises(PaneUserPlanError) as caught:
            self.compile(drafts, {self.source.device_id: receipt})
        self.assertIs(caught.exception.reason, PaneUserRefusal.PAIRED_STABLE_IDENTITY_REQUIRED)
        self.assertEqual(self.native.engines, [])

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

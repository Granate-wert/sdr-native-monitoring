"""RF-plan changes through exact V2 graphs/owners/pump, fake SDK only."""

from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdr_monitor.domain.pane_scheduler import PaneControlGapReason
from sdr_monitor.services.pane_resource_session import PaneResourceError
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, PaneUserPlanError
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session

from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


class RfShiftProductTests(unittest.TestCase):
    def setUp(self) -> None:
        self.native, self.ad = _ad_graph()
        self.hf = hackrf_fixture()
        self.hf_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=self.hf.live, device_catalog=self.hf.catalog, analyzer_hackrf=self.hf.hackrf))
        source = self.ad.live.discover(startup=True)[0]
        pool = PaneProductGraphPool(lambda key: self.ad if key == "pane-resource-1" else self.hf_graph)
        self.prepared = prepare_user_pane_session((PaneSlotDraft(1, source.device_id, 100e6, 108e6),
                                                   PaneSlotDraft(2, "source-hackrf", 140e6, 148e6),
                                                   PaneSlotDraft(3)), pool_factory=lambda: pool)
        self.handle = self.prepared.handle
        apply_user_pane_session(self.prepared)

    def tearDown(self) -> None:
        for future in self.handle.pump.stop_all().values():
            future.result(timeout=4)
        self.handle.shutdown_after_stop()
        self.assertEqual(self.handle.pool.staged_resource_ids, ())

    def test_preview_stop_apply_start_same_graph_fresh_epoch_and_no_peer_restart(self) -> None:
        first = self.handle.pump.start_resource("pane-resource-1").result(timeout=4)
        peer = self.handle.pump.start_resource("pane-resource-2").result(timeout=4)
        peer_binding = self.handle.preparer.bindings["pane-2"]
        peer_layer = self.handle.preparer._layers["pane-2"]
        peer_control = self.hf.factory.controls[-1]
        peer_config = peer_control.request
        initial_engines = len(self.native.engines)
        preview = self.handle.preview_rf_shift(1, 20e6).result(timeout=4)
        self.assertEqual(len(self.native.engines), initial_engines)
        self.assertTrue(self.ad.live.is_running())
        with self.assertRaisesRegex(RuntimeError, "Stop is required"):
            self.handle.apply_rf_shift(preview)
        self.handle.stop_for_rf_shift(preview).result(timeout=4)
        self.assertFalse(self.ad.live.is_running())
        self.handle.apply_rf_shift(preview).result(timeout=4)
        self.assertIs(self.handle.layout, preview.proposal.proposed_context.plan.layout)
        self.assertIs(self.handle.session.schedule, self.handle.layout.schedule)
        self.assertEqual(self.handle.preparer.bindings["pane-1"].crop.start_hz, 120e6)
        self.assertIs(self.handle.preparer.bindings["pane-2"], peer_binding)
        self.assertIs(self.handle.preparer._layers["pane-2"], peer_layer)
        self.assertEqual(peer_control.request, peer_config)
        self.assertEqual(peer_control.stops, [])
        self.assertEqual(len(self.hf.factory.controls), 1)
        next_run = self.handle.pump.start_resource("pane-resource-1").result(timeout=4)
        self.assertGreater(next_run.host_activation_serial, first.host_activation_serial)
        self.assertIs(next_run.planned_control_gap.reason, PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE)
        readback = self.ad.live.current_snapshot()
        self.assertEqual(readback.applied.applied.center_hz, 124e6)
        self.assertEqual((readback.applied.applied.sample_rate_hz, readback.applied.applied.fft_size), (20e6, 4096))
        states = {item.physical_stream_resource_id: item for item in self.handle.pump.snapshot()}
        self.assertEqual(states["pane-resource-2"].activation, peer)
        self.assertIs(states["pane-resource-2"].phase, PanePumpPhase.RUNNING)

    def test_stopped_preview_does_not_reclaim_old_application_adapter(self) -> None:
        self.handle.pump.start_resource("pane-resource-1").result(timeout=4)
        self.handle.pump.stop_resource("pane-resource-1").result(timeout=4)
        preview = self.handle.preview_rf_shift(1, 10e6).result(timeout=4)
        self.assertFalse(preview.resource.restart_required)
        self.handle.apply_rf_shift(preview).result(timeout=4)
        self.handle.pump.start_resource("pane-resource-1").result(timeout=4)
        self.assertEqual(self.ad.live.current_snapshot().applied.applied.center_hz, 114e6)

    def test_current_recording_refuses_rf_stop_but_ordinary_stop_remains_available(self) -> None:
        self.handle.pump.start_resource("pane-resource-1").result(timeout=4)
        preview = self.handle.preview_rf_shift(1, 10e6).result(timeout=4)
        owner = self.handle.session._runtimes["pane-resource-1"].owner
        with patch.object(owner, "recording_active", return_value=True):
            with self.assertRaisesRegex(PaneResourceError, "recording conflicts"):
                self.handle.stop_for_rf_shift(preview).result(timeout=4)
            self.assertTrue(self.ad.live.is_running())
            self.handle.pump.stop_resource("pane-resource-1").result(timeout=4)
        self.assertFalse(self.ad.live.is_running())

    def test_source_revision_change_refuses_preview_without_receiver_mutation(self) -> None:
        selection = self.ad.live.current_source_selection()
        changed = replace(selection, revision=selection.revision + 1)
        with patch.object(self.ad.live, "current_source_selection", return_value=changed):
            with self.assertRaisesRegex(PaneUserPlanError, "selected receiver changed"):
                self.handle.preview_rf_shift(1, 10e6).result(timeout=4)
        self.assertEqual(self.native.engines, [])

    def test_start_and_terminal_close_wait_for_accepted_control(self) -> None:
        for future in self.handle.pump.stop_all().values():
            future.result(timeout=4)
        self.assertTrue(self.handle.can_close())
        entered, release = Event(), Event()
        original = self.handle.session.preview_resource_plan

        def blocked(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test control operation was not released")
            return original(*args)

        with patch.object(self.handle.session, "preview_resource_plan", side_effect=blocked):
            pending = self.handle.preview_rf_shift(1, 10e6)
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.handle.pump.control_pending())
                self.assertFalse(self.handle.can_close())
                with self.assertRaises(RuntimeError):
                    self.handle.pump.start_resource("pane-resource-1")
                stopped = self.handle.pump.stop_resource("pane-resource-1")
            finally:
                release.set()
            pending.result(timeout=4)
            stopped.result(timeout=4)
        self.assertEqual(self.native.engines, [])

    def test_explicit_stop_cancels_queued_preview_before_any_rf_or_routing_mutation(self) -> None:
        worker = self.handle.pump._workers["pane-resource-1"]
        old = self.handle.layout
        with worker._condition:
            pending = self.handle.preview_rf_shift(1, 10e6)
            stopped = self.handle.pump.stop_resource("pane-resource-1")
        with self.assertRaisesRegex(RuntimeError, "cancelled by explicit Stop"):
            pending.result(timeout=4)
        stopped.result(timeout=4)
        self.assertIs(self.handle.layout, old)
        self.assertEqual(self.native.engines, [])

    def test_partial_presentation_commit_fault_never_exposes_start_but_stop_close_work(self) -> None:
        preview = self.handle.preview_rf_shift(1, 10e6).result(timeout=4)
        self.handle.stop_for_rf_shift(preview).result(timeout=4)
        with patch.object(self.handle.preparer, "commit_resource_layout", side_effect=RuntimeError("PRIVATE payload")):
            with self.assertRaisesRegex(RuntimeError, "command was refused"):
                self.handle.apply_rf_shift(preview).result(timeout=4)
        self.assertNotIn("pane-resource-1", self.handle.pump.startable_resource_ids())
        self.handle.pump.stop_resource("pane-resource-1").result(timeout=4)
        self.assertNotIn("pane-resource-1", self.handle.pump.startable_resource_ids())
        self.assertEqual(self.native.engines, [])

    def test_changed_selection_after_preview_refuses_before_rf_stop(self) -> None:
        self.handle.pump.start_resource("pane-resource-1").result(timeout=4)
        preview = self.handle.preview_rf_shift(1, 10e6).result(timeout=4)
        selection = self.ad.live.current_source_selection()
        changed = replace(selection, revision=selection.revision + 1)
        with patch.object(self.ad.live, "current_source_selection", return_value=changed):
            with self.assertRaisesRegex(PaneUserPlanError, "selected receiver changed"):
                self.handle.stop_for_rf_shift(preview).result(timeout=4)
        self.assertTrue(self.ad.live.is_running())

    def test_handoff_cleanup_failure_completes_future_and_explicit_stop_recovers_same_worker(self) -> None:
        self.handle.pump.start_resource("pane-resource-1").result(timeout=4)
        with patch.object(self.handle.queue, "clear", side_effect=RuntimeError("PRIVATE queue state")):
            with self.assertRaisesRegex(RuntimeError, "Stop did not confirm"):
                self.handle.pump.stop_resource("pane-resource-1").result(timeout=4)
        self.assertFalse(self.ad.live.is_running())
        self.assertNotIn("pane-resource-1", self.handle.pump.startable_resource_ids())
        self.handle.pump.stop_resource("pane-resource-1").result(timeout=4)
        self.handle.pump.start_resource("pane-resource-1").result(timeout=4)
        self.assertTrue(self.ad.live.is_running())


if __name__ == "__main__":
    unittest.main()

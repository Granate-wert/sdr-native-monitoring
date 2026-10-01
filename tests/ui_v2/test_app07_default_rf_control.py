"""Full default RF control through real composition; fake SDK/serial only."""

from __future__ import annotations

from copy import copy
from dataclasses import replace, fields
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.application.analyzer_continuous_sweep import AnalyzerContinuousSweepApplicationService
from sdr_monitor.application.analyzer_rf_change import AnalyzerRfChangeRejected, AnalyzerRfStopRejected
from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.recording import RecordingState
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest

import tests.ui_v2.test_app07_default_rf_profile as profiles


def not_cancelled():
    return False


class DefaultRfControlTests(unittest.TestCase):
    setUp = profiles.DefaultRfProfileTests.setUp
    rich_ad = profiles.DefaultRfProfileTests.rich_ad
    rich_hf = profiles.DefaultRfProfileTests.rich_hf
    stage_hf = profiles.DefaultRfProfileTests.stage_hf

    def assert_preserved(self, before, after, changed):
        for field in fields(before):
            if field.name not in changed:
                self.assertEqual(getattr(before, field.name), getattr(after, field.name), field.name)

    def test_ad_stop_apply_gui_ack_start_preserves_every_profile_field_and_real_ids(self):
        self.ad.live.apply_configuration(self.rich_ad())
        self.ad.live.start()
        before = self.ad.live.capture_rf_context()
        proposal = self.ad.live.preview_rf_shift(5e6)
        self.ad.live.stop_rf_rtbw(proposal, cancelled=not_cancelled)
        receipt = self.ad.live.apply_rf_shift(proposal, cancelled=not_cancelled)
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.IDLE)
        self.assertEqual(len(self.ad_native.engines), 1)
        for operation in (self.ad.live.start, lambda: self.ad.live.start_rf_rtbw(receipt, cancelled=not_cancelled)):
            with self.assertRaises(AnalyzerRfChangeRejected):
                operation()
        self.ad.live.acknowledge_rf_apply(receipt)
        after = self.ad.live.start_rf_rtbw(receipt, cancelled=not_cancelled)
        self.assertIs(after.state, LiveSessionState.RUNNING)
        self.assertEqual(len(self.ad_native.engines), 2)
        self.assertGreater(after.acquisition_epoch, before.acquisition_epoch)
        # The existing logical Live session ID is retained. Actual acquisition
        # epoch and application operation advance; do not manufacture IDs.
        self.assertEqual(after.session_id, before.session_id)
        self.assertGreater(self.ad.analyzer.state.operation_id, before.state.operation_id)
        self.assertEqual(after.applied.applied.center_hz, before.request.center_hz + 5e6)
        self.assert_preserved(before.request, after.applied.applied, {"center_hz"})
        self.ad.live.stop()
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.start_rf_rtbw(receipt, cancelled=not_cancelled)
        self.assertEqual(len(self.ad_native.engines), 2)

    def test_hackrf_exact_full_stage_and_next_start_assign_generation_without_defaults(self):
        self.stage_hf()
        self.hf.live.start()
        before = self.hf.live.capture_rf_context()
        proposal = self.hf.live.preview_rf_shift(5e6)
        self.hf.live.stop_rf_rtbw(proposal, cancelled=not_cancelled)
        receipt = self.hf.live.apply_rf_shift(proposal, cancelled=not_cancelled)
        self.assertEqual(receipt.armed_context.request.center_frequency_hz, 155e6)
        self.assertGreater(receipt.armed_context.configuration_generation, before.configuration_generation)
        self.assert_preserved(before.request, receipt.armed_context.request,
                              {"center_frequency_hz", "configuration_generation"})
        self.hf.live.acknowledge_rf_apply(receipt)
        result = self.hf.live.start_rf_rtbw(receipt, cancelled=not_cancelled)
        self.assertIs(result.state, LiveSessionState.RUNNING)
        self.assertGreater(result.generation, receipt.snapshot.generation)
        self.assertGreater(result.acquisition_epoch, before.acquisition_epoch)
        self.assert_preserved(before.request, result.hackrf_request,
                              {"center_frequency_hz", "configuration_generation"})

    def test_cancelled_stop_apply_acknowledged_start_are_inert_at_each_boundary(self):
        self.ad.live.apply_configuration(self.rich_ad())
        self.ad.live.start()
        proposal = self.ad.live.preview_rf_shift(5e6)
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.stop_rf_rtbw(proposal, cancelled=lambda: True)
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.RUNNING)
        self.ad.live.stop_rf_rtbw(proposal, cancelled=not_cancelled)
        before = self.ad.live.current_snapshot()
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.apply_rf_shift(proposal, cancelled=lambda: True)
        self.assertIs(self.ad.live.current_snapshot(), before)
        receipt = self.ad.live.apply_rf_shift(proposal, cancelled=not_cancelled)
        self.ad.live.acknowledge_rf_apply(receipt)
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.start_rf_rtbw(receipt, cancelled=lambda: True)
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.IDLE)
        self.assertEqual(len(self.ad_native.engines), 1)

    def test_missing_or_forged_receipt_and_failed_gui_ack_bar_every_start_until_explicit_stop(self):
        self.ad.live.apply_configuration(self.rich_ad())
        proposal = self.ad.live.preview_rf_shift(5e6)
        receipt = self.ad.live.apply_rf_shift(proposal, cancelled=not_cancelled)
        forged = copy(receipt)
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.acknowledge_rf_apply(forged)
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.start()
        self.assertTrue(self.ad.live.current_snapshot().stop_required)
        result = self.ad.live.stop()
        self.assertFalse(result.stop_required)
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.acknowledge_rf_apply(receipt)
        self.assertIs(self.ad.live.start().state, LiveSessionState.RUNNING)

    def test_changed_profile_or_selection_after_apply_refuses_even_with_gui_ack(self):
        self.ad.live.apply_configuration(self.rich_ad())
        proposal = self.ad.live.preview_rf_shift(5e6)
        receipt = self.ad.live.apply_rf_shift(proposal, cancelled=not_cancelled)
        self.ad.live.acknowledge_rf_apply(receipt)
        self.ad.live.apply_configuration(replace(receipt.armed_context.request, gain_db=22))
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.start_rf_rtbw(receipt, cancelled=not_cancelled)
        self.assertEqual(self.ad_native.engines, [])

    def test_recording_race_after_gui_ack_refuses_without_consuming_or_starting(self):
        self.ad.live.apply_configuration(self.rich_ad())
        receipt = self.ad.live.apply_rf_shift(self.ad.live.preview_rf_shift(5e6), cancelled=not_cancelled)
        self.ad.live.acknowledge_rf_apply(receipt)
        for state in (RecordingState.RECORDING, None):
            with self.subTest(state=state), patch.object(self.ad.services.live_sdr, "native_recording_health",
                    return_value=SimpleNamespace(state=state)):
                with self.assertRaises((AnalyzerRfChangeRejected, RuntimeError)):
                    self.ad.live.start_rf_rtbw(receipt, cancelled=not_cancelled)
        self.assertEqual(self.ad_native.engines, [])
        self.ad.live.stop()

    def sweep(self, key):
        graph = getattr(self, key)
        display = SimpleNamespace(start=Mock(), stop=Mock(), poll_latest=Mock(return_value=object()))
        if key == "ad":
            graph.live.apply_configuration(self.rich_ad())
            graph.sweep_router._native = display
            request = ContinuousSweepPlanRequest(100e6, 300e6, analysis_bins_per_usable_window=4096,
                acquisition_buffer_samples=32768, segment_frame_timeout_ms=2345, line_snapshot_rate_hz=75)
        else:
            selection = self.sources["hf" if key == "hf" else "ts"]
            if key == "hf":
                self.stage_hf()
                display.preflight = Mock()
                # Same selected/router/controller composition, fake physical
                # display/SDK boundary with no factory or serial I/O.
                graph.sweep_router._hackrf = display
                request = HackrfSweepRequest(selection.selected, selection.revision,
                                            100_000_000, 300_000_000, 2048, 16, 24, 37)
            else:
                graph.sweep_router._instrument = display
                display.preflight = Mock()
                request = TinySaSweepRequest(selection.selected, selection.revision,
                    100_000_000, 300_000_000, points=1001, timeout_s=91, interval_s=.75)
        facade = AnalyzerContinuousSweepApplicationService(graph.live, graph.sweep_router)
        self.addCleanup(facade.close)
        facade.start(request)
        return graph, display, facade

    def test_three_sweep_families_keep_host_intent_and_terminal_before_new_controller_epoch(self):
        for key in ("ad", "hf", "ts"):
            with self.subTest(family=key):
                graph, display, facade = self.sweep(key)
                proposal = graph.live.preview_rf_shift(5e6)
                facade.stop_rf(proposal, cancelled=not_cancelled)
                display.poll_latest.assert_not_called()
                receipt = graph.live.apply_rf_shift(proposal, cancelled=not_cancelled)
                self.assertEqual(display.start.call_count, 1)
                self.assertEqual(receipt.armed_context.request, proposal.expected.request)
                graph.live.acknowledge_rf_apply(receipt)
                with self.assertRaisesRegex(RuntimeError, "terminal"):
                    facade.start_rf(receipt, cancelled=not_cancelled)
                self.assertIs(graph.live._rf_receipt, receipt)
                facade.poll_latest()
                facade.start_rf(receipt, cancelled=not_cancelled)
                accepted = graph.analyzer.accepted_sweep_request
                self.assertEqual(accepted.start_hz, proposal.request.start_hz)
                self.assertGreater(accepted.epoch, proposal.expected.request.epoch)
                self.assert_preserved(proposal.request, accepted, {"epoch"})
                self.assertEqual(display.start.call_count, 2)
                facade.stop()
                facade.poll_latest()

    def test_sweep_recording_or_stale_stop_admission_does_not_dispatch_or_drop_ownership(self):
        graph, display, facade = self.sweep("ad")
        proposal = graph.live.preview_rf_shift(5e6)
        with patch.object(graph.services.live_sdr, "native_recording_health",
                          return_value=SimpleNamespace(state=RecordingState.RECORDING)):
            with self.assertRaises(AnalyzerRfStopRejected):
                facade.stop_rf(proposal, cancelled=not_cancelled)
        self.assertTrue(facade.stop_required)
        self.assertFalse(facade._terminal_poll_pending)
        display.stop.assert_not_called()
        self.assertIs(graph.analyzer.state.phase, AnalyzerPhase.RUNNING)
        facade.stop()
        self.assertTrue(facade._terminal_poll_pending)


if __name__ == "__main__":
    unittest.main()

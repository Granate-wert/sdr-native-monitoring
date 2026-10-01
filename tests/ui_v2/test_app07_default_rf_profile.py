"""Default Analyzer full-profile admission over actual owners; fake SDK/serial.

No Windows input, real RX, RF quality/performance or completed UI chain claim.
"""

from __future__ import annotations

from copy import copy
from dataclasses import fields, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.application.analyzer_continuous_sweep import AnalyzerContinuousSweepApplicationService
from sdr_monitor.application.analyzer_rf_change import (
    AnalyzerRfChangeRejected, AnalyzerRfContext, compile_analyzer_rf_shift,
)
from sdr_monitor.application.analyzer_session import AnalyzerMode, AnalyzerPhase, AnalyzerSessionState
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.live import BackendKind, LiveAdmissionRejected, LiveConfiguration
from sdr_monitor.domain.recording import RecordingState
from sdr_monitor.domain.sweep_speed import SweepSpeedProfile
from sdr_monitor.domain.sweep_statistics import SweepStatisticsSettings
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.domain.tinysa_settings import TinySaInputMode
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory

from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_graph
from tests.ui_v2.test_app06_tinysa_runtime_settings import settings_graph, full_plan
from tests.ui_v2.test_app06_tinysa_external_correction import curve
from tests.test_app06_hackrf_sweep_display import setup_owner


class DefaultRfProfileTests(unittest.TestCase):
    def setUp(self):
        self.ad_native, self.ad = _ad_graph()
        self.addCleanup(self.ad.live.shutdown)
        self.hf_fixture = hackrf_graph()
        self.hf = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=self.hf_fixture.live, device_catalog=self.hf_fixture.catalog,
            analyzer_hackrf=self.hf_fixture.hackrf))
        self.addCleanup(self.hf.live.shutdown)
        self.ts_fixture = settings_graph()
        self.ts = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=self.ts_fixture.live, device_catalog=self.ts_fixture.catalog,
            analyzer_tinysa=self.ts_fixture.instrument))
        self.addCleanup(self.ts.live.shutdown)
        self.sources = {}
        for key, graph in (("ad", self.ad), ("hf", self.hf), ("ts", self.ts)):
            discovered = graph.live.discover(startup=True)
            source = next(item for item in discovered if item.family.value == {"ad": "ad936x", "hf": "hackrf", "ts": "tinysa"}[key])
            graph.live.select_device(source.device_id)
            self.sources[key] = graph.live.current_source_selection()

    def rich_ad(self):
        return LiveConfiguration(center_hz=128e6, sample_rate_hz=61.44e6, analog_bandwidth_hz=56e6,
            fft_size=8192, overlap_ratio=.75, detector="rms", window="blackman_harris_4term",
            averaging_frames=7, snapshot_rate_hz=480, backend=BackendKind.CPU,
            persistence_enabled=True, persistence_mode="rolling-exact", persistence_power_min_db=-130,
            persistence_power_max_db=10, persistence_power_bins=64, persistence_window_frames=23,
            persistence_half_life_s=2.5, persistence_snapshot_rate_hz=17, profile_id="full-profile")

    def rich_hf(self):
        return HackrfLiveRequest(150e6, 20e6, 20_000_000, 16, 24,
            fft_size=8192, hop_size=2048, window="nuttall", detector="rms", averaging_frames=7,
            source_id=self.sources["hf"].selected.device_id, slot_count=24, ready_capacity=16,
            presentation_capacity=8, persistence_enabled=True, persistence_mode="rolling_exact",
            persistence_power_bins=64, persistence_window_frames=31, persistence_half_life_s=2,
            persistence_snapshot_rate_hz=13)

    def context(self, key, request, applied=None):
        selection = self.sources[key]
        sweep = isinstance(request, (ContinuousSweepPlanRequest, HackrfSweepRequest, TinySaSweepRequest))
        state = AnalyzerSessionState(AnalyzerMode.SWEEP if sweep else AnalyzerMode.RTBW,
            AnalyzerPhase.RUNNING, operation_id=19, sweep_epoch=request.epoch if sweep else None)
        return AnalyzerRfContext(selection.selected, selection.revision, state, request, applied,
            "retained-session", 11, request.epoch if sweep else 4, "rx1", "host_steady_ns", "dBm" if key == "ts" else "dBFS/bin")

    def assert_non_frequency_fields(self, original, shifted, names):
        for field in fields(original):
            if field.name not in names:
                self.assertEqual(getattr(original, field.name), getattr(shifted, field.name), field.name)

    def test_ad_rtbw_preserves_full_applied_dsp_persistence_and_profile(self):
        original = self.rich_ad()
        proposal = compile_analyzer_rf_shift(self.context("ad", original), 5_000_000.5)
        self.assertEqual(proposal.request.center_hz, 133_000_001)
        self.assert_non_frequency_fields(original, proposal.request, {"center_hz"})
        self.assertEqual((proposal.requested_shift_hz, proposal.effective_shift_hz, proposal.quantum_hz),
                         (5_000_000.5, 5_000_001, 1))

    def test_hackrf_rtbw_preserves_queues_gains_hop_group_and_generation(self):
        original = self.rich_hf()
        proposal = compile_analyzer_rf_shift(self.context("hf", original), -1_000_000.5)
        self.assertEqual(proposal.request.center_frequency_hz, 148_999_999)
        self.assert_non_frequency_fields(original, proposal.request, {"center_frequency_hz"})

    def test_ad_sweep_preserves_full_live_and_retention_speed_buffer_settings(self):
        original = ContinuousSweepPlanRequest(100e6, 300e6, epoch=31, output_queue_capacity=8,
            segment_frame_timeout_ms=2345, acquisition_buffer_samples=32768,
            line_snapshot_rate_hz=75, analysis_bins_per_usable_window=4096,
            speed_profile=SweepSpeedProfile.QUICK, statistics=SweepStatisticsSettings())
        applied = self.rich_ad()
        context = self.context("ad", original, applied)
        proposal = compile_analyzer_rf_shift(context, 25e6)
        self.assertIs(proposal.expected.applied_live, applied)
        self.assert_non_frequency_fields(original, proposal.request, {"start_hz", "stop_hz"})
        self.assertEqual((proposal.request.start_hz, proposal.request.stop_hz), (125e6, 325e6))

    def test_hackrf_sweep_rounds_whole_mhz_without_changing_fft_gain_preview_epoch(self):
        selection = self.sources["hf"]
        original = HackrfSweepRequest(selection.selected, selection.revision, 100_000_000, 300_000_000,
                                      2048, 16, 24, 37, epoch=5)
        proposal = compile_analyzer_rf_shift(self.context("hf", original), -1_500_000)
        self.assertEqual((proposal.effective_shift_hz, proposal.quantum_hz), (-2e6, 1e6))
        self.assertEqual(proposal.request.start_hz, 98_000_000)
        self.assert_non_frequency_fields(original, proposal.request, {"start_hz", "stop_hz"})
        self.assertIs(proposal.request.source, original.source)

    def test_tinysa_preserves_full_settings_input_deadlines_points_and_correction(self):
        selection = self.sources["ts"]
        original = TinySaSweepRequest(selection.selected, selection.revision, 100_000_000, 300_000_000,
            points=1001, timeout_s=91, epoch=7, repeat_until_stop=True, interval_s=.75,
            settings=full_plan(), input_mode=TinySaInputMode.LOW, readback_settings=True, frontend_chain="coax-A")
        original = replace(original, external_correction=curve(original), allow_correction_extrapolation=True)
        proposal = compile_analyzer_rf_shift(self.context("ts", original), 1_000_000.5)
        self.assertEqual((proposal.request.start_hz, proposal.request.stop_hz), (101_000_001, 301_000_001))
        self.assert_non_frequency_fields(original, proposal.request, {"start_hz", "stop_hz"})
        self.assertIs(proposal.request.settings, original.settings)
        self.assertIs(proposal.request.external_correction, original.external_correction)
        self.assertEqual(self.ts_fixture.serials, [])

    def test_tinysa_correction_range_refuses_without_silent_extrapolation(self):
        selection = self.sources["ts"]
        request = TinySaSweepRequest(selection.selected, selection.revision, 100_000_000, 300_000_000,
            points=1001, epoch=7, input_mode=TinySaInputMode.LOW, readback_settings=True, frontend_chain="coax-A")
        request = replace(request, external_correction=curve(request))
        with self.assertRaises(ValueError):
            compile_analyzer_rf_shift(self.context("ts", request), 1e6)
        self.assertEqual(self.ts_fixture.serials, [])

    def test_invalid_offsets_and_wrong_family_epoch_revision_fail_closed(self):
        original = self.rich_ad()
        context = self.context("ad", original)
        for value in (True, 0, .1, float("nan"), float("inf"), -1e12, 10 ** 400):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                compile_analyzer_rf_shift(context, value)
        for changed in ({"request": self.rich_hf()}, {"state": replace(context.state, phase=AnalyzerPhase.ERROR)},
                        {"state": replace(context.state, operation_id=True)}, {"applied_live": original}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                replace(context, **changed)
        selection = self.sources["hf"]
        request = HackrfSweepRequest(selection.selected, selection.revision, 100_000_000, 300_000_000,
                                    2048, 16, 24, 20, epoch=7)
        context = self.context("hf", request)
        for changed in ({"selection_revision": selection.revision + 1},
                        {"state": replace(context.state, sweep_epoch=8)}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                replace(context, **changed)

    def test_forged_proposal_cannot_change_non_rf_fields_or_source_reference(self):
        context = self.context("ad", self.rich_ad())
        proposal = compile_analyzer_rf_shift(context, 5e6)
        with self.assertRaises(AnalyzerRfChangeRejected):
            replace(proposal, request=replace(proposal.request, fft_size=4096))
        with self.assertRaises(AnalyzerRfChangeRejected):
            replace(proposal, effective_shift_hz=6e6)
        selection = self.sources["hf"]
        request = HackrfSweepRequest(selection.selected, selection.revision, 100_000_000, 300_000_000,
                                    2048, 16, 24, 20, epoch=7)
        proposal = compile_analyzer_rf_shift(self.context("hf", request), 5e6)
        with self.assertRaises(AnalyzerRfChangeRejected):
            replace(proposal, request=replace(proposal.request, source=copy(request.source)))

    def test_actual_ad_preview_is_inert_and_rejects_capability_before_stop(self):
        self.ad.live.apply_configuration(self.rich_ad())
        before = self.ad.live.current_snapshot()
        preview = self.ad.live.preview_rf_shift(5e6)
        self.assertEqual(preview.request, replace(before.applied.applied, center_hz=before.applied.applied.center_hz + 5e6))
        self.assertIs(self.ad.live.current_snapshot(), before)
        self.assertEqual(self.ad_native.engines, [])
        with self.assertRaises(AnalyzerRfChangeRejected):
            self.ad.live.preview_rf_shift(6e9)
        self.assertIs(self.ad.live.current_snapshot(), before)

    def stage_hf(self):
        selection = self.sources["hf"]
        request = replace(self.rich_hf(), averaging_frames=1, persistence_enabled=False, persistence_mode="disabled")
        snapshot = self.hf.live.current_snapshot()
        self.hf.live.stage_hackrf_configuration(HackrfConfigurationPatch(request, selection.revision, snapshot.generation))

    def test_actual_hackrf_preview_reuses_native_admission_without_factory_probe_or_generation(self):
        self.stage_hf()
        before = self.hf.live.current_snapshot()
        proposal = self.hf.live.preview_rf_shift(5e6)
        self.assertEqual(proposal.request, replace(before.hackrf_request, center_frequency_hz=155e6))
        self.assertEqual(self.hf_fixture.observation.probes, 0)
        self.assertEqual(self.hf_fixture.factory.controls, [])
        self.assertIs(self.hf.live.current_snapshot(), before)
        self.hf_fixture.native.HACKRF_UI_BRIDGE_CONTRACT_VERSION = 0
        with self.assertRaises(LiveAdmissionRejected):
            self.hf.live.preview_rf_shift(5e6)
        self.assertIs(self.hf.live.current_snapshot(), before)

    def test_recording_and_unknown_state_refuse_rf_but_ordinary_stop_still_works(self):
        self.ad.live.apply_configuration(self.rich_ad())
        self.ad.live.start()
        proposal = self.ad.live.preview_rf_shift(5e6)
        for state in (RecordingState.RECORDING, None):
            with self.subTest(state=state), patch.object(self.ad.services.live_sdr, "native_recording_health",
                    return_value=SimpleNamespace(state=state)):
                with self.assertRaises((AnalyzerRfChangeRejected, RuntimeError)):
                    with self.ad.live.rf_change_transaction(proposal):
                        self.fail("RF control admitted a recording/unknown state")
                self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.RUNNING)
        with patch.object(self.ad.services.live_sdr, "native_recording_health", return_value=SimpleNamespace(state=RecordingState.RECORDING)):
            self.ad.live.stop()
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.IDLE)

    def test_source_configuration_and_new_start_invalidate_old_control_context(self):
        self.ad.live.apply_configuration(self.rich_ad())
        proposal = self.ad.live.preview_rf_shift(5e6)
        self.ad.live.apply_configuration(replace(self.rich_ad(), gain_db=22))
        with self.assertRaises(AnalyzerRfChangeRejected):
            with self.ad.live.rf_change_transaction(proposal):
                self.fail("stale profile entered RF control")
        self.ad.live.start()
        proposal = self.ad.live.preview_rf_shift(5e6)
        self.ad.live.stop()

        self.ad.live.start()
        with self.assertRaises(AnalyzerRfChangeRejected):
            with self.ad.live.rf_change_transaction(proposal):
                self.fail("superseded operation entered RF control")
        self.ad.live.stop()

    def test_changed_selection_revision_refuses_before_any_control_effect(self):
        self.ad.live.apply_configuration(self.rich_ad())
        self.ad.live.start()
        proposal = self.ad.live.preview_rf_shift(5e6)
        selection = self.sources["ad"]
        with patch.object(self.ad.sources, "current", return_value=replace(selection, revision=selection.revision + 1)):
            with self.assertRaises(AnalyzerRfChangeRejected):
                with self.ad.live.rf_change_transaction(proposal):
                    self.ad.live.stop()
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.RUNNING)

    def test_fresh_frame_sequence_does_not_invalidate_scalar_control_identity(self):
        self.ad.live.apply_configuration(self.rich_ad())
        self.ad.live.start()
        proposal = self.ad.live.preview_rf_shift(5e6)
        before = self.ad.live.current_snapshot()
        with patch.object(self.ad.services.live_sdr, "latest_snapshot", return_value=replace(before, sequence=before.sequence + 17)):
            with self.ad.live.rf_change_transaction(proposal):
                pass
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.RUNNING)

    def test_pane_capture_claim_cannot_be_stolen_by_default_rf_control(self):
        self.ad.live.apply_configuration(self.rich_ad())
        proposal = self.ad.live.preview_rf_shift(5e6)
        claim = object()
        with self.ad.live.pane_control_transaction(claim):
            with self.assertRaises(RuntimeError):
                with self.ad.live.rf_change_transaction(proposal):
                    self.fail("default RF control stole a pane-owned receiver")
            self.ad.live.release_pane_control(claim)
        self.assertEqual(self.ad_native.engines, [])

    def test_forged_frequency_proposal_is_rejected_again_at_control_boundary(self):
        self.ad.live.apply_configuration(self.rich_ad())
        self.ad.live.start()
        proposal = self.ad.live.preview_rf_shift(5e6)
        forged = copy(proposal)
        object.__setattr__(forged, "request", replace(proposal.request, gain_db=22))
        with self.assertRaises(AnalyzerRfChangeRejected):
            with self.ad.live.rf_change_transaction(forged):
                self.ad.live.stop()
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.RUNNING)

    def test_ad_extended_native_refusal_and_reduced_memory_bounds_happen_before_stop(self):
        profile = replace(self.rich_ad(), fft_size=2048, persistence_enabled=False,
                          persistence_mode="disabled", center_hz=3e9)
        self.ad.live.apply_configuration(profile)
        display = SimpleNamespace(start=Mock(), stop=Mock(), poll_latest=Mock())
        self.ad.sweep_router._native = display
        # Qualify geometry for the fake accepted Start, then restore the actual
        # legacy fixture: RF preview must not bypass its missing sibling gate.
        with patch.object(self.ad.live, "_sweep_preflight", NativeContinuousSweepPlanFactory.preflight_profile):
            self.ad.live.start_sweep(ContinuousSweepPlanRequest(70e6, 5990e6,
                output_queue_capacity=2, analysis_bins_per_usable_window=1024))
        with self.assertRaises(ValueError):
            self.ad.live.preview_rf_shift(1e6)
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.RUNNING)
        display.stop.assert_not_called()
        with self.assertRaisesRegex(ValueError, "memory budget"):
            NativeContinuousSweepPlanFactory.preflight_profile(replace(profile, fft_size=16384),
                ContinuousSweepPlanRequest(70e6, 5990e6, analysis_bins_per_usable_window=8192))
        display.stop.assert_not_called()

    def test_guard_spans_check_and_existing_stop_under_same_native_transaction(self):
        self.ad.live.apply_configuration(self.rich_ad())
        self.ad.live.start()
        proposal = self.ad.live.preview_rf_shift(5e6)
        with self.ad.live.rf_change_transaction(proposal):
            # No new owner or executor; Stop uses the same application route.
            self.ad.live.stop()
        self.assertIs(self.ad.analyzer.state.phase, AnalyzerPhase.IDLE)
        with self.ad.live.rf_change_transaction(proposal, stopped=True):
            pass  # No Apply or Start is implicitly performed by the scope.
        self.assertEqual(len(self.ad_native.engines), 1)

    def test_hackrf_sweep_preflight_does_not_claim_or_create_and_legacy_refuses_padding(self):
        service, native, _control, exclusion, request, selection = setup_owner()
        self.addCleanup(service.close)
        service.preflight(request, selection)
        native.create_hackrf_sweep_runtime_control.assert_not_called()
        self.assertEqual(exclusion.events, [])
        extended = replace(request, stop_hz=121_000_000)
        with self.assertRaises(LiveAdmissionRejected):
            service.preflight(extended, selection)
        native.create_hackrf_sweep_runtime_control.assert_not_called()
        self.assertEqual(exclusion.events, [])

    def test_tinysa_preflight_is_same_owner_and_has_no_serial_or_claim(self):
        selection = self.sources["ts"]
        request = TinySaSweepRequest(selection.selected, selection.revision, 100_000_000, 300_000_000,
            points=1001, settings=full_plan(), input_mode=TinySaInputMode.LOW, readback_settings=True)
        self.ts.sweep_router.preflight_family(request)
        self.assertEqual(self.ts_fixture.serials, [])
        self.assertIsNone(self.ts_fixture.live._external_analyzer_owner)
        with self.assertRaises(LiveAdmissionRejected):
            self.ts.sweep_router.preflight_family(replace(request, selection_revision=selection.revision + 1))

    def test_sweep_guard_uses_owned_facade_and_preserves_one_terminal_poll(self):
        self.ad.live.apply_configuration(self.rich_ad())
        display = SimpleNamespace(start=Mock(), stop=Mock(), poll_latest=Mock(return_value=object()))
        self.ad.sweep_router._native = display  # publication-only fake; lifecycle stays production.
        facade = AnalyzerContinuousSweepApplicationService(self.ad.live, self.ad.sweep_router)
        self.addCleanup(facade.close)
        facade.start(ContinuousSweepPlanRequest(100e6, 300e6, analysis_bins_per_usable_window=4096))
        accepted = self.ad.analyzer.accepted_sweep_request
        proposal = self.ad.live.preview_rf_shift(5e6)
        self.assertIs(proposal.expected.request, accepted)
        with self.ad.live.rf_change_transaction(proposal):
            facade.stop()
        display.poll_latest.assert_not_called()
        self.assertTrue(facade._terminal_poll_pending)
        facade.poll_latest()
        display.poll_latest.assert_called_once()
        self.assertFalse(facade._terminal_poll_pending)
        with self.ad.live.rf_change_transaction(proposal, stopped=True):
            pass
        display.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()

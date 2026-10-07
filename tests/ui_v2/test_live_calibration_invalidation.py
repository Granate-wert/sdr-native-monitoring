"""Exact authority/control/generation admission; raw owner stays unchanged."""
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from sdr_monitor.application.analyzer_session import AnalyzerMode
from sdr_monitor.domain.live import LiveSessionState
from tests.ui_v2.test_live_calibration_composition import CalibrationCompositionFixture


class LiveCalibrationInvalidationTests(CalibrationCompositionFixture):
    def test_stop_acceptance_and_frontend_edit_detach_before_worker_ack(self):
        self.bind()
        self.select_profile()
        old = self.model.state.current
        self.worker.stop()
        self.assertIsNone(self.model.state.binding)
        self.assertIsNone(self.model.state.current)
        self.assertFalse(self.model.is_valid(old))
        self.wait(lambda: not self.worker._pending_commands)
        # Re-arm through the SAME explicit worker/owner lifecycle. Restoring a
        # RUNNING snapshot alone would leave the acknowledged owner IDLE and
        # give its next Stop no dispatched run to clean up.
        self.worker.start()
        self.wait(lambda: not self.worker._pending_commands)
        self.assertTrue(self.port.is_running())
        self.assertTrue(self.presenter.owner._analyzer.stop_required)
        self.bind()
        self.model.frontend_edited()
        self.assertIsNone(self.model.state.binding)
        self.assertIsNone(self.model.state.current)

    def test_stopped_no_frame_pane_sweep_noncanonical_bind_refuse(self):
        original = self.port.current
        for changes in (dict(state=LiveSessionState.CONNECTED), dict(spectrum=None),
                        dict(receiver_id="BOTH"), dict(receiver_id="rx1"), dict(receiver_id=None)):
            self.port.current = replace(original, **changes)
            self.model.bind(self.frontend)
            self.wait(lambda: not self.model.state.busy)
            with self.subTest(changes=changes):
                self.assertIsNotNone(self.model.state.error)
                self.assertIsNone(self.model.state.binding)
                self.assertIsNone(self.model.state.current)
        self.port.current = original
        owner = self.presenter.owner
        owner._pane_control_claim = object()
        self.model.bind(self.frontend)
        self.wait(lambda: not self.model.state.busy)
        self.assertIn("pane-owned", self.model.state.error)
        owner._pane_control_claim = None
        analyzer = owner._analyzer
        owner._analyzer = SimpleNamespace(state=SimpleNamespace(mode=AnalyzerMode.SWEEP))
        try:
            self.model.bind(self.frontend)
            self.wait(lambda: not self.model.state.busy)
            self.assertIn("Sweep", self.model.state.error)
        finally:
            owner._analyzer = analyzer

    def test_foreign_repeated_aba_stale_preview_reject_without_selection_change(self):
        self.bind()
        lane = self.presenter._lane
        binding = self.model.state.binding
        owner = self.presenter.owner
        peer = owner.captured_bound_calibration_lane(self.registry, binding)
        profile = self.facts.profile(binding.signature)
        service = self.registry.for_device(self.facts.facts.device, binding.endpoint)
        foreign = peer.preview_selection(profile)
        receipt = service.selection_receipt()
        self.presenter._set(preview=foreign)
        self.model.select()
        self.wait(lambda: not self.model.state.busy)
        self.assertIsNotNone(self.model.state.error)
        self.assertEqual(service.selection_receipt(), receipt)
        actual = lane.preview_selection(profile)
        lane.apply_selection(actual)
        self.presenter._set(preview=actual)
        self.model.select()
        self.wait(lambda: not self.model.state.busy)
        self.assertIsNotNone(self.model.state.error)
        aba = lane.preview_selection(profile)
        lane.apply_selection(lane.preview_selection(None))
        lane.apply_selection(lane.preview_selection(profile))
        receipt = service.selection_receipt()
        self.presenter._set(preview=aba)
        self.model.select()
        self.wait(lambda: not self.model.state.busy)
        self.assertIsNotNone(self.model.state.error)
        self.assertEqual(service.selection_receipt(), receipt)
        stale = lane.preview_selection(profile)
        self.port.current = replace(self.port.current, session_id="changed")
        self.presenter._set(preview=stale)
        self.model.select()
        self.wait(lambda: not self.model.state.busy)
        self.assertIsNotNone(self.model.state.error)
        self.assertEqual(service.selection_receipt(), receipt)
        peer.close()

    def test_shared_rx_selection_close_is_view_local_and_cadence_advancement_valid(self):
        self.bind()
        profile = self.select_profile()
        lane = self.presenter._lane
        peer = self.presenter.owner.captured_bound_calibration_lane(self.registry, self.model.state.binding)
        self.assertEqual(peer.capture(lambda _: True).captured.profile, profile)
        original = lane.correct
        def advance(handle):
            for n in range(2, 20):
                frame = replace(self.facts.frame, sequence=n)
                self.port.current = replace(self.facts.current, spectrum=frame, sequence=n)
            return original(handle)
        old = self.model.state.current
        with patch.object(lane, "correct", side_effect=advance):
            self.presenter.request_current()
            self.wait(lambda: self.model.state.current is not old)
        current = self.model.state.current
        self.assertIs(current.frame.publication.analytical.raw, self.facts.frame)
        self.assertTrue(self.model.is_valid(current))
        self.model.close_binding()
        self.assertEqual(peer.capture(lambda _: True).captured.profile, profile)
        self.assertFalse(lane.is_valid(current.frame.publication))
        peer.close()

    def test_rebind_during_active_correction_and_old_ordinal_cannot_roll_back(self):
        self.bind()
        lane = self.presenter._lane
        first = self.model.state.current
        started, release = Event(), Event()
        original = lane.correct
        def held(handle):
            started.set()
            release.wait(3)
            return original(handle)
        with patch.object(lane, "correct", side_effect=held):
            self.presenter.request_current()
            self.assertTrue(started.wait(1))
            self.model.bind(self.frontend)
            self.assertIsNone(self.model.state.current)
            release.set()
            self.wait(lambda: self.model.state.current is not None or self.model.state.error is not None)
        self.assertIsNone(self.model.state.error)
        self.assertFalse(self.model.is_valid(first))
        newest = self.model.state.current
        self.presenter.request_current()
        self.wait(lambda: self.model.state.current is not newest)
        self.assertFalse(self.model.is_valid(newest))
        self.assertTrue(self.model.is_valid(self.model.state.current))

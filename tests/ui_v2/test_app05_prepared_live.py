"""Actual V2 RTBW preparation and stale-control barriers, without hardware."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import LiveSessionState
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.ui.v2.state import live_view_state
from sdr_monitor.ui.v2.state.prepared_live import LiveSnapshotPreparer
from tests import test_app02_analyzer_workspace_product as product


def measurement(fixture, sequence=1):
    snapshot = fixture.live.latest_snapshot()
    config = snapshot.applied.applied
    frame = LiveSpectrumFrame(
        sequence=sequence, timestamp_ns=100 + sequence, source_id="fake-pluto-usb",
        config_generation=snapshot.generation, center_frequency_hz=config.center_hz,
        sample_rate_hz=config.sample_rate_hz, fft_size=config.fft_size, hop_size=config.fft_size,
        frequencies_hz=config.center_hz + (np.arange(config.fft_size) - config.fft_size / 2)
        * config.sample_rate_hz / config.fft_size,
        values=np.full(config.fft_size, -70 + sequence, dtype=np.float32), unit="dBFS/bin",
    )
    return replace(snapshot, spectrum=frame)


class PreparedLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = product.AnalyzerWorkspaceProductTests("runTest")
        self.fixture.app = self.app
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.fixture.select_and_apply()

    def start(self):
        f = self.fixture
        f.page.primary.click()
        f.wait(lambda: f.live.is_running() and not f.composition.view_model.state.busy)

    def test_actual_composition_prepares_once_off_gui_and_reuses_on_busy_labels(self):
        f = self.fixture
        gui = threading.get_ident()
        threads, deliveries = [], []
        original = live_view_state.bundle_from_live

        def bundle(snapshot):
            threads.append(threading.get_ident())
            return original(snapshot)

        f.presenter.prepared_snapshot_ready.connect(lambda state: deliveries.append(threading.get_ident()))
        with patch.object(live_view_state, "bundle_from_live", side_effect=bundle), \
             patch("sdr_monitor.ui.v2.spectrum.scene.adapt_spectrum_frame",
                   side_effect=AssertionError("GUI validation forbidden")), \
             patch("sdr_monitor.ui.v2.spectrum.scene.finite_value_extent",
                   side_effect=AssertionError("GUI Auto Y forbidden")):
            self.start()
            snapshot = measurement(f)
            f.live._snapshot = snapshot
            f.presenter.offer_snapshot_for_render(snapshot)
            f.wait(lambda: f.composition.view_model.state.spectrum is snapshot.spectrum)
            state = f.composition.view_model.state
            self.assertIs(state.prepared_spectrum.view.source_frame, state.analyzer_bundle)
            self.assertIs(state.spectrum, snapshot.spectrum)
            self.assertIsNotNone(state.waterfall_line)
            calls = len(threads)
            for _ in range(20):
                f.composition.view_model.refresh_presentation()
            self.assertEqual(len(threads), calls)
            f.page.primary.click()
            f.wait(lambda: not f.live.is_running() and not f.composition.view_model.state.busy)
        self.assertTrue(threads)
        self.assertTrue(all(t != gui for t in threads))
        self.assertEqual(len(set(threads)), 1)
        self.assertTrue(deliveries)
        self.assertTrue(all(t == gui for t in deliveries))

    def test_slow_preparation_is_single_flight_latest_only_and_stop_rejects_old_running(self):
        f = self.fixture
        self.start()
        entered, release = threading.Event(), threading.Event()
        original = f.presenter._snapshot_preparer
        calls, delivered = [], []

        def slow(snapshot):
            calls.append(snapshot)
            if len(calls) == 1:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test preparation barrier expired")
            return original(snapshot)

        f.presenter._snapshot_preparer = slow
        f.presenter.prepared_snapshot_ready.connect(lambda state: delivered.append(state.snapshot))
        # This test drives exact offers/flushes itself. A real timer racing the
        # first barrier would add an unrelated pending offer to the count.
        f.presenter._poll_timer.stop()
        try:
            first = measurement(f)
            f.live._snapshot = first
            f.presenter.offer_snapshot_for_render(first)
            f.presenter._display_scheduler._flush()
            f.wait(entered.is_set)
            future = f.presenter._preparation_future
            for sequence in range(2, 102):
                f.presenter.offer_snapshot_for_render(measurement(f, sequence))
                f.presenter._display_scheduler._flush()
            self.assertEqual(len(calls), 1)
            self.assertIs(f.presenter._preparation_future, future)
            self.assertEqual(f.presenter._pending_preparation[0].spectrum.sequence, 101)
            self.assertEqual(f.presenter.preparation_superseded, 99)
            self.assertFalse(any(s.spectrum is first.spectrum for s in delivered))
            # Start's earlier empty RUNNING poll may already have been queued
            # before installing the barrier. The invariant below starts at
            # Stop acceptance, not at an unrelated earlier GUI pump.
            delivered.clear()
            f.page.primary.click()
            self.assertTrue(f.composition.view_model.state.busy)
            self.assertIsNone(f.presenter._pending_preparation)
            # A poll while Stop is busy still sees the old RUNNING state.
            # The terminal acknowledgement must invalidate it as well.
            f.presenter.offer_snapshot_for_render(measurement(f, 102))
            f.presenter._display_scheduler._flush()
            release.set()
            f.wait(lambda: not f.live.is_running() and not f.composition.view_model.state.busy)
            f.wait(lambda: f.presenter._preparation_future is None)
            self.assertTrue(delivered)
            self.assertTrue(all(s.state is not LiveSessionState.RUNNING for s in delivered),
                            [(s.state, getattr(s.spectrum, "sequence", None)) for s in delivered])
            self.assertGreaterEqual(f.presenter.preparation_stale, 1)
            self.assertEqual(f.events, ["rtbw-start", "rtbw-stop"])
        finally:
            release.set()

    def test_queued_completed_result_cannot_undo_apply(self):
        f = self.fixture
        old = measurement(f)
        # A stopped snapshot may retain a last frame; queue it without pumping Qt.
        f.presenter._emit_snapshot(old)
        future = f.presenter._preparation_future
        future.result(timeout=3)
        delivered = []
        f.presenter.prepared_snapshot_ready.connect(lambda state: delivered.append(state.snapshot))
        config = replace(f.live.latest_snapshot().applied.applied, gain_db=20)
        f.presenter.apply_configuration(config)
        f.wait(lambda: not f.composition.view_model.state.busy)
        self.assertFalse(any(snapshot is old for snapshot in delivered))
        self.assertEqual(f.composition.view_model.state.snapshot.applied.applied.gain_db, 20)
        self.assertGreaterEqual(f.presenter.preparation_stale, 1)

    def test_preparation_failure_is_visible_and_explicit_stop_still_works(self):
        f = self.fixture
        self.start()
        original = f.presenter._snapshot_preparer

        def broken(snapshot):
            if snapshot.state is LiveSessionState.RUNNING and snapshot.spectrum is not None:
                raise RuntimeError("injected display failure")
            return original(snapshot)

        f.presenter._snapshot_preparer = broken
        snapshot = measurement(f)
        f.live._snapshot = snapshot
        f.presenter.offer_snapshot_for_render(snapshot)
        f.wait(lambda: "injected display failure" in (f.composition.view_model.state.error_label or ""))
        self.assertIsNone(f.page.visualization.spectrum_scene.latest_frame)
        self.assertTrue(f.live.is_running())
        self.assertTrue(f.page.primary.isEnabled())
        f.page.primary.click()
        f.wait(lambda: not f.live.is_running() and not f.composition.view_model.state.busy)
        self.assertIsNotNone(f.page.visualization.spectrum_scene.latest_frame)

    def test_prepared_mapping_keeps_coherence_rejection_and_exact_identity(self):
        f = self.fixture
        snapshot = measurement(f)
        wrong = replace(snapshot, active_source_id="fake-pluto-usb",
                        spectrum=replace(snapshot.spectrum, source_id="wrong-source"))
        prepared = LiveSnapshotPreparer()(wrong)
        self.assertIsNone(prepared.analyzer_bundle)
        self.assertIsNone(prepared.prepared_spectrum)
        mapped = live_view_state.build_live_view_state(wrong, prepared_measurement=prepared)
        self.assertEqual(mapped.measurement_unavailable_reason, prepared.measurement_unavailable_reason)
        with self.assertRaisesRegex(ValueError, "exact snapshot"):
            live_view_state.build_live_view_state(snapshot, prepared_measurement=prepared)

    def test_fast_completed_future_cannot_release_busy_before_gui_acknowledgement(self):
        f = self.fixture
        original_submit = f.presenter._executor.submit

        def already_completed(*args, **kwargs):
            future = original_submit(*args, **kwargs)
            future.result(timeout=3)  # test-only forcing inline done-callback race
            return future

        with patch.object(f.presenter._executor, "submit", side_effect=already_completed):
            f.page.primary.click()
        self.assertTrue(f.live.is_running())
        self.assertTrue(f.composition.view_model.state.busy)
        self.assertFalse(f.page.primary.isEnabled())
        f.wait(lambda: not f.composition.view_model.state.busy)
        self.assertEqual(f.composition.view_model.state.snapshot.state, LiveSessionState.RUNNING)

    def test_completed_preparation_is_not_delivered_after_shutdown(self):
        f = self.fixture
        delivered = []
        f.presenter.prepared_snapshot_ready.connect(delivered.append)
        f.presenter._emit_snapshot(measurement(f))
        f.presenter._preparation_future.result(timeout=3)
        f.shell.close()
        f.wait(lambda: f.shell._is_closed)
        self.app.processEvents()
        self.assertEqual(delivered, [])
        self.assertIsNone(f.presenter._preparation_future)
        self.assertIsNone(f.presenter._pending_preparation)

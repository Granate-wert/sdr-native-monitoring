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

    def test_coherent_delivery_queues_projection_before_next_preparation(self):
        from sdr_monitor.ui.v2.spectrum import projection
        f = self.fixture
        entered, release = threading.Event(), threading.Event()
        original_prepare = f.presenter._snapshot_preparer
        original_project = projection.project_spectrum
        operations = []
        first, second = measurement(f, 1), measurement(f, 2)

        def prepare(snapshot):
            operations.append(("prepare", getattr(snapshot.spectrum, "sequence", None)))
            if snapshot.spectrum is first.spectrum:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test preparation barrier")
            return original_prepare(snapshot)

        def project(request, **kwargs):
            source = request.traces[0][1].source_frame.spectrum
            operations.append(("project", source.sequence))
            return original_project(request, **kwargs)

        with patch.object(f.presenter, "_snapshot_preparer", side_effect=prepare), \
             patch.object(projection, "project_spectrum", side_effect=project):
            try:
                revision = f.presenter._control_revision
                f.presenter._offer_preparation(first, revision)
                f.wait(entered.is_set)
                f.presenter._offer_preparation(second, revision)
                release.set()
                f.wait(lambda: f.page.visualization.spectrum_scene.displayed_frame is not None
                       and f.page.visualization.spectrum_scene.displayed_frame.spectrum is second.spectrum
                       and f.composition.spectrum_projector._future is None)
            finally:
                release.set()
        self.assertLess(operations.index(("project", 1)), operations.index(("prepare", 2)))

    def test_live_boundary_commits_after_render_consumers_not_on_chrome_updates(self):
        f = self.fixture
        seen = []
        scene = f.page.visualization.spectrum_scene
        f.composition.spectrum_projector.commit_requested.connect(lambda: seen.append(scene.latest_frame))
        snapshot = measurement(f)
        f.presenter._emit_snapshot(snapshot)
        f.wait(lambda: bool(seen))
        self.assertIs(seen[-1].spectrum, snapshot.spectrum)
        count = len(seen)
        for _ in range(10):
            f.page._render(f.composition.analyzer_view_model.state)
        self.assertEqual(len(seen), count)

    def test_projection_ack_releases_only_latest_unprepared_frame(self):
        from sdr_monitor.ui.v2.spectrum import projection
        f = self.fixture
        entered, release = threading.Event(), threading.Event()
        original = projection.project_spectrum
        first = measurement(f, 1)
        calls = []
        prepare = f.presenter._snapshot_preparer

        def preparing(snapshot):
            calls.append(getattr(snapshot.spectrum, "sequence", None))
            return prepare(snapshot)

        def projecting(request, **kwargs):
            if request.traces[0][1].source_frame.spectrum is first.spectrum:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("projection acknowledgement barrier")
            return original(request, **kwargs)

        with patch.object(projection, "project_spectrum", side_effect=projecting), \
             patch.object(f.presenter, "_snapshot_preparer", side_effect=preparing):
            try:
                revision = f.presenter._control_revision
                f.presenter._offer_preparation(first, revision)
                f.wait(entered.is_set)
                for sequence in range(2, 102):
                    f.presenter._offer_preparation(measurement(f, sequence), revision)
                self.assertTrue(f.presenter._projection_in_flight)
                self.assertIsNone(f.presenter._preparation_future)
                self.assertEqual(calls, [1])
                latest = f.presenter._pending_preparation[0]
                self.assertEqual(latest.spectrum.sequence, 101)
                release.set()
                f.wait(lambda: f.page.visualization.spectrum_scene.displayed_frame is not None
                       and f.page.visualization.spectrum_scene.displayed_frame.spectrum is latest.spectrum)
                f.wait(lambda: not f.presenter._projection_in_flight)
                self.assertEqual(calls, [1, 101])
            finally:
                release.set()

    def test_projection_backpressure_does_not_block_control_or_refresh(self):
        f = self.fixture
        f.presenter.set_projection_in_flight(True)
        f.presenter._offer_preparation(measurement(f), f.presenter._control_revision)
        self.assertIsNone(f.presenter._preparation_future)
        f.presenter.refresh_snapshot()
        f.wait(lambda: f.presenter._preparation_future is None and f.presenter._pending_preparation is None)
        config = replace(f.live.latest_snapshot().applied.applied, gain_db=20)
        f.presenter.apply_configuration(config)
        f.wait(lambda: not f.composition.view_model.state.busy)
        self.assertEqual(f.live.latest_snapshot().applied.applied.gain_db, 20)
        f.presenter.set_projection_in_flight(False)

    def test_waiting_cadence_slot_takes_newest_pending_without_second_emission(self):
        f = self.fixture
        scheduler = f.presenter._display_scheduler
        scheduler._timer.stop()
        f.presenter.set_projection_in_flight(True)
        old, latest = measurement(f, 1), measurement(f, 2)
        seen = []
        original = f.presenter._snapshot_preparer

        def prepare(snapshot):
            seen.append(snapshot)
            return original(snapshot)

        with patch.object(f.presenter, "_snapshot_preparer", side_effect=prepare):
            f.presenter.offer_snapshot_for_render(old)
            scheduler._flush()  # One cadence ticket, blocked by projection ack.
            f.presenter.offer_snapshot_for_render(latest)  # No second timer tick.
            emitted = scheduler.metrics.emitted
            f.presenter.set_projection_in_flight(False)
            f.wait(lambda: f.presenter._preparation_future is None)
            self.assertEqual(len(seen), 1)
            self.assertIs(seen[0], latest)
            self.assertFalse(scheduler.pending)
            self.assertEqual(scheduler.metrics.emitted, emitted)
            self.assertEqual(scheduler.metrics.preparation_replacements, 1)
            scheduler._flush()
            self.assertEqual(len(seen), 1)
            self.assertEqual(scheduler.metrics.emitted, emitted)

    def test_pending_replacement_preserves_generation_order_deadline_and_metrics(self):
        scheduler = self.fixture.presenter._display_scheduler
        scheduler._timer.stop()
        admitted = replace(measurement(self.fixture), sequence=10)
        deadline = scheduler._next_deadline_s
        for candidate in (replace(admitted, generation=admitted.generation - 1),
                          replace(admitted, generation=admitted.generation + 1),
                          replace(admitted, sequence=9)):
            scheduler.offer(candidate)
            self.assertIsNone(scheduler.take_pending_replacement(admitted))
            self.assertTrue(scheduler.pending)
        newest = replace(admitted, sequence=11)
        scheduler.offer(newest)
        before = scheduler.metrics
        self.assertIs(scheduler.take_pending_replacement(admitted), newest)
        self.assertEqual(scheduler.metrics.emitted, before.emitted)
        self.assertEqual(scheduler.metrics.preparation_replacements, 1)
        self.assertEqual(scheduler._next_deadline_s, deadline)
        self.assertFalse(scheduler.pending)
        scheduler.reset_metrics()
        self.assertEqual(scheduler.metrics.preparation_replacements, 0)

    def test_pending_scheduler_cannot_replace_explicit_refresh_or_cross_revision(self):
        f = self.fixture
        scheduler = f.presenter._display_scheduler
        scheduler._timer.stop()
        first, latest = measurement(f, 1), measurement(f, 2)
        f.presenter.offer_snapshot_for_render(latest)
        seen = []
        original = f.presenter._snapshot_preparer

        def prepare(snapshot):
            seen.append(snapshot)
            return original(snapshot)

        with patch.object(f.presenter, "_snapshot_preparer", side_effect=prepare):
            f.presenter._offer_preparation(first, f.presenter._control_revision, render=False)
            f.wait(lambda: f.presenter._preparation_future is None)
            self.assertIs(seen[-1], first)
            self.assertTrue(scheduler.pending)
            f.presenter._control_revision += 1  # Acknowledgement seals old offers.
            f.presenter._offer_preparation(first, f.presenter._control_revision)
            f.wait(lambda: f.presenter._preparation_future is None)
            self.assertIs(seen[-1], first)
            self.assertEqual(scheduler.metrics.preparation_replacements, 0)
            scheduler._flush()  # The sealed old revision must not reappear.
            self.assertEqual(len(seen), 2)

    def test_prepared_pending_source_precedes_next_preparation_on_actual_worker(self):
        from sdr_monitor.ui.v2.spectrum import projection
        from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
        f = self.fixture
        first, second, third = (measurement(f, sequence) for sequence in (1, 2, 3))
        prepared_second = f.presenter._snapshot_preparer(second)
        entered, release = threading.Event(), threading.Event()
        original_prepare = f.presenter._snapshot_preparer
        original_project = projection.project_spectrum
        operations = []

        def preparing(snapshot):
            operations.append(("prepare", snapshot.spectrum.sequence))
            return original_prepare(snapshot)

        def projecting(request, **kwargs):
            sequence = request.traces[0][1].source_frame.spectrum.sequence
            operations.append(("project", sequence))
            if sequence == 1:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("pending prepared source barrier")
            return original_project(request, **kwargs)

        port = f.composition.spectrum_projector
        with patch.object(projection, "project_spectrum", side_effect=projecting), \
             patch.object(f.presenter, "_snapshot_preparer", side_effect=preparing):
            try:
                revision = f.presenter._control_revision
                f.presenter._offer_preparation(first, revision)
                f.wait(entered.is_set)
                # Recreate a prepared source arriving behind an earlier viewport
                # task, as can happen after showing/resizing a running scene.
                port.offer(replace(port._active,
                    traces=((TraceKind.CURRENT, prepared_second.prepared_spectrum.view),),
                    prepared=prepared_second.prepared_spectrum))
                f.presenter._offer_preparation(third, revision)
                release.set()
                f.wait(lambda: ("prepare", 3) in operations)
                f.wait(lambda: port._future is None and f.presenter._preparation_future is None)
                self.assertLess(operations.index(("project", 2)), operations.index(("prepare", 3)))
            finally:
                release.set()

    def test_owner_close_disconnects_late_prepared_commit(self):
        f = self.fixture
        seen = []
        port = f.composition.spectrum_projector
        port.commit_requested.connect(lambda: seen.append(True))
        state = f.composition.view_model.state
        f.shell.close()
        f.wait(lambda: f.shell._is_closed)
        self.assertIsNone(f.composition._projection_delivery_signal)
        f.composition._disconnect_projection_delivery()  # idempotent cleanup
        f.presenter.prepared_snapshot_ready.emit(state)
        port.request_commit()  # closed port rejects a late external request
        self.assertEqual(seen, [])

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

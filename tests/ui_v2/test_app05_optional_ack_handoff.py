"""Distinguish owned worker time, queued GUI ack and a real cadence ticket."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np

from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
from sdr_monitor.ui.v2.spectrum import projection
from tests.ui_v2.test_app05_prepared_live import measurement
from tests.ui_v2 import test_app05_persistence_wiring as wiring


class OptionalAckHandoffTests(unittest.TestCase):
    setUp = wiring.PersistenceCompositionTests.setUp

    def exercise(self, *, admitted):
        f = self.f
        scene, port = f.page.visualization.spectrum_scene, f.composition.spectrum_projector
        scheduler = f.presenter._display_scheduler
        # Own the timer boundary explicitly: no wall-clock sleep or fabricated
        # throughput inference. Real presenter/composition/executor/Qt remain.
        scheduler._timer.stop()
        f.presenter._poll_timer.stop()
        first = measurement(f, 1)
        f.presenter._emit_snapshot(first)
        f.wait(lambda: scene.displayed_frame is not None
               and scene.displayed_frame.spectrum is first.spectrum and port._future is None
               and f.presenter._preparation_future is None)
        first = replace(first, persistence=synthetic_persistence(first.spectrum, 32, 1, 1))
        second, newest = measurement(f, 2), measurement(f, 3)
        entered, release = threading.Event(), threading.Event()
        density_prepare = projection.prepare_persistence_image
        dispatched = []
        submit = f.presenter._executor.submit

        def blocked_density(request, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("optional completion boundary")
            return density_prepare(request, **kwargs)

        def observe_submit(operation, *args, **kwargs):
            if getattr(operation, "__name__", "") == "_prepare":
                dispatched.append(args[0])
            return submit(operation, *args, **kwargs)

        with patch.object(projection, "prepare_persistence_image", side_effect=blocked_density), \
             patch.object(f.presenter._executor, "submit", side_effect=observe_submit):
            try:
                f.presenter._emit_snapshot(first)
                f.wait(lambda: entered.is_set() and scene.displayed_frame is not None
                       and scene.displayed_frame.spectrum is first.spectrum)
                active, future = port._active, port._future
                self.assertFalse(future.done())
                self.assertTrue(f.presenter._projection_in_flight)
                if admitted:
                    f.presenter._offer_preparation(second, f.presenter._control_revision)
                f.presenter.offer_snapshot_for_render(newest)
                dispatch_count = len(dispatched)
                self.assertIsNone(f.presenter._preparation_future)
                release.set()
                result = future.result(timeout=3)  # test holds GUI ack, not product waiting
                self.assertIs(result.request, active)
                self.assertIs(port._future, future)
                self.assertTrue(f.presenter._projection_in_flight)
                self.assertEqual(len(dispatched), dispatch_count)
                self.assertEqual(scene.persistence_metrics.image_uploads, 0)
                emissions = scheduler.metrics.emitted
                port._finish(future)  # synchronous final GUI ack, no event-loop tick
                self.assertEqual(scene.persistence_metrics.image_uploads, 1)
                # Preparation validates/copies domain density into its owned
                # view; GUI must commit that exact view, not the raw input.
                self.assertIs(scene._persistence._uploaded_density, active.persistence.view.density)
                np.testing.assert_array_equal(active.persistence.view.density, first.persistence.density)
                self.assertEqual(scheduler.metrics.emitted, emissions)
                if admitted:
                    self.assertEqual(len(dispatched), dispatch_count + 1)
                    self.assertIs(dispatched[-1].spectrum, newest.spectrum)
                    self.assertFalse(scheduler.pending)
                else:
                    self.assertEqual(len(dispatched), dispatch_count)
                    self.assertTrue(scheduler.pending)
                    self.assertIsNone(f.presenter._preparation_future)
                    scheduler._flush()  # ONLY this explicit cadence boundary admits it
                    self.assertEqual(len(dispatched), dispatch_count + 1)
                    self.assertIs(dispatched[-1].spectrum, newest.spectrum)
                f.wait(lambda: scene.displayed_frame.spectrum is newest.spectrum
                       and port._future is None and f.presenter._preparation_future is None)
                self.assertEqual(scene.persistence_metrics.image_uploads, 1)
                self.assertFalse(f.presenter._projection_in_flight)
            finally:
                release.set()
        self.assertEqual(f.events, [])  # no receiver start/configuration side effect

    def test_final_ack_dispatches_existing_ticket_immediately_with_latest_source(self):
        self.exercise(admitted=True)

    def test_final_ack_cannot_invent_a_cadence_ticket_for_pending_publication(self):
        self.exercise(admitted=False)

    def density_deadline(self, *, overdue):
        f = self.f
        scene, port = f.page.visualization.spectrum_scene, f.composition.spectrum_projector
        f.presenter._display_scheduler._timer.stop()
        f.presenter._poll_timer.stop()
        source = measurement(f)
        f.presenter._emit_snapshot(source)
        f.wait(lambda: scene.displayed_frame is not None and port._future is None
               and f.presenter._preparation_future is None)
        source = replace(source, persistence=synthetic_persistence(source.spectrum, 32, 1, 1))
        overlay = scene._persistence
        entered, release = threading.Event(), threading.Event()
        original = projection.prepare_persistence_image

        def hold(request, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("density deadline barrier")
            return original(request, **kwargs)

        with patch.object(projection, "prepare_persistence_image", side_effect=hold):
            try:
                f.presenter._emit_snapshot(source)
                f.wait(entered.is_set)
                active, future = port._active, port._future
                start, period = overlay._worker_request_ns, overlay._interval_ns
                values = np.full(active.persistence.view.density.shape, .25, np.float32)
                values.setflags(write=False)
                newest = replace(active.persistence.view.source_frame, density=values)
                scene.set_persistence_frame(newest, now_ns=start + 1)
                self.assertIs(overlay.worker_request, active.persistence)
                release.set()
                future.result(timeout=3)
                ack = start + (2 * period if overdue else period // 2)
                with patch("sdr_monitor.ui.v2.spectrum.persistence_overlay.monotonic_ns", return_value=ack):
                    port._finish(future)
                self.assertEqual(overlay.metrics.image_uploads, 1)
                self.assertEqual(overlay._last_upload_ns, start)
                self.assertTrue(overlay._timer.isActive())
                expected_delay = max(1, int((start + period - ack) / 1_000_000))
                self.assertEqual(overlay._timer.interval(), expected_delay)
                overlay._timer.stop()  # deliver precisely this pending timeout below
                self.assertIsNone(overlay.worker_request)
                if not overdue:
                    overlay.flush_pending(start + period - 1)
                    self.assertIsNone(overlay.worker_request)
                    overlay._timer.stop()
                overlay.flush_pending(max(ack, start + period))
                self.assertIs(overlay.worker_request.view.source_frame, newest)
                scene.commit_projection()
                f.wait(lambda: overlay.metrics.image_uploads == 2 and port._future is None)
                self.assertIs(overlay._uploaded_density, values)
                self.assertEqual(port.allocation_budget.snapshot().reserved_bytes, 0)
            finally:
                release.set()
        self.assertEqual(f.events, [])

    def test_short_worker_wait_rearms_only_remaining_start_to_start_period(self):
        self.density_deadline(overdue=False)

    def test_overdue_worker_wait_does_not_add_a_new_density_period_after_ack(self):
        self.density_deadline(overdue=True)


if __name__ == "__main__":
    unittest.main()

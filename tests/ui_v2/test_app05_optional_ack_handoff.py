"""Distinguish owned worker time, queued GUI ack and a real cadence ticket."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

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
                scheduler.offer(newest)
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
                self.assertIs(scene._persistence._uploaded_density, first.persistence.density)
                self.assertEqual(scheduler.metrics.emitted, emissions)
                if admitted:
                    self.assertEqual(len(dispatched), dispatch_count + 1)
                    self.assertIs(dispatched[-1], newest)
                    self.assertFalse(scheduler.pending)
                else:
                    self.assertEqual(len(dispatched), dispatch_count)
                    self.assertTrue(scheduler.pending)
                    self.assertIsNone(f.presenter._preparation_future)
                    scheduler._flush()  # ONLY this explicit cadence boundary admits it
                    self.assertEqual(len(dispatched), dispatch_count + 1)
                    self.assertIs(dispatched[-1], newest)
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


if __name__ == "__main__":
    unittest.main()

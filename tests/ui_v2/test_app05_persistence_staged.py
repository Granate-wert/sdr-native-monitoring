"""Required spectrum delivery before optional density, without another owner."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from scripts.benchmark_app04_poll_overload import run_qt_until
from sdr_monitor.ui.v2.spectrum import projection
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame
from sdr_monitor.ui.v2.spectrum.persistence_projection import prepare_persistence_image, persistence_image_reserve
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_app05_persistence_worker import view
from tests.ui_v2.test_app05_viewport_projection import DisplayFrame
from tests.ui_v2 import test_app05_persistence_wiring as wiring


class StagedPersistenceQueueTests(unittest.TestCase):
    setUpClass = classmethod(wiring.PersistenceWiringTests.setUpClass.__func__)
    setUp = wiring.PersistenceWiringTests.setUp
    tearDown = wiring.PersistenceWiringTests.tearDown
    pump = wiring.PersistenceWiringTests.pump
    drain = wiring.PersistenceWiringTests.drain
    spectrum = wiring.PersistenceWiringTests.spectrum
    density = wiring.PersistenceWiringTests.density

    def test_final_before_queued_stage_paints_and_commits_only_once(self):
        with patch.object(self.scene, "_paint_trace", wraps=self.scene._paint_trace) as paint:
            self.density()
            self.worker.finish()  # both Qt notifications queued, Future complete
            future = self.port._future
            serial = self.port._active_serial
            self.assertIsNotNone(self.port._required_result)
            self.port._finish(future)  # deliberate reversed delivery order
            self.port._accept_required(serial)
            self.pump()
            self.assertEqual(paint.call_count, 1)
            self.assertEqual(self.scene.persistence_metrics.image_uploads, 1)
            self.assertIsNone(self.port._required_result)
            self.assertIsNone(self.scene._early_projection_request)

    def test_old_scalar_notification_cannot_consume_new_active_stage(self):
        self.density()
        self.worker.finish()
        old_serial = self.port._active_serial
        self.port._finish(self.port._future)
        self.scene._persistence._last_upload_ns = 0
        self.density(.8)
        self.worker.finish()
        result = self.port._required_result
        self.assertIsNotNone(result)
        self.port._accept_required(old_serial)
        self.assertIs(self.port._required_result, result)
        self.port._accept_required(self.port._active_serial)
        self.assertIs(self.scene._early_projection_request, result.request)
        self.drain()
        self.assertEqual(self.scene.persistence_metrics.image_uploads, 2)

    def test_dispose_releases_stage_before_queued_notification(self):
        self.density()
        self.worker.finish()
        self.assertIsNotNone(self.port._required_result)
        self.port.dispose()
        self.assertIsNone(self.port._required_result)
        self.scene.clear_measurement()
        self.port.release_presentation_after_shutdown()
        self.pump()
        self.assertIsNone(self.scene.displayed_frame)
        self.assertIsNone(self.scene._early_projection_request)
        self.assertIsNone(self.scene._persistence.image_item.image)
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)


class StagedPersistenceBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def exercise(self, *, cancel=False):
        entered, release = threading.Event(), threading.Event()
        threads = []
        budget = PresentationAllocationBudget(8 * 1024 * 1024)
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-density-staged")
        port = projection.SpectrumProjector(pool.submit, allocation_budget=budget)
        scene = SpectrumScene()
        scene.plot_item.getAxis("left").setWidth(80)
        scene.set_projection_port(port)
        scene.resize(1100, 600)
        scene.show()
        for _ in range(8):
            self.app.processEvents()
        x = np.linspace(100e6, 101e6, 4096)
        y = np.full(4096, -70, np.float32)
        x.setflags(write=False)
        y.setflags(write=False)
        frame = DisplayFrame(x, y, "dBm")
        density = view(np.full((4, 4096), .2, np.float32))
        activity = []
        port.work_active_changed.connect(activity.append)

        def prepare(request, **kwargs):
            threads.append(threading.get_ident())
            if len(threads) == 1:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("optional density barrier")
            return prepare_persistence_image(request, **kwargs)

        try:
            with patch.object(projection, "prepare_persistence_image", side_effect=prepare), \
                 patch.object(scene, "_paint_trace", wraps=scene._paint_trace) as paint:
                scene.set_frame(frame, prepared=PreparedSpectrumFrame(frame))
                scene.set_persistence_frame(density.source_frame)
                scene.commit_projection()
                run_qt_until(lambda: entered.is_set() and scene.displayed_frame is frame, 2)
                active, future = port._active, port._future
                self.assertFalse(future.done())
                self.assertEqual(activity, [True])
                self.assertEqual(paint.call_count, 1)
                self.assertIsNone(scene._persistence.image_item.image)
                self.assertEqual(budget.snapshot().reserved_bytes, persistence_image_reserve(active.persistence))
                if cancel:
                    port.set_suspended(True)  # does not release active ownership
                    self.assertIs(port._future, future)
                    self.assertEqual(activity, [True])
                release.set()
                run_qt_until(lambda: port._future is None, 2)
                self.assertIsNone(scene._early_projection_request)
                self.assertIsNone(port._required_result)
                self.assertEqual(budget.snapshot().reserved_bytes, 0)
                if cancel:
                    self.assertIsNone(scene._persistence.image_item.image)
                    self.assertIs(scene.displayed_frame, frame)
                    self.assertEqual(activity[-1], False)
                    port.set_suspended(False)
                    run_qt_until(lambda: scene.persistence_metrics.image_uploads == 1 and port._future is None, 2)
                else:
                    self.assertEqual(paint.call_count, 1, "density completion repainted the same spectrum")
                    self.assertEqual(scene.persistence_metrics.image_uploads, 1)
                self.assertEqual(len(set(threads)), 1)
                self.assertNotIn(threading.get_ident(), threads)
                self.assertLessEqual(budget.snapshot().peak_bytes, budget.limit_bytes)
        finally:
            release.set()
            port.dispose()
            pool.shutdown(wait=True, cancel_futures=True)
            port.release_presentation_after_shutdown()
            scene.clear_measurement()
            scene.close()
            scene.deleteLater()
            self.app.processEvents()

    def test_required_pixels_arrive_while_density_is_blocked_and_owner_remains_busy(self):
        self.exercise()

    def test_control_cancellation_after_required_delivery_keeps_pixels_and_releases_at_ack(self):
        self.exercise(cancel=True)


if __name__ == "__main__":
    unittest.main()

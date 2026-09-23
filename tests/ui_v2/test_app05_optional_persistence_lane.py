"""Optional density work cannot hold the required spectrum projection lane."""

import threading
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame
from sdr_monitor.ui.v2.spectrum.persistence_projection import prepare_persistence_image
from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_app04_sweep_coverage import line
from tests.ui_v2.test_app05_persistence_worker import view
from tests.ui_v2.test_app05_viewport_projection import DisplayFrame, ManualWorker


class OptionalPersistenceLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.spectrum_worker = ManualWorker()
        self.persistence_worker = ManualWorker()
        self.budget = PresentationAllocationBudget(8 * 1024 * 1024)
        self.projector = SpectrumProjector(
            self.spectrum_worker.submit,
            allocation_budget=self.budget,
            persistence_submit=self.persistence_worker.submit,
        )
        self.scene = SpectrumScene()
        self.scene.plot_item.getAxis("left").setWidth(80)
        self.scene.set_projection_port(self.projector)
        self.scene.resize(1000, 600)
        self.scene.show()
        self.pump()

    def tearDown(self):
        self.projector.dispose()
        for worker in (self.spectrum_worker, self.persistence_worker):
            while worker.jobs:
                worker.finish()
                self.pump()
        self.pump()
        self.projector.release_presentation_after_shutdown()
        self.scene.close()
        self.scene.deleteLater()
        self.pump()
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)

    def pump(self):
        for _ in range(10):
            self.app.processEvents()

    def set_spectrum(self, sequence, value):
        source = line(sequence, np.full(4096, value, dtype=np.float32))
        frame = DisplayFrame(source.frequencies_hz, source.values_db, source.unit)
        self.scene.set_frame(frame, prepared=PreparedSpectrumFrame(frame))
        self.scene.commit_projection()
        return frame

    def finish_spectrum(self, expected):
        for _ in range(5):
            if not self.spectrum_worker.jobs:
                break
            self.assertIs(self.projector._active.traces[0][1].source_frame, expected)
            self.spectrum_worker.finish()
            self.pump()
        self.assertIs(self.scene.displayed_frame, expected)

    def test_density_worker_does_not_block_multiple_current_spectrum_frames(self):
        first = self.set_spectrum(1, -70)
        self.finish_spectrum(first)

        density = view(np.full((4, 64), .35, dtype=np.float32))
        self.scene.set_persistence_frame(density.source_frame)
        self.scene.commit_projection()
        self.assertEqual(len(self.spectrum_worker.jobs), 0)
        self.assertEqual(len(self.persistence_worker.jobs), 1)
        self.assertTrue(self.projector.persistence_projector.has_active)
        request = self.projector.persistence_projector._active.request
        expected_image = prepare_persistence_image(request).image.copy()

        second = self.set_spectrum(2, -55)
        self.assertEqual(len(self.spectrum_worker.jobs), 1)
        self.assertEqual(len(self.persistence_worker.jobs), 1)
        self.finish_spectrum(second)

        third = self.set_spectrum(3, -42)
        self.assertGreaterEqual(len(self.spectrum_worker.jobs), 1)
        self.assertEqual(len(self.persistence_worker.jobs), 1)
        self.finish_spectrum(third)

        self.persistence_worker.finish()
        self.pump()
        np.testing.assert_array_equal(self.scene._persistence.image_item.image, expected_image)
        self.assertEqual(self.scene.persistence_metrics.image_uploads, 1)
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)

    def test_hidden_page_rejects_finished_but_late_density_ack(self):
        first = self.set_spectrum(1, -70)
        self.finish_spectrum(first)
        density = view(np.full((4, 32), .4, dtype=np.float32))
        self.scene.set_persistence_frame(density.source_frame)
        self.scene.commit_projection()
        self.assertEqual(len(self.persistence_worker.jobs), 1)

        self.persistence_worker.finish()  # Completed off-thread; GUI ack is still queued.
        uploads = self.scene.persistence_metrics.image_uploads
        self.scene.set_presentation_active(False)
        self.pump()
        self.assertEqual(self.scene.persistence_metrics.image_uploads, uploads)
        self.assertFalse(self.scene._persistence.image_item.isVisible())
        self.assertFalse(self.projector.persistence_projector.has_active)
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)

    def test_live_presenter_creates_and_joins_optional_worker_only_when_used(self):
        from sdr_monitor.application import LiveSessionApplicationService
        from sdr_monitor.services.live_session import InMemoryLiveSessionService
        from sdr_monitor.ui.presenters.live_presenter import LivePresenter

        QApplication.instance() or QApplication([])
        presenter = LivePresenter(LiveSessionApplicationService(InMemoryLiveSessionService()))
        try:
            self.assertIsNone(presenter._persistence_executor)
            name = presenter.submit_persistence_task(lambda: threading.current_thread().name).result(timeout=1)
            self.assertTrue(name.startswith("sdr-persistence"))
            presenter.shutdown(timeout_s=.25)
            self.assertTrue(presenter._shutdown_persistence_executor_complete)
            self.assertFalse(any(thread.name.startswith("sdr-persistence") for thread in threading.enumerate()))
            with self.assertRaisesRegex(RuntimeError, "persistence worker is closing"):
                presenter.submit_persistence_task(lambda: None)
        finally:
            presenter.shutdown()

    def test_v2_composition_joins_optional_lane_before_terminal_release(self):
        from sdr_monitor.application import LiveSessionApplicationService
        from sdr_monitor.services.live_session import InMemoryLiveSessionService
        from sdr_monitor.ui.presenters.live_presenter import LivePresenter
        from sdr_monitor.ui.v2.product_live import compose_v2_live_product
        from sdr_monitor.ui.v2.spectrum.persistence_projection import (
            PersistenceImagePolicy,
            PersistenceImageRequest,
        )
        from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode

        presenter = LivePresenter(LiveSessionApplicationService(InMemoryLiveSessionService()))
        budget = PresentationAllocationBudget(8 * 1024 * 1024)
        composition = compose_v2_live_product(
            presenter,
            projection_submit=presenter.submit_display_task,
            persistence_submit=presenter.submit_persistence_task,
            allocation_budget=budget,
        )
        projector = composition.spectrum_projector
        self.assertIsNotNone(projector)
        optional = projector.persistence_projector
        self.assertIsNotNone(optional)
        density = view(np.full((4, 65536), .25, dtype=np.float32))
        optional.offer(object(), PersistenceImageRequest(
            density,
            PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, True),
        ))
        self.assertTrue(optional.has_active)

        composition.shutdown()

        self.assertTrue(presenter._closed)
        self.assertFalse(optional.has_active)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertFalse(any(thread.name.startswith("sdr-persistence") for thread in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()

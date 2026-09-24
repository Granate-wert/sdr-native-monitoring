"""Opt-in same-executor density slices retain exact work and release on ACK."""

import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from scripts.benchmark_app04_poll_overload import run_qt_until
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
from sdr_monitor.ui.v2.spectrum.persistence_projection import (
    PersistenceImagePolicy,
    PersistenceImageRequest,
    prepare_persistence_image,
)
from sdr_monitor.ui.v2.spectrum.persistence_projector import PersistenceProjector
from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector
from tests.ui_v2.test_app05_persistence_worker import spectrum_request, view
from tests.ui_v2.test_app05_viewport_projection import ManualWorker


class ResumablePersistenceOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_request(self, *, mode=PersistenceRenderMode.VISUAL, revision=1):
        values = np.full((2, 65537), .35, np.float32)
        values[:, ::103] = .8
        return PersistenceImageRequest(view(values), PersistenceImagePolicy(revision, mode, True))

    def test_only_complete_bit_exact_image_is_published_and_reservation_closes(self):
        worker = ManualWorker()
        budget = PresentationAllocationBudget(8 * 1024 * 1024)
        port = PersistenceProjector(worker.submit, allocation_budget=budget,
                                    resumable=True, max_chunks=1)
        owner, current = object(), self.make_request()
        deliveries = []
        port.ready.connect(deliveries.append)
        expected = prepare_persistence_image(current)
        try:
            port.offer(owner, current)
            run_qt_until(lambda: bool(worker.jobs), 1)
            chunks = 0
            while not deliveries:
                worker.finish()
                run_qt_until(lambda: bool(worker.jobs) or bool(deliveries), 1)
                chunks += 1
                self.assertLess(chunks, 30)
                if not deliveries:
                    self.assertGreater(budget.snapshot().reserved_bytes, 0)
            self.assertGreater(chunks, 2)
            self.assertEqual(len(deliveries), 1)
            self.assertIs(deliveries[0].request, current)
            np.testing.assert_array_equal(deliveries[0].result.image.view(np.uint32),
                                          expected.image.view(np.uint32))
            self.assertEqual(port.completed, 1)
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
            self.assertEqual(port.retained_bytes, 0)
        finally:
            port.dispose()
            port.release_after_shutdown()

    def test_required_spectrum_queues_before_density_then_preempts_next_slice(self):
        worker = ManualWorker()
        budget = PresentationAllocationBudget(8 * 1024 * 1024)
        port = SpectrumProjector(worker.submit, allocation_budget=budget,
                                 persistence_submit=worker.submit,
                                 resumable_persistence=True, persistence_max_chunks=1)
        delivered, density_delivered = [], []
        port.ready.connect(delivered.append)
        port.persistence_ready.connect(density_delivered.append)
        owner = object()
        first = spectrum_request(self.make_request(), owner)
        second = spectrum_request(None, owner)
        try:
            port.offer(first)
            self.assertEqual(len(worker.jobs), 1)  # required, not density
            worker.finish()
            run_qt_until(lambda: len(delivered) == 1 and bool(worker.jobs), 1)
            # One density slice is now submitted. A newly admitted spectrum
            # queues behind only this finite slice, never the entire image.
            port.offer(second)
            self.assertEqual(len(worker.jobs), 2)
            worker.finish()
            run_qt_until(lambda: bool(worker.jobs) and worker.jobs[0][1].__name__ == "project", 1)
            worker.finish()
            run_qt_until(lambda: len(delivered) == 2, 1)
            self.assertIs(delivered[1].request, second)
            self.assertEqual(density_delivered, [])
            chunks = 0
            while not density_delivered:
                run_qt_until(lambda: bool(worker.jobs), 1)
                worker.finish()
                run_qt_until(lambda: bool(worker.jobs) or bool(density_delivered), 1)
                chunks += 1
                self.assertLess(chunks, 30)
            self.assertTrue(density_delivered[0].result.matches(first.persistence))
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
        finally:
            port.dispose()
            while worker.jobs:
                worker.finish()
                self.app.processEvents()
            port.release_presentation_after_shutdown()

    def test_policy_change_and_dispose_discard_partial_without_late_publish(self):
        worker = ManualWorker()
        budget = PresentationAllocationBudget(8 * 1024 * 1024)
        port = PersistenceProjector(worker.submit, allocation_budget=budget,
                                    resumable=True, max_chunks=1)
        owner = object()
        old = self.make_request()
        new = self.make_request(revision=2)
        deliveries = []
        port.ready.connect(deliveries.append)
        try:
            port.offer(owner, old)
            run_qt_until(lambda: bool(worker.jobs), 1)
            old_future = worker.jobs[0][0]
            worker.finish()
            old_future.result(timeout=1)  # Hold the GUI ACK on purpose.
            port.offer(owner, new)
            run_qt_until(lambda: bool(worker.jobs), 1)
            self.assertGreater(port.cancelled, 0)
            self.assertTrue(all(item.request is not old for item in deliveries))
            port.dispose()
            while worker.jobs:
                worker.finish()
            port.release_after_shutdown()
            self.app.processEvents()
            self.assertEqual(deliveries, [])
            self.assertEqual(port.retained_bytes, 0)
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
        finally:
            port.dispose()
            port.release_after_shutdown()

    def test_suspend_between_slices_restarts_latest_once_without_partial_publish(self):
        worker = ManualWorker()
        budget = PresentationAllocationBudget(8 * 1024 * 1024)
        port = PersistenceProjector(worker.submit, allocation_budget=budget,
                                    resumable=True, max_chunks=1)
        owner, current = object(), self.make_request()
        delivered = []
        port.ready.connect(delivered.append)
        expected = prepare_persistence_image(current)
        try:
            port.offer(owner, current)
            run_qt_until(lambda: bool(worker.jobs), 1)
            future = worker.jobs[0][0]
            worker.finish()
            self.assertIsNone(future.result(timeout=1))
            port._finish(future)  # Ack one partial slice without pumping next timer.
            self.assertTrue(port.has_active)
            self.assertEqual(worker.jobs, [])
            port.set_suspended(True)
            self.assertFalse(port.has_active)
            self.assertTrue(port.has_pending)
            self.assertEqual(delivered, [])
            self.app.processEvents()
            self.assertEqual(worker.jobs, [])
            port.set_suspended(False)
            run_qt_until(lambda: bool(worker.jobs), 1)
            chunks = 0
            while not delivered:
                worker.finish()
                run_qt_until(lambda: bool(worker.jobs) or bool(delivered), 1)
                chunks += 1
                self.assertLess(chunks, 30)
            self.assertEqual(len(delivered), 1)
            np.testing.assert_array_equal(delivered[0].result.image.view(np.uint32),
                                          expected.image.view(np.uint32))
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
            self.assertEqual(port.retained_bytes, 0)
        finally:
            port.dispose()
            port.release_after_shutdown()

    def test_suspend_queued_slice_cancels_before_execution_then_resumes(self):
        worker = ManualWorker()
        port = PersistenceProjector(worker.submit, resumable=True, max_chunks=1)
        owner, current = object(), self.make_request()
        delivered = []
        port.ready.connect(delivered.append)
        try:
            port.offer(owner, current)
            run_qt_until(lambda: bool(worker.jobs), 1)
            port.set_suspended(True)
            worker.finish()  # Future was cancelled before running.
            run_qt_until(lambda: not port.has_active, 1)
            self.assertTrue(port.has_pending)
            self.assertEqual(delivered, [])
            port.set_suspended(False)
            run_qt_until(lambda: bool(worker.jobs), 1)
            port.dispose()
            worker.finish()
            port.release_after_shutdown()
            self.assertEqual(delivered, [])
            self.assertEqual(port.retained_bytes, 0)
        finally:
            port.dispose()
            port.release_after_shutdown()


if __name__ == "__main__":
    unittest.main()

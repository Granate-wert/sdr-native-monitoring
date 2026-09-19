"""Allocation accounting + actual projector admission, no device or RSS claim."""
from dataclasses import dataclass, replace
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.contracts import SpectrumFrameView, TraceKind
from sdr_monitor.ui.v2.spectrum.projection import ProjectionRequest, SpectrumProjector
from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays, union_bytes
from tests.ui_v2.test_app05_viewport_projection import ManualWorker
from tests.ui_v2 import test_app05_viewport_projection as viewport_fixture


def request(owner=None):
    x, y = np.arange(100, dtype=np.float64), np.ones(100, dtype=np.float32)
    x.setflags(write=False)
    y.setflags(write=False)
    view = SpectrumFrameView(object(), x, y, "dBFS/bin")
    return ProjectionRequest(owner or object(), 1, (0, 100, 10), ((TraceKind.CURRENT, view),))


class RetainedByteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump(self):
        for _ in range(10):
            self.app.processEvents()

    def test_backing_storage_not_slice_length_and_shared_allocation_counted_once(self):
        root = np.arange(1000, dtype=np.float64)
        a, b = root[1:2], root[10:20]
        self.assertEqual(union_bytes(retained_arrays(a), retained_arrays(b)), 8000)
        self.assertEqual(union_bytes(retained_arrays(a, b, root.copy())), 16000)
        backing = bytes(4096)
        self.assertEqual(union_bytes(retained_arrays(np.frombuffer(backing, dtype=np.uint8, count=1))), 4096)

    def test_nested_source_arrays_accounted_but_scalar_metadata_not_walked(self):
        @dataclass(frozen=True)
        class Publication:
            density: np.ndarray
            statistics: object = None
            acquisitions: tuple = ()

        publication = Publication(np.zeros(32, dtype=np.float32), Publication(np.zeros(8)), tuple(range(20000)))
        view = SpectrumFrameView(publication, np.arange(4, dtype=np.float64), np.ones(4), "dBm")
        self.assertEqual(union_bytes(retained_arrays(view)), 128 + 64 + 32 + 32)
        with self.assertRaises(ValueError):
            retained_arrays(np.array([object()], dtype=object))

    def test_shared_requests_deduplicate_sources_but_reserve_each_result(self):
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit, max_retained_bytes=4500)
        first = request()
        projector.offer(first)
        self.assertEqual(projector.retained_bytes, 1200 + 1440)
        projector.offer(replace(first))
        self.assertEqual(projector.retained_bytes, 1200 + 2880)
        self.assertLessEqual(projector.peak_retained_bytes, 4500)
        projector.dispose()
        self.pump()
        self.assertEqual(projector.retained_bytes, 0)

    def test_overflow_rejects_unretained_latest_and_retries_after_active_ack(self):
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit, max_retained_bytes=4500)
        first = request()
        errors, retries = [], []
        projector.failed.connect(lambda value, error: errors.append(error))
        projector.retry_ready.connect(lambda: retries.append(1))
        projector.offer(first)
        worker.jobs[0][0].set_running_or_notify_cancel()
        projector.offer(request(first.owner))
        self.assertEqual(projector.byte_rejections, 1)
        self.assertEqual(projector.retained_bytes, 2640)
        self.assertIsNone(projector._pending)
        self.assertEqual(len(worker.jobs), 1)
        self.assertEqual(len(errors), 1)
        # Simulate completion without ManualWorker's second running transition.
        future, operation = worker.jobs.pop(0)
        future.set_result(operation())
        self.assertEqual(projector.retained_bytes, 2640)  # queued GUI ack still owns it
        self.pump()
        self.assertEqual(projector.retained_bytes, 0)
        self.assertEqual(retries, [1])
        projector.offer(request(first.owner))
        self.assertEqual(len(worker.jobs), 1)
        projector.dispose()
        self.pump()

    def test_intrinsically_oversize_has_no_retry_loop_and_no_job(self):
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit, max_retained_bytes=100)
        retries = []
        projector.retry_ready.connect(lambda: retries.append(1))
        projector.offer(request())
        self.pump()
        self.assertEqual(projector.byte_rejections, 1)
        self.assertEqual(projector.retained_bytes, 0)
        self.assertEqual(worker.jobs, [])
        self.assertEqual(retries, [])

    def test_suspend_resume_and_submit_failure_release_reservations(self):
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit, max_retained_bytes=4500)
        projector.offer(request())
        projector.set_suspended(True)
        self.assertLessEqual(projector.retained_bytes, 4500)
        self.pump()
        self.assertEqual(projector.retained_bytes, 1200)
        projector.set_suspended(False)
        self.assertEqual(projector.retained_bytes, 2640)
        projector.dispose()
        self.pump()
        self.assertEqual(projector.retained_bytes, 0)
        def broken(operation):
            raise RuntimeError("executor unavailable")
        failed = SpectrumProjector(broken)
        failed.offer(request())
        self.assertEqual(failed.retained_bytes, 0)

    def test_actual_scene_reoffers_latest_after_capacity_ack_without_new_frame(self):
        fixture = viewport_fixture.ViewportProjectionTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        try:
            fixture.port.max_retained_bytes = 1_500_000
            fixture.admit(sequence=1)
            latest = fixture.admit(sequence=2, value=-35)
            self.assertGreaterEqual(fixture.port.byte_rejections, 1)
            self.assertIsNotNone(fixture.scene._projection_error)
            fixture.drain()
            self.assertIs(fixture.scene.displayed_frame, latest)
            self.assertIsNone(fixture.scene._projection_error)
            self.assertLessEqual(fixture.port.peak_retained_bytes, 1_500_000)
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()

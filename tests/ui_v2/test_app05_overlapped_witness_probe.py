"""The opt-in hash overlap observer may not change Visual pixels or history."""

import threading
import unittest
from concurrent.futures import CancelledError, ThreadPoolExecutor

import numpy as np

from scripts.probe_app05_overlapped_witness import OverlappedWitnessProbe
from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
    DensityValueMode,
    PersistenceDensityView,
    PersistenceRenderMode,
)
from sdr_monitor.ui.v2.spectrum.persistence_projection import (
    PersistenceImagePolicy,
    PersistenceImageRequest,
    prepare_persistence_image,
)


def _view(values: np.ndarray) -> PersistenceDensityView:
    density = np.asarray(values, dtype=np.float32)
    frequencies = np.linspace(1e9, 1.01e9, density.shape[1] + 1)
    levels = np.linspace(-120, -20, density.shape[0] + 1)
    for array in (density, frequencies, levels):
        array.setflags(write=False)
    return PersistenceDensityView(object(), density, frequencies, levels,
                                  DensityValueMode.PROBABILITY, "dBm")


class OverlappedWitnessProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, True)
        self.view = _view(np.array([[0, .25, .75, 1.0], [0, .5, .5, 0]], np.float32))

    def test_worker_output_and_digest_match_stock_across_history(self) -> None:
        base_history = probe_history = None
        with OverlappedWitnessProbe() as probe, ThreadPoolExecutor(max_workers=1) as worker:
            for revision in range(1, 4):
                baseline = prepare_persistence_image(PersistenceImageRequest(
                    self.view, self.policy, base_history))
                candidate = worker.submit(probe.prepare, PersistenceImageRequest(
                    self.view, self.policy, probe_history)).result(timeout=5)
                self.assertEqual(candidate.image.tobytes(), baseline.image.tobytes())
                self.assertEqual(candidate.input_witness.digest, baseline.input_witness.digest)
                base_history = baseline.as_history(revision)
                probe_history = candidate.as_history(revision)
            restore = worker.submit(probe.prepare, PersistenceImageRequest(
                self.view, self.policy, probe_history, rematerialize=True)).result(timeout=5)
            self.assertIs(restore.image, probe_history.image)
            self.assertEqual(probe.parallel_completed, 3)
            self.assertEqual(probe.rematerializations, 1)
        self.assertFalse(probe.report()["active_at_close"])
        self.assertFalse(any(t.name.startswith("app05-witness-probe") for t in threading.enumerate()))

    def test_mutated_same_publication_rejects_rematerialization(self) -> None:
        with OverlappedWitnessProbe() as probe, ThreadPoolExecutor(max_workers=1) as worker:
            first = worker.submit(probe.prepare, PersistenceImageRequest(
                self.view, self.policy)).result(timeout=5)
            self.view.density.setflags(write=True)
            self.view.density[0, 1] = .9
            self.view.density.setflags(write=False)
            history = first.as_history(1)
            restore = worker.submit(probe.prepare, PersistenceImageRequest(
                self.view, self.policy, history, rematerialize=True)).result(timeout=5)
            expected = prepare_persistence_image(PersistenceImageRequest(
                self.view, self.policy, history, rematerialize=False))
            self.assertIsNot(restore.image, first.image)
            self.assertEqual(restore.image.tobytes(), expected.image.tobytes())
            self.assertNotEqual(restore.input_witness.digest, first.input_witness.digest)

    def test_cancel_while_both_tasks_active_does_not_leave_helper(self) -> None:
        cancelled = threading.Event()
        hash_entered = threading.Event()
        map_entered = threading.Event()
        release = threading.Event()
        with OverlappedWitnessProbe() as probe, ThreadPoolExecutor(max_workers=1) as worker:
            original_hash = probe._original_witness
            original_prepare = probe._original_prepare

            def held_hash(view, *, cancelled=None):
                hash_entered.set()
                if not release.wait(2):
                    raise AssertionError("test hash barrier expired")
                return original_hash(view, cancelled=cancelled)

            def held_prepare(request, *, cancelled=None):
                map_entered.set()
                if not release.wait(2):
                    raise AssertionError("test mapping barrier expired")
                return original_prepare(request, cancelled=cancelled)

            probe._original_witness = held_hash
            probe._original_prepare = held_prepare
            pending = worker.submit(probe.prepare, PersistenceImageRequest(
                self.view, self.policy), cancelled=cancelled.is_set)
            self.assertTrue(hash_entered.wait(2))
            self.assertTrue(map_entered.wait(2))
            cancelled.set()
            release.set()
            with self.assertRaises(CancelledError):
                pending.result(timeout=5)
            self.assertFalse(probe._active.locked())
            self.assertEqual(probe.parallel_started, 1)
            self.assertEqual(probe.parallel_cancelled, 1)
            self.assertEqual(probe.parallel_completed, 0)
        self.assertFalse(any(t.name.startswith("app05-witness-probe") for t in threading.enumerate()))

    def test_helper_error_rejects_entire_result_and_drains(self) -> None:
        with OverlappedWitnessProbe() as probe, ThreadPoolExecutor(max_workers=1) as worker:
            def broken_witness(_view, *, cancelled=None):
                raise RuntimeError("synthetic witness failure")

            probe._original_witness = broken_witness
            with self.assertRaisesRegex(RuntimeError, "synthetic witness failure"):
                worker.submit(probe.prepare, PersistenceImageRequest(
                    self.view, self.policy)).result(timeout=5)
            self.assertFalse(probe._active.locked())
            self.assertEqual(probe.parallel_started, 1)
            self.assertEqual(probe.parallel_failed, 1)
            self.assertEqual(probe.parallel_completed, 0)
        self.assertFalse(any(t.name.startswith("app05-witness-probe") for t in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()

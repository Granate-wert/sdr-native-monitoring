"""Exact immutable density preparation on the existing spectrum executor."""
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch
import weakref

import numpy as np
from PySide6.QtWidgets import QApplication

from scripts.benchmark_app04_poll_overload import run_qt_until
from sdr_monitor.ui.v2.spectrum import persistence_projection as density_worker
from sdr_monitor.ui.v2.spectrum import projection
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
    DensityValueMode, PersistenceRenderMode, adapt_persistence_density,
)
from sdr_monitor.ui.v2.spectrum.persistence_projection import (
    PersistenceImagePolicy, PersistenceImageRequest, prepare_persistence_image,
    persistence_image_reserve,
)
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_app05_viewport_projection import ManualWorker
from tests.ui_v2.test_spectrum_scene import _persistence_frame


def view(values, mode=DensityValueMode.PROBABILITY):
    frame = _persistence_frame(values, mode=mode)
    for array in (frame.density, frame.frequency_edges_hz, frame.level_edges):
        array.setflags(write=False)
    return adapt_persistence_density(frame)


def request(values=None, *, policy=None, history=None):
    return PersistenceImageRequest(view(np.full((4, 16), .5, np.float32) if values is None else values),
        policy or PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, True), history)


def spectrum_request(density, owner=None):
    return projection.ProjectionRequest(owner or object(), 1, (0, 1, 100), (), persistence=density)


class PersistenceWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bit_exact_direct_visual_against_existing_overlay_with_missing_and_layouts(self):
        scene = SpectrumScene()
        try:
            for mode in PersistenceRenderMode:
                for logarithmic in (False, True):
                    for value_mode in DensityValueMode:
                        scene._persistence.set_render_mode(mode)
                        scene._persistence.set_logarithmic(logarithmic)
                        scene._persistence._visual_buffer = None
                        history = None
                        for serial in range(3):
                            values = np.random.default_rng(serial).random((3, 65539)).astype(np.float32)
                            values[:, ::101] = np.nan
                            values[:, ::109] = np.inf
                            values[:, ::113] = -np.inf
                            values[:, ::127] = 0
                            if value_mode is DensityValueMode.COUNT:
                                values *= 400
                            if serial == 1:
                                values = values[:, ::-1]
                            elif serial == 2:
                                values = np.asfortranarray(values)
                            source = view(values, value_mode)
                            source_copy = source.density.copy()
                            policy = PersistenceImagePolicy(4, mode, logarithmic)
                            current = PersistenceImageRequest(source, policy, history)
                            result = prepare_persistence_image(current)
                            expected = scene._persistence._render_image(source)
                            np.testing.assert_array_equal(result.image.view(np.uint32), expected.view(np.uint32))
                            np.testing.assert_array_equal(source.density, source_copy)
                            self.assertFalse(result.image.flags.writeable)
                            self.assertTrue(result.image.flags.owndata)
                            if history is not None:
                                self.assertFalse(np.shares_memory(result.image, history.image))
                            self.assertTrue(result.matches(current))
                            self.assertFalse(result.matches(replace(current, policy=replace(policy, revision=5))))
                            history = result.as_history(serial)
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()

    def test_history_has_no_source_or_previous_chain_and_policy_geometry_reset(self):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, False)
        current = request(policy=policy)
        source_ref = weakref.ref(current.view.density)
        result = prepare_persistence_image(current)
        history = result.as_history(7)
        del result, current
        self.assertIsNone(source_ref())
        for candidate in (replace(policy, revision=2), replace(policy, logarithmic=True),
                          replace(policy, mode=PersistenceRenderMode.DIRECT)):
            current = request(np.full((4, 16), .8, np.float32), policy=candidate, history=history)
            actual = prepare_persistence_image(current)
            expected = prepare_persistence_image(replace(current, history=None))
            np.testing.assert_array_equal(actual.image, expected.image)
        current = request(np.full((4, 8), .8, np.float32), policy=policy, history=history)
        np.testing.assert_array_equal(prepare_persistence_image(current).image, .8)

    def test_rejects_mutable_handoff_and_invalid_policy(self):
        frame = _persistence_frame(np.zeros((2, 4), np.float32))
        with self.assertRaisesRegex(ValueError, "immutable"):
            PersistenceImageRequest(adapt_persistence_density(frame), PersistenceImagePolicy(0, PersistenceRenderMode.DIRECT, False))
        for revision in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                PersistenceImagePolicy(revision, PersistenceRenderMode.DIRECT, False)

    def test_cancel_at_chunk_boundary_never_mutates_visual_history(self):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, False)
        history = prepare_persistence_image(request(np.full((3, 65539), .25, np.float32), policy=policy)).as_history(1)
        before = history.image.copy()
        current = request(np.full((3, 65539), .75, np.float32), policy=policy, history=history)
        cancelled = threading.Event()
        original = density_worker.map_density_row_for_display
        sizes = []

        def mapping(values, **kwargs):
            sizes.append(values.size)
            original(values, **kwargs)
            cancelled.set()

        with patch.object(density_worker, "map_density_row_for_display", side_effect=mapping):
            with self.assertRaises(CancelledError):
                prepare_persistence_image(current, cancelled=cancelled.is_set)
        self.assertEqual(sizes, [65536])
        np.testing.assert_array_equal(history.image, before)
        cancelled.clear()
        self.assertTrue(prepare_persistence_image(current).matches(current))

    def test_existing_executor_policy_barrier_discards_old_and_uses_one_worker(self):
        entered, release = threading.Event(), threading.Event()
        threads, delivered = [], []
        first = request()
        second = replace(first, policy=replace(first.policy, revision=2, logarithmic=False))
        original = projection.prepare_persistence_image
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-density-existing") as executor:
            budget = PresentationAllocationBudget()
            port = projection.SpectrumProjector(executor.submit, allocation_budget=budget)
            port.ready.connect(delivered.append)

            def preparing(value, **kwargs):
                threads.append(threading.get_ident())
                if value is first:
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("density policy barrier")
                return original(value, **kwargs)

            try:
                with patch.object(projection, "prepare_persistence_image", side_effect=preparing):
                    offered = spectrum_request(first)
                    port.offer(offered)
                    run_qt_until(entered.is_set, 1)
                    port.offer(replace(offered, persistence=second))
                    self.assertTrue(port._cancel.is_set())
                    release.set()
                    run_qt_until(lambda: len(delivered) == 1 and port._future is None, 2)
                self.assertTrue(delivered[0].persistence.matches(second))
                self.assertEqual(len(set(threads)), 1)
                self.assertNotEqual(threads[0], threading.get_ident())
                self.assertEqual(port.cancelled, 1)
                self.assertEqual(budget.snapshot().reserved_bytes, 0)
            finally:
                release.set()
                port.dispose()

    def test_shared_reservation_denial_cancel_error_and_dispose_release(self):
        for outcome in ("success", "cancel", "error", "dispose", "denied"):
            with self.subTest(outcome=outcome):
                worker = ManualWorker()
                density = request()
                budget = PresentationAllocationBudget(1 if outcome == "denied" else 1000000)
                port = projection.SpectrumProjector(worker.submit, allocation_budget=budget)
                delivered = []
                port.ready.connect(delivered.append)
                try:
                    offered = spectrum_request(density)
                    port.offer(offered)
                    if outcome == "denied":
                        self.assertEqual(worker.jobs, [])
                    else:
                        self.assertEqual(budget.snapshot().reserved_bytes, persistence_image_reserve(density))
                        if outcome == "cancel":
                            port.cancel_pending(offered.owner)
                        if outcome == "dispose":
                            port.dispose()
                        worker.finish(RuntimeError("mapping failed") if outcome == "error" else None)
                        run_qt_until(lambda: port._future is None, 1)
                    self.assertEqual(budget.snapshot().reserved_bytes, 0)
                    self.assertEqual(len(delivered), int(outcome == "success"))
                    if delivered:
                        self.assertTrue(delivered[0].persistence.matches(density))
                finally:
                    port.dispose()


if __name__ == "__main__":
    unittest.main()

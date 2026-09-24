"""Exact immutable density preparation on the existing spectrum executor."""
import threading
import unittest
import weakref
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from scripts.benchmark_app04_poll_overload import run_qt_until
from sdr_monitor.ui.v2.spectrum import persistence_projection as density_worker
from sdr_monitor.ui.v2.spectrum import projection
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.contracts import SpectrumFrameView, TraceKind
from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
    DensityValueMode,
    PersistenceRenderMode,
    adapt_persistence_density,
)
from sdr_monitor.ui.v2.spectrum.persistence_projection import (
    PersistenceImagePolicy,
    PersistenceImageRequest,
    persistence_image_reserve,
    prepare_persistence_image,
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

    def visual_pair(self, mode=DensityValueMode.PROBABILITY):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, False)
        old = prepare_persistence_image(PersistenceImageRequest(
            view(np.full((4, 16), .2, np.float32), mode), policy)).as_history(1)
        values = np.full((4, 16), .8, np.float32)
        values[:, 0] = .05  # Exercise both attack and release, also COUNT normalization.
        current = PersistenceImageRequest(view(values, mode), policy, old)
        return current, prepare_persistence_image(current).as_history(2)

    def test_rematerialization_reuses_exact_accepted_image_and_count_labels(self):
        for mode in DensityValueMode:
            current, history = self.visual_pair(mode)
            restore = replace(current, history=history, rematerialize=True)
            before = history.image.copy()
            for revision in range(3, 6):
                result = prepare_persistence_image(restore)
                self.assertIs(result.image, history.image)
                np.testing.assert_array_equal(result.image.view(np.uint32), before.view(np.uint32))
                self.assertEqual(result.count_maximum, history.count_maximum)
                self.assertTrue(result.matches(restore))
                self.assertFalse(result.matches(replace(restore, rematerialize=False)))
                self.assertFalse(result.image.flags.writeable)
                restore = replace(restore, history=result.as_history(revision))

    def test_new_update_and_new_equal_publication_still_advance_once(self):
        current, history = self.visual_pair()
        update = replace(current, history=history)
        expected = prepare_persistence_image(update)
        self.assertFalse(np.array_equal(expected.image, history.image))
        new_view = view(current.view.density.copy())
        for restore in (False, True):
            actual = prepare_persistence_image(replace(update, view=new_view, rematerialize=restore))
            np.testing.assert_array_equal(actual.image, expected.image)
            self.assertIsNot(actual.image, history.image)

    def test_same_array_mutated_bytes_and_edges_cannot_claim_freshness(self):
        for target in ("density", "frequency_edges_hz", "level_edges"):
            current, history = self.visual_pair()
            array = getattr(current.view, target)
            # Deliberately violate publication immutability BETWEEN requests:
            # ndarray identity/read-only flags are not validation certificates.
            array.setflags(write=True)
            array.flat[1] += .01 if target == "density" else .001
            array.setflags(write=False)
            restore = replace(current, history=history, rematerialize=True)
            actual = prepare_persistence_image(restore)
            expected = prepare_persistence_image(replace(restore, rematerialize=False))
            self.assertIsNot(actual.image, history.image)
            np.testing.assert_array_equal(actual.image, expected.image)

    def test_visual_fallback_restores_without_advancing_and_new_data_advances(self):
        scene = SpectrumScene()
        try:
            overlay = scene._persistence
            overlay.set_render_mode(PersistenceRenderMode.VISUAL)
            overlay.set_logarithmic(False)
            current, history = self.visual_pair()
            overlay._render_image(view(np.full((4, 16), .2, np.float32)))
            accepted = overlay._render_image(current.view).copy()
            np.testing.assert_array_equal(accepted, history.image)
            for _ in range(3):
                np.testing.assert_array_equal(overlay._render_image(current.view, rematerialize=True), accepted)
            advanced = overlay._render_image(view(current.view.density.copy()), rematerialize=True)
            self.assertFalse(np.array_equal(advanced, accepted))
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()

    def test_witness_hash_is_cancellable_bounded_and_absent_in_direct(self):
        current = request(np.asfortranarray(np.ones((3, 65539), np.float64)),
                          policy=PersistenceImagePolicy(0, PersistenceRenderMode.VISUAL, False))
        checks = []

        def cancelled():
            checks.append(True)
            return len(checks) == 3

        with patch.object(density_worker, "map_density_row_for_display", side_effect=AssertionError("mapping before hash")):
            with self.assertRaises(CancelledError):
                prepare_persistence_image(current, cancelled=cancelled)
        self.assertEqual(len(checks), 3)
        self.assertEqual(density_worker.persistence_witness_scratch(current.view), 65536 * 8)
        small = request(policy=current.policy)
        self.assertEqual(density_worker.persistence_witness_scratch(small.view), 17 * 8)
        self.assertEqual(persistence_image_reserve(small), 4 * 16 * 4 + 17 * 8)
        with patch.object(density_worker, "persistence_input_witness", side_effect=AssertionError("Direct must not hash")):
            prepare_persistence_image(request())

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

    def test_zero_chunks_skip_full_transfer_but_preserve_signed_zero_and_visual_decay(self):
        scene = SpectrumScene()
        try:
            for mode in PersistenceRenderMode:
                for logarithmic in (False, True):
                    for value_mode in DensityValueMode:
                        scene._persistence.set_render_mode(mode)
                        scene._persistence.set_logarithmic(logarithmic)
                        scene._persistence._visual_buffer = None
                        policy = PersistenceImagePolicy(0, mode, logarithmic)
                        previous = view(np.full((3, 65539), .25, np.float32), value_mode)
                        history = prepare_persistence_image(PersistenceImageRequest(previous, policy)).as_history(1)
                        scene._persistence._render_image(previous)
                        zero = np.zeros((3, 65539), np.float32)
                        zero[:, ::2] = -0.0
                        source = view(zero, value_mode)
                        with patch.object(density_worker, "map_density_row_for_display",
                                          wraps=density_worker.map_density_row_for_display) as mapping:
                            actual = prepare_persistence_image(PersistenceImageRequest(source, policy, history))
                        expected = scene._persistence._render_image(source)
                        np.testing.assert_array_equal(actual.image.view(np.uint32), expected.view(np.uint32))
                        self.assertEqual(mapping.call_count, 0)
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()

    def test_native_visual_row_kernel_matches_numpy_bits_and_rejects_unsafe_rows(self):
        kernel = density_worker._visual_smoothing_kernel()
        if kernel is None:
            self.skipTest("this active native module predates the optional Visual row kernel")
        edge_bits = np.array((
            0x00000000, 0x80000000, 0x00000001,
            0x007fffff, 0x00800000,
            0x3effffff, 0x3f000000, 0x3f000001, 0x3f7fffff,
            0x3f800000,
        ), dtype=np.uint32).view(np.float32)
        paired = np.array([(left, right) for left in edge_bits for right in edge_bits],
                          dtype=np.float32)
        random_display = np.random.default_rng(20260923).random(
            (65536, 2), dtype=np.float32)
        for pairs in (paired, random_display):
            mapped = np.ascontiguousarray(pairs[:, 0])
            old = np.ascontiguousarray(pairs[:, 1])
            old.setflags(write=False)
            expected, actual = np.empty_like(mapped), np.empty_like(mapped)
            mask = np.empty(mapped.size, dtype=np.bool_)
            with np.errstate(all="ignore"):
                np.subtract(mapped, old, out=expected)
                np.multiply(expected, .18, out=expected)
                np.greater_equal(mapped, old, out=mask)
                np.multiply(expected, .65 / .18, out=expected, where=mask)
                np.add(old, expected, out=expected)
                kernel(mapped, old, actual)
            np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))
        with self.assertRaises(ValueError):
            kernel(mapped[::2], old[::2], actual[::2])
        with self.assertRaises(ValueError):
            kernel(mapped, old, mapped)
        actual.setflags(write=False)
        with self.assertRaises(ValueError):
            kernel(mapped, old, actual)

    def test_native_visual_path_keeps_chunk_boundaries_and_immutable_history(self):
        kernel = density_worker._visual_smoothing_kernel()
        if kernel is None:
            self.skipTest("this active native module predates the optional Visual row kernel")
        policy = PersistenceImagePolicy(3, PersistenceRenderMode.VISUAL, True)
        prior = view(np.full((3, 65537), .2, np.float32))
        accepted = prepare_persistence_image(PersistenceImageRequest(prior, policy)).as_history(1)
        before = accepted.image.view(np.uint32).copy()
        values = np.random.default_rng(9).random((3, 65537), dtype=np.float32)
        values[:, ::101] = np.nan
        current = PersistenceImageRequest(view(values[:, ::-1]), policy, accepted)
        with patch.object(density_worker, "_visual_smoothing_kernel", return_value=None):
            expected = prepare_persistence_image(current)
        calls = []

        def observed(mapped, old, target):
            calls.append(mapped.size)
            kernel(mapped, old, target)

        with patch.object(density_worker, "_visual_smoothing_kernel", return_value=observed):
            actual = prepare_persistence_image(current)
        self.assertEqual(calls, [65536, 1] * 3)
        np.testing.assert_array_equal(actual.image.view(np.uint32), expected.image.view(np.uint32))
        np.testing.assert_array_equal(accepted.image.view(np.uint32), before)
        self.assertFalse(actual.image.flags.writeable)
        self.assertTrue(actual.image.flags.owndata)

    def test_native_fastpath_rejects_untrusted_or_replaced_history(self):
        kernel = density_worker._visual_smoothing_kernel()
        if kernel is None:
            self.skipTest("this active native module predates the optional Visual row kernel")
        policy = PersistenceImagePolicy(3, PersistenceRenderMode.VISUAL, False)
        prior = view(np.full((2, 64), .2, np.float32))
        accepted = prepare_persistence_image(PersistenceImageRequest(prior, policy)).as_history(1)
        invalid = np.full((2, 64), .2, np.float32)
        invalid[0, 0] = np.nan
        invalid.setflags(write=False)
        changed = request(np.full((2, 64), .8, np.float32), policy=policy,
                          history=replace(accepted, image=invalid))
        with patch.object(density_worker, "_visual_smoothing_kernel",
                          side_effect=AssertionError("untrusted history cannot use native")):
            actual = prepare_persistence_image(changed)
        self.assertIsNone(actual._worker_image_ref)
        expected = prepare_persistence_image(changed)
        np.testing.assert_array_equal(actual.image.view(np.uint32), expected.image.view(np.uint32))
        continued = replace(changed, history=actual.as_history(2))
        with patch.object(density_worker, "_visual_smoothing_kernel",
                          side_effect=AssertionError("untrusted history cannot become native")):
            prepare_persistence_image(continued)

    def test_dense_and_strided_chunks_do_not_add_an_input_zero_scan(self):
        original = np.any
        for values in (np.full((3, 65539), .25, np.float32),
                       np.asfortranarray(np.zeros((3, 65539), np.float32)),
                       np.zeros((3, 65539), np.float32)[:, ::-1]):
            source = view(values)
            scans = []

            def scan(values, *args, **kwargs):
                if np.shares_memory(values, source.density):
                    scans.append(values.size)
                return original(values, *args, **kwargs)

            with patch.object(density_worker.np, "any", side_effect=scan):
                result = prepare_persistence_image(PersistenceImageRequest(source,
                    PersistenceImagePolicy(0, PersistenceRenderMode.DIRECT, True)))
            self.assertFalse(scans)
            self.assertEqual(result.image.shape, source.density.shape)

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
        np.testing.assert_array_equal(prepare_persistence_image(current).image,
                                      np.full((4, 8), .8, np.float32))
        current = request(np.full((4, 16), .8, np.float32), policy=policy, history=history)
        edges = current.view.frequency_edges_hz + 1e6
        edges.setflags(write=False)
        shifted = replace(current.view.source_frame, frequency_edges_hz=edges)
        current = replace(current, view=adapt_persistence_density(shifted))
        np.testing.assert_array_equal(prepare_persistence_image(current).image,
                                      np.full((4, 16), .8, np.float32))

    def test_rejects_mutable_handoff_and_invalid_policy(self):
        frame = _persistence_frame(np.zeros((2, 4), np.float32))
        with self.assertRaisesRegex(ValueError, "immutable"):
            PersistenceImageRequest(adapt_persistence_density(frame), PersistenceImagePolicy(0, PersistenceRenderMode.DIRECT, False))
        for revision in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                PersistenceImagePolicy(revision, PersistenceRenderMode.DIRECT, False)

    def test_count_reduction_drops_previous_finite_selection_before_next_chunk(self):
        selections = []

        class TrackedDensity(np.ndarray):
            def __getitem__(self, index):
                selected = isinstance(index, np.ndarray) and index.dtype == np.bool_
                if selected:
                    # Instrument array allocation lifetime, not timing/RSS.
                    self_test.assertTrue(all(ref() is None for ref in selections),
                                         "overlapping full finite-selection chunks")
                result = super().__getitem__(index)
                if selected:
                    selections.append(weakref.ref(result))
                return result

        self_test = self
        original = view(np.ones((3, 65539), np.float64), DensityValueMode.COUNT)
        tracked = original.density.view(TrackedDensity)
        current = PersistenceImageRequest(replace(original, density=tracked),
                                         PersistenceImagePolicy(0, PersistenceRenderMode.DIRECT, False))
        result = prepare_persistence_image(current)
        self.assertEqual(len(selections), 6)
        self.assertTrue(all(ref() is None for ref in selections))
        np.testing.assert_array_equal(result.image, 1)

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
                    self.assertEqual(budget.snapshot().reserved_bytes, persistence_image_reserve(first))
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
        for outcome in ("success", "cancel", "error", "dispose", "denied", "density-denied"):
            with self.subTest(outcome=outcome):
                worker = ManualWorker()
                density = request(np.full((4, 256), .5, np.float32)) if outcome == "density-denied" else request()
                budget = PresentationAllocationBudget(1 if outcome == "denied" else
                                                      9000 if outcome == "density-denied" else 1000000)
                port = projection.SpectrumProjector(worker.submit, allocation_budget=budget)
                delivered = []
                port.ready.connect(delivered.append)
                try:
                    offered = spectrum_request(density)
                    if outcome == "density-denied":
                        frequencies, values = np.arange(16, dtype=np.float64), np.full(16, -70, np.float32)
                        frequencies.setflags(write=False)
                        values.setflags(write=False)
                        trace = SpectrumFrameView(object(), frequencies, values, "dBm")
                        offered = replace(offered, viewport=(0, 16, 100), traces=((TraceKind.CURRENT, trace),))
                    port.offer(offered)
                    if outcome == "denied":
                        self.assertEqual(worker.jobs, [])
                    else:
                        # No trace outputs here; density reserve belongs to
                        # worker execution, not to an unbounded GUI image job.
                        self.assertEqual(budget.snapshot().reserved_bytes, 2304 if outcome == "density-denied" else 0)
                        if outcome == "cancel":
                            port.cancel_pending(offered.owner)
                        if outcome == "dispose":
                            port.dispose()
                        worker.finish(RuntimeError("mapping failed") if outcome == "error" else None)
                        run_qt_until(lambda: port._future is None, 1)
                    self.assertEqual(budget.snapshot().reserved_bytes, 0)
                    self.assertEqual(len(delivered), int(outcome in ("success", "density-denied")))
                    if outcome == "density-denied":
                        self.assertIsNone(delivered[0].persistence)
                        self.assertIn("budget exceeded", delivered[0].persistence_error)
                        self.assertIs(delivered[0].request, offered)
                        self.assertEqual(len(delivered[0].traces), 1)
                        np.testing.assert_array_equal(delivered[0].traces[0][1].values, -70)
                    elif delivered:
                        self.assertTrue(delivered[0].persistence.matches(density))
                finally:
                    port.dispose()


if __name__ == "__main__":
    unittest.main()

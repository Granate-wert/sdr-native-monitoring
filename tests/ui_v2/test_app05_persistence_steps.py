"""Exact, bounded optional-image continuation before any product wiring."""

from concurrent.futures import CancelledError
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, PersistenceRenderMode
from sdr_monitor.ui.v2.spectrum import persistence_projection as density_worker
from sdr_monitor.ui.v2.spectrum.persistence_projection import (
    PersistenceImagePolicy,
    PersistenceImageRequest,
    PersistenceImageSteps,
    persistence_image_reserve,
    prepare_persistence_image,
)
from tests.ui_v2.test_app05_persistence_worker import view


def finish(steps: PersistenceImageSteps, *, batch: int):
    result = None
    calls = 0
    while result is None:
        result = steps.advance(max_chunks=batch)
        calls += 1
        if calls > 1000:
            raise AssertionError("persistence continuation did not finish")
    return result, calls


class PersistenceImageStepsTests(unittest.TestCase):
    def test_all_modes_layouts_and_chunk_sizes_match_existing_worker_bit_for_bit(self):
        base = np.random.default_rng(20260924).random((3, 65539), dtype=np.float32)
        base[:, ::101] = np.nan
        base[:, ::109] = np.inf
        base[:, ::113] = -np.inf
        base[:, ::127] = -0.0
        for value_mode in DensityValueMode:
            for mode in PersistenceRenderMode:
                for logarithmic in (False, True):
                    for layout in ("c", "f", "reverse"):
                        with self.subTest(value_mode=value_mode, mode=mode,
                                          logarithmic=logarithmic, layout=layout):
                            values = base * (400 if value_mode is DensityValueMode.COUNT else 1)
                            if layout == "f":
                                values = np.asfortranarray(values)
                            elif layout == "reverse":
                                values = values[:, ::-1]
                            source = view(values, value_mode)
                            policy = PersistenceImagePolicy(3, mode, logarithmic)
                            request = PersistenceImageRequest(source, policy)
                            expected = prepare_persistence_image(request)
                            for batch in (1, 7, 128):
                                result, calls = finish(PersistenceImageSteps(request), batch=batch)
                                self.assertGreaterEqual(calls, 1)
                                np.testing.assert_array_equal(
                                    result.image.view(np.uint32), expected.image.view(np.uint32))
                                self.assertEqual(result.count_maximum, expected.count_maximum)
                                self.assertEqual(result.quantitative_labels, expected.quantitative_labels)
                                self.assertEqual(
                                    None if result.input_witness is None else result.input_witness.digest,
                                    None if expected.input_witness is None else expected.input_witness.digest)
                                self.assertFalse(result.image.flags.writeable)
                                self.assertTrue(result.image.flags.owndata)

    def test_visual_history_rematerialization_and_new_equal_publication(self):
        policy = PersistenceImagePolicy(4, PersistenceRenderMode.VISUAL, False)
        first = PersistenceImageRequest(view(np.full((4, 65539), .2, np.float32)), policy)
        history = prepare_persistence_image(first).as_history(1)
        fresh = np.full((4, 65539), .8, np.float32)
        fresh[:, ::17] = .05
        update = PersistenceImageRequest(view(fresh), policy, history)
        expected = prepare_persistence_image(update)
        result, _ = finish(PersistenceImageSteps(update), batch=5)
        np.testing.assert_array_equal(result.image.view(np.uint32), expected.image.view(np.uint32))
        self.assertEqual(result.input_witness.digest, expected.input_witness.digest)
        accepted = result.as_history(2)
        restore = replace(update, history=accepted, rematerialize=True)
        restored, _ = finish(PersistenceImageSteps(restore), batch=3)
        self.assertIs(restored.image, accepted.image)
        self.assertEqual(restored.input_witness.digest, expected.input_witness.digest)
        new_array = view(update.view.density.copy())
        advanced, _ = finish(PersistenceImageSteps(replace(restore, view=new_array)), batch=3)
        self.assertIsNot(advanced.image, accepted.image)
        reference = prepare_persistence_image(replace(restore, view=new_array))
        np.testing.assert_array_equal(advanced.image.view(np.uint32), reference.image.view(np.uint32))

    def test_cancel_and_reservation_never_publish_partial_image(self):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, True)
        request = PersistenceImageRequest(view(np.ones((64, 65536), np.float32)), policy)
        budget = PresentationAllocationBudget(64 * 1024 * 1024)
        with budget.reserve(persistence_image_reserve(request), request) as reservation:
            steps = PersistenceImageSteps(request)
            for _ in range(4):
                self.assertIsNone(steps.advance(max_chunks=5))
            self.assertIsNone(steps._image)
            with self.assertRaises(CancelledError):
                steps.advance(max_chunks=5, cancelled=lambda: True)
            self.assertIsNone(steps._image)
            self.assertFalse(hasattr(steps, "request"))
            with self.assertRaises(RuntimeError):
                steps.advance()
            result, _ = finish(PersistenceImageSteps(request), batch=5)
            reservation.commit(result.image)
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_cancel_at_final_hash_and_map_boundaries_is_terminal(self):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, False)
        initial = PersistenceImageRequest(view(np.full((2, 16), .4, np.float32)), policy)
        history = prepare_persistence_image(initial).as_history(1)
        restore = replace(initial, history=history, rematerialize=True)
        hashing = PersistenceImageSteps(restore)

        def after_hash():
            return hashing._stage == "witness" and hashing._hash_array == 3

        with self.assertRaises(CancelledError):
            hashing.advance(max_chunks=100, cancelled=after_hash)
        self.assertEqual(hashing._stage, "aborted")
        self.assertFalse(hasattr(hashing, "request"))
        with self.assertRaises(RuntimeError):
            hashing.advance()

        mapping = PersistenceImageSteps(initial)

        def after_map():
            return mapping._stage == "map" and mapping._map_row == 2

        with self.assertRaises(CancelledError):
            mapping.advance(max_chunks=100, cancelled=after_map)
        self.assertEqual(mapping._stage, "aborted")
        self.assertIsNone(mapping._image)
        with self.assertRaises(RuntimeError):
            mapping.advance()

    def test_partial_scratch_allocation_failure_discards_continuation(self):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, False)
        initial = PersistenceImageRequest(view(np.full((2, 16), .2, np.float32)), policy)
        history = prepare_persistence_image(initial).as_history(1)
        update = replace(initial, history=history)
        steps = PersistenceImageSteps(update)
        original = np.empty
        calls = 0

        def fail_scratch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise MemoryError("injected scratch allocation failure")
            return original(*args, **kwargs)

        with patch.object(density_worker.np, "empty", side_effect=fail_scratch):
            with self.assertRaises(MemoryError):
                steps.advance(max_chunks=100)
        self.assertEqual(steps._stage, "aborted")
        self.assertIsNone(steps._image)
        self.assertFalse(hasattr(steps, "request"))
        with self.assertRaises(RuntimeError):
            steps.advance()

    def test_reject_zero_or_boolean_chunk_quota(self):
        request = PersistenceImageRequest(
            view(np.ones((2, 16), np.float32)),
            PersistenceImagePolicy(0, PersistenceRenderMode.DIRECT, False),
        )
        steps = PersistenceImageSteps(request)
        for value in (0, -1, True, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                steps.advance(max_chunks=value)


if __name__ == "__main__":
    unittest.main()

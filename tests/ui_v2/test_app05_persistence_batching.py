"""Exact bounded C-array density batching; no acquisition or Qt application."""
from concurrent.futures import CancelledError
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum import persistence_projection as worker
from sdr_monitor.ui.v2.spectrum.allocation_budget import (
    PresentationAllocationBudget,
    PresentationBudgetExceeded,
)
from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
    DensityValueMode,
    PersistenceDensityFrame,
    PersistenceRenderMode,
    adapt_persistence_density,
    map_density_row_for_display,
)
from sdr_monitor.ui.v2.spectrum.persistence_projection import (
    IMAGE_BATCH,
    PersistenceImagePolicy,
    PersistenceImageRequest,
    persistence_image_reserve,
    prepare_persistence_image,
)
from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays, union_bytes


class DensitySubclass(np.ndarray):
    """A caller's ndarray subclass must not enter the flattened fast path."""


def make_view(values, value_mode=DensityValueMode.PROBABILITY):
    values.setflags(write=False)
    frequencies = np.linspace(204e6 - 61.44e6 / 2, 204e6 + 61.44e6 / 2, values.shape[1] + 1)
    levels = np.linspace(-140., 20., values.shape[0] + 1)
    frequencies.setflags(write=False)
    levels.setflags(write=False)
    frame = PersistenceDensityFrame(values, frequencies, levels, value_mode, "dBFS")
    view = adapt_persistence_density(frame)
    # Public construction correctly normalizes subclasses with np.asarray;
    # retain a shared-backing subclass view to exercise the worker's defensive
    # fallback, as in the pre-existing TrackedDensity selection-lifetime test.
    return replace(view, density=values) if type(values) is not np.ndarray else view


def row_reference(request):
    """Frozen pre-batching numerical sequence, exhaustive original row domains."""
    view = request.view
    history = worker._compatible_history(request)
    maximum = 0.
    if view.value_mode is DensityValueMode.COUNT:
        for row in view.density:
            for first in range(0, row.size, IMAGE_BATCH):
                finite = row[first:first + IMAGE_BATCH]
                finite = finite[np.isfinite(finite)]
                if finite.size:
                    maximum = max(maximum, float(np.max(finite)))
                del finite
    image = np.empty(view.density.shape, np.float32)
    scratch = None if history is None else np.empty(min(IMAGE_BATCH, image.shape[1]), np.float32)
    mask = None if history is None else np.empty(min(IMAGE_BATCH, image.shape[1]), np.bool_)
    for index, row in enumerate(view.density):
        for first in range(0, row.size, IMAGE_BATCH):
            source = row[first:first + IMAGE_BATCH]
            target = image[index, first:first + source.size]
            mapped = target if scratch is None else scratch[:source.size]
            if source.flags.c_contiguous and source[0] == 0.0 and not np.any(source):
                np.copyto(mapped, source)
            else:
                map_density_row_for_display(source, value_mode=view.value_mode,
                    logarithmic=request.policy.logarithmic, count_maximum=maximum, out=mapped)
            if history is not None:
                assert mask is not None
                old = history[index, first:first + source.size]
                row_mask = mask[:source.size]
                np.subtract(mapped, old, out=target)
                np.multiply(target, .18, out=target)
                np.greater_equal(mapped, old, out=row_mask)
                np.multiply(target, .65 / .18, out=target, where=row_mask)
                np.add(old, target, out=target)
    return image, maximum


class PersistenceBatchingTests(unittest.TestCase):
    def assert_exact(self, result, request, expected, maximum):
        self.assertTrue(result.matches(request))
        self.assertIs(result.view.source_frame, request.view.source_frame)
        self.assertEqual(result.image.shape, request.view.density.shape)
        self.assertEqual(result.image.dtype, np.float32)
        self.assertFalse(result.image.flags.writeable)
        self.assertTrue(result.image.flags.owndata)
        self.assertTrue(result.image.flags.c_contiguous)
        self.assertEqual(result.count_maximum, maximum)
        for row in range(expected.shape[0]):
            np.testing.assert_array_equal(result.image[row].view(np.uint32), expected[row].view(np.uint32))

    def history_for(self, view, policy):
        previous = np.full(view.density.shape, .2, np.float32)
        prior = make_view(previous, view.value_mode)
        return prepare_persistence_image(PersistenceImageRequest(prior, policy)).as_history(7)

    def test_all_modes_dtypes_layouts_missing_values_and_mixed_zero_rows_match_original_bits(self):
        for dtype in (np.float32, np.float64):
            for layout in ("C", "F", "reverse", "subclass"):
                for value_mode in DensityValueMode:
                    values = np.random.default_rng(123).random((20, 4099)).astype(dtype)
                    if value_mode is DensityValueMode.COUNT:
                        values *= 1000
                    values[0] = 0.
                    values[0, ::2] = -0.
                    values[7] = -0.
                    values[15] = 0.
                    values[15, 1::2] = -0.
                    values[3, :4] = (np.nan, np.inf, -np.inf, -0.)
                    values[18, :3] = (-0., 0., .1)
                    if layout == "F":
                        values = np.asfortranarray(values)
                    elif layout == "reverse":
                        values = values[:, ::-1]
                    elif layout == "subclass":
                        values = values.view(DensitySubclass)
                    view = make_view(values, value_mode)
                    input_before = view.density.copy()
                    for mode in PersistenceRenderMode:
                        for logarithmic in (False, True):
                            with self.subTest(dtype=dtype, layout=layout, value=value_mode,
                                              mode=mode, logarithmic=logarithmic):
                                policy = PersistenceImagePolicy(4, mode, logarithmic)
                                history = self.history_for(view, policy) if mode is PersistenceRenderMode.VISUAL else None
                                history_before = None if history is None else history.image.copy()
                                request = PersistenceImageRequest(view, policy, history)
                                expected, maximum = row_reference(request)
                                self.assert_exact(prepare_persistence_image(request), request, expected, maximum)
                                np.testing.assert_array_equal(view.density, input_before)
                                if history is not None:
                                    np.testing.assert_array_equal(history.image.view(np.uint32), history_before.view(np.uint32))

    def test_unaligned_wide_original_zero_chunk_and_zero_subsection_keep_exact_bits(self):
        # Flat batches cut across row starts. A zero subsection must not gain
        # identity, whereas the OLD 3-cell signed-zero tail must keep identity.
        values = np.full((3, IMAGE_BATCH + 3), .25, np.float32)
        values[0, IMAGE_BATCH:] = (-0., 0., -0.)
        values[1, :IMAGE_BATCH] = -0.
        values[1, 0] = .5  # original domain is nonzero despite a zero flat subsection
        values[2, :IMAGE_BATCH] = -0.
        view = make_view(values)
        for mode in PersistenceRenderMode:
            for logarithmic in (False, True):
                policy = PersistenceImagePolicy(1, mode, logarithmic)
                history = self.history_for(view, policy) if mode is PersistenceRenderMode.VISUAL else None
                request = PersistenceImageRequest(view, policy, history)
                expected, maximum = row_reference(request)
                with self.subTest(mode=mode, logarithmic=logarithmic):
                    self.assert_exact(prepare_persistence_image(request), request, expected, maximum)

    def test_full_narrow_c_array_maps_sixteen_noncopying_chunks_and_count_reduces_sixteen(self):
        for value_mode in DensityValueMode:
            for mode in PersistenceRenderMode:
                policy = PersistenceImagePolicy(1, mode, True)
                values = np.full((256, 4096), .5 if value_mode is DensityValueMode.PROBABILITY else 8., np.float32)
                view = make_view(values, value_mode)
                history = self.history_for(view, policy) if mode is PersistenceRenderMode.VISUAL else None
                request = PersistenceImageRequest(view, policy, history)
                expected, maximum = row_reference(request)
                sizes, reductions = [], []
                original_mapping, original_finite = worker.map_density_row_for_display, np.isfinite

                def mapping(source, **kwargs):
                    sizes.append(source.size)
                    self.assertTrue(np.shares_memory(source, view.density))
                    self.assertEqual(source.ndim, 1)
                    original_mapping(source, **kwargs)

                def finite(source, *args, **kwargs):
                    if not sizes and np.shares_memory(source, view.density):
                        reductions.append(source.size)
                    return original_finite(source, *args, **kwargs)

                with self.subTest(value=value_mode, mode=mode), patch.object(
                        worker, "map_density_row_for_display", side_effect=mapping), patch.object(
                        worker.np, "isfinite", side_effect=finite):
                    result = prepare_persistence_image(request)
                self.assertEqual(sizes, [IMAGE_BATCH] * 16)
                self.assertEqual(reductions, [IMAGE_BATCH] * 16 if value_mode is DensityValueMode.COUNT else [])
                self.assert_exact(result, request, expected, maximum)

    def test_strided_and_subclass_density_retain_row_calls_and_row_reserve(self):
        for layout in ("F", "reverse", "subclass"):
            values = np.full((4, 4096), .5, np.float32)
            values = (np.asfortranarray(values) if layout == "F" else
                      values[:, ::-1] if layout == "reverse" else values.view(DensitySubclass))
            request = PersistenceImageRequest(make_view(values), PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, True))
            with patch.object(worker, "map_density_row_for_display", wraps=worker.map_density_row_for_display) as mapping:
                prepare_persistence_image(request)
            self.assertEqual(mapping.call_count, 4)
            self.assertEqual([call.args[0].size for call in mapping.call_args_list], [4096] * 4)
            self.assertEqual(persistence_image_reserve(request), values.size * 4 + 4096 * 5)

    def test_cancel_after_first_full_mapping_batch_preserves_accepted_history(self):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, True)
        view = make_view(np.full((256, 4096), .8, np.float32))
        history = self.history_for(view, policy)
        before = history.image.copy()
        request = PersistenceImageRequest(view, policy, history)
        cancelled, sizes = threading.Event(), []
        original = worker.map_density_row_for_display

        def mapping(source, **kwargs):
            sizes.append(source.size)
            original(source, **kwargs)
            cancelled.set()

        with patch.object(worker, "map_density_row_for_display", side_effect=mapping):
            with self.assertRaises(CancelledError):
                prepare_persistence_image(request, cancelled=cancelled.is_set)
        self.assertEqual(sizes, [IMAGE_BATCH])
        np.testing.assert_array_equal(history.image.view(np.uint32), before.view(np.uint32))
        cancelled.clear()
        expected, maximum = row_reference(request)
        self.assert_exact(prepare_persistence_image(request), request, expected, maximum)

    def test_count_cancellation_after_first_full_reduction_batch_precedes_mapping(self):
        request = PersistenceImageRequest(make_view(np.full((256, 4096), 8., np.float64), DensityValueMode.COUNT),
                                          PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, True))
        cancelled, sizes = threading.Event(), []
        original = np.isfinite

        def finite(source, *args, **kwargs):
            sizes.append(source.size)
            result = original(source, *args, **kwargs)
            cancelled.set()
            return result

        with patch.object(worker.np, "isfinite", side_effect=finite), patch.object(
                worker, "map_density_row_for_display", side_effect=AssertionError("mapping before reduction cancellation")):
            with self.assertRaises(CancelledError):
                prepare_persistence_image(request, cancelled=cancelled.is_set)
        self.assertEqual(sizes, [IMAGE_BATCH])

    def test_actual_batch_scratch_reserved_before_allocation_and_pressure_denies(self):
        for dtype in (np.float32, np.float64):
            for mode in PersistenceRenderMode:
                view = make_view(np.full((256, 4096), .5, dtype))
                policy = PersistenceImagePolicy(1, mode, True)
                history = self.history_for(view, policy) if mode is PersistenceRenderMode.VISUAL else None
                request = PersistenceImageRequest(view, policy, history)
                per_cell = max(6 if history is not None else 5, view.density.dtype.itemsize + 1)
                expected_reserve = view.density.size * 4 + IMAGE_BATCH * per_cell
                self.assertEqual(persistence_image_reserve(request), expected_reserve)
                sources = union_bytes(retained_arrays(request))
                old_row_reserve = view.density.size * 4 + 4096 * per_cell
                budget = PresentationAllocationBudget(sources + old_row_reserve)
                self.assertTrue(budget.admit_sources(request))
                with patch.object(worker.np, "empty", side_effect=AssertionError("allocated before reserve")):
                    with self.assertRaises(PresentationBudgetExceeded):
                        with budget.reserve(persistence_image_reserve(request), request):
                            prepare_persistence_image(request)
                self.assertEqual(budget.snapshot().reserved_bytes, 0)
                self.assertEqual(budget.snapshot().rejections, 1)
                sufficient = PresentationAllocationBudget(sources + expected_reserve)
                self.assertTrue(sufficient.admit_sources(request))
                with sufficient.reserve(persistence_image_reserve(request), request) as reservation:
                    result = prepare_persistence_image(request)
                    reservation.commit(result.image)
                self.assertEqual(sufficient.snapshot().reserved_bytes, 0)


if __name__ == "__main__":
    unittest.main()

"""Compiled proof of the optional live-readonly Waterfall storage leaf."""

import gc
import importlib.util
import os
from pathlib import Path
import sys
import threading
import unittest

import numpy as np

from tests.native_test_dependencies import native_test_dll_directory


class ProtectedWaterfallBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected:
            raise unittest.SkipTest("requires explicit matching protected-storage native; no fallback")
        path = Path(selected).resolve(strict=True)
        scope = native_test_dll_directory(str(path))
        scope.__enter__()
        cls.addClassCleanup(scope.__exit__, None, None, None)
        spec = importlib.util.spec_from_file_location("_sdr_native", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("explicit native module has no loadable extension spec")
        cls.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.native)
        if Path(cls.native.__file__).resolve() != path:
            raise AssertionError("native loader did not load the explicitly selected file")
        if cls.native.WATERFALL_PROTECTED_STORAGE_PROTOCOL_VERSION != 1:
            raise AssertionError("explicit native does not implement protected Waterfall protocol 1")

    def storage(self, rows=3, columns=4):
        return self.native._WaterfallProtectedStorage(rows, columns)

    def exporter_and_memoryview(self, array, exporter_type):
        base = array.base
        if isinstance(base, memoryview):
            self.assertTrue(base.readonly)
            exporter = base.obj
            base_view = base
        else:
            exporter = base
            base_view = memoryview(exporter)
        self.assertIsInstance(exporter, exporter_type)
        self.assertTrue(base_view.readonly)
        self.assertTrue(memoryview(exporter).readonly)
        return exporter, base_view

    def test_readonly_exports_metadata_and_unconstructible_exporters(self):
        storage = self.storage()
        data = storage.data
        revisions = storage.row_revisions
        float_owner, float_base_view = self.exporter_and_memoryview(
            data, self.native._WaterfallFloatBuffer)
        revision_owner, revision_base_view = self.exporter_and_memoryview(
            revisions, self.native._WaterfallRevisionBuffer)
        self.assertEqual(data.shape, (3, 4))
        self.assertEqual(data.dtype, np.dtype(np.float32))
        self.assertEqual(revisions.shape, (3,))
        self.assertEqual(revisions.dtype, np.dtype(np.uint64))
        self.assertFalse(data.flags.writeable)
        self.assertFalse(revisions.flags.writeable)
        self.assertTrue(memoryview(data).readonly)
        self.assertTrue(memoryview(revisions).readonly)
        for base_view in (float_base_view, revision_base_view,
                          memoryview(float_owner), memoryview(revision_owner)):
            with self.assertRaises(TypeError):
                base_view.cast("B")[0] = 9
        self.assertEqual(float_owner.allocation_nbytes, data.nbytes)
        self.assertEqual(revision_owner.allocation_nbytes, revisions.nbytes)
        self.assertNotEqual(float_owner.allocation_id, revision_owner.allocation_id)
        self.assertEqual(storage.storage_bytes, data.nbytes + revisions.nbytes)
        self.assertEqual(storage.revision, 0)
        np.testing.assert_array_equal(revisions, 0)
        np.testing.assert_array_equal(data, 0)

        for original in (data, revisions):
            for exported in (original, original.view(), original.reshape(-1)):
                self.assertFalse(exported.flags.writeable)
                with self.assertRaises(ValueError):
                    exported.setflags(write=True)
                with self.assertRaises(ValueError):
                    exported.flat[0] = 9
                self.assertTrue(memoryview(exported).readonly)
                with self.assertRaises(TypeError):
                    memoryview(exported).cast("B")[0] = 9
        for exporter_type in (self.native._WaterfallFloatBuffer,
                              self.native._WaterfallRevisionBuffer):
            with self.assertRaises(TypeError):
                exporter_type()
        with self.assertRaises(AttributeError):
            storage.data = data
        with self.assertRaises(AttributeError):
            float_owner.allocation_nbytes = 0

        second = self.storage(1, 2)
        second_data = second.data
        second_rows = second.row_revisions
        second_float_id = self.exporter_and_memoryview(
            second_data, self.native._WaterfallFloatBuffer)[0].allocation_id
        second_revision_id = self.exporter_and_memoryview(
            second_rows, self.native._WaterfallRevisionBuffer)[0].allocation_id
        self.assertEqual(self.exporter_and_memoryview(
            storage.data, self.native._WaterfallFloatBuffer)[0].allocation_id,
            float_owner.allocation_id)
        self.assertEqual(self.exporter_and_memoryview(
            storage.row_revisions, self.native._WaterfallRevisionBuffer)[0].allocation_id,
            revision_owner.allocation_id)
        self.assertEqual(len({float_owner.allocation_id, revision_owner.allocation_id,
                              second_float_id, second_revision_id}), 4)

    def test_constructor_strict_bounds_and_invalid_write_is_transactional(self):
        for args in ((True, 2), (2, False), (0, 2), (2, 0), (4097, 1), (1, 2049),
                     (2.0, 2), ("2", 2), (-1, 2)):
            with self.subTest(args=args), self.assertRaises((TypeError, ValueError, OverflowError)):
                self.storage(*args)

        storage = self.storage()
        data = storage.data
        revisions = storage.row_revisions
        before_bits = data.view(np.uint32).copy()
        before_revisions = revisions.copy()

        class ArraySubclass(np.ndarray):
            pass

        wrong_endian = np.dtype(">f4" if sys.byteorder == "little" else "<f4")
        invalid_rows = (
            (True, np.zeros(4, np.float32)),
            (3, np.zeros(4, np.float32)),
            (0, np.zeros(4, np.float64)),
            (0, np.zeros(4, dtype=wrong_endian)),
            (0, np.zeros((1, 4), np.float32)),
            (0, np.zeros(3, np.float32)),
            (0, np.arange(8, dtype=np.float32)[::2]),
            (0, np.zeros(4, np.float32).view(ArraySubclass)),
            (0, [1.0, 2.0, 3.0, 4.0]),
        )
        for index, row in invalid_rows:
            with self.subTest(index=index, row_type=type(row).__name__), \
                    self.assertRaises((TypeError, ValueError, IndexError, OverflowError)):
                storage.write_row(index, row)
            np.testing.assert_array_equal(data.view(np.uint32), before_bits)
            np.testing.assert_array_equal(revisions, before_revisions)
            self.assertEqual(storage.revision, 0)

        for index in (-1, 4):
            with self.assertRaises((IndexError, ValueError)):
                storage.write_row(index, np.ones(4, np.float32))
            np.testing.assert_array_equal(data.view(np.uint32), before_bits)
            np.testing.assert_array_equal(revisions, before_revisions)

        class IndexLike:
            def __init__(self):
                self.calls = 0

            def __index__(self):
                self.calls += 1
                return 1

        index_like = IndexLike()
        with self.assertRaises(TypeError):
            storage.write_row(index_like, np.full(4, 7.0, np.float32))
        self.assertEqual(index_like.calls, 0)
        self.assertEqual(storage.revision, 0)
        self.assertEqual(int(storage.row_revisions[1]), 0)

        constructor_index = IndexLike()
        with self.assertRaises(TypeError):
            self.storage(constructor_index, 2)
        self.assertEqual(constructor_index.calls, 0)

        calls = []

        class ReentrantIndex:
            def __index__(self):
                calls.append("called")
                storage.clear()
                raise RuntimeError("must not be invoked")

        before_bits = storage.data.view(np.uint32).copy()
        before_rows = storage.row_revisions.copy()
        before_revision = storage.revision
        with self.assertRaises(TypeError):
            storage.write_row(ReentrantIndex(), np.full(4, 8.0, np.float32))
        with self.assertRaises(TypeError):
            self.storage(ReentrantIndex(), 2)
        self.assertEqual(calls, [])
        np.testing.assert_array_equal(storage.data.view(np.uint32), before_bits)
        np.testing.assert_array_equal(storage.row_revisions, before_rows)
        self.assertEqual(storage.revision, before_revision)
        with self.assertRaises(OverflowError):
            storage.write_row(1 << 100, np.full(4, 9.0, np.float32))
        np.testing.assert_array_equal(storage.data.view(np.uint32), before_bits)
        np.testing.assert_array_equal(storage.row_revisions, before_rows)
        self.assertEqual(storage.revision, before_revision)

    def test_exact_bits_overlap_live_alias_and_clear_revision(self):
        storage = self.storage(2, 4)
        bits = np.array([0x7FC01234, 0x80000000, 0x7F800000, 0xFF800000,
                         0x3F800000, 0xBF800000, 0x00000001, 0xFFFFFFFF], dtype=np.uint32)
        exact = bits[:4].view(np.float32)
        live_data = storage.data
        live_revisions = storage.row_revisions
        storage.write_row(0, exact)
        np.testing.assert_array_equal(live_data[0].view(np.uint32), bits[:4])
        np.testing.assert_array_equal(live_revisions, np.array([1, 0], np.uint64))
        self.assertEqual(storage.revision, 1)

        # The source is readonly but overlaps the destination row; memmove must
        # preserve the pre-write bytes rather than corrupting them in place.
        overlap = live_data.reshape(-1)[1:5]
        overlap_bits = overlap.view(np.uint32).copy()
        storage.write_row(0, overlap)
        np.testing.assert_array_equal(live_data[0].view(np.uint32), overlap_bits)
        np.testing.assert_array_equal(live_revisions, np.array([2, 0], np.uint64))
        self.assertEqual(storage.revision, 2)

        storage.write_row(1, bits[4:].view(np.float32))
        self.assertEqual(storage.revision, 3)

        # Append-like physical slot order wraps 0, 1, 0. The later slot-0
        # write must replace only that row and keep the global revision monotone.
        first_slot0 = np.array([101, 102, 103, 104], np.float32)
        slot1 = np.array([201, 202, 203, 204], np.float32)
        latest_slot0 = np.array([301, 302, 303, 304], np.float32)
        storage.write_row(0, first_slot0)
        storage.write_row(1, slot1)
        storage.write_row(0, latest_slot0)
        np.testing.assert_array_equal(live_data[0], latest_slot0)
        np.testing.assert_array_equal(live_data[1], slot1)
        np.testing.assert_array_equal(live_revisions, np.array([6, 5], np.uint64))
        self.assertEqual(storage.revision, 6)

        storage.clear()
        self.assertEqual(storage.revision, 7)
        np.testing.assert_array_equal(live_data, 0)
        np.testing.assert_array_equal(live_revisions, np.array([7, 7], np.uint64))

    def test_canonical_root_budget_and_reconstructed_alias_extent_limitation(self):
        from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
        from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_roots

        storage = self.storage(3, 4)
        data = storage.data
        revisions = storage.row_revisions
        float_exporter, _float_base_view = self.exporter_and_memoryview(
            data, self.native._WaterfallFloatBuffer)
        revision_exporter, _revision_base_view = self.exporter_and_memoryview(
            revisions, self.native._WaterfallRevisionBuffer)
        budget = PresentationAllocationBudget(4096)
        budget.observe(data, revisions)
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes)

        canonical_tiny = data[:1, :1]
        budget.observe(canonical_tiny)
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes)
        del data
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes)
        del canonical_tiny
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, revisions.nbytes)
        self.assertEqual(float_exporter.allocation_nbytes, 3 * 4 * np.dtype(np.float32).itemsize)

        # Reconstructing an ndarray directly from the PEP3118 exporter skips
        # the canonical NumPy root; current retained_roots sees only the tiny
        # view. Exporter extent/id metadata is the required future integration
        # hook, not a claim that the present ledger already consumes it.
        reconstructed = np.frombuffer(memoryview(float_exporter), dtype=np.float32, count=1)
        tracked = retained_roots(reconstructed)
        tracked_bytes = sum(size for size, _aliases in tracked.values())
        self.assertEqual(float_exporter.allocation_nbytes, 3 * 4 * np.dtype(np.float32).itemsize)
        self.assertEqual(reconstructed.nbytes, np.dtype(np.float32).itemsize)
        self.assertLess(tracked_bytes, float_exporter.allocation_nbytes)
        self.assertEqual(revision_exporter.allocation_nbytes, 3 * np.dtype(np.uint64).itemsize)
        self.assertNotEqual(float_exporter.allocation_id, revision_exporter.allocation_id)

        del reconstructed, revisions
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 0)
        self.assertEqual(float_exporter.allocation_nbytes, 3 * 4 * np.dtype(np.float32).itemsize)
        self.assertEqual(revision_exporter.allocation_nbytes, 3 * np.dtype(np.uint64).itemsize)

    def test_creator_thread_identity_guards_invalid_and_valid_mutations(self):
        storage = self.storage()
        initial_data = storage.data.view(np.uint32).copy()
        initial_revisions = storage.row_revisions.copy()
        failures = []

        def foreign_thread():
            for operation in (
                lambda: storage.write_row(True, [1, 2, 3, 4]),
                storage.clear,
            ):
                try:
                    operation()
                except RuntimeError as error:
                    failures.append(str(error))
                except Exception as error:  # noqa: BLE001 - capture wrong-precondition order
                    failures.append(f"wrong error: {type(error).__name__}: {error}")
                else:
                    failures.append("foreign mutation unexpectedly succeeded")

        thread = threading.Thread(target=foreign_thread)
        thread.start()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 2)
        self.assertTrue(all("creator thread" in failure for failure in failures), failures)
        np.testing.assert_array_equal(storage.data.view(np.uint32), initial_data)
        np.testing.assert_array_equal(storage.row_revisions, initial_revisions)
        self.assertEqual(storage.revision, 0)

    def test_creator_exit_does_not_transfer_controller_authority(self):
        fixture = {}

        def create_on_thread():
            storage = self.storage(2, 3)
            storage.write_row(1, np.array([17.0, -0.0, np.nan], np.float32))
            fixture["storage"] = storage
            fixture["data"] = storage.data
            fixture["revisions"] = storage.row_revisions

        creator = threading.Thread(target=create_on_thread)
        creator.start()
        creator.join(timeout=3)
        self.assertFalse(creator.is_alive())
        storage = fixture["storage"]
        data = fixture["data"]
        revisions = fixture["revisions"]
        before_bits = data.view(np.uint32).copy()
        before_revisions = revisions.copy()
        errors = []

        def foreign_thread_after_creator_exit():
            try:
                storage.write_row(0, np.array([1.0, 2.0, 3.0], np.float32))
            except RuntimeError as error:
                errors.append(str(error))
            except Exception as error:  # noqa: BLE001 - distinguish wrong rejection order
                errors.append(f"wrong error: {type(error).__name__}: {error}")
            try:
                storage.clear()
            except RuntimeError as error:
                errors.append(str(error))
            except Exception as error:  # noqa: BLE001 - distinguish wrong rejection order
                errors.append(f"wrong error: {type(error).__name__}: {error}")

        foreign = threading.Thread(target=foreign_thread_after_creator_exit)
        foreign.start()
        foreign.join(timeout=3)
        self.assertFalse(foreign.is_alive())
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("creator thread" in error for error in errors), errors)
        np.testing.assert_array_equal(data.view(np.uint32), before_bits)
        np.testing.assert_array_equal(revisions, before_revisions)
        self.assertEqual(int(revisions[1]), 1)
        self.assertEqual(float(data[1, 0]), 17.0)
        self.assertTrue(np.signbit(data[1, 1]))
        self.assertTrue(np.isnan(data[1, 2]))

    def test_exports_pin_only_their_vector_after_controller_deletion(self):
        storage = self.storage(2, 3)
        values = np.array([11.0, -0.0, np.nan], np.float32)
        storage.write_row(1, values)
        data = storage.data
        revisions = storage.row_revisions
        float_exporter, _float_base_view = self.exporter_and_memoryview(
            data, self.native._WaterfallFloatBuffer)
        revision_exporter, _revision_base_view = self.exporter_and_memoryview(
            revisions, self.native._WaterfallRevisionBuffer)
        float_id = float_exporter.allocation_id
        revision_id = revision_exporter.allocation_id
        del storage
        gc.collect()
        self.assertEqual(float_id, self.exporter_and_memoryview(
            data, self.native._WaterfallFloatBuffer)[0].allocation_id)
        self.assertEqual(revision_id, self.exporter_and_memoryview(
            revisions, self.native._WaterfallRevisionBuffer)[0].allocation_id)
        np.testing.assert_array_equal(data[1, :2], values[:2])
        self.assertTrue(np.isnan(data[1, 2]))
        self.assertEqual(int(revisions[1]), 1)
        self.assertEqual(float_exporter.allocation_nbytes, data.nbytes)
        self.assertEqual(revision_exporter.allocation_nbytes, revisions.nbytes)
        self.assertNotEqual(float_id, revision_id)


if __name__ == "__main__":
    unittest.main()

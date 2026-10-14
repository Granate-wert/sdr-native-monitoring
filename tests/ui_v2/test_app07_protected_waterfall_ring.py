"""Protected Waterfall ring accounting and operation parity tests."""

import threading
import unittest
from types import ModuleType
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.waterfall.bounded_ring import BoundedWaterfallRenderer
from sdr_monitor.ui.v2.waterfall.sweep_rows import SweepRowStamp, SweepRowState
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)


class ProtectedWaterfallRingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native, cls.support = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def renderer(self, limit=4096):
        renderer = BoundedWaterfallRenderer()
        renderer.allocation_budget = PresentationAllocationBudget(limit)
        return renderer

    def test_budget_admits_protected_ring_and_preserves_strided_wrap_order(self):
        renderer = self.renderer()
        generic = BoundedWaterfallRenderer()
        renderer.append(np.array([1, 0, 2, 0, 3, 0, 4, 0], np.float32)[::2], rows=2, timestamp_ns=10)
        generic.append(np.array([1, 0, 2, 0, 3, 0, 4, 0], np.float32)[::2], rows=2, timestamp_ns=10)
        ring = renderer.buffer
        self.assertIsNotNone(ring)
        self.assertIs(type(ring._protected_storage), self.native._WaterfallProtectedStorage)
        self.assertIs(ring.protected_data, ring._data)
        self.assertFalse(ring.protected_data.flags.writeable)
        self.assertFalse(ring.protected_row_revisions.flags.writeable)
        ring._protected_scratch.fill(77.0)
        renderer.append(np.array([5, 6, 7, 8], np.float32), rows=2, timestamp_ns=20)
        generic.append(np.array([5, 6, 7, 8], np.float32), rows=2, timestamp_ns=20)
        np.testing.assert_array_equal(ring._protected_scratch, np.full(4, 77.0, np.float32))
        renderer.append(np.array([9, 0, 10, 0, 11, 0, 12, 0], np.float32)[::2], rows=2, timestamp_ns=30)
        generic.append(np.array([9, 0, 10, 0, 11, 0, 12, 0], np.float32)[::2], rows=2, timestamp_ns=30)
        np.testing.assert_array_equal(np.concatenate(renderer.tiles()),
                                      np.array([[5, 6, 7, 8], [9, 10, 11, 12]], np.float32))
        np.testing.assert_array_equal(renderer.timestamps_ns(), np.array([20, 30], np.int64))
        np.testing.assert_array_equal(np.concatenate(renderer.tiles()).view(np.uint32),
                                      np.concatenate(generic.tiles()).view(np.uint32))
        np.testing.assert_array_equal(renderer.timestamps_ns(), generic.timestamps_ns())
        self.assertEqual(renderer.sweep_stamps(), generic.sweep_stamps())
        self.assertEqual(ring.protected_revision, 3)
        for tile in renderer.tiles():
            self.assertFalse(tile.flags.writeable)

    def test_foreign_strided_append_refuses_before_scratch_or_ring_state_changes(self):
        renderer = self.renderer()
        renderer.append(np.arange(4, dtype=np.float32), rows=3, timestamp_ns=11)
        ring = renderer.buffer
        ring._protected_scratch.fill(123.0)
        before = {
            "bits": ring._data.view(np.uint32).copy(),
            "revisions": ring._protected_revisions.copy(),
            "timestamps": ring._timestamps_ns.copy(),
            "stamps": tuple(ring._sweep_stamps),
            "indices": dict(ring._sweep_indices),
            "scratch": ring._protected_scratch.copy(),
            "count": ring.count,
            "cursor": ring._write_index,
            "epoch": ring._sweep_epoch,
            "epoch_set": ring._sweep_epoch_set,
            "latest": ring._latest_sweep_sequence,
        }
        failures = []

        def foreign_append():
            try:
                ring.append(np.arange(8, dtype=np.float32)[::2], timestamp_ns=22,
                            sweep_stamp=SweepRowStamp(1, 1, SweepRowState.PARTIAL, 7))
            except RuntimeError as error:
                failures.append(str(error))
            except Exception as error:  # noqa: BLE001 - validate refusal ordering
                failures.append(f"wrong error: {type(error).__name__}: {error}")
            else:
                failures.append("foreign append unexpectedly succeeded")

        worker = threading.Thread(target=foreign_append)
        worker.start()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIn("creator thread", failures[0])
        np.testing.assert_array_equal(ring._data.view(np.uint32), before["bits"])
        np.testing.assert_array_equal(ring._protected_revisions, before["revisions"])
        np.testing.assert_array_equal(ring._timestamps_ns, before["timestamps"])
        np.testing.assert_array_equal(ring._protected_scratch, before["scratch"])
        self.assertEqual(tuple(ring._sweep_stamps), before["stamps"])
        self.assertEqual(ring._sweep_indices, before["indices"])
        self.assertEqual((ring.count, ring._write_index, ring._sweep_epoch,
                          ring._sweep_epoch_set, ring._latest_sweep_sequence),
                         (before["count"], before["cursor"], before["epoch"],
                          before["epoch_set"], before["latest"]))

    def test_sweep_replace_separator_resize_clear_and_budget_refusal(self):
        renderer = self.renderer()
        generic = BoundedWaterfallRenderer()
        first = SweepRowStamp(1, 1, SweepRowState.PARTIAL, 2)
        final = SweepRowStamp(1, 2, SweepRowState.COMPLETE, 2)
        self.assertEqual(renderer.upsert_sweep(np.full(4, -4, np.float32), rows=3, stamp=first), "append")
        self.assertEqual(generic.upsert_sweep(np.full(4, -4, np.float32), rows=3, stamp=first), "append")
        self.assertEqual(renderer.upsert_sweep(np.full(4, -3, np.float32), rows=3, stamp=final), "replace")
        self.assertEqual(generic.upsert_sweep(np.full(4, -3, np.float32), rows=3, stamp=final), "replace")
        self.assertTrue(renderer.append_sweep_separator(stamp=SweepRowStamp(2, 1, SweepRowState.PARTIAL, 3)))
        self.assertTrue(generic.append_sweep_separator(stamp=SweepRowStamp(2, 1, SweepRowState.PARTIAL, 3)))
        late_old_epoch = SweepRowStamp(1, 3, SweepRowState.COMPLETE, 2)
        self.assertEqual(renderer.upsert_sweep(np.full(4, 9, np.float32), rows=3, stamp=late_old_epoch), "reject")
        self.assertEqual(generic.upsert_sweep(np.full(4, 9, np.float32), rows=3, stamp=late_old_epoch), "reject")
        self.assertEqual(renderer.buffer.count, 2)
        self.assertEqual(renderer.buffer.chronological_sweep_stamps(), (final, None))
        np.testing.assert_array_equal(np.concatenate(renderer.tiles()).view(np.uint32),
                                      np.concatenate(generic.tiles()).view(np.uint32))
        np.testing.assert_array_equal(renderer.timestamps_ns(), generic.timestamps_ns())
        old = renderer.buffer
        renderer.resize_rows(4)
        generic.resize_rows(4)
        self.assertIsNot(renderer.buffer, old)
        self.assertEqual(renderer.buffer.count, 2)
        self.assertEqual(renderer.buffer.chronological_sweep_stamps(), (final, None))
        np.testing.assert_array_equal(np.concatenate(renderer.tiles()).view(np.uint32),
                                      np.concatenate(generic.tiles()).view(np.uint32))
        np.testing.assert_array_equal(renderer.timestamps_ns(), generic.timestamps_ns())
        renderer.clear()
        generic.clear()
        self.assertEqual(renderer.buffer.count, 0)

        # The optional protected overhead is denied while the original ring
        # reservation remains admissible under this exact small limit.
        rows, columns = 2, 4
        legacy_bytes = rows * (columns * 4 + 8)
        fallback = self.renderer(legacy_bytes)
        fallback.append(np.ones(columns, np.float32), rows=rows, timestamp_ns=1)
        self.assertIsNone(fallback.buffer._protected_storage)
        self.assertEqual(fallback.buffer.count, 1)

    def test_no_budget_keeps_direct_generic_ring(self):
        renderer = BoundedWaterfallRenderer()
        renderer.append(np.ones(4, np.float32), rows=2, timestamp_ns=1)
        self.assertIsNone(renderer.buffer._protected_storage)

    def test_nonbuiltin_dimension_keeps_original_generic_ring(self):
        renderer = self.renderer()
        renderer.append(np.ones(4, np.float32), rows=np.int64(2), timestamp_ns=1)
        self.assertIsNone(renderer.buffer._protected_storage)
        self.assertEqual(renderer.buffer.rows, 2)

    def test_forged_or_old_runtime_uses_original_generic_ring(self):
        fake = ModuleType("sdr_monitor._sdr_native")
        fake.WATERFALL_PROTECTED_STORAGE_PROTOCOL_VERSION = 1
        fake._WaterfallProtectedStorage = self.native._WaterfallProtectedStorage
        fake._WaterfallFloatBuffer = self.native._WaterfallFloatBuffer
        fake._WaterfallRevisionBuffer = self.native._WaterfallRevisionBuffer
        with patch.dict("sys.modules", {"sdr_monitor._sdr_native": fake}):
            renderer = self.renderer()
            renderer.append(np.ones(4, np.float32), rows=2, timestamp_ns=1)
            self.assertIsNone(renderer.buffer._protected_storage)
            self.assertEqual(renderer.buffer.count, 1)


if __name__ == "__main__":
    unittest.main()

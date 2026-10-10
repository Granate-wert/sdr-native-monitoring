"""Focused guards for redundant unknown-time RTBW AxisItem invalidation."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPicture
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.waterfall.axis import WaterfallTimeAxis
from sdr_monitor.ui.v2.waterfall.contracts import WaterfallDirection
from sdr_monitor.ui.v2.waterfall.sweep_rows import SweepRowStamp, SweepRowState


class _RecordingTimeAxis(WaterfallTimeAxis):
    def __init__(self) -> None:
        self.update_calls = 0
        super().__init__()

    def update(self, *args, **kwargs):
        self.update_calls += 1
        return super().update(*args, **kwargs)


class UnknownTimeAxisReuseTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.axes: list[_RecordingTimeAxis] = []

    def tearDown(self) -> None:
        for axis in self.axes:
            axis.deleteLater()
        self.app.processEvents()

    def _axis(self) -> _RecordingTimeAxis:
        axis = _RecordingTimeAxis()
        axis.update_calls = 0
        self.axes.append(axis)
        return axis

    @staticmethod
    def _sync(
        axis: WaterfallTimeAxis,
        *,
        rows: int = 300,
        capacity: int = 300,
        direction: WaterfallDirection = WaterfallDirection.NEWEST_AT_TOP,
        rate: int = 30,
        timestamps: np.ndarray | tuple[int, ...] = (),
        known: bool = False,
        stamps: tuple[SweepRowStamp | None, ...] = (),
    ) -> None:
        axis.set_presentation_timebase(
            direction=direction,
            rows_per_second=rate,
            display_rows=rows,
            capacity_rows=capacity,
            timestamps_ns=timestamps,
            timestamps_known=known,
            sweep_stamps=stamps,
        )

    def test_identical_unknown_time_full_ring_keeps_cached_picture_without_update(self) -> None:
        axis = self._axis()
        no_sweep_rows = (None,) * 300
        stored_unknown_times = np.zeros(300, dtype=np.int64)
        self._sync(axis, timestamps=stored_unknown_times, stamps=no_sweep_rows)
        self.assertEqual(axis._timestamps_ns.size, 0)
        self.assertFalse(axis._timestamps_known)
        self.assertFalse(axis._has_any_sweep_stamp)

        picture = object()
        axis.picture = picture
        axis.update_calls = 0
        self._sync(axis, timestamps=stored_unknown_times, stamps=no_sweep_rows)

        self.assertIs(axis.picture, picture)
        self.assertEqual(axis.update_calls, 0)
        self.assertEqual(axis._display_rows, 300)
        self.assertEqual(axis._capacity_rows, 300)

    def test_identical_empty_unknown_model_can_reuse_but_known_empty_transition_invalidates(self) -> None:
        axis = self._axis()
        self._sync(axis, rows=0, capacity=300)
        picture = object()
        axis.picture = picture
        axis.update_calls = 0
        self._sync(axis, rows=0, capacity=300)
        self.assertIs(axis.picture, picture)
        self.assertEqual(axis.update_calls, 0)

        axis.update_calls = 0
        self._sync(axis, rows=0, capacity=300, known=True)
        self.assertEqual(axis.update_calls, 1)
        self.assertIsNone(axis.picture)
        self.assertTrue(axis._timestamps_known)

        # Even the same qualified-but-empty input keeps the legacy redraw path.
        axis.picture = object()
        axis.update_calls = 0
        self._sync(axis, rows=0, capacity=300, known=True)
        self.assertEqual(axis.update_calls, 1)
        self.assertIsNone(axis.picture)

        # The empty known -> unknown qualification transition also invalidates.
        axis.picture = object()
        axis.update_calls = 0
        self._sync(axis, rows=0, capacity=300, known=False)
        self.assertEqual(axis.update_calls, 1)
        self.assertIsNone(axis.picture)

    def test_known_unknown_transitions_keep_conversion_and_redraw_semantics(self) -> None:
        axis = self._axis()
        times = np.asarray([1_000, 1_040, 1_080], dtype=np.int64)
        self._sync(axis, rows=3, capacity=3, timestamps=times, known=True)
        self.assertEqual(axis._timestamps_ns.tolist(), times.tolist())
        self.assertTrue(axis._timestamps_known)

        axis.picture = object()
        axis.update_calls = 0
        self._sync(axis, rows=3, capacity=3, timestamps=times, known=False)
        self.assertEqual(axis.update_calls, 1)
        self.assertIsNone(axis.picture)
        self.assertEqual(axis._timestamps_ns.size, 0)
        self.assertFalse(axis._timestamps_known)

        axis.picture = object()
        axis.update_calls = 0
        self._sync(axis, rows=3, capacity=3, timestamps=times, known=True)
        self.assertEqual(axis.update_calls, 1)
        self.assertEqual(axis._timestamps_ns.tolist(), times.tolist())

    def test_full_array_conversion_still_runs_when_timestamp_provenance_is_unknown(self) -> None:
        axis = self._axis()
        no_sweep_rows = (None,) * 300
        self._sync(axis, timestamps=np.zeros(300, dtype=np.int64), stamps=no_sweep_rows)
        picture = object()
        axis.picture = picture
        axis.update_calls = 0
        with self.assertRaises((TypeError, ValueError)):
            self._sync(axis, rows=300, capacity=300,
                       timestamps=np.asarray(["not-an-int64"], dtype=object), known=False)
        self.assertIs(axis.picture, picture)
        self.assertEqual(axis.update_calls, 0)
        self.assertEqual(axis._timestamps_ns.size, 0)

    def test_failed_derivation_for_changed_scalars_forces_recovery_redraw_then_reuse(self) -> None:
        changes = (
            {"capacity": 360},
            {"direction": WaterfallDirection.NEWEST_AT_BOTTOM},
            {"rate": 60},
            {"rows": 299},
        )
        malformed = np.asarray(["not-an-int64"], dtype=object)
        valid_unknown_times = np.zeros(300, dtype=np.int64)

        for change in changes:
            with self.subTest(change=change):
                axis = self._axis()
                self._sync(axis, timestamps=valid_unknown_times)
                stale_picture = object()
                axis.picture = stale_picture
                axis.update_calls = 0

                with self.assertRaises((TypeError, ValueError)):
                    self._sync(axis, timestamps=malformed, **change)
                self.assertIs(axis.picture, stale_picture)
                self.assertFalse(axis._presentation_model_complete)

                # The successful call sees the setter's partially-mutated
                # scalar model, but must discard the stale pre-failure picture.
                self._sync(axis, timestamps=valid_unknown_times, **change)
                self.assertTrue(axis._presentation_model_complete)
                self.assertEqual(axis.update_calls, 1)
                self.assertIsNone(axis.picture)

                # Once a complete picture is present again, an identical
                # unknown-time setter may take the narrow reuse path.
                recovered_picture = object()
                axis.picture = recovered_picture
                axis.update_calls = 0
                self._sync(axis, timestamps=valid_unknown_times, **change)
                self.assertIs(axis.picture, recovered_picture)
                self.assertEqual(axis.update_calls, 0)

    def test_unknown_direction_rate_and_capacity_changes_do_not_skip_invalidation(self) -> None:
        cases = (
            {"direction": WaterfallDirection.NEWEST_AT_BOTTOM},
            {"rate": 60},
            {"capacity": 360},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                axis = self._axis()
                self._sync(axis)
                axis.picture = object()
                axis.update_calls = 0
                self._sync(axis, **changes)
                self.assertEqual(axis.update_calls, 1)
                self.assertIsNone(axis.picture)

    def test_gaps_are_recomputed_and_direction_mapping_is_preserved(self) -> None:
        axis = self._axis()
        times = np.asarray([1_000_000_000, 1_040_000_000,
                            1_600_000_000, 1_640_000_000], dtype=np.int64)
        self._sync(axis, rows=4, capacity=8, timestamps=times, known=True)
        top_gaps = axis._gap_rows
        self.assertTrue(top_gaps)
        axis.picture = object()
        axis.update_calls = 0
        self._sync(axis, rows=4, capacity=8, timestamps=times, known=True,
                   direction=WaterfallDirection.NEWEST_AT_BOTTOM)
        self.assertEqual(axis.update_calls, 1)
        self.assertTrue(axis._gap_rows)
        self.assertNotEqual(axis._gap_rows, top_gaps)

    def test_sweep_partial_complete_and_mixed_epoch_stamps_always_take_redraw_path(self) -> None:
        axis = self._axis()
        partial = SweepRowStamp(8, 1, SweepRowState.PARTIAL, 2)
        complete = SweepRowStamp(8, 2, SweepRowState.COMPLETE, 2)
        other_epoch = SweepRowStamp(9, 1, SweepRowState.GAP, 3)
        axis.picture = object()
        axis.update_calls = 0
        self._sync(axis, rows=2, capacity=4, stamps=(partial, None))
        self.assertEqual(axis.update_calls, 1)
        self.assertIsNone(axis.picture)
        self.assertTrue(axis._has_sweep_stamps)

        axis.picture = object()
        axis.update_calls = 0
        self._sync(axis, rows=2, capacity=4, stamps=(complete, None))
        self.assertEqual(axis.update_calls, 1)
        self.assertEqual(axis._sweep_stamps, (complete, None))

        axis.picture = object()
        axis.update_calls = 0
        self._sync(axis, rows=2, capacity=4, stamps=(complete, other_epoch))
        self.assertEqual(axis.update_calls, 1)
        self.assertTrue(axis._multiple_sweep_epochs)

    def test_external_picture_invalidation_is_not_resurrected_or_hidden(self) -> None:
        axis = self._axis()
        self._sync(axis)
        # Models style/font/view/locale/resize invalidating the inherited cache.
        axis.picture = None
        axis.update_calls = 0
        self._sync(axis)
        self.assertEqual(axis.update_calls, 1)
        self.assertIsNone(axis.picture)

    def test_positive_unknown_growth_converts_placeholders_and_retains_real_picture(self) -> None:
        axis = self._axis()
        self._sync(axis, rows=1, timestamps=np.zeros(1, dtype=np.int64))
        picture = QPicture()
        axis.picture = picture  # Unit invalidation probe, not a benchmark picture.
        axis.update_calls = 0
        original_asarray = np.asarray
        with patch("sdr_monitor.ui.v2.waterfall.axis.np.asarray", wraps=original_asarray) as converted:
            for rows in (2, 4, 17, 60, 299, 300):
                placeholders = np.zeros(rows, dtype=np.int64)
                self._sync(axis, rows=rows, timestamps=placeholders)
                self.assertEqual(axis._display_rows, rows)
                self.assertEqual(axis._capacity_rows, 300)
                self.assertIs(axis._direction, WaterfallDirection.NEWEST_AT_TOP)
                self.assertEqual(axis._row_origin, 0)
                self.assertEqual(axis._rows_per_second, 30)
                self.assertEqual(axis._producer_interval_ns, 1_000_000_000 // 30)
                self.assertTrue(axis._presentation_model_complete)
                self.assertFalse(axis._timestamps_known)
                self.assertEqual(axis._timestamps_ns.size, 0)
                self.assertEqual(axis._sweep_stamps, ())
                self.assertFalse(axis._has_any_sweep_stamp)
                self.assertFalse(axis._has_sweep_stamps)
                self.assertFalse(axis._multiple_sweep_epochs)
                self.assertFalse(axis._gap_rows)
                self.assertFalse(axis._age_ticks)
                self.assertIsNone(axis._age_step_ns)
                self.assertIs(axis.picture, picture)
                self.assertEqual(axis.update_calls, 0)
            self.assertEqual(converted.call_count, 6)
            self.assertTrue(all(call.kwargs == {"dtype": np.int64}
                                for call in converted.call_args_list))

    def test_growth_empty_boundary_and_shrink_299_still_invalidate(self) -> None:
        for before, after in ((0, 1), (300, 299), (2, 0)):
            with self.subTest(before=before, after=after):
                axis = self._axis()
                self._sync(axis, rows=before, timestamps=np.zeros(before, dtype=np.int64))
                axis.picture = QPicture()
                axis.update_calls = 0
                self._sync(axis, rows=after, timestamps=np.zeros(after, dtype=np.int64))
                self.assertIsNone(axis.picture)
                self.assertEqual(axis.update_calls, 1)

    def test_growth_requires_empty_axis_stamps_not_all_none_slots(self) -> None:
        for before_stamps, after_stamps in (((None,), (None, None)),
                                            ((None,), ()), ((), (None, None))):
            with self.subTest(before_stamps=before_stamps, after_stamps=after_stamps):
                axis = self._axis()
                self._sync(axis, rows=1, stamps=before_stamps)
                axis.picture = QPicture()
                axis.update_calls = 0
                self._sync(axis, rows=2, stamps=after_stamps)
                self.assertIsNone(axis.picture)
                self.assertEqual(axis.update_calls, 1)
                self.assertFalse(axis._has_any_sweep_stamp)

    def test_growth_changed_settings_or_bottom_mapping_still_invalidate(self) -> None:
        cases = (
            ({}, {"capacity": 360}),
            ({}, {"rate": 60}),
            ({}, {"direction": WaterfallDirection.NEWEST_AT_BOTTOM}),
            ({"direction": WaterfallDirection.NEWEST_AT_BOTTOM},
             {"direction": WaterfallDirection.NEWEST_AT_BOTTOM}),
        )
        for before_settings, after_settings in cases:
            with self.subTest(before=before_settings, after=after_settings):
                axis = self._axis()
                self._sync(axis, rows=1, **before_settings)
                axis.picture = QPicture()
                axis.update_calls = 0
                self._sync(axis, rows=2, **after_settings)
                self.assertIsNone(axis.picture)
                self.assertEqual(axis.update_calls, 1)

    def test_failed_growth_conversion_forces_recovery_redraw_before_later_growth_reuse(self) -> None:
        axis = self._axis()
        self._sync(axis, rows=1, timestamps=np.zeros(1, dtype=np.int64))
        stale_picture = QPicture()
        axis.picture = stale_picture
        axis.update_calls = 0
        with self.assertRaises((TypeError, ValueError)):
            self._sync(axis, rows=2, timestamps=np.asarray(["not-an-int64"], dtype=object))
        self.assertFalse(axis._presentation_model_complete)
        self.assertEqual(axis._display_rows, 2)
        self.assertIs(axis.picture, stale_picture)
        self.assertEqual(axis.update_calls, 0)

        # Recovery itself grows again, but its prior model is incomplete.
        self._sync(axis, rows=3, timestamps=np.zeros(3, dtype=np.int64))
        self.assertTrue(axis._presentation_model_complete)
        self.assertIsNone(axis.picture)
        self.assertEqual(axis.update_calls, 1)
        recovered_picture = QPicture()
        axis.picture = recovered_picture
        axis.update_calls = 0
        self._sync(axis, rows=4, timestamps=np.zeros(4, dtype=np.int64))
        self.assertIs(axis.picture, recovered_picture)
        self.assertEqual(axis.update_calls, 0)

    def test_growth_with_external_none_or_non_qpicture_does_not_reuse(self) -> None:
        for picture in (None, object()):
            with self.subTest(picture_type=type(picture).__name__):
                axis = self._axis()
                self._sync(axis, rows=1)
                axis.picture = picture
                axis.update_calls = 0
                self._sync(axis, rows=2)
                self.assertIsNone(axis.picture)
                self.assertEqual(axis.update_calls, 1)


if __name__ == "__main__":
    unittest.main()

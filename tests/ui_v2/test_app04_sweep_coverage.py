"""Coverage is physical, bounded display data; history never becomes current."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineGapReason, SweepLineState
from sdr_monitor.ui.v2.spectrum.sweep_coverage import CURRENT, PREVIOUS, MISSING, MAX_COLUMNS, SweepCoverageState


def line(sequence, values, *, epoch=1):
    values = np.asarray(values, dtype=np.float32)
    missing = np.isnan(values) | np.isposinf(values)
    return SweepLineFrame(
        sequence, epoch, 123, "coverage-fixture", SweepLineState.GAP if missing.any() else SweepLineState.COMPLETE,
        100e6 + np.arange(values.size) * 1000, values, missing.astype(np.uint16),
        np.where(missing, -1, 0), (), ((0, 1),),
        (SweepLineGapReason.MISSING_SEGMENT,) if missing.any() else (), "dBFS/bin")


def snapshot(frame):
    return ContinuousSweepDisplaySnapshot(frame, ContinuousSweepDisplayMetrics())


class SweepCoverageTests(unittest.TestCase):
    def test_fully_current_batches_skip_old_extrema_and_keep_exact_empty_geometry(self):
        for count, width in ((20, 100), (200_003, 1024), (200_003, 17)):
            with self.subTest(count=count, width=width):
                state = SweepCoverageState()
                state.accept(snapshot(line(1, np.full(count, -10.0))))
                values = np.full(count, -80.0)
                values[::7] = -np.inf  # Measured zero is current, not a hole.
                state.accept(snapshot(line(2, values)))
                with patch("sdr_monitor.ui.v2.spectrum.sweep_coverage.extrema_rows",
                           side_effect=AssertionError("fully replaced history was reduced")):
                    actual = state.project(0, 1e12, width)
                np.testing.assert_array_equal(actual.states, np.full(actual.states.size, CURRENT))
                self.assertTrue(np.isnan(actual.history.values).all())
                self.assertEqual(actual.history.display_point_count,
                                 count if count <= width * 4 else actual.states.size)
                self.assertFalse(actual.history.values.flags.writeable)

    def test_optimized_history_matches_reference_across_current_batch_and_short_tail(self):
        from sdr_monitor.ui.v2.spectrum.envelope_batch import extrema_rows
        from sdr_monitor.ui.v2.spectrum import sweep_coverage
        state = SweepCoverageState()
        count = 200_003
        old = np.linspace(-95, -35, count, dtype=np.float32)
        state.accept(snapshot(line(1, old)))
        values = np.full(count, np.nan)
        values[65_536:131_072] = -np.inf
        values[-3:] = -70
        state.accept(snapshot(line(2, values)))
        # Force the reference path only for the small row-count condition,
        # without changing NumPy globally or the extrema implementation.
        class NumpyReference:
            def __getattr__(self, name):
                return getattr(np, name)

            @staticmethod
            def all(_value):
                return False

        for width in (16, 1024, 2048):
            with self.subTest(width=width):
                with patch.object(sweep_coverage, "np", NumpyReference()):
                    expected = state.project(0, 1e12, width)
                actual = state.project(0, 1e12, width)
                for a, b in ((actual.edges_hz, expected.edges_hz),
                             (actual.states, expected.states),
                             (actual.history.values, expected.history.values),
                             (actual.history.frequencies_hz, expected.history.frequencies_hz)):
                    np.testing.assert_array_equal(a, b)
        # Exercise a whole middle batch and the keep_small branch separately.
        with patch.object(sweep_coverage, "MAX_COLUMNS", count):
            expected_x, expected_y = extrema_rows(
                state.current.frequencies_hz.reshape(-1, 1),
                state.previous.values_db.reshape(-1, 1),
                np.isnan(values).reshape(-1, 1), keep_small=True)
            actual = state.project(0, 1e12, count)
            np.testing.assert_array_equal(actual.history.frequencies_hz, expected_x)
            np.testing.assert_array_equal(actual.history.values, expected_y)

    def test_measured_zero_owns_coverage_without_showing_stale_history(self):
        state = SweepCoverageState()
        state.accept(snapshot(line(1, [-20, -np.inf, -90, -np.inf])))
        state.accept(snapshot(line(2, [-np.inf, np.nan, np.nan, -np.inf])))
        view = state.project(0, 1e9, 100)
        np.testing.assert_array_equal(view.states, [CURRENT, PREVIOUS, PREVIOUS, CURRENT])
        self.assertEqual(view.history.values[np.isfinite(view.history.values)].tolist(), [-90])
        self.assertNotIn(-20, view.history.values)

    def test_exact_bin_states_history_is_masked_at_new_measurements_and_holes(self):
        state = SweepCoverageState()
        old = line(1, [-80, -80, -20, np.nan, -80, -80])
        new = line(3, [np.nan, -70, np.nan, np.nan, -70, np.nan])
        state.accept(snapshot(old))
        state.accept(snapshot(new))
        view = state.project(100e6, 101e6, 100)
        np.testing.assert_array_equal(view.states, [PREVIOUS, CURRENT, PREVIOUS, MISSING, CURRENT, PREVIOUS])
        np.testing.assert_equal(view.history.values, [-80, np.nan, -20, np.nan, np.nan, -80])
        self.assertEqual(state.previous.sequence, 1)  # Never fabricate missing pass 2.
        self.assertIs(state.current, new)
        self.assertIs(state.previous, old)
        np.testing.assert_equal(new.values_db, [np.nan, -70, np.nan, np.nan, -70, np.nan])
        self.assertEqual(view.edges_hz[0], 100e6 - 500)
        self.assertEqual(view.edges_hz[-1], 100e6 + 5500)
        self.assertFalse(view.states.flags.writeable)

    def test_mixed_subpixel_coverage_and_history_peak_never_bridge_current(self):
        state = SweepCoverageState()
        state.accept(snapshot(line(1, [-90, -10, -90, -50, -80, np.nan, -90, -90])))
        state.accept(snapshot(line(2, [np.nan, np.nan, np.nan, -70, np.nan, np.nan, np.nan, -70])))
        view = state.project(100e6, 101e6, 1)
        self.assertEqual(view.states.tolist(), [CURRENT | PREVIOUS | MISSING])
        self.assertIn(-10, view.history.values)
        self.assertLessEqual(view.history.display_point_count, 9)
        self.assertTrue(np.isnan(view.history.values).any())
        self.assertNotIn(100e6 + 3000, view.history.frequencies_hz)

    def test_bounds_large_grid_reuses_sources_and_zoom_preserves_irregular_edges(self):
        state = SweepCoverageState()
        count = 2_000_000
        old = line(1, np.full(count, -90.0))
        values = np.full(count, np.nan)
        values[::2] = -60
        new = line(2, values)
        state.accept(snapshot(old))
        state.accept(snapshot(new))
        view = state.project(0, 3e9, 99999)
        self.assertLessEqual(view.states.size, MAX_COLUMNS)
        self.assertLessEqual(view.history.display_point_count, 9 * MAX_COLUMNS)
        self.assertIs(state.previous.values_db, old.values_db)
        self.assertIs(state.current.values_db, new.values_db)
        self.assertLess(sum(a.nbytes for a in (view.edges_hz, view.states,
                                             view.history.values, view.history.frequencies_hz)), 400_000)
        state.clear()
        irregular = replace(line(3, [-90, np.nan, -70]), frequencies_hz=np.array([1., 2., 20.]))
        state.accept(snapshot(irregular))
        np.testing.assert_equal(state.project(0, 30, 100).edges_hz, [.5, 1.5, 11., 29.])

    def test_incompatible_history_empty_and_identical_snapshot(self):
        old = line(1, [-90, -90, -90])
        for change in (dict(epoch=2), dict(source_id="other"), dict(unit="dBm"),
                       dict(frequencies_hz=old.frequencies_hz + 1)):
            with self.subTest(change=change):
                state = SweepCoverageState()
                state.accept(snapshot(old))
                newer = replace(line(2, [np.nan, -70, np.nan]), **change)
                state.accept(snapshot(newer))
                self.assertIsNone(state.previous)
                self.assertFalse(state.accept(snapshot(newer)))
                self.assertTrue(state.accept(snapshot(None)))
                self.assertIsNone(state.current)
        with self.assertRaises(TypeError):
            state.accept(object())

    def test_first_pass_and_offscreen_do_not_fabricate_history(self):
        state = SweepCoverageState()
        state.accept(snapshot(line(7, [np.nan, -70, np.nan])))
        view = state.project(0, 101e6, 100)
        self.assertEqual(view.states.tolist(), [MISSING, CURRENT, MISSING])
        self.assertEqual(view.history.display_point_count, 0)
        self.assertIsNone(state.previous)
        self.assertEqual(state.project(0, 1, 100).states.tolist(), [MISSING])
        state.clear()
        with self.assertRaises(ValueError):
            state.project(0, 1, 100)

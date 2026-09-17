"""Coverage is physical, bounded display data; history never becomes current."""
from dataclasses import replace
import unittest

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

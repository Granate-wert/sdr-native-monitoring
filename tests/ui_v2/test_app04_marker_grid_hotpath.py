"""Exact marker/grid semantics without full-grid allocations per interaction."""
from dataclasses import replace
import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum import SpectrumFrameView
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene, _nearest_finite_index, _same_grid
import tests.ui_v2.test_spectrum_scene as fixture


class MarkerGridHotpathTests(unittest.TestCase):
    def test_nearest_matches_full_finite_reference_on_irregular_grids_and_gaps(self):
        rng = np.random.default_rng(741)
        for count in (2, 257, 4097, 131075):
            frequencies = np.cumsum(rng.uniform(.01, 20, count))
            for fraction in (0., .1, .5, 1.):
                values = rng.normal(size=count)
                values[rng.random(count) < fraction] = np.nan
                values[::29] = np.inf
                values[::31] = -np.inf
                view = SpectrumFrameView(None, frequencies, values, "dBFS/bin")
                finite = np.flatnonzero(np.isfinite(values))
                queries = np.concatenate(([-1, frequencies[-1]+1],
                                          rng.uniform(0, frequencies[-1], 30), frequencies[:5]))
                for query in queries:
                    expected = (None if not finite.size else
                                int(finite[np.argmin(np.abs(frequencies[finite] - query))]))
                    self.assertEqual(_nearest_finite_index(view, float(query)), expected)

    def test_long_gaps_bound_scratch_and_preserve_lower_frequency_tie(self):
        count = 2000000
        values = np.full(count, np.nan)
        values[[0, count-1]] = -80
        frequencies = np.arange(count, dtype=np.float64)
        view = SpectrumFrameView(None, frequencies, values, "dBFS/bin")
        with patch("sdr_monitor.ui.v2.spectrum.scene.np.isfinite", wraps=np.isfinite) as finite:
            self.assertEqual(_nearest_finite_index(view, (count-1)/2), 0)
            self.assertLessEqual(max(np.size(call.args[0]) for call in finite.call_args_list), 65536)
        for query in (np.nan, np.inf, -np.inf):
            self.assertIsNone(_nearest_finite_index(view, query))

    def test_dense_marker_does_not_scan_or_index_full_source(self):
        count = 2000000
        values = np.full(count, -80.)
        view = SpectrumFrameView(None, np.arange(count, dtype=np.float64), values, "dBFS/bin")
        with patch("sdr_monitor.ui.v2.spectrum.scene.np.isfinite", wraps=np.isfinite) as finite:
            self.assertEqual(_nearest_finite_index(view, 999999.5), 999999)
            self.assertEqual(finite.call_count, 3)  # query + two adjacent values
            self.assertTrue(all(np.size(call.args[0]) == 1 for call in finite.call_args_list))

    def test_grid_comparison_checks_interior_in_bounded_batches(self):
        source = np.arange(2000000, dtype=np.float64)
        other = source.copy()
        with patch("sdr_monitor.ui.v2.spectrum.scene.np.array_equal", wraps=np.array_equal) as equal:
            self.assertTrue(_same_grid(source, other))
            self.assertLessEqual(max(call.args[0].size for call in equal.call_args_list), 65536)
            self.assertEqual(sum(call.args[0].size for call in equal.call_args_list), source.size)
        other[65536] += .25
        self.assertFalse(_same_grid(source, other))
        self.assertFalse(_same_grid(source, source.astype(np.float32)))
        self.assertFalse(_same_grid(None, source))


class SceneGridRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_one_owned_grid_survives_equal_publications_and_detects_mutable_alias(self):
        scene = SpectrumScene()
        try:
            frame = fixture._frame()
            scene.set_frame(frame)
            baseline = scene._measurement_grid
            self.assertFalse(baseline.flags.writeable)
            self.assertFalse(np.shares_memory(baseline, frame.frequencies_hz))
            scene.place_marker("M1", frame.frequencies_hz[100])
            scene.view_box.setXRange(frame.frequencies_hz[50], frame.frequencies_hz[150], padding=0)
            viewport = scene.view_box.viewRange()[0]
            for _ in range(5):
                scene.set_frame(replace(frame, frequencies_hz=frame.frequencies_hz.copy()))
                self.assertIs(scene._measurement_grid, baseline)
                self.assertEqual(scene.view_box.viewRange()[0], viewport)
                self.assertEqual(len(scene.markers), 1)
            # Unchanged endpoints/count must not hide a changed interior bin.
            frame.frequencies_hz[100] += .25
            scene.set_frame(frame)
            self.assertIsNot(scene._measurement_grid, baseline)
            self.assertEqual(scene.markers, ())
            self.assertNotEqual(scene.view_box.viewRange()[0], viewport)
            scene.clear_measurement()
            self.assertIsNone(scene._measurement_grid)
        finally:
            scene.close()
            scene.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

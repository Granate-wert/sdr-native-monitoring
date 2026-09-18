"""Exact worker-prepared Auto Y; no acquisition, receiver or performance claim."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame, finite_value_extent
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_spectrum_scene import SyntheticSpectrumFrame


def frame(values):
    values = np.asarray(values, dtype=np.float64)
    frequencies = np.arange(values.size, dtype=np.float64)
    values.setflags(write=False)
    frequencies.setflags(write=False)
    return SyntheticSpectrumFrame(frequencies, values)


class PreparedAutoYTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scene = SpectrumScene()
        self.reference = SpectrumScene()
        self.addCleanup(self.dispose)

    def dispose(self):
        for scene in (self.scene, self.reference):
            scene.close()
            scene.deleteLater()
        self.app.processEvents()

    def test_exact_finite_extrema_with_bounded_scratch(self):
        rng = np.random.default_rng(52)
        for size in (0, 1, 65535, 65536, 65537, 2000003):
            values = rng.normal(-70, 10, size)
            values[::7] = np.nan
            values[::11] = -np.inf
            values[::19] = np.inf
            finite = values[np.isfinite(values)]
            expected = None if not finite.size else (float(np.min(finite)), float(np.max(finite)))
            original = np.isfinite
            sizes = []

            def record(chunk):
                sizes.append(chunk.size)
                return original(chunk)

            with self.subTest(size=size), patch(
                    "sdr_monitor.ui.v2.spectrum.contracts.np.isfinite", side_effect=record):
                self.assertEqual(finite_value_extent(values), expected)
                self.assertTrue(all(n <= 65536 for n in sizes))

    def test_prepared_matches_original_auto_y_policy_including_empty_finite_set(self):
        for values in ([-140, -90, np.nan, -20], [7, 7, 7, 7],
                       [np.nan, np.inf, -np.inf, np.nan], [-90, -80, -70, -60]):
            source = frame(values)
            prepared = PreparedSpectrumFrame(source)
            self.reference.set_frame(source)
            with patch("sdr_monitor.ui.v2.spectrum.scene.finite_value_extent",
                       side_effect=AssertionError("GUI reduction forbidden")):
                self.scene.set_frame(source, prepared=prepared)
            self.assertEqual(self.scene.view_box.viewRange()[1], self.reference.view_box.viewRange()[1])
            self.assertIs(self.scene.latest_frame, source)

    def test_hidden_latest_resume_and_rejected_packet_do_not_reuse_old_extrema(self):
        first, latest = frame([-150, -140, -130, -120]), frame([-40, -30, -20, -10])
        self.scene.set_frame(first, prepared=PreparedSpectrumFrame(first))
        self.scene.set_presentation_active(False)
        self.scene.set_frame(latest, prepared=PreparedSpectrumFrame(latest))
        with self.assertRaisesRegex(ValueError, "exact publication"):
            self.scene.set_frame(first, prepared=PreparedSpectrumFrame(latest))
        self.reference.set_frame(latest)
        with patch("sdr_monitor.ui.v2.spectrum.scene.finite_value_extent",
                   side_effect=AssertionError("GUI reduction forbidden")):
            self.scene.set_presentation_active(True)
        self.assertIs(self.scene.latest_frame, latest)
        self.assertEqual(self.scene.view_box.viewRange()[1], self.reference.view_box.viewRange()[1])
        self.scene.clear_measurement()
        unprepared = replace(first, unit="dBFS/bin")
        with patch("sdr_monitor.ui.v2.spectrum.scene.finite_value_extent",
                   wraps=finite_value_extent) as reduce:
            self.scene.set_frame(unprepared)
        self.assertGreater(reduce.call_count, 0)
        self.reference.set_frame(unprepared)
        self.assertEqual(self.scene.view_box.viewRange()[1], self.reference.view_box.viewRange()[1])

"""APP-03 T12 linked Waterfall controls remain presentation-only."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.waterfall import SpectrumWaterfallView, WaterfallLineFrame


def _line(value: float, timestamp_ns: int, *, unit: str = "dBm") -> WaterfallLineFrame:
    return WaterfallLineFrame(
        values=np.full(4, value, dtype=np.float32),
        frequency_edges_hz=np.linspace(99.5, 103.5, 5, dtype=np.float64),
        timestamp_ns=timestamp_ns,
        configuration_generation=1,
        unit_label=unit,
    )


def _spectrum(unit: str, lower: float, upper: float) -> SimpleNamespace:
    return SimpleNamespace(
        frequencies_hz=np.linspace(100.0, 103.0, 4, dtype=np.float64),
        values=np.linspace(lower, upper, 4, dtype=np.float32),
        unit=unit,
    )


class App03WaterfallLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.view = SpectrumWaterfallView(settings=QSettings(
            str(Path(self._temporary.name) / "waterfall.ini"), QSettings.Format.IniFormat,
        ))
        self.view.resize(1000, 700)
        self.view.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()
        self._temporary.cleanup()

    def test_public_spectrum_signal_follows_only_matching_waterfall_unit(self) -> None:
        pane = self.view.waterfall_pane
        pane.set_line(_line(-90.0, 1_000_000_000))

        # Exercise SpectrumScene's real signal connection, not the pane method.
        self.view.spectrum_scene.set_frame(_spectrum("dBm", -101.0, -41.0))
        self.assertLessEqual(pane.config.level_min, -101.0)
        self.assertGreaterEqual(pane.config.level_max, -41.0)

        before = (pane.config.level_min, pane.config.level_max)
        pane.set_line(_line(-80.0, 2_000_000_000, unit="dBFS/bin"))
        self.view.spectrum_scene.set_frame(_spectrum("dBm", -70.0, -20.0))
        self.assertEqual((pane.config.level_min, pane.config.level_max), before)

        self.view.spectrum_scene.set_frame(_spectrum("dBFS/bin", -75.0, -25.0))
        self.assertLessEqual(pane.config.level_min, -75.0)
        self.assertGreaterEqual(pane.config.level_max, -25.0)

    def test_accepted_and_rejected_history_rate_changes_keep_admitted_rows_local(self) -> None:
        pane = self.view.waterfall_pane
        for index, value in enumerate((-90.0, -80.0, -70.0), start=1):
            pane.set_line(_line(value, index * 1_000_000_000))
        retained_before = np.concatenate(pane._renderer.tiles(), axis=0).copy()

        # 30 -> 60 Hz expands local capacity: already admitted rows stay intact.
        pane.set_rows_per_second(60)
        self.assertEqual(pane.config.rows_per_second, 60)
        self.assertEqual(pane.history_rows, 3)
        np.testing.assert_array_equal(np.concatenate(pane._renderer.tiles(), axis=0), retained_before)

        # The over-budget transition is rejected, with the same local rows and
        # no receiver/service object anywhere in this composed presentation view.
        pane.set_history_seconds(60)
        rows_before_rejection = np.concatenate(pane._renderer.tiles(), axis=0).copy()
        pane.set_rows_per_second(120)
        self.assertEqual(pane.config.rows_per_second, 60)
        self.assertEqual(pane.history_rows, 3)
        self.assertEqual(pane.metrics.configuration_rejections, 1)
        np.testing.assert_array_equal(np.concatenate(pane._renderer.tiles(), axis=0), rows_before_rejection)


if __name__ == "__main__":
    unittest.main()

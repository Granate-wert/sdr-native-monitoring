"""Waterfall time labels require published compatible producer provenance."""

from __future__ import annotations

import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.identity import TimestampQuality
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_spectrum
from sdr_monitor.ui.v2.waterfall import WaterfallPane


def _frame(sequence: int, timestamp_ns: int, *, known: bool) -> LiveSpectrumFrame:
    return LiveSpectrumFrame(
        sequence=sequence, timestamp_ns=timestamp_ns, center_frequency_hz=100.0,
        sample_rate_hz=2.0, fft_size=2, hop_size=1,
        frequencies_hz=np.array((99.0, 100.0)), values=np.array((-80.0, -70.0), dtype=np.float32),
        unit="dBm", source_id="source", config_generation=1,
        clock_domain="unix_ns" if known else None,
        timestamp_quality=TimestampQuality.HARDWARE if known else TimestampQuality.UNKNOWN,
    )


class WaterfallTimeProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.pane = WaterfallPane()

    def tearDown(self) -> None:
        self.pane.close()
        self.pane.deleteLater()

    def test_known_producer_time_is_relative_but_unknown_time_is_sequence_only(self) -> None:
        first = waterfall_line_from_spectrum(_frame(1, 1_000_000_000, known=True))
        second = waterfall_line_from_spectrum(_frame(2, 2_000_000_000, known=True))
        self.assertTrue(first.timestamp_known)
        self.pane.set_line(first)
        self.pane.set_line(second)
        self.assertEqual(self.pane._time_axis.tickStrings([0.0, 1.0], 1.0, 1.0)[1], "−1.0 с")

        unknown = waterfall_line_from_spectrum(_frame(3, 1, known=False))
        self.assertFalse(unknown.timestamp_known)
        self.pane.set_line(unknown)
        self.assertEqual(self.pane.history_rows, 1)  # Provenance switch starts a display epoch.
        self.assertEqual(self.pane._time_axis.tickStrings([0.0], 1.0, 1.0), [""])

    def test_unknown_sequence_orders_rows_without_timestamp_cadence_or_fake_out_of_order(self) -> None:
        self.pane.set_line(waterfall_line_from_spectrum(_frame(2, 9_000_000_000, known=False)))
        self.pane.set_line(waterfall_line_from_spectrum(_frame(3, 1, known=False)))
        self.assertEqual(self.pane.history_rows, 2)
        self.pane.set_line(waterfall_line_from_spectrum(_frame(1, 99_000_000_000, known=False)))
        self.assertEqual(self.pane.history_rows, 2)
        self.assertEqual(self.pane.metrics.rows_out_of_order_rejected, 1)


if __name__ == "__main__":
    unittest.main()

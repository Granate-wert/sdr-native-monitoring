"""APP-02 F11-F14 viewport, range, marker and LOD regressions."""

from __future__ import annotations

from dataclasses import dataclass
import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum import TraceKind
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene


@dataclass(frozen=True, slots=True)
class Frame:
    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str = "dBFS/Hz"


def _frame(points: int = 10_000, offset: float = 0.0) -> Frame:
    frequencies = np.linspace(100.0, 200.0, points)
    values = (-80.0 + 10.0 * np.sin(np.linspace(0.0, 40.0, points)) + offset).astype(np.float32)
    frequencies.setflags(write=False)
    values.setflags(write=False)
    return Frame(frequencies, values)


class SpectrumInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_same_grid_frames_preserve_user_viewport(self) -> None:
        scene = SpectrumScene()
        scene.resize(700, 500)
        scene.set_frame(_frame())
        scene.plot_item.setXRange(125.0, 145.0, padding=0.0)
        expected = tuple(scene.plot_item.getViewBox().viewRange()[0])
        for index in range(100):
            scene.set_frame(_frame(offset=float(index) / 100.0))
        actual = tuple(scene.plot_item.getViewBox().viewRange()[0])
        self.assertTrue(np.allclose(actual, expected))

    def test_auto_y_contains_both_finite_edges(self) -> None:
        scene = SpectrumScene()
        frequencies = np.array((100.0, 101.0, 102.0))
        values = np.array((-120.0, -70.0, -20.0), dtype=np.float32)
        frequencies.setflags(write=False)
        values.setflags(write=False)
        scene.set_frame(Frame(frequencies, values))
        lower, upper = scene.plot_item.getViewBox().viewRange()[1]
        self.assertLess(lower, -120.0)
        self.assertGreater(upper, -20.0)

    def test_refresh_preserves_selected_marker_and_uses_full_source(self) -> None:
        scene = SpectrumScene()
        scene.set_frame(_frame(101))
        scene.place_marker("M1", 120.0)
        scene.place_marker("M2", 180.0)
        self.assertEqual(scene._selected_marker_id, "M2")
        updated = _frame(101, offset=3.0)
        scene.set_frame(updated)
        self.assertEqual(scene._selected_marker_id, "M2")
        self.assertEqual(len(scene.markers), 2)
        marker = next(item for item in scene.markers if item.marker_id == "M1")
        index = int(np.argmin(np.abs(updated.frequencies_hz - marker.frequency_hz)))
        self.assertEqual(marker.value, float(updated.values[index]))

    def test_zoom_rebuilds_lod_for_every_trace_from_bounded_views(self) -> None:
        scene = SpectrumScene()
        current = _frame()
        average = _frame(offset=-3.0)
        scene.set_frame(current)
        scene.set_trace(TraceKind.AVERAGE, average)
        scene.plot_item.setXRange(140.0, 141.0, padding=0.0)
        for kind in (TraceKind.CURRENT, TraceKind.AVERAGE):
            envelope = scene.trace_envelope(kind)
            self.assertIsNotNone(envelope)
            finite = envelope.frequencies_hz[np.isfinite(envelope.frequencies_hz)]
            self.assertGreaterEqual(float(finite[0]), 139.99)
            self.assertLessEqual(float(finite[-1]), 141.01)
            self.assertLessEqual(envelope.display_point_count, max(1, int(scene.plot_item.getViewBox().width())) * 4)
        self.assertTrue(np.shares_memory(scene._trace_views[TraceKind.CURRENT].values, current.values))
        self.assertTrue(np.shares_memory(scene._trace_views[TraceKind.AVERAGE].values, average.values))


if __name__ == "__main__":
    unittest.main()

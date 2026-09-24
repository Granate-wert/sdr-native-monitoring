"""Clipping-safe Auto Y stability and UI V2 presentation-only regressions."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.auto_range import AutoVerticalRange
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame, VerticalRangeMode
from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_app05_viewport_projection import ManualWorker
from tests.ui_v2.test_spectrum_scene import SyntheticSpectrumFrame


def _frame(values: list[float]) -> SyntheticSpectrumFrame:
    trace = np.asarray(values, dtype=np.float64)
    frequencies = np.linspace(100_000_000, 101_000_000, len(trace))
    return SyntheticSpectrumFrame(frequencies, trace)


def _prepared(values: list[float]) -> tuple[SyntheticSpectrumFrame, PreparedSpectrumFrame]:
    source = _frame(values)
    source.frequencies_hz.setflags(write=False)
    source.values.setflags(write=False)
    return source, PreparedSpectrumFrame(source)


class AutoVerticalRangeTests(unittest.TestCase):
    def test_small_extrema_changes_hold_and_crossing_expands_without_clipping(self) -> None:
        policy = AutoVerticalRange()
        original = policy.update((-100.0, -80.0), now_ns=0)
        self.assertEqual(original, (-103.0, -77.0))
        self.assertEqual(policy.update((-99.0, -81.0), now_ns=100), original)
        expanded = policy.update((-100.0, -70.0), now_ns=200)
        assert expanded is not None
        self.assertLessEqual(expanded[0], -100.0)
        self.assertGreaterEqual(expanded[1], -70.0)
        self.assertGreater(expanded[1], original[1])

    def test_spike_expands_then_shrinks_only_after_dwell(self) -> None:
        policy = AutoVerticalRange()
        original = policy.update((-100.0, -80.0), now_ns=0)
        expanded = policy.update((-100.0, -10.0), now_ns=10)
        self.assertNotEqual(expanded, original)
        self.assertEqual(policy.update((-100.0, -80.0), now_ns=20), expanded)
        self.assertEqual(policy.update((-100.0, -80.0), now_ns=1_500_000_019), expanded)
        self.assertEqual(policy.update((-100.0, -80.0), now_ns=1_500_000_020), original)

    def test_alternating_extrema_cancel_shrink_and_disjoint_retune_is_immediate(self) -> None:
        policy = AutoVerticalRange()
        expanded = policy.update((-100.0, -10.0), now_ns=0)
        self.assertEqual(policy.update((-100.0, -80.0), now_ns=10), expanded)
        self.assertEqual(policy.update((-100.0, -10.0), now_ns=1_000_000_000), expanded)
        self.assertEqual(policy.update((-100.0, -80.0), now_ns=1_100_000_000), expanded)
        self.assertEqual(policy.update((-100.0, -80.0), now_ns=2_000_000_000), expanded)
        shifted = policy.update((10.0, 20.0), now_ns=2_000_000_001)
        self.assertEqual(shifted, (5.0, 25.0))

    def test_nonfinite_only_retains_bounds_and_reset_rebases(self) -> None:
        policy = AutoVerticalRange()
        self.assertIsNone(policy.update(None, now_ns=0))
        old = policy.update((-100.0, -80.0), now_ns=1)
        self.assertEqual(policy.update(None, now_ns=2), old)
        policy.reset()
        self.assertEqual(policy.update((-40.0, -20.0), now_ns=3), (-43.0, -17.0))


class SpectrumSceneAutoVerticalRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.scene = SpectrumScene()
        self.addCleanup(self._dispose)

    def _dispose(self) -> None:
        self.scene.close()
        self.scene.deleteLater()
        self.app.processEvents()

    def test_stable_frame_does_not_rewrite_range_or_readout(self) -> None:
        events: list[tuple[float, float, str]] = []
        self.scene.vertical_range_changed.connect(lambda low, high, unit: events.append((low, high, unit)))
        self.scene.set_frame(_frame([-100.0, -80.0, -90.0]))
        first_range = self.scene.view_box.viewRange()[1]
        first_summary = self.scene._range_readout.text()
        first_count = len(events)
        with (patch.object(self.scene.plot_item, "setYRange", wraps=self.scene.plot_item.setYRange) as set_y,
              patch.object(self.scene._range_readout, "setText", wraps=self.scene._range_readout.setText) as set_text):
            self.scene.set_frame(_frame([-99.0, -81.0, -90.0]))
        set_y.assert_not_called()
        set_text.assert_not_called()
        self.assertEqual(self.scene.view_box.viewRange()[1], first_range)
        self.assertEqual(self.scene._range_readout.text(), first_summary)
        self.assertEqual(len(events), first_count + 1)
        self.assertLessEqual(first_range[0], -99.0)
        self.assertGreaterEqual(first_range[1], -81.0)
        self.scene.set_frame(_frame([-100.0, -10.0, -90.0]))
        expanded = self.scene.view_box.viewRange()[1]
        self.assertLessEqual(expanded[0], -100.0)
        self.assertGreaterEqual(expanded[1], -10.0)
        self.assertGreater(len(events), first_count)

    def test_manual_lock_and_explicit_auto_rebase(self) -> None:
        self.scene.set_frame(_frame([-100.0, -80.0, -90.0]))
        self.scene.set_reference_level(-20.0)
        self.scene.set_db_per_division(5.0)
        manual = self.scene.view_box.viewRange()[1]
        self.scene.set_frame(_frame([-140.0, -10.0, -80.0]))
        self.assertEqual(self.scene.range_mode, VerticalRangeMode.MANUAL)
        self.assertEqual(self.scene.view_box.viewRange()[1], manual)
        self.scene.set_vertical_lock(True)
        self.scene.set_frame(_frame([-150.0, 0.0, -80.0]))
        self.assertEqual(self.scene.view_box.viewRange()[1], manual)
        self.scene.set_auto_range()
        self.assertEqual(self.scene.range_mode, VerticalRangeMode.AUTO)
        auto = self.scene.view_box.viewRange()[1]
        self.assertLessEqual(auto[0], -150.0)
        self.assertGreaterEqual(auto[1], 0.0)

    def test_hidden_latest_rebases_on_show_and_nonfinite_only_does_not_move(self) -> None:
        self.scene.set_frame(_frame([-150.0, -120.0]))
        self.scene.set_presentation_active(False)
        self.scene.set_frame(_frame([-40.0, -10.0]))
        self.scene.set_presentation_active(True)
        latest_range = self.scene.view_box.viewRange()[1]
        self.assertLessEqual(latest_range[0], -40.0)
        self.assertGreaterEqual(latest_range[1], -10.0)
        self.scene.set_frame(_frame([float("nan"), float("inf")]))
        self.assertEqual(self.scene.view_box.viewRange()[1], latest_range)

    def test_projected_stale_result_cannot_rebase_hidden_latest_auto_range(self) -> None:
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit)
        self.addCleanup(projector.dispose)
        self.scene.set_projection_port(projector)
        self.scene.resize(1100, 600)
        self.scene.show()
        for _ in range(8):
            self.app.processEvents()

        old, old_prepared = _prepared([-150.0, -120.0, -130.0])
        self.scene.set_frame(old, prepared=old_prepared)
        self.scene.commit_projection()
        self.assertEqual(len(worker.jobs), 1)
        self.scene.set_presentation_active(False)
        latest, latest_prepared = _prepared([-40.0, -10.0, -30.0])
        self.scene.set_frame(latest, prepared=latest_prepared)
        self.scene.set_presentation_active(True)
        before_old_delivery = self.scene.view_box.viewRange()[1]

        worker.finish()
        for _ in range(8):
            self.app.processEvents()
        self.assertEqual(self.scene.view_box.viewRange()[1], before_old_delivery)
        self.assertIsNot(self.scene.displayed_frame, old)

        for _ in range(8):
            if self.scene.displayed_frame is latest:
                break
            for _ in range(8):
                self.app.processEvents()
            if worker.jobs:
                worker.finish()
        for _ in range(8):
            self.app.processEvents()
        self.assertIs(self.scene.displayed_frame, latest)
        lower, upper = self.scene.view_box.viewRange()[1]
        self.assertLessEqual(lower, -40.0)
        self.assertGreaterEqual(upper, -10.0)

    def test_fhd_qhd_qt_surface_maps_new_extrema_inside_plot(self) -> None:
        """Qt-surface smoke, not desktop/DWM scanout or pixel-exact acceptance."""

        self.scene.set_frame(_frame([-100.0, -90.0, -10.0]))
        for width, height in ((1920, 1080), (2560, 1440)):
            with self.subTest(geometry=(width, height)):
                self.scene.resize(width, height)
                self.scene.show()
                for _ in range(8):
                    self.app.processEvents()
                surface = self.scene.grab()
                self.assertFalse(surface.isNull())
                logical_size = surface.deviceIndependentSize()
                self.assertAlmostEqual(logical_size.width(), width, delta=1)
                self.assertAlmostEqual(logical_size.height(), height, delta=1)
                lower, upper = self.scene.view_box.viewRange()[1]
                self.assertLessEqual(lower, -100.0)
                self.assertGreaterEqual(upper, -10.0)
                for frequency, level in ((100_000_000.0, -100.0), (101_000_000.0, -10.0)):
                    point = self.scene.view_box.mapViewToDevice(QPointF(frequency, level))
                    self.assertIsNotNone(point)
                    assert point is not None
                    self.assertGreaterEqual(point.y(), 0)
                    self.assertLessEqual(point.y(), self.scene.view_box.height())


if __name__ == "__main__":
    unittest.main()

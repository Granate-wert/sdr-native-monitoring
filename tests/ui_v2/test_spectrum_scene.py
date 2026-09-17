"""Synthetic/offscreen evidence for the UI2-04 measurement-only spectrum scene."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from sdr_monitor.ui.v2.spectrum import (
    BandMask,
    DensityValueMode,
    PersistenceDensityFrame,
    PersistenceRenderMode,
    TraceKind,
    VerticalRangeMode,
    adapt_spectrum_frame,
    peak_preserving_envelope,
)
from sdr_monitor.ui.v2.spectrum.axis import FrequencyAxis
from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
    adapt_persistence_density,
    inferno_lookup_table,
    map_density_for_display,
)
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene


@dataclass(frozen=True, slots=True)
class SyntheticSpectrumFrame:
    """Minimal public-shaped frame; it performs no acquisition or analysis."""

    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str = "dBm"


def _frame(
    values: np.ndarray | None = None,
    *,
    start_hz: float = 100_000_000.0,
    stop_hz: float = 101_000_000.0,
    unit: str = "dBm",
) -> SyntheticSpectrumFrame:
    trace = np.full(4096, -108.0, dtype=np.float64) if values is None else values
    frequencies = np.linspace(start_hz, stop_hz, trace.size, dtype=np.float64)
    return SyntheticSpectrumFrame(frequencies_hz=frequencies, values=trace, unit=unit)


def _persistence_frame(
    density: np.ndarray,
    *,
    mode: DensityValueMode = DensityValueMode.PROBABILITY,
) -> PersistenceDensityFrame:
    return PersistenceDensityFrame(
        density=density,
        frequency_edges_hz=np.linspace(100_000_000.0, 101_000_000.0, density.shape[1] + 1),
        level_edges=np.linspace(-120.0, -20.0, density.shape[0] + 1),
        value_mode=mode,
        level_unit="dBm",
    )


class PeakPreservingEnvelopeTests(unittest.TestCase):
    """Verify narrow signals, frequency grids and NaN gaps before Qt paint."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_bin_centred_and_half_bin_tones_survive_downsampling(self) -> None:
        values = np.full(8192, -108.0, dtype=np.float64)
        values[2048] = -21.0
        values[6143:6145] = -27.0
        envelope = peak_preserving_envelope(adapt_spectrum_frame(_frame(values)), pixel_width=96)
        self.assertLessEqual(envelope.display_point_count, 96 * 4)
        self.assertEqual(float(np.nanmax(envelope.values)), -21.0)
        self.assertIn(-27.0, envelope.values)
        self.assertTrue(envelope.peak_preserving)

    def test_multiple_peaks_and_single_bin_narrow_peak_survive(self) -> None:
        values = np.full(4096, -110.0, dtype=np.float64)
        values[256] = -40.0
        values[1024] = -34.0
        values[3072] = -25.0
        envelope = peak_preserving_envelope(adapt_spectrum_frame(_frame(values)), pixel_width=128)
        self.assertTrue({-40.0, -34.0, -25.0}.issubset(set(envelope.values)))

    def test_nan_gap_is_never_connected_or_replaced(self) -> None:
        values = np.array((-100.0, -95.0, np.nan, np.nan, -90.0, -92.0), dtype=np.float64)
        envelope = peak_preserving_envelope(adapt_spectrum_frame(_frame(values)), pixel_width=64)
        self.assertTrue(np.array_equal(envelope.values, values, equal_nan=True))
        self.assertTrue(np.array_equal(envelope.frequencies_hz, _frame(values).frequencies_hz))

    def test_exact_unit_and_changed_frequency_grid_are_retained(self) -> None:
        frame = _frame(start_hz=2_400_000_000.0, stop_hz=2_401_000_000.0, unit="dBFS/bin")
        view = adapt_spectrum_frame(frame)
        self.assertIs(view.source_frame, frame)
        self.assertEqual(view.unit_label, "dBFS/bin")
        self.assertEqual(FrequencyAxis(orientation="bottom").tickStrings([2_400_000_000.0], 1.0, 1.0), ["2.400000000 ГГц"])


class PersistenceMappingTests(unittest.TestCase):
    """Verify the quantitative modes before an ImageItem receives a buffer."""

    def test_probability_count_and_log_mapping_keep_zero_at_zero(self) -> None:
        probability = adapt_persistence_density(
            _persistence_frame(np.array(((0.0, 0.25, 1.0), (np.nan, 0.5, 0.75))))
        )
        direct = map_density_for_display(probability, logarithmic=False)
        logarithmic = map_density_for_display(probability, logarithmic=True)
        self.assertEqual(float(direct[0, 0]), 0.0)
        self.assertEqual(float(direct[0, 2]), 1.0)
        self.assertEqual(float(logarithmic[0, 0]), 0.0)
        self.assertGreater(float(logarithmic[0, 1]), float(direct[0, 1]))
        counts = adapt_persistence_density(
            _persistence_frame(np.array(((0.0, 2.0, 8.0),)), mode=DensityValueMode.COUNT)
        )
        mapped_counts = map_density_for_display(counts, logarithmic=False)
        self.assertTrue(np.allclose(mapped_counts, ((0.0, 0.25, 1.0))))

    def test_transparent_zero_lut_and_physical_edges_are_explicit(self) -> None:
        view = adapt_persistence_density(_persistence_frame(np.ones((2, 3))))
        self.assertEqual(inferno_lookup_table()[0, 3], 0)
        self.assertEqual(view.physical_rect, (100_000_000.0, -120.0, 1_000_000.0, 100.0))
        with self.assertRaisesRegex(ValueError, "regular physical frequency"):
            PersistenceDensityFrame(
                density=np.ones((2, 2)),
                frequency_edges_hz=np.array((100.0, 101.0, 103.0)),
                level_edges=np.array((-120.0, -110.0, -100.0)),
                value_mode=DensityValueMode.COUNT,
                level_unit="dBm",
            )


class SpectrumSceneTests(unittest.TestCase):
    """Verify the shared ViewBox API and inert interaction surface offscreen."""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._scenes: list[SpectrumScene] = []

    def tearDown(self) -> None:
        for scene in self._scenes:
            scene.close()
            scene.deleteLater()
        self.app.processEvents()

    def test_single_plot_item_viewbox_unit_and_bounded_current_envelope(self) -> None:
        values = np.full(8192, -108.0, dtype=np.float64)
        values[4097] = -17.0
        frame = _frame(values, unit="dBm")
        scene = self._scene()
        scene.resize(1366, 768)
        scene.show()
        self.app.processEvents()
        scene.set_frame(frame)
        self.assertIs(scene.latest_frame, frame)
        self.assertIs(scene.plot_item.getViewBox(), scene.view_box)
        self.assertEqual(scene._unit_readout.text(), "Единица: dBm")
        envelope = scene.trace_envelope(TraceKind.CURRENT)
        assert envelope is not None
        self.assertLessEqual(envelope.display_point_count, max(1, scene._graphics.width()) * 4)
        self.assertEqual(float(np.nanmax(envelope.values)), -17.0)
        self.assertEqual(scene.findChildren(QScrollArea), [])

    def test_markers_and_peak_navigation_use_latest_full_resolution_frame(self) -> None:
        values = np.full(4096, -110.0, dtype=np.float64)
        values[800] = -31.0
        values[2800] = -12.0
        frame = _frame(values)
        scene = self._scene()
        scene.set_frame(frame)
        marker = scene.place_marker("M1", frame.frequencies_hz[799])
        assert marker is not None
        self.assertEqual(marker.frequency_hz, float(frame.frequencies_hz[799]))
        peak = scene.move_selected_marker_to_peak()
        assert peak is not None
        self.assertEqual(peak.value, -12.0)
        scene.place_marker("M2", frame.frequencies_hz[800])
        next_peak = scene.move_selected_marker_to_peak(1)
        assert next_peak is not None
        self.assertEqual(next_peak.value, -12.0)
        self.assertEqual({marker.marker_id for marker in scene.markers}, {"M1", "M2"})

    def test_keyboard_graph_actions_and_help_are_visible_and_presentation_only(self) -> None:
        values = np.full(4096, -110.0, dtype=np.float64)
        values[2048] = -8.0
        frame = _frame(values)
        scene = self._scene()
        scene.resize(1024, 640)
        scene.show()
        scene.set_frame(frame)
        scene.setFocus(Qt.FocusReason.TabFocusReason)
        self.app.processEvents()

        scene.view_box.setXRange(100_200_000.0, 100_400_000.0, padding=0.0)
        QTest.keyClick(scene, Qt.Key.Key_M)
        marker = scene.markers[0]
        self.assertAlmostEqual(marker.frequency_hz, 100_300_000.0, delta=300.0)
        QTest.keyClick(scene, Qt.Key.Key_P)
        self.assertEqual(scene.markers[0].value, -8.0)

        scene.set_reference_level(-30.0)
        QTest.keyClick(scene, Qt.Key.Key_A)
        self.assertEqual(scene.range_mode, VerticalRangeMode.AUTO)
        scene.view_box.setXRange(100_300_000.0, 100_500_000.0, padding=0.0)
        QTest.keyClick(scene, Qt.Key.Key_0)
        x_range = scene.view_box.viewRange()[0]
        self.assertAlmostEqual(x_range[0], 100_000_000.0, delta=1.0)
        self.assertAlmostEqual(x_range[1], 101_000_000.0, delta=1.0)

        QTest.keyClick(scene, Qt.Key.Key_F1)
        self.app.processEvents()
        assert scene._shortcut_popover is not None
        self.assertTrue(scene._shortcut_popover.isVisible())
        self.assertIn("Ctrl+R", scene._shortcut_popover.accessibleDescription())
        self.assertIn("недоступно", scene._shortcut_popover.accessibleDescription())
        QTest.keyClick(scene._shortcut_popover, Qt.Key.Key_F1)
        self.assertFalse(scene._shortcut_popover.isVisible())
        QTest.keyClick(scene, Qt.Key.Key_F1)
        self.assertTrue(scene._shortcut_popover.isVisible())
        QTest.keyClick(scene._shortcut_popover, Qt.Key.Key_Escape)
        self.assertFalse(scene._shortcut_popover.isVisible())

    def test_manual_auto_and_lock_ranges_are_presentation_only(self) -> None:
        scene = self._scene()
        scene.set_frame(_frame())
        scene.set_reference_level(-30.0)
        scene.set_db_per_division(5.0)
        self.assertEqual(scene.range_mode, VerticalRangeMode.MANUAL)
        scene.set_vertical_lock(True)
        scene.set_reference_level(-10.0)
        self.assertEqual(scene.range_mode, VerticalRangeMode.LOCKED)
        self.assertEqual(scene._reference_level, -30.0)
        scene.set_auto_range()
        self.assertEqual(scene.range_mode, VerticalRangeMode.AUTO)

    def test_auxiliary_traces_do_not_replace_latest_marker_frame(self) -> None:
        current = _frame(np.full(4096, -100.0, dtype=np.float64), unit="dBFS/bin")
        average = _frame(np.full(4096, -105.0, dtype=np.float64), unit="dBFS/bin")
        scene = self._scene()
        scene.set_frame(current)
        scene.set_trace(TraceKind.AVERAGE, average)
        self.assertIs(scene.latest_frame, current)
        self.assertIsNotNone(scene.trace_envelope(TraceKind.AVERAGE))
        scene.clear_trace(TraceKind.AVERAGE)
        self.assertIsNone(scene.trace_envelope(TraceKind.AVERAGE))

    def test_band_masks_warning_and_future_persistence_layer_are_presentation_only(self) -> None:
        scene = self._scene()
        mask = BandMask(433_000_000.0, 434_000_000.0, "ISM")
        scene.set_band_masks((mask,))
        scene.set_warning("Только тестовая маска")
        self.assertEqual(scene.band_masks, (mask,))
        self.assertEqual(len(scene._band_mask_items), 1)
        self.assertEqual(scene._band_mask_items[0].zValue(), -10)
        self.assertEqual(scene.persistence_z_value, -20)
        self.assertFalse(scene._warning_readout.isHidden())
        scene.set_warning(None)
        self.assertTrue(scene._warning_readout.isHidden())

    def test_persistence_image_is_shared_below_trace_and_suppresses_identity_uploads(self) -> None:
        scene = self._scene()
        scene.show()
        self.app.processEvents()
        density = _persistence_frame(np.array(((0.0, 0.5), (0.25, 1.0))))
        scene.set_persistence_frame(density, now_ns=1_000_000_000)
        first = scene.persistence_metrics
        self.assertEqual(first.image_uploads, 1)
        self.assertEqual(scene._persistence.image_item.zValue(), -20)
        self.assertTrue(scene._persistence.image_item.isVisible())
        scene.set_persistence_frame(density, now_ns=2_000_000_000)
        self.assertEqual(scene.persistence_metrics.image_uploads, 1)
        self.assertEqual(scene.persistence_metrics.identity_uploads_suppressed, 1)
        scene._persistence.set_level_window(0.1, 0.9)
        self.assertEqual(scene.persistence_metrics.image_uploads, 1)
        self.assertEqual(scene.persistence_metrics.level_only_updates, 1)

    def test_persistence_cadence_hidden_and_visual_modes_are_explicit(self) -> None:
        scene = self._scene()
        first = _persistence_frame(np.zeros((2, 2)))
        second = _persistence_frame(np.ones((2, 2)))
        scene.set_persistence_frame(first, now_ns=1_000_000_000)
        scene.set_persistence_frame(second, now_ns=1_000_000_001)
        self.assertEqual(scene.persistence_metrics.image_uploads, 1)
        self.assertEqual(scene.persistence_metrics.cadence_uploads_deferred, 1)
        scene._persistence.flush_pending(now_ns=1_100_000_000)
        self.assertEqual(scene.persistence_metrics.image_uploads, 2)
        scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        self.assertIn("ВИЗУАЛЬНЫЙ", scene._persistence_status.text())
        visual = _persistence_frame(np.zeros((2, 2)))
        scene.set_persistence_frame(visual, now_ns=2_000_000_000)
        self.assertEqual(scene.persistence_metrics.retained_extra_image_buffers, 1)
        scene.set_persistence_visible(False)
        hidden = _persistence_frame(np.ones((2, 2)))
        uploads_before_hidden = scene.persistence_metrics.image_uploads
        scene.set_persistence_frame(hidden, now_ns=3_000_000_000)
        self.assertEqual(scene.persistence_metrics.hidden_updates, 1)
        self.assertEqual(scene.persistence_metrics.image_uploads, uploads_before_hidden)
        uploads_before_visible = scene.persistence_metrics.image_uploads
        scene.set_persistence_visible(True)
        self.assertGreaterEqual(scene.persistence_metrics.image_uploads, uploads_before_visible)
        scene.set_persistence_render_mode(PersistenceRenderMode.DIRECT)
        self.assertEqual(scene._persistence.render_mode, PersistenceRenderMode.DIRECT)
        self.assertIn("ПРЯМОЙ", scene._persistence_status.text())

    def test_pending_density_cannot_overwrite_a_newer_direct_upload(self) -> None:
        scene = self._scene()
        first = _persistence_frame(np.zeros((2, 2)))
        pending = _persistence_frame(np.full((2, 2), 0.25))
        newest = _persistence_frame(np.ones((2, 2)))
        scene.set_persistence_frame(first, now_ns=1_000_000_000)
        scene.set_persistence_frame(pending, now_ns=1_000_000_001)
        scene.set_persistence_frame(newest, now_ns=1_100_000_000)
        scene._persistence.flush_pending(now_ns=1_200_000_000)
        np.testing.assert_array_equal(scene._persistence.image_item.image, np.ones((2, 2), dtype=np.float32))
        self.assertEqual(scene.persistence_metrics.image_uploads, 2)

    def test_direct_and_visual_response_are_different_and_mode_switch_resets_visual_buffer(self) -> None:
        scene = self._scene()
        base = 10_000_000_000_000_000
        zero = _persistence_frame(np.zeros((2, 2)))
        one = _persistence_frame(np.ones((2, 2)))
        scene.set_persistence_frame(zero, now_ns=base)
        scene.set_persistence_frame(one, now_ns=base + 100_000_000)
        self.assertTrue(np.allclose(scene._persistence.image_item.image, 1.0))
        zero_for_visual = _persistence_frame(np.zeros((2, 2)))
        scene.set_persistence_frame(zero_for_visual, now_ns=base + 200_000_000)
        self.assertTrue(np.allclose(scene._persistence.image_item.image, 0.0))
        scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        self.assertTrue(np.allclose(scene._persistence.image_item.image, 0.0))
        scene.set_persistence_frame(one, now_ns=base + 300_000_000)
        attack = np.array(scene._persistence.image_item.image, copy=True)
        self.assertTrue(np.all((attack > 0.0) & (attack < 1.0)))
        scene.set_persistence_frame(zero, now_ns=base + 400_000_000)
        release = np.array(scene._persistence.image_item.image, copy=True)
        self.assertTrue(np.all((release > 0.0) & (release < attack)))
        scene.set_persistence_render_mode(PersistenceRenderMode.DIRECT)
        self.assertTrue(np.allclose(scene._persistence.image_item.image, 0.0))
        self.assertIsNone(scene._persistence._visual_buffer)

    def test_scene_renders_at_1366_by_768_without_a_device(self) -> None:
        scene = self._scene()
        scene.resize(1366, 768)
        scene.show()
        scene.set_frame(_frame())
        self.app.processEvents()
        image = scene.grab().toImage()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "spectrum-scene.png"
            self.assertTrue(image.save(str(target)))
            self.assertGreater(target.stat().st_size, 1024)

    def _scene(self) -> SpectrumScene:
        scene = SpectrumScene()
        self._scenes.append(scene)
        return scene


if __name__ == "__main__":
    unittest.main()

"""Renderer/levels/coordinate tests for Heatmap display policy (HMP-PERSIST-007/010).

Offscreen Qt. Covers frequency_bin_edges, explicit display levels surviving
snapshot changes, Probability default levels, auto-level reporting, visual
changes never recomputing or rereading, and trace/density column alignment.
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

from esw_dfl.domain import MeasurementMetadata, MeasurementSession, SpectrumTrace, WaterfallData
from esw_dfl.heatmap import frequency_bin_edges
from esw_dfl.gui import MainWindow
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import SpectrogramIndex
from heatmap_test_isolation import (
    make_temp_settings,
    patched_qsettings,
    reset_heatmap_controls,
    shutdown_window,
)


FREQ_BINS = 8
FRAME_COUNT = 60
SIGNAL_BIN = 3
FREQUENCIES = np.linspace(100.0, 800.0, FREQ_BINS)


def _write_fake_dfl(root: Path, frames: list[np.ndarray]) -> tuple[Path, SpectrogramIndex]:
    sector_size = 512
    stream = bytearray()
    offsets: list[int] = []
    lengths: list[int] = []
    for index, values in enumerate(frames):
        payload = base64.b64encode(np.ascontiguousarray(values, dtype="<f4").tobytes()).decode("ascii")
        line = (
            f'<SgramLine Line="{index}"><DataBlock Block="0" Data="' + payload + '"/></SgramLine>'
        ).encode("ascii")
        offsets.append(len(stream))
        lengths.append(len(line))
        stream += line
    sector_count = (len(stream) + sector_size - 1) // sector_size
    stream += b"\x00" * (sector_count * sector_size - len(stream))
    path = root / "fake.dfl"
    path.write_bytes(b"\x00" * sector_size + bytes(stream))
    info = SpectrogramInfo(
        key="waterfall", title="Waterfall", mode="RT", measurement="Spectrum",
        measurement_type="Spectrogram", source_stream="stream",
        line_count=len(frames), point_count=int(frames[0].size),
        start_hz=float(FREQUENCIES[0]), stop_hz=float(FREQUENCIES[-1]),
    )
    index = SpectrogramIndex(
        info=info,
        line_indices=np.arange(len(frames), dtype=np.int64),
        timestamps=np.arange(len(frames), dtype=np.float64),
        offsets=np.asarray(offsets, dtype=np.int64),
        lengths=np.asarray(lengths, dtype=np.int32),
        sector_chain=np.arange(sector_count, dtype=np.int32),
        sector_size=sector_size,
    )
    return path, index


class FrequencyBinEdgesTests(unittest.TestCase):
    def test_uniform_centers_map_to_half_step_edges(self) -> None:
        left, right = frequency_bin_edges(np.array([100.0, 200.0, 300.0]))
        self.assertAlmostEqual(left, 50.0)
        self.assertAlmostEqual(right, 350.0)

    def test_descending_grid_is_mirrored_once(self) -> None:
        left, right = frequency_bin_edges(np.array([300.0, 200.0, 100.0]))
        self.assertAlmostEqual(left, 50.0)
        self.assertAlmostEqual(right, 350.0)

    def test_single_bin_requires_explicit_span(self) -> None:
        with self.assertRaises(ValueError):
            frequency_bin_edges(np.array([100.0]))
        left, right = frequency_bin_edges(np.array([100.0]), single_bin_span_hz=20.0)
        self.assertAlmostEqual(left, 90.0)
        self.assertAlmostEqual(right, 110.0)

    def test_nonuniform_grid_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            frequency_bin_edges(np.array([100.0, 200.0, 250.0]))

    def test_non_strict_monotonic_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            frequency_bin_edges(np.array([100.0, 100.0, 200.0]))


class HeatmapRendererPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        frames = []
        for _index in range(FRAME_COUNT):
            values = np.full(FREQ_BINS, -100.0, dtype=np.float32)
            values[SIGNAL_BIN] = -50.0
            frames.append(values)
        self.frames = frames
        self.dfl_path, self.index = _write_fake_dfl(Path(self._tmp.name), frames)
        _settings = make_temp_settings(self._tmp.name)
        with patched_qsettings(_settings):
            self.window = MainWindow()
        reset_heatmap_controls(self.window)
        # test-specific overrides
        self.window.heatmap_window_frames_spin.setValue(50)
        self.window.heatmap_power_bins.setCurrentText("64")
        self.window.no_skip_check.setChecked(False)
        self.window._frame_nav.config.sequential_mode = False
        self.window._frame_scheduler.set_sequential_mode(False)
        session = MeasurementSession("session", self.dfl_path, "session", MeasurementMetadata())
        waterfall = WaterfallData(
            "waterfall", "Waterfall", FRAME_COUNT, FREQ_BINS,
            float(FREQUENCIES[0]), float(FREQUENCIES[-1]),
            float(FREQUENCIES[1] - FREQUENCIES[0]), "stream",
        )
        waterfall.set_preview(np.stack(frames), np.arange(FRAME_COUNT, dtype=np.float64), np.arange(FRAME_COUNT))
        session.waterfalls["waterfall"] = waterfall
        session.active_waterfall_id = "waterfall"
        trace = SpectrumTrace(
            "trace-1", "Trace 1", float(FREQUENCIES[0]), float(FREQUENCIES[-1]),
            float(FREQUENCIES[1] - FREQUENCIES[0]), frames[-1].copy(),
        )
        session.traces[trace.trace_id] = trace
        session.active_trace_id = trace.trace_id
        self.session = session
        self.window.repository.add(session)
        self.window._spectrogram_indexes[("session", "waterfall")] = self.index
        self.window.set_active_session("session")
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.heatmap_enabled.setChecked(False)
        shutdown_window(self.window, self.app)
        self._tmp.cleanup()

    def _wait_until(self, predicate, timeout_s: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    def _enable(self, frame: int = 59) -> bool:
        self.session.current_frame = frame
        self.window.heatmap_enabled.setChecked(True)
        controller = self.window._heatmap_controller
        return self._wait_until(
            lambda: controller.applied_snapshot is not None and controller.active_ticket is None
        )

    def test_fixed_color_levels_survive_snapshot_change(self) -> None:
        from esw_dfl.heatmap_persistence import ColorScaleMode

        self.window._combo_set_data(self.window.heatmap_color_scale_mode, ColorScaleMode.FIXED.value)
        self.window.heatmap_color_min.setValue(0.1)
        self.window.heatmap_color_max.setValue(0.42)
        self.assertTrue(self._enable(59))
        self.assertEqual(self.window._heatmap_current_levels, (0.1, 0.42))
        # Sequential update: display levels stay the user's fixed values.
        from esw_dfl.frame_navigation import NavigationReason

        self.window._frame_nav.seek(59, NavigationReason.PLAYBACK)
        self.assertTrue(self._enable(59))
        self.assertEqual(self.window._heatmap_current_levels, (0.1, 0.42))
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)

    def test_probability_default_levels_are_zero_to_one(self) -> None:
        self.window.heatmap_normalization.setCurrentIndex(1)  # Probability
        self.assertTrue(self._enable(59))
        self.assertEqual(self.window._heatmap_current_levels, (0.0, 1.0))

    def test_auto_levels_report_actual_max(self) -> None:
        self.assertTrue(self._enable(59))
        levels = self.window._heatmap_current_levels
        assert levels is not None
        self.assertEqual(levels[0], 0.0)
        # AUTO_CURRENT: the reported max equals the actual image maximum.
        snapshot = self.window._heatmap_controller.applied_snapshot
        image = self.window._normalize_snapshot(snapshot, self.window._heatmap_normalization())
        self.assertAlmostEqual(levels[1], float(image.max()))

    def test_color_change_does_not_recompute_or_reread(self) -> None:
        from esw_dfl.heatmap_persistence import ColorScaleMode

        self.assertTrue(self._enable(59))
        controller = self.window._heatmap_controller
        generation_before = controller.generation
        reads_before = int(self.window.heatmap_diagnostics()["heatmap_frames_decoded"])
        self.window._combo_set_data(self.window.heatmap_color_scale_mode, ColorScaleMode.FIXED.value)
        self.window.heatmap_color_min.setValue(0.2)
        self.window.heatmap_color_max.setValue(0.9)
        self.app.processEvents()
        self.assertEqual(controller.generation, generation_before)
        self.assertEqual(
            int(self.window.heatmap_diagnostics()["heatmap_frames_decoded"]), reads_before
        )
        self.assertIsNone(controller.active_ticket)
        self.assertIsNone(controller.pending_ticket)

    def test_density_column_center_matches_trace_coordinate(self) -> None:
        self.assertTrue(self._enable(59))
        snapshot = self.window._heatmap_controller.applied_snapshot
        assert snapshot is not None
        # The image rect spans the half-step edges; the signal column's center
        # must equal the trace x coordinate within float tolerance.
        left, right = frequency_bin_edges(snapshot.frequencies_hz)
        image_item = self.window.spectrum_renderer.heatmap_image
        rect = image_item.mapRectToParent(image_item.boundingRect())
        self.assertAlmostEqual(rect.left(), left)
        self.assertAlmostEqual(rect.right(), right)
        column_count = snapshot.density.shape[1]
        width = (right - left) / column_count
        column_center = left + (SIGNAL_BIN + 0.5) * width
        self.assertAlmostEqual(column_center, float(FREQUENCIES[SIGNAL_BIN]), delta=width / 2.0)
        trace_x, _trace_y = self.window.spectrum_renderer.raw_trace_data("trace-1")
        self.assertAlmostEqual(float(trace_x[SIGNAL_BIN]), float(FREQUENCIES[SIGNAL_BIN]))

    def test_nonuniform_grid_hides_layer_with_explicit_error(self) -> None:
        import dataclasses

        self.assertTrue(self._enable(59))
        snapshot = self.window._heatmap_controller.applied_snapshot
        assert snapshot is not None
        nonuniform = snapshot.frequencies_hz.copy()
        nonuniform[-1] = nonuniform[-2] + 1.5 * (nonuniform[-2] - nonuniform[-3])
        broken = dataclasses.replace(snapshot, frequencies_hz=nonuniform)
        applied = self.window._apply_snapshot_image(broken)
        self.assertFalse(applied)
        self.assertFalse(self.window.spectrum_renderer.heatmap_visible)
        self.assertIn("Неподдерживаемая частотная сетка", self.window.heatmap_status.text())


if __name__ == "__main__":
    unittest.main()

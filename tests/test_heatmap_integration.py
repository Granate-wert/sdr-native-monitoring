"""GUI integration tests for the Heatmap Spectrum feature (ТЗ §23.2).

The fixture mirrors ``tests/test_ui_requirements.py``: an offscreen Qt
application, a synthetic session with a hand-built ``SpectrogramIndex`` and a
fake DFL file readable by ``SpectrogramFrameReader`` through a sector chain.

Worker synchronization is event-driven: tests pump the Qt event loop until an
observable condition holds (``_wait_until``) instead of sleeping fixed
intervals. Tests that need deterministic control over the worker lifecycle
replace ``esw_dfl.gui.compute_heatmap`` with a blocking wrapper around the
real implementation; the wrapper always releases before returning, so no test
can hang even on failure.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QMessageBox

from esw_dfl.domain import MeasurementMetadata, MeasurementSession, SpectrumTrace, WaterfallData
from esw_dfl.gui import MainWindow
from esw_dfl.heatmap import HeatmapConfig
from esw_dfl.heatmap_export import export_heatmap_json
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import SpectrogramIndex, SpectrogramRow
from heatmap_test_isolation import (
    make_temp_settings,
    patched_qsettings,
    reset_heatmap_controls,
    shutdown_window,
)


FREQ_BINS = 8
FRAME_COUNT = 8
SIGNAL_BIN = 3
SIGNAL_POWER = -50.0
NOISE_POWER = -100.0


def _make_frames() -> list[np.ndarray]:
    # Noise level varies per frame so its density spreads across power rows,
    # while the constant signal at SIGNAL_BIN accumulates a unique maximum.
    frames = []
    for index in range(FRAME_COUNT):
        values = np.full(FREQ_BINS, NOISE_POWER - (index % 4), dtype=np.float32)
        values[SIGNAL_BIN] = SIGNAL_POWER
        frames.append(values)
    return frames


def _write_fake_dfl(
    root: Path,
    name: str,
    frames: list[np.ndarray],
    start_hz: float = 100.0,
    step_hz: float = 100.0,
) -> tuple[Path, SpectrogramInfo, SpectrogramIndex]:
    """Build a fake CFB-less file readable by SpectrogramFrameReader via a sector chain."""
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
    path = root / name
    path.write_bytes(b"\x00" * sector_size + bytes(stream))
    point_count = int(frames[0].size)
    info = SpectrogramInfo(
        key="waterfall",
        title="Waterfall",
        mode="RT",
        measurement="Spectrum",
        measurement_type="Spectrogram",
        source_stream="stream",
        line_count=len(frames),
        point_count=point_count,
        start_hz=start_hz,
        stop_hz=start_hz + step_hz * (point_count - 1),
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
    return path, info, index


class _BlockingReaderFactory:
    """reader_factory whose readers block every read until ``release`` is set.

    Used to hold a controller job in-flight deterministically; the release is
    always set before the test ends, so no test can hang even on failure.
    """

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, path: Path, index: SpectrogramIndex) -> Any:
        factory = self

        class _Reader:
            def __init__(self) -> None:
                from esw_dfl.spectrogram import SpectrogramFrameReader

                self._reader = SpectrogramFrameReader(path, index)

            def read_frame(self, frame_index: int) -> SpectrogramRow:
                factory.started.set()
                factory.release.wait(timeout=30.0)
                return self._reader.read_frame(frame_index)

            def close(self) -> None:
                self._reader.close()

        return _Reader()


class HeatmapIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.frames = _make_frames()
        self.dfl_path, self.info, self.index = _write_fake_dfl(root, "fake_a.dfl", self.frames)
        _settings = make_temp_settings(root)
        with patched_qsettings(_settings):
            self.window = MainWindow()
        reset_heatmap_controls(self.window)
        self.session = self._add_session("session-a", self.dfl_path, self.frames, self.index)
        self.window.set_active_session("session-a")
        self.window._show_frame(0)
        self.app.processEvents()
        # Ensure deterministic navigation settings for all tests (same recipe
        # as tests/test_ui_requirements.py).
        self.window._frame_nav.config.sequential_mode = False
        self.window._frame_scheduler.set_sequential_mode(False)
        self.window.no_skip_check.setChecked(False)
        self.window._frame_nav.config.wheel_step = 1
        self.window._frame_nav.reset(0)
        self.window.time_slider.setValue(0)
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.heatmap_enabled.setChecked(False)
        shutdown_window(self.window, self.app)
        self._tmp.cleanup()

    # --- fixture helpers ---------------------------------------------------
    def _add_session(
        self,
        session_id: str,
        path: Path,
        frames: list[np.ndarray],
        index: SpectrogramIndex,
    ) -> MeasurementSession:
        session = MeasurementSession(session_id, path, session_id, MeasurementMetadata())
        waterfall = WaterfallData(
            "waterfall",
            "Waterfall",
            len(frames),
            int(frames[0].size),
            float(index.info.start_hz),
            float(index.info.stop_hz),
            (float(index.info.stop_hz) - float(index.info.start_hz)) / max(1, int(frames[0].size) - 1),
            "stream",
        )
        values = np.stack(frames)
        waterfall.set_preview(
            values, np.arange(len(frames), dtype=np.float64), np.arange(len(frames))
        )
        session.waterfalls[waterfall.waterfall_id] = waterfall
        session.active_waterfall_id = waterfall.waterfall_id
        trace = SpectrumTrace(
            "trace-1",
            "Trace 1",
            float(index.info.start_hz),
            float(index.info.stop_hz),
            waterfall.frequency_step_hz,
            frames[-1].copy(),
        )
        session.traces[trace.trace_id] = trace
        session.active_trace_id = trace.trace_id
        self.window.repository.add(session)
        self.window._spectrogram_indexes[(session_id, "waterfall")] = index
        for frame, row_values in enumerate(values):
            self.window._frame_loader._cache[(session_id, "waterfall", frame)] = SpectrogramRow(
                frame, float(frame), row_values.copy()
            )
        return session

    def _wait_until(self, predicate: Callable[[], bool], timeout_s: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    def _wait_applied(self, timeout_s: float = 10.0) -> bool:
        """Wait until the latest request is applied and no worker is running."""
        controller = self.window._heatmap_controller
        return self._wait_until(
            lambda: (
                controller.active_ticket is None
                and controller.pending_ticket is None
                and controller.applied_snapshot is not None
                and controller.applied_snapshot.generation == controller.generation
            ),
            timeout_s,
        )

    @property
    def _applied_result(self):
        """The synthesized HeatmapResult compatibility alias of the applied snapshot."""
        return self.window._heatmap_applied

    def _enable_last_n(self, window: int = FRAME_COUNT, current_frame: int = FRAME_COUNT - 1) -> None:
        self.session.current_frame = current_frame
        self.window.heatmap_range_mode.setCurrentIndex(0)  # LAST_N
        self.window.heatmap_window_frames_spin.setValue(window)
        self.window.heatmap_enabled.setChecked(True)

    # --- ТЗ §23.2 tests ----------------------------------------------------
    def test_heatmap_builds_from_multiple_frames(self) -> None:
        self._enable_last_n()
        self.assertTrue(self._wait_applied(), "heatmap result was not applied in time")
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)
        result = self.window._heatmap_applied
        assert result is not None
        self.assertEqual(result.processed_frames, FRAME_COUNT)
        self.assertTrue(result.exact)
        self.assertEqual(int(result.density.sum()), FRAME_COUNT * FREQ_BINS)
        self.assertEqual(
            self.window.heatmap_diagnostics()["heatmap_applied_generation"],
            self.window._heatmap_controller.generation,
        )

    def test_density_maximum_is_at_the_signal_frequency(self) -> None:
        self._enable_last_n()
        self.assertTrue(self._wait_applied())
        result = self.window._heatmap_applied
        assert result is not None
        power_bin, freq_bin = np.unravel_index(int(np.argmax(result.density)), result.density.shape)
        self.assertEqual(int(freq_bin), SIGNAL_BIN)
        expected_power_bin = int(
            np.argmin(np.abs(result.power_axis_dbm - SIGNAL_POWER))
        )
        self.assertEqual(int(power_bin), expected_power_bin)

    def test_trace_stays_above_heatmap_layer(self) -> None:
        self._enable_last_n()
        self.assertTrue(self._wait_applied())
        renderer = self.window.spectrum_renderer
        heatmap_z = renderer.heatmap_image.zValue()
        self.assertLess(heatmap_z, 0.0)
        self.assertIn("trace-1", renderer.items)
        for trace_id, item in renderer.items.items():
            self.assertGreater(item.zValue(), heatmap_z, trace_id)
        for marker_pair in renderer.markers.values():
            self.assertGreater(marker_pair[0].zValue(), heatmap_z)
        for axis_name in ("bottom", "left"):
            self.assertGreater(renderer.plot.getAxis(axis_name).zValue(), heatmap_z)

    def test_opacity_change_does_not_recompute(self) -> None:
        self._enable_last_n()
        self.assertTrue(self._wait_applied())
        controller = self.window._heatmap_controller
        generation_before = controller.generation
        self.window.heatmap_opacity.setValue(0.30)
        self.app.processEvents()
        self.assertEqual(self.window.spectrum_renderer.heatmap_image.opacity(), 0.30)
        self.assertEqual(controller.generation, generation_before)
        self.assertIsNone(controller.active_ticket)
        self.assertIsNone(controller.pending_ticket)
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)

    def test_palette_change_does_not_reread_dfl(self) -> None:
        self._enable_last_n()
        self.assertTrue(self._wait_applied())
        controller = self.window._heatmap_controller
        generation_before = controller.generation
        reads_before = int(self.window.heatmap_diagnostics()["heatmap_frames_decoded"])
        lut_before = self.window.spectrum_renderer.heatmap_lut.copy()
        self.window.heatmap_palette.setCurrentText("Inferno")
        self.app.processEvents()
        self.assertEqual(controller.generation, generation_before)
        self.assertEqual(int(self.window.heatmap_diagnostics()["heatmap_frames_decoded"]), reads_before)
        lut_after = self.window.spectrum_renderer.heatmap_lut
        self.assertFalse(np.array_equal(lut_before[128], lut_after[128]))
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)

    def test_new_request_replaces_pending(self) -> None:
        factory = _BlockingReaderFactory()
        controller = self.window._heatmap_controller
        controller._reader_factory = factory
        self._enable_last_n(window=4)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.assertIsNotNone(controller.active_ticket)
        # Two more structural changes: each cancels the active worker and
        # replaces the single pending slot with the latest parameters.
        self.window.heatmap_window_frames_spin.setValue(5)
        self.window.heatmap_window_frames_spin.setValue(6)
        self.app.processEvents()
        pending = controller.pending_ticket
        self.assertIsNotNone(pending)
        assert pending is not None and pending.request is not None
        self.assertEqual(pending.request.config.window_frames, 6)
        self.assertEqual(pending.generation, controller.generation)
        self.assertEqual(self.window.heatmap_diagnostics()["heatmap_pending_request"], 1)
        factory.release.set()
        self.assertTrue(self._wait_applied())
        result = self.window._heatmap_applied
        assert result is not None
        self.assertEqual(result.config.window_frames, 6)

    def test_stale_result_is_not_applied(self) -> None:
        factory = _BlockingReaderFactory()
        controller = self.window._heatmap_controller
        controller._reader_factory = factory
        self._enable_last_n()
        self.assertTrue(factory.started.wait(timeout=10.0))
        generation_before = controller.generation
        # «Очистить» bumps the generation without cancelling the worker, so
        # the in-flight result becomes stale.
        self.window._heatmap_clear()
        factory.release.set()
        self.assertTrue(
            self._wait_until(
                lambda: self.window.heatmap_diagnostics()["heatmap_stale_results_discarded"] >= 1
            )
        )
        self.assertGreater(controller.generation, generation_before)
        self.assertIsNone(self.window._heatmap_applied)
        self.assertFalse(self.window.spectrum_renderer.heatmap_visible)

    def test_session_switch_does_not_show_foreign_heatmap(self) -> None:
        # Session B with a different frequency grid (6 bins).
        frames_b = [np.full(6, NOISE_POWER, dtype=np.float32) for _ in range(FRAME_COUNT)]
        path_b, _info_b, index_b = _write_fake_dfl(
            Path(self._tmp.name), "fake_b.dfl", frames_b, start_hz=500.0
        )
        self._add_session("session-b", path_b, frames_b, index_b)

        # SELECTED mode: manual recompute only, so the switch never starts a worker.
        self.window.heatmap_range_mode.setCurrentIndex(2)
        self.window.heatmap_enabled.setChecked(True)
        self.assertTrue(self._wait_applied())
        result_a = self.window._heatmap_applied
        assert result_a is not None
        self.assertEqual(result_a.frequencies_hz.size, FREQ_BINS)

        self.window.set_active_session("session-b")
        self.app.processEvents()
        self.assertFalse(self.window.spectrum_renderer.heatmap_visible)
        self.assertIsNone(self.window._heatmap_applied)

        # Switching back restores session A's heatmap from the cache without a worker.
        self.window.set_active_session("session-a")
        self.assertTrue(self._wait_until(lambda: self.window.spectrum_renderer.heatmap_visible))
        restored = self.window._heatmap_applied
        assert restored is not None
        self.assertEqual(restored.frequencies_hz.size, FREQ_BINS)

    def test_session_removal_frees_heatmap_resources(self) -> None:
        # SELECTED (fixed) mode: the computed result lives in the fixed LRU cache.
        self.window.heatmap_range_mode.setCurrentIndex(2)
        self.window.heatmap_start_spin.setValue(1)
        self.window.heatmap_end_spin.setValue(FRAME_COUNT)
        self.window.heatmap_enabled.setChecked(True)
        self.assertTrue(self._wait_applied())
        self.assertGreater(self.window._heatmap_cache.total_size_bytes, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window._remove_session("session-a")
        self.app.processEvents()
        self.assertEqual(self.window._heatmap_cache.total_size_bytes, 0)
        self.assertIsNone(self.window._heatmap_controller.pending_ticket)
        self.assertIsNone(self.window._heatmap_applied)
        self.assertFalse(self.window.spectrum_renderer.heatmap_visible)
        self.assertEqual(self.window.heatmap_diagnostics()["heatmap_memory_bytes"], 0)

    def test_playback_updates_density_and_keeps_latest_target_bounded(self) -> None:
        from esw_dfl.frame_navigation import NavigationReason
        from heatmap_persistence_fixtures import offline_density

        self._enable_last_n(window=4)
        self.assertTrue(self._wait_applied())
        controller = self.window._heatmap_controller
        max_tickets = 0
        # Simulate a 200-frame playback burst delivered as logical span events.
        for frame in range(200):
            self.window._frame_nav.seek(frame % FRAME_COUNT, NavigationReason.PLAYBACK)
            self.app.processEvents()
            max_tickets = max(
                max_tickets,
                int(controller.active_ticket is not None) + int(controller.pending_ticket is not None),
            )
        self.assertLessEqual(max_tickets, 2)
        # A merely idle controller can still expose the previous snapshot while
        # the final queued Qt span event is waiting to run. The acceptance
        # condition must name the requested target, not just "no active job".
        last = 199 % FRAME_COUNT
        self.assertTrue(
            self._wait_until(
                lambda: (
                    controller.active_ticket is None
                    and controller.pending_ticket is None
                    and controller.applied_snapshot is not None
                    and controller.applied_snapshot.generation == controller.generation
                    and controller.applied_snapshot.target_frame == last
                )
            )
        )
        snapshot = controller.applied_snapshot
        assert snapshot is not None
        # The final target's exact window is dense and offline-equal.
        np.testing.assert_array_equal(
            snapshot.density, offline_density(self.frames, last - 4 + 1, last, power_bins=256)
        )
        self.assertEqual(snapshot.target_frame, last)

    def test_exact_mode_processes_every_frame_of_the_range(self) -> None:
        self.window.heatmap_range_mode.setCurrentIndex(2)  # SELECTED
        self.window.heatmap_start_spin.setValue(1)
        self.window.heatmap_end_spin.setValue(FRAME_COUNT)
        self.window.heatmap_compute_mode.setCurrentIndex(0)  # exact FULL_RANGE
        self.window.heatmap_enabled.setChecked(True)
        self.assertTrue(self._wait_applied())
        result = self.window._heatmap_applied
        assert result is not None
        self.assertEqual(result.processed_frames, FRAME_COUNT)
        self.assertEqual(result.total_frames_in_range, FRAME_COUNT)
        self.assertTrue(result.exact)
        self.assertIn("Selected Range · Fixed", self.window.heatmap_status.text())
        self.assertNotIn("Preview", self.window.heatmap_status.text())

    def test_preview_mode_is_explicitly_marked(self) -> None:
        self.window.heatmap_range_mode.setCurrentIndex(2)  # SELECTED
        self.window.heatmap_start_spin.setValue(1)
        self.window.heatmap_end_spin.setValue(FRAME_COUNT)
        self.window.heatmap_compute_mode.setCurrentIndex(1)  # SAMPLED_RANGE preview
        real_build = self.window._heatmap_build_config

        def shrink() -> HeatmapConfig:
            return dataclasses.replace(real_build(), max_preview_frames=3)

        with patch.object(self.window, "_heatmap_build_config", shrink):
            self.window.heatmap_enabled.setChecked(True)
            self.assertTrue(self._wait_applied())
        result = self.window._heatmap_applied
        assert result is not None
        self.assertFalse(result.exact)
        self.assertLess(result.processed_frames, result.total_frames_in_range)
        self.assertIn("Preview", self.window.heatmap_status.text())

    def test_source_dfl_is_not_modified(self) -> None:
        digest_before = hashlib.sha256(self.dfl_path.read_bytes()).hexdigest()
        self._enable_last_n()
        self.assertTrue(self._wait_applied())
        self.window.heatmap_palette.setCurrentText("Inferno")
        self.window.heatmap_opacity.setValue(0.4)
        self.window.heatmap_normalization.setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)
        digest_after = hashlib.sha256(self.dfl_path.read_bytes()).hexdigest()
        self.assertEqual(digest_before, digest_after)

    def test_corrupted_heatmap_settings_fall_back_to_defaults(self) -> None:
        settings = self.window.settings
        settings.setValue("heatmap/window_frames", "junk")
        settings.setValue("heatmap/power_min_dbm", None)
        settings.setValue("heatmap/power_max_dbm", "1e999")
        settings.setValue("heatmap/opacity", "not-a-number")
        settings.setValue("heatmap/half_life_seconds", [])
        settings.setValue("heatmap/half_life_unit", "junk")
        settings.setValue("heatmap/enabled", "junk")
        settings.setValue("heatmap/range_mode", "junk")
        settings.setValue("heatmap/power_bins", "junk")
        settings.setValue("heatmap/palette", "junk")
        settings.sync()
        from esw_dfl import gui as gui_module

        # Construction-time restore must survive the poisoned store (review MAJOR).
        with patch.object(gui_module, "QSettings", lambda *args: settings):
            window2 = MainWindow()
        try:
            self.assertFalse(window2.heatmap_enabled.isChecked())
            self.assertEqual(window2.heatmap_window_frames_spin.value(), 500)
            self.assertEqual(window2.heatmap_power_min.value(), -120.0)
            self.assertEqual(window2.heatmap_power_max.value(), 0.0)
            self.assertEqual(window2.heatmap_opacity.value(), 0.65)
            self.assertEqual(window2.heatmap_half_life_spin.value(), 1.0)
            self.assertEqual(window2.heatmap_half_life_unit.currentText(), "s")
            self.assertEqual(window2.heatmap_power_bins.currentText(), "256")
            self.assertEqual(window2.heatmap_palette.currentText(), "Viridis")
            self.assertEqual(window2.heatmap_range_mode.currentIndex(), 0)
            self.assertEqual(window2.heatmap_normalization.currentIndex(), 2)
        finally:
            settings.remove("heatmap")
            settings.sync()
            window2.heatmap_enabled.setChecked(False)
            shutdown_window(window2, self.app)

    def test_heatmap_settings_roundtrip_preserves_all_values(self) -> None:
        # Review MAJOR #7: restoring one control must not clobber the
        # persisted values of the controls restored after it.
        settings = self.window.settings  # isolated temp ini (see setUp)
        self.window.heatmap_range_mode.setCurrentIndex(2)  # SELECTED: no rolling requests
        self.window.heatmap_window_frames_spin.setValue(777)
        self.window.heatmap_opacity.setValue(0.33)
        self.window.heatmap_palette.setCurrentText("Inferno")
        self.window.heatmap_power_min.setValue(-100.0)
        self.window.heatmap_half_life_spin.setValue(250.0)
        self.window.heatmap_half_life_unit.setCurrentText("ms")
        settings.sync()
        from esw_dfl import gui as gui_module

        with patch.object(gui_module, "QSettings", lambda *args: settings):
            window2 = MainWindow()
        try:
            self.assertEqual(window2.heatmap_range_mode.currentIndex(), 2)
            self.assertEqual(window2.heatmap_window_frames_spin.value(), 777)
            self.assertAlmostEqual(window2.heatmap_opacity.value(), 0.33)
            self.assertEqual(window2.heatmap_palette.currentText(), "Inferno")
            self.assertEqual(window2.heatmap_power_min.value(), -100.0)
            self.assertEqual(window2.heatmap_half_life_spin.value(), 250.0)
            self.assertEqual(window2.heatmap_half_life_unit.currentText(), "ms")
        finally:
            window2.heatmap_enabled.setChecked(False)
            shutdown_window(window2, self.app)

    def test_heatmap_settings_default_roundtrip(self) -> None:
        settings = self.window.settings  # holds the defaults written in setUp
        settings.sync()
        from esw_dfl import gui as gui_module

        with patch.object(gui_module, "QSettings", lambda *args: settings):
            window2 = MainWindow()
        try:
            self.assertFalse(window2.heatmap_enabled.isChecked())
            self.assertEqual(window2.heatmap_range_mode.currentIndex(), 0)
            self.assertEqual(window2.heatmap_window_frames_spin.value(), 500)
            self.assertEqual(window2.heatmap_opacity.value(), 0.65)
            self.assertEqual(window2.heatmap_palette.currentText(), "Viridis")
            self.assertEqual(window2.heatmap_power_min.value(), -120.0)
            self.assertEqual(window2.heatmap_power_max.value(), 0.0)
            self.assertEqual(window2.heatmap_half_life_spin.value(), 1.0)
            self.assertEqual(window2.heatmap_half_life_unit.currentText(), "s")
        finally:
            window2.heatmap_enabled.setChecked(False)
            shutdown_window(window2, self.app)

    def test_cache_hit_updates_requested_generation_and_audit(self) -> None:
        from esw_dfl import gui as gui_module

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if getattr(record, "event_name", None) == "HEATMAP_REQUESTED":
                    records.append(record)

        handler = _Capture()
        gui_module.LOGGER.addHandler(handler)
        try:
            self.window.heatmap_range_mode.setCurrentIndex(2)  # SELECTED: no auto triggers
            self.window.heatmap_start_spin.setValue(1)
            self.window.heatmap_end_spin.setValue(FRAME_COUNT)
            self.window.heatmap_enabled.setChecked(True)
            self.assertTrue(self._wait_applied())
            controller = self.window._heatmap_controller
            first_generation = controller.generation
            # Identical second request: served from the cache, still audited.
            self.window._heatmap_recalculate()
            self.app.processEvents()
            self.assertTrue(self.window.spectrum_renderer.heatmap_visible)
            self.assertEqual(controller.generation, first_generation + 1)
            self.assertEqual(len(records), 2)
            self.assertFalse(records[0].event_details["cache_hit"])
            self.assertTrue(records[1].event_details["cache_hit"])
            diag = self.window.heatmap_diagnostics()
            self.assertEqual(diag["heatmap_requested_generation"], controller.generation)
            self.assertEqual(diag["heatmap_applied_generation"], controller.generation)
        finally:
            gui_module.LOGGER.removeHandler(handler)

    def test_json_export_has_computed_and_export_timestamps(self) -> None:
        self._enable_last_n()
        self.assertTrue(self._wait_applied())
        result = self.window._heatmap_applied
        assert result is not None
        self.assertIsNotNone(result.computed_at)
        path = Path(self._tmp.name) / "heatmap.json"
        export_heatmap_json(
            result,
            path,
            source_path=self.session.source_path,
            session_id=self.session.session_id,
            waterfall_id="waterfall",
            source_id="stream",
            frame_range=self.window._heatmap_applied_range,
            display_config=self.window._heatmap_build_display_config(),
            persistence_snapshot=self.window._heatmap_applied_snapshot,
        )
        metadata = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["calculation_timestamp"], result.computed_at)
        self.assertIn("export_timestamp", metadata)
        computed = datetime.fromisoformat(str(metadata["calculation_timestamp"]))
        exported = datetime.fromisoformat(str(metadata["export_timestamp"]))
        self.assertLessEqual(computed, exported)

    def test_cancel_settles_with_final_status(self) -> None:
        factory = _BlockingReaderFactory()
        controller = self.window._heatmap_controller
        controller._reader_factory = factory
        self._enable_last_n()
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.window._heatmap_cancel()
        self.assertIn("Отмена", self.window.heatmap_status.text())
        factory.release.set()
        self.assertTrue(self._wait_until(lambda: controller.active_ticket is None))
        self.assertIn("Отменено", self.window.heatmap_status.text())
        self.assertGreaterEqual(self.window.heatmap_diagnostics()["heatmap_cancel_count"], 1)


if __name__ == "__main__":
    unittest.main()

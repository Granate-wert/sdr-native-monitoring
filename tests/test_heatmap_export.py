"""Export metadata tests for Heatmap (HMP-PERSIST-009).

Offscreen Qt where needed: JSON must carry the CURRENT display config (which
may differ from the computation config after a restyle), the persistence
snapshot target/bounds, and never serialize stale state.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

from esw_dfl.domain import MeasurementMetadata, MeasurementSession, SpectrumTrace, WaterfallData
from esw_dfl.frame_navigation import NavigationReason
from esw_dfl.gui import MainWindow
from esw_dfl.heatmap import density_hash
from esw_dfl.heatmap_export import export_heatmap_json, export_heatmap_png
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import SpectrogramIndex
from test_heatmap_integration import _BlockingReaderFactory
from heatmap_test_isolation import (
    make_temp_settings,
    patched_qsettings,
    reset_heatmap_controls,
    shutdown_window,
)


FREQ_BINS = 8
FRAME_COUNT = 60
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


class HeatmapExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        frames = []
        for _index in range(FRAME_COUNT):
            values = np.full(FREQ_BINS, -100.0, dtype=np.float32)
            values[3] = -50.0
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

    def _enable(self) -> bool:
        self.session.current_frame = 59
        self.window.heatmap_enabled.setChecked(True)
        controller = self.window._heatmap_controller
        return self._wait_until(
            lambda: controller.applied_snapshot is not None and controller.active_ticket is None
        )

    def _export_json(self) -> dict[str, Any]:
        result = self.window._heatmap_applied
        assert result is not None
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
        return json.loads(path.read_text(encoding="utf-8"))

    def test_json_uses_current_display_normalization_after_restyle(self) -> None:
        self.assertTrue(self._enable())
        hash_before = density_hash(self.window._heatmap_applied_snapshot.density)
        self.window.heatmap_normalization.setCurrentIndex(1)  # Probability
        self.app.processEvents()
        metadata = self._export_json()
        self.assertEqual(metadata["display"]["normalization"], "probability")
        self.assertEqual(metadata["raw_density"]["normalization"], "count")
        self.assertEqual(density_hash(self.window._heatmap_applied_snapshot.density), hash_before)

    def test_json_contains_persistence_target_and_actual_bounds(self) -> None:
        self.assertTrue(self._enable())
        metadata = self._export_json()
        persistence = metadata["persistence"]
        self.assertEqual(persistence["mode"], "rolling_exact")
        self.assertEqual(persistence["target_frame"], 59)
        self.assertEqual(persistence["frame_end"], 59)
        self.assertEqual(persistence["frame_start"], 10)
        self.assertEqual(persistence["window_unit"], "frames")
        self.assertFalse(persistence["stale"])
        self.assertTrue(persistence["exact"])

    def test_json_rejects_stale_snapshot(self) -> None:
        self.assertTrue(self._enable())
        # A rebuild in flight makes the applied snapshot stale: export is blocked.
        factory = _BlockingReaderFactory()
        self.window._heatmap_controller._reader_factory = factory
        self.window._frame_nav.seek(10, NavigationReason.FRAME_INPUT)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.app.processEvents()
        self.assertEqual(self.window._heatmap_controller.phase.name, "REBUILDING")
        with patch.object(self.window, "_show_error"):
            self.assertIsNone(self.window._current_heatmap_result())
        factory.release.set()
        self.assertTrue(
            self._wait_until(lambda: self.window._heatmap_controller.active_ticket is None)
        )

    def test_png_uses_current_display_levels_without_recompute(self) -> None:
        self.assertTrue(self._enable())
        controller = self.window._heatmap_controller
        reads_before = int(self.window.heatmap_diagnostics()["heatmap_frames_decoded"])
        generation_before = controller.generation
        snapshot = self.window._heatmap_applied_snapshot
        normalized = self.window._normalize_snapshot(snapshot, self.window._heatmap_normalization())
        path = Path(self._tmp.name) / "heatmap.png"
        export_heatmap_png(
            self.window._heatmap_applied,
            normalized,
            self.window.spectrum_renderer.heatmap_lut,
            0.65,
            path,
            levels=self.window._heatmap_current_levels,
        )
        self.assertGreater(path.stat().st_size, 0)
        self.assertEqual(
            int(self.window.heatmap_diagnostics()["heatmap_frames_decoded"]), reads_before
        )
        self.assertEqual(controller.generation, generation_before)


if __name__ == "__main__":
    unittest.main()

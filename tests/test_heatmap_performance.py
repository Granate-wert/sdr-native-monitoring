"""Performance and lifecycle tests for the Heatmap Spectrum pipeline (ТЗ §23.3).

The synthetic 100 000 x 1001 dataset is served by a fake streaming
``SpectrogramFrameReader`` patched into ``esw_dfl.heatmap_worker`` — no giant
CFB container and no full value matrix is ever materialized. Assertions are
deterministic bounds (memory ceiling, progress-call ceiling, cancellation
latency, pending-request ceiling), not wall-clock timings, per ТЗ §23.3.

Measured on the development machine (2026-07, Python 3.13, tracemalloc on):
the full 100k-frame exact run takes ~11 s, peaks at ~3 MiB of tracked
allocations and emits ~51 progress callbacks.
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl import heatmap_worker
from esw_dfl.heatmap import HeatmapConfig, HeatmapRangeMode
from esw_dfl.heatmap_worker import compute_heatmap
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import OperationCancelled, SpectrogramIndex, SpectrogramRow
from heatmap_test_isolation import shutdown_window


FRAME_COUNT = 100_000
FREQ_BINS = 1001
# Bounded-memory budget: density (256 x 1001 x uint32 ~= 1 MiB) + position
# vector (~0.8 MiB) + batch/index slack. A full float32 frame matrix would be
# ~400 MiB, so this ceiling proves the streaming path.
MEMORY_BUDGET_BYTES = 64 * 2**20
# Progress is throttled to ~10 events/s; the measured run emits ~51 calls.
PROGRESS_CALL_BUDGET = 1000
# Generous cancellation bound; the measured latency is a few milliseconds.
CANCEL_LATENCY_BUDGET_S = 2.0


def _synthetic_index(frame_count: int, freq_bins: int) -> tuple[SpectrogramInfo, SpectrogramIndex]:
    info = SpectrogramInfo(
        key="waterfall",
        title="Waterfall",
        mode="RT",
        measurement="Spectrum",
        measurement_type="Spectrogram",
        source_stream="stream",
        line_count=frame_count,
        point_count=freq_bins,
        start_hz=100.0,
        stop_hz=200.0,
    )
    index = SpectrogramIndex(
        info=info,
        line_indices=np.arange(frame_count, dtype=np.int64),
        timestamps=np.arange(frame_count, dtype=np.float64),
        offsets=np.arange(frame_count, dtype=np.int64) * 16,
        lengths=np.ones(frame_count, dtype=np.int32),
    )
    return info, index


class _FakeStreamingReader:
    """Serves deterministic synthetic frames one at a time, like the real reader."""

    last_rows_read = 0

    def __init__(self, _path: str | Path, index: SpectrogramIndex) -> None:
        self._buffer = np.full(index.info.point_count, -80.0, dtype=np.float32)
        self._buffer[::7] = -40.0
        self.rows_read = 0

    def read_frame(self, frame_index: int) -> SpectrogramRow:
        self.rows_read += 1
        type(self).last_rows_read = self.rows_read
        return SpectrogramRow(frame_index, float(frame_index), self._buffer)

    def iter_frames(
        self,
        frame_indices: Any,
        cancel: threading.Event | None = None,
    ) -> Any:
        try:
            for frame_index in frame_indices:
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("Операция отменена")
                row = self.read_frame(int(frame_index))
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("Операция отменена")
                yield row
        finally:
            self.close()

    def close(self) -> None:
        pass


class _SlowReader(_FakeStreamingReader):
    def __init__(self, path: str | Path, index: SpectrogramIndex, delay_s: float) -> None:
        super().__init__(path, index)
        self._delay_s = delay_s

    def read_frame(self, frame_index: int) -> SpectrogramRow:
        time.sleep(self._delay_s)
        return super().read_frame(frame_index)


def _make_slow_reader(path: str | Path, index: SpectrogramIndex) -> _SlowReader:
    return _SlowReader(path, index, delay_s=0.0005)


class HeatmapStreamingPerformanceTests(unittest.TestCase):
    """One shared exact 100k-frame run; individual tests assert its bounds."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.info, cls.index = _synthetic_index(FRAME_COUNT, FREQ_BINS)
        frequencies = np.linspace(cls.info.start_hz, cls.info.stop_hz, FREQ_BINS)
        config = HeatmapConfig(range_mode=HeatmapRangeMode.FULL, power_bins=256, batch_size=2000)
        cls.progress_calls: list[tuple[int, int]] = []
        cls.reader = None
        tracemalloc.start()
        started = time.monotonic()
        with patch.object(heatmap_worker, "SpectrogramFrameReader", _FakeStreamingReader):
            cls.result = compute_heatmap(
                "synthetic.dfl",
                cls.info,
                frequencies,
                config,
                generation=1,
                session_id="perf",
                waterfall_id="waterfall",
                source_id="stream",
                index=cls.index,
                progress=lambda processed, total: cls.progress_calls.append((processed, total)),
            )
        cls.elapsed_s = time.monotonic() - started
        _current, cls.peak_traced_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    def test_memory_stays_bounded_over_100k_frames(self) -> None:
        self.assertLessEqual(
            self.peak_traced_bytes,
            MEMORY_BUDGET_BYTES,
            f"peak traced memory {self.peak_traced_bytes / 2**20:.1f} MiB exceeds the "
            f"{MEMORY_BUDGET_BYTES // 2**20} MiB budget; the frame matrix must never materialize",
        )

    def test_final_processed_count_matches_full_range(self) -> None:
        self.assertEqual(self.result.processed_frames, FRAME_COUNT)
        self.assertEqual(self.result.total_frames_in_range, FRAME_COUNT)
        self.assertTrue(self.result.exact)
        self.assertEqual(int(self.result.density.sum()), FRAME_COUNT * FREQ_BINS)

    def test_every_frame_was_streamed_once(self) -> None:
        # The density sum already proves all frames were processed; here we
        # additionally assert the streaming source was used frame-by-frame.
        self.assertEqual(_FakeStreamingReader.last_rows_read, FRAME_COUNT)

    def test_progress_events_are_throttled(self) -> None:
        self.assertGreaterEqual(len(self.progress_calls), 1)
        self.assertLessEqual(len(self.progress_calls), PROGRESS_CALL_BUDGET)
        self.assertLess(len(self.progress_calls), FRAME_COUNT // 100)
        self.assertEqual(self.progress_calls[-1], (FRAME_COUNT, FRAME_COUNT))

    def test_cancellation_is_responsive(self) -> None:
        frame_count = 50_000
        info, index = _synthetic_index(frame_count, FREQ_BINS)
        frequencies = np.linspace(info.start_hz, info.stop_hz, FREQ_BINS)
        config = HeatmapConfig(range_mode=HeatmapRangeMode.FULL, power_bins=256, batch_size=50)
        cancel = threading.Event()
        failure: list[BaseException] = []

        def run() -> None:
            try:
                with patch.object(heatmap_worker, "SpectrogramFrameReader", _make_slow_reader):
                    compute_heatmap(
                        "synthetic.dfl",
                        info,
                        frequencies,
                        config,
                        generation=1,
                        session_id="perf",
                        waterfall_id="waterfall",
                        source_id="stream",
                        index=index,
                        cancel=cancel,
                    )
            except OperationCancelled as exc:
                failure.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        started = time.monotonic()
        worker.start()
        time.sleep(0.3)
        cancel.set()
        worker.join(timeout=CANCEL_LATENCY_BUDGET_S)
        latency = time.monotonic() - started
        self.assertFalse(worker.is_alive(), "worker did not finish within the cancellation budget")
        self.assertTrue(failure and isinstance(failure[0], OperationCancelled))
        self.assertLess(latency, 0.3 + CANCEL_LATENCY_BUDGET_S)


class HeatmapPendingGrowthTests(unittest.TestCase):
    """Qt-level check: a rolling-request burst never grows a pending backlog."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from esw_dfl.domain import MeasurementMetadata, MeasurementSession, WaterfallData
        from esw_dfl.gui import MainWindow
        from heatmap_test_isolation import (
            make_temp_settings,
            patched_qsettings,
            reset_heatmap_controls,
        )

        self._tmp = tempfile.TemporaryDirectory()
        frames = [np.full(8, -60.0, dtype=np.float32) for _ in range(8)]
        path, info, index = self._write_fake_dfl(Path(self._tmp.name), frames)
        _settings = make_temp_settings(self._tmp.name)
        with patched_qsettings(_settings):
            self.window = MainWindow()
        reset_heatmap_controls(self.window)
        # test-specific override: tiny window for the 8-frame fixture
        self.window.heatmap_window_frames_spin.setValue(4)
        session = MeasurementSession("session", path, "session", MeasurementMetadata())
        waterfall = WaterfallData(
            "waterfall", "Waterfall", 8, 8, info.start_hz, info.stop_hz,
            (info.stop_hz - info.start_hz) / 7.0, "stream",
        )
        values = np.stack(frames)
        waterfall.set_preview(values, np.arange(8, dtype=np.float64), np.arange(8))
        session.waterfalls["waterfall"] = waterfall
        session.active_waterfall_id = "waterfall"
        self.window.repository.add(session)
        self.window._spectrogram_indexes[("session", "waterfall")] = index
        for frame, row_values in enumerate(values):
            self.window._frame_loader._cache[("session", "waterfall", frame)] = SpectrogramRow(
                frame, float(frame), row_values.copy()
            )
        self.session = session
        self.window.set_active_session("session")
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.heatmap_enabled.setChecked(False)
        shutdown_window(self.window, self.app)
        self._tmp.cleanup()

    @staticmethod
    def _write_fake_dfl(
        root: Path, frames: list[np.ndarray]
    ) -> tuple[Path, SpectrogramInfo, SpectrogramIndex]:
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
            "waterfall", "Waterfall", "RT", "Spectrum", "Spectrogram", "stream",
            len(frames), int(frames[0].size), 100.0, 800.0,
        )
        index = SpectrogramIndex(
            info,
            np.arange(len(frames), dtype=np.int64),
            np.arange(len(frames), dtype=np.float64),
            np.asarray(offsets, dtype=np.int64),
            np.asarray(lengths, dtype=np.int32),
            np.arange(sector_count, dtype=np.int32),
            sector_size,
        )
        return path, info, index

    def _wait_until(self, predicate: Callable[[], bool], timeout_s: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    def test_rolling_request_burst_does_not_grow_pending(self) -> None:
        """200 playback spans keep at most one active + one pending ticket."""
        from esw_dfl.frame_navigation import NavigationReason

        self.window.heatmap_enabled.setChecked(True)
        controller = self.window._heatmap_controller
        self.assertTrue(
            self._wait_until(lambda: controller.applied_snapshot is not None)
        )
        max_tickets = 0
        for frame in range(200):
            self.window._frame_nav.seek(frame % 8, NavigationReason.PLAYBACK)
            self.app.processEvents()
            max_tickets = max(
                max_tickets,
                int(controller.active_ticket is not None) + int(controller.pending_ticket is not None),
            )
        self.assertLessEqual(max_tickets, 2, "active+pending grew beyond the bounded queue")
        # Wait until the latest playback target is actually applied: with no
        # active/pending tickets the final seek may still sit on a settle
        # timer, so asserting immediately races with it.
        self.assertTrue(
            self._wait_until(
                lambda: (
                    controller.active_ticket is None
                    and controller.pending_ticket is None
                    and controller.applied_snapshot is not None
                    and controller.applied_snapshot.target_frame == 199 % 8
                )
            )
        )
        snapshot = controller.applied_snapshot
        assert snapshot is not None
        self.assertEqual(snapshot.target_frame, 199 % 8)


if __name__ == "__main__":
    unittest.main()

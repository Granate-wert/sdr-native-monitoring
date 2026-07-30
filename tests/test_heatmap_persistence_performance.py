"""Structural performance tests for the persistence controller (review P3/§10.3).

Proves the architectural complexity bounds without invented wall-clock
thresholds: sequential advance reads exactly one frame, latencies are recorded
in diagnostics, backlog stays bounded, and rolling memory does not grow with
the recording length.
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from esw_dfl.frame_navigation import FrameSpanEvent, NavigationReason
from esw_dfl.heatmap_persistence import PersistenceConfig, PersistenceMode, PersistenceSourceKey
from esw_dfl.heatmap_persistence_controller import (
    HeatmapPersistenceController,
    PersistenceSourceContext,
)
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import SpectrogramFrameReader, SpectrogramIndex, SpectrogramRow
from heatmap_persistence_fixtures import make_frame, offline_density


FREQ_BINS = 8
FRAME_COUNT = 1000
WINDOW = 50
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


class _CountingFactory:
    def __init__(self) -> None:
        self.reads: list[int] = []

    def __call__(self, path: Path, index: SpectrogramIndex) -> Any:
        factory = self

        class _Reader:
            def __init__(self) -> None:
                self._reader = SpectrogramFrameReader(path, index)

            def read_frame(self, frame_index: int) -> SpectrogramRow:
                factory.reads.append(int(frame_index))
                return self._reader.read_frame(frame_index)

            def close(self) -> None:
                self._reader.close()

        return _Reader()


class ControllerPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.frames = [make_frame(3) for _ in range(FRAME_COUNT)]
        self.dfl_path, self.index = _write_fake_dfl(Path(self._tmp.name), self.frames)
        self.factory = _CountingFactory()
        self.controller = HeatmapPersistenceController(
            thread_pool=QThreadPool.globalInstance(),
            reader_factory=self.factory,
            audit=lambda *args, **kwargs: None,
        )
        self.controller.set_context(
            PersistenceSourceContext(
                session_id="session",
                waterfall_id="waterfall",
                source_id="stream",
                source_path=self.dfl_path,
                frequencies_hz=FREQUENCIES,
                index=self.index,
                info=self.index.info,
                source_key=PersistenceSourceKey("session", "waterfall", "stream", "grid"),
            )
        )
        self.controller.enable(
            PersistenceConfig(
                mode=PersistenceMode.ROLLING_EXACT,
                window_frames=WINDOW,
                power_min_dbm=-120.0,
                power_max_dbm=0.0,
                power_bins=64,
            ),
            WINDOW - 1,
            float(WINDOW - 1),
        )

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.app.sendPostedEvents()
        self.app.processEvents()
        QThreadPool.globalInstance().waitForDone(3000)
        self.app.processEvents()
        self._tmp.cleanup()

    def _wait_until(self, predicate, timeout_s: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    def _wait_target(self, target: int, timeout_s: float = 30.0) -> bool:
        return self._wait_until(
            lambda: self.controller.applied_snapshot is not None
            and self.controller.applied_snapshot.target_frame == target
            and self.controller.active_ticket is None
            and self.controller.pending_ticket is None,
            timeout_s,
        )

    def _span(self, previous: int, new: int) -> FrameSpanEvent:
        return FrameSpanEvent(
            previous_target=previous,
            new_target=new,
            direction=1,
            reason=NavigationReason.PLAYBACK,
            generation=new,
        )

    def test_sequential_one_frame_one_read_and_latency_metrics(self) -> None:
        self.assertTrue(self._wait_target(WINDOW - 1))
        reads_after_build = len(self.factory.reads)
        for frame in range(WINDOW, WINDOW + 50):
            self.controller.on_frame_span(self._span(frame - 1, frame))
            self.app.processEvents()
        self.assertTrue(self._wait_target(WINDOW + 49))
        # Sequential advance: exactly one entered frame read per single step.
        entered = self.factory.reads[reads_after_build:]
        self.assertEqual(len(entered), 50)
        diag = self.controller.diagnostics()
        self.assertGreaterEqual(diag["heatmap_sequential_updates"], 1)
        self.assertIn("heatmap_advance_latency_p50_ms", diag)
        self.assertIn("heatmap_advance_latency_p95_ms", diag)
        self.assertIn("heatmap_advance_latency_max_ms", diag)
        snapshot = self.controller.applied_snapshot
        assert snapshot is not None
        np.testing.assert_array_equal(
            snapshot.density, offline_density(self.frames, WINDOW, WINDOW + 49)
        )

    def test_playback_backlog_stays_bounded(self) -> None:
        self.assertTrue(self._wait_target(WINDOW - 1))
        max_tickets = 0
        for frame in range(WINDOW, FRAME_COUNT):
            self.controller.on_frame_span(self._span(frame - 1, frame))
            tickets = int(self.controller.active_ticket is not None) + int(
                self.controller.pending_ticket is not None
            )
            max_tickets = max(max_tickets, tickets)
        self.assertTrue(self._wait_target(FRAME_COUNT - 1, timeout_s=60.0))
        self.assertLessEqual(max_tickets, 2)
        snapshot = self.controller.applied_snapshot
        assert snapshot is not None
        np.testing.assert_array_equal(
            snapshot.density,
            offline_density(self.frames, FRAME_COUNT - WINDOW, FRAME_COUNT - 1),
        )
        self.assertEqual(self.controller.diagnostics()["heatmap_lag_frames"], 0)

    def test_rolling_memory_is_bounded_by_window_not_recording(self) -> None:
        self.assertTrue(self._wait_target(WINDOW - 1))
        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        for frame in range(WINDOW, FRAME_COUNT):
            self.controller.on_frame_span(self._span(frame - 1, frame))
            if frame % 100 == 0:
                self.app.processEvents()
        self.assertTrue(self._wait_target(FRAME_COUNT - 1, timeout_s=60.0))
        state = self.controller.engine_state
        assert state is not None
        engine_bytes = state.accumulator.memory_bytes()
        deque_bytes = len(state.contributions) * (FREQ_BINS * 2 + 64)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        # Engine-held structures stay window-sized; no per-recording growth.
        self.assertLessEqual(engine_bytes + deque_bytes, 4 * 2**20)
        self.assertEqual(len(state.contributions), WINDOW)
        self.assertLess(current, baseline + 8 * 2**20)
        self.assertLess(peak, baseline + 32 * 2**20)

    # --- §10.2/§10.4 performance contract metrics ------------------------------
    def test_frame_period_and_ratio_come_from_record_timestamps(self) -> None:
        self.assertTrue(self._wait_target(WINDOW - 1))
        diag = self.controller.diagnostics()
        # The fake record uses arange timestamps: period == 1.0 s, never hard-coded.
        self.assertEqual(diag["frame_period_s"], 1.0)
        self.assertIn("processing_to_frame_period_ratio", diag)
        self.assertGreaterEqual(diag["analytical_frames_processed"], WINDOW)
        self.assertGreater(diag["density_bytes"], 0)
        self.assertGreater(diag["contribution_ring_bytes"], 0)
        self.assertIn("initial_rebuild_latency_ms", diag)
        self.assertIn("frames_decoded_per_update", diag)

    def test_capacity_warning_when_processing_exceeds_frame_period(self) -> None:
        # Frame period of the record: 1 ms. Slow reader: 25 ms/frame -> ratio >> 1.
        events: list[str] = []
        original = self.controller._audit_cb
        self.controller._audit_cb = lambda event, **details: (
            events.append(event),
            original(event, **details),
        )[0]
        import dataclasses

        import numpy as np

        slow_index = dataclasses.replace(self.index, timestamps=np.arange(FRAME_COUNT, dtype=float) * 0.001)
        self.controller.set_context(
            PersistenceSourceContext(
                session_id="session",
                waterfall_id="waterfall",
                source_id="stream",
                source_path=self.dfl_path,
                frequencies_hz=FREQUENCIES,
                index=slow_index,
                info=slow_index.info,
                source_key=PersistenceSourceKey("session", "waterfall", "stream", "grid"),
            )
        )
        self.controller._reader_factory = _SlowFactory(0.025)
        self.controller.enable(
            PersistenceConfig(
                mode=PersistenceMode.ROLLING_EXACT,
                window_frames=10,
                power_min_dbm=-120.0,
                power_max_dbm=0.0,
                power_bins=64,
            ),
            19,
            0.019,
        )
        self.assertTrue(self._wait_target(19, timeout_s=60.0))
        for frame in range(20, 40):
            self.controller.on_frame_span(self._span(frame - 1, frame))
            self.app.processEvents()
        self.assertTrue(self._wait_target(39, timeout_s=60.0))
        self.assertIn("HEATMAP_CAPACITY_WARNING", events)
        diag = self.controller.diagnostics()
        self.assertGreater(diag["processing_to_frame_period_ratio"], 1.0)
        self.assertAlmostEqual(diag["frame_period_s"], 0.001, places=6)

    def test_memory_bounded_for_window_sizes_500_1000_5000(self) -> None:
        from esw_dfl.heatmap import HeatmapAccumulator

        for window in (500, 1000, 5000):
            accumulator = HeatmapAccumulator(
                freq_bins=FREQ_BINS,
                power_min_dbm=-120.0,
                power_max_dbm=0.0,
                power_bins=64,
            )
            for frame_index in range(window):
                accumulator.add_frame(self.frames[frame_index % FRAME_COUNT])
            engine_bytes = accumulator.memory_bytes()
            ring_bytes = window * (FREQ_BINS * 2 + 64)
            # Window-scaled structures only; nothing scales with FRAME_COUNT.
            self.assertLessEqual(engine_bytes + ring_bytes, 2 * 2**20, f"window={window}")


class _SlowFactory:
    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    def __call__(self, path, index):
        delay = self._delay_s

        class _Reader:
            def __init__(self) -> None:
                self._reader = SpectrogramFrameReader(path, index)

            def read_frame(self, frame_index: int) -> SpectrogramRow:
                time.sleep(delay)
                return self._reader.read_frame(frame_index)

            def close(self) -> None:
                self._reader.close()

        return _Reader()


if __name__ == "__main__":
    unittest.main()

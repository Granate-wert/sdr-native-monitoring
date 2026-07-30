"""Controller-level tests for HeatmapPersistenceController (review P2/P3).

Offscreen Qt, real fake-DFL sources read through SpectrogramFrameReader (or a
blocking/counting factory), no MainWindow involved: routing, generations,
logical targets, lifecycle.
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from esw_dfl import heatmap_persistence_controller as controller_module
from esw_dfl.frame_navigation import FrameSpanEvent, NavigationReason
from esw_dfl.heatmap import HeatmapAccumulator, HeatmapConfig, HeatmapRangeMode
from esw_dfl.heatmap_persistence import (
    PersistenceConfig,
    PersistenceMode,
    PersistencePhase,
    PersistenceSourceKey,
    WindowUnit,
)
from esw_dfl.heatmap_persistence_controller import (
    HeatmapPersistenceController,
    PersistenceSourceContext,
)
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import SpectrogramFrameReader, SpectrogramIndex, SpectrogramRow
from heatmap_persistence_fixtures import make_fixture_ab, offline_density


FREQ_BINS = 8
POWER_MIN = -120.0
POWER_MAX = 0.0
POWER_BINS = 64
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
        key="waterfall",
        title="Waterfall",
        mode="RT",
        measurement="Spectrum",
        measurement_type="Spectrogram",
        source_stream="stream",
        line_count=len(frames),
        point_count=int(frames[0].size),
        start_hz=float(FREQUENCIES[0]),
        stop_hz=float(FREQUENCIES[-1]),
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
    """reader_factory that counts read_frame calls per produced reader."""

    def __init__(self) -> None:
        self.reads: list[int] = []

    def __call__(self, path: Path, index: SpectrogramIndex) -> "_CountingReader":
        return _CountingReader(SpectrogramFrameReader(path, index), self.reads)


class _CountingReader:
    def __init__(self, reader: SpectrogramFrameReader, reads: list[int]) -> None:
        self._reader = reader
        self._reads = reads

    def read_frame(self, frame_index: int) -> SpectrogramRow:
        self._reads.append(int(frame_index))
        return self._reader.read_frame(frame_index)

    def close(self) -> None:
        self._reader.close()


class _BlockingFactory:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, path: Path, index: SpectrogramIndex) -> "_BlockingReader":
        return _BlockingReader(SpectrogramFrameReader(path, index), self.started, self.release)


class _BlockingReader:
    def __init__(self, reader: SpectrogramFrameReader, started: threading.Event, release: threading.Event) -> None:
        self._reader = reader
        self._started = started
        self._release = release

    def read_frame(self, frame_index: int) -> SpectrogramRow:
        self._started.set()
        self._release.wait(timeout=30.0)
        return self._reader.read_frame(frame_index)

    def close(self) -> None:
        self._reader.close()


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.frames = make_fixture_ab()
        self.dfl_path, self.index = _write_fake_dfl(Path(self._tmp.name), self.frames)
        self.audit: list[tuple[str, dict]] = []
        self.controller = HeatmapPersistenceController(
            thread_pool=QThreadPool.globalInstance(),
            audit=lambda event, **details: self.audit.append((event, details)),
        )
        self.context = PersistenceSourceContext(
            session_id="session",
            waterfall_id="waterfall",
            source_id="stream",
            source_path=self.dfl_path,
            frequencies_hz=FREQUENCIES,
            index=self.index,
            info=self.index.info,
            source_key=PersistenceSourceKey("session", "waterfall", "stream", "grid"),
        )
        self.controller.set_context(self.context)

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.app.sendPostedEvents()
        self.app.processEvents()
        QThreadPool.globalInstance().waitForDone(3000)
        self.app.processEvents()
        self._tmp.cleanup()

    # --- helpers ---------------------------------------------------------------
    def _wait_until(self, predicate, timeout_s: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    def _config(self, window: int = 50, mode: PersistenceMode = PersistenceMode.ROLLING_EXACT) -> PersistenceConfig:
        return PersistenceConfig(
            mode=mode,
            window_frames=window,
            power_min_dbm=POWER_MIN,
            power_max_dbm=POWER_MAX,
            power_bins=POWER_BINS,
        )

    def _enable(self, target: int, window: int = 50, **kwargs) -> None:
        self.controller.enable(self._config(window, **kwargs), target, float(target))

    def _span(self, previous: int, new: int, reason: NavigationReason = NavigationReason.PLAYBACK) -> FrameSpanEvent:
        direction = (new > previous) - (new < previous)
        return FrameSpanEvent(
            previous_target=previous,
            new_target=new,
            direction=direction,
            reason=reason,
            generation=new,
        )

    def _applied(self, timeout_s: float = 10.0) -> bool:
        return self._wait_until(
            lambda: self.controller.applied_snapshot is not None and self.controller.active_ticket is None
        )

    # --- tests -------------------------------------------------------------------
    def test_sequential_rolling_does_not_call_compute_heatmap(self) -> None:
        factory = _CountingFactory()
        self.controller._reader_factory = factory
        with patch.object(controller_module, "compute_heatmap") as compute_spy:
            self._enable(99)
            self.assertTrue(self._applied())
            reads_after_build = len(factory.reads)
            self.controller.on_frame_span(self._span(99, 100))
            self.assertTrue(
                self._wait_until(
                    lambda: self.controller.applied_snapshot is not None
                    and self.controller.applied_snapshot.target_frame == 100
                    and self.controller.active_ticket is None
                )
            )
            compute_spy.assert_not_called()
            self.assertEqual(factory.reads[reads_after_build:], [100])
            snapshot = self.controller.applied_snapshot
            self.assertEqual((snapshot.frame_start, snapshot.frame_end), (51, 100))
            self.assertEqual(self.controller.diagnostics()["heatmap_sequential_updates"], 1)
            np.testing.assert_array_equal(snapshot.density, offline_density(self.frames, 51, 100))

    def test_sequential_time_window_reads_only_entered_frame(self) -> None:
        factory = _CountingFactory()
        self.controller._reader_factory = factory
        config = PersistenceConfig(
            mode=PersistenceMode.ROLLING_EXACT,
            window_unit=WindowUnit.SECONDS,
            window_frames=50,
            window_seconds=50.0,
            power_min_dbm=POWER_MIN,
            power_max_dbm=POWER_MAX,
            power_bins=POWER_BINS,
        )
        with patch.object(controller_module, "compute_heatmap") as compute_spy:
            self.controller.enable(config, 99, 99.0)
            self.assertTrue(self._applied())
            reads_after_build = len(factory.reads)
            self.controller.on_frame_span(self._span(99, 100))
            self.assertTrue(
                self._wait_until(
                    lambda: self.controller.applied_snapshot is not None
                    and self.controller.applied_snapshot.target_frame == 100
                    and self.controller.active_ticket is None
                )
            )
            compute_spy.assert_not_called()
        self.assertEqual(factory.reads[reads_after_build:], [100])
        snapshot = self.controller.applied_snapshot
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (50, 100))
        self.assertEqual(self.controller.diagnostics()["heatmap_sequential_updates"], 1)
        np.testing.assert_array_equal(snapshot.density, offline_density(self.frames, 50, 100))

    def test_non_overlapping_playback_jump_consumes_every_intermediate_frame(self) -> None:
        factory = _CountingFactory()
        self.controller._reader_factory = factory
        self._enable(100)
        self.assertTrue(self._applied())
        factory.reads.clear()
        self.controller.on_frame_span(self._span(100, 250))
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 250
                and self.controller.active_ticket is None
            )
        )
        # The stream consumes every analytical frame, even though the UI sent one jump.
        self.assertEqual(factory.reads, list(range(101, 251)))
        snapshot = self.controller.applied_snapshot
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (201, 250))
        np.testing.assert_array_equal(snapshot.density, offline_density(self.frames, 201, 250))

    def test_playback_jump_above_1000_frames_consumes_every_source_frame(self) -> None:
        # The UI is allowed to publish only the final target. Rolling Exact
        # must still visit each source spectrum from its analytical cursor.
        frames = [self.frames[index % len(self.frames)] for index in range(1301)]
        dfl_path, index = _write_fake_dfl(Path(self._tmp.name), frames)
        self.frames = frames
        self.index = index
        self.context = PersistenceSourceContext(
            session_id="session",
            waterfall_id="waterfall",
            source_id="stream",
            source_path=dfl_path,
            frequencies_hz=FREQUENCIES,
            index=index,
            info=index.info,
            source_key=PersistenceSourceKey("session", "waterfall", "stream", "grid"),
        )
        self.controller.set_context(self.context)
        factory = _CountingFactory()
        self.controller._reader_factory = factory
        self._enable(99)
        self.assertTrue(self._applied())
        updates_before = int(self.controller.diagnostics()["heatmap_sequential_updates"])
        factory.reads.clear()
        self.controller.on_frame_span(self._span(99, 1200))
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 1200
            )
        )
        self.assertEqual(factory.reads, list(range(100, 1201)))
        self.assertEqual(
            int(self.controller.diagnostics()["heatmap_sequential_updates"])
            - updates_before,
            1101,
        )
        snapshot = self.controller.applied_snapshot
        assert snapshot is not None
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (1151, 1200))
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 1151, 1200))

    def test_span_event_drives_target_before_ui_snapshot(self) -> None:
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self._enable(10)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.controller.on_frame_span(self._span(10, 20))
        # The logical target moves synchronously, before any worker finishes.
        self.assertIsNotNone(self.controller.desired_target)
        self.assertEqual(self.controller.desired_target.frame_index, 20)
        self.assertIsNone(self.controller.applied_snapshot)
        factory.release.set()
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 20
            )
        )

    def test_latest_pending_replaces_previous_pending(self) -> None:
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self._enable(10)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.controller.on_frame_span(self._span(10, 50, NavigationReason.FRAME_INPUT))
        self.controller.on_frame_span(self._span(50, 60, NavigationReason.FRAME_INPUT))
        pending = self.controller.pending_ticket
        self.assertIsNotNone(pending)
        self.assertEqual(pending.target_frame, 60)
        factory.release.set()
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 60
                and self.controller.pending_ticket is None
            )
        )
        # The superseded pending target 50 never started a worker.
        started_for_50 = [
            details for name, details in self.audit
            if name == "HEATMAP_REBUILD_STARTED" and details.get("target_frame") == 50
        ]
        self.assertEqual(started_for_50, [])

    def test_late_result_is_discarded_after_clear(self) -> None:
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self._enable(10)
        self.assertTrue(factory.started.wait(timeout=10.0))
        # clear() bumps the generation without cancelling the worker: its late
        # result must be discarded, never applied.
        self.controller.clear()
        factory.release.set()
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.diagnostics()["heatmap_stale_results_discarded"] >= 1
            )
        )
        self.assertIsNone(self.controller.applied_snapshot)

    def test_pause_then_catches_latest_target(self) -> None:
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self._enable(10)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.controller.on_frame_span(self._span(10, 20, NavigationReason.FRAME_INPUT))
        self.controller.pause()
        factory.release.set()
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 20
                and self.controller.active_ticket is None
            )
        )
        self.assertEqual(self.controller.diagnostics()["heatmap_lag_frames"], 0)
        self.assertEqual(self.controller.phase, PersistencePhase.CURRENT)

    def test_stop_builds_initial_window_at_frame_zero(self) -> None:
        self._enable(199)
        self.assertTrue(self._applied())
        self.controller.stop(target=0)
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 0
                and self.controller.active_ticket is None
            )
        )
        snapshot = self.controller.applied_snapshot
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (0, 0))
        self.assertEqual(int(snapshot.density.sum()), FREQ_BINS)

    def test_loop_resets_epoch_and_rebuilds_initial_window(self) -> None:
        self._enable(199)
        self.assertTrue(self._applied())
        self.controller.on_frame_span(self._span(199, 5, NavigationReason.PLAYBACK))
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 5
                and self.controller.active_ticket is None
            )
        )
        events = [name for name, _details in self.audit]
        self.assertIn("HEATMAP_LOOP_EPOCH_RESET", events)
        snapshot = self.controller.applied_snapshot
        # No pre-loop contribution survives in the new epoch's window.
        np.testing.assert_array_equal(snapshot.density, offline_density(self.frames, 0, 5))

    def test_context_switch_cancels_and_shutdown_is_final(self) -> None:
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self._enable(10)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.controller.set_context(None)
        factory.release.set()
        self.assertTrue(self._wait_until(lambda: self.controller.active_ticket is None))
        self.assertIsNone(self.controller.applied_snapshot)
        self.controller.shutdown()
        self.assertEqual(self.controller.phase, PersistencePhase.SHUTTING_DOWN)
        self.controller.enable(self._config(), 5, 5.0)
        self.assertIsNone(self.controller.applied_snapshot)

    def test_fixed_full_ignores_playback_spans(self) -> None:
        config = HeatmapConfig(
            range_mode=HeatmapRangeMode.FULL,
            power_min_dbm=POWER_MIN,
            power_max_dbm=POWER_MAX,
            power_bins=POWER_BINS,
        )
        self.controller._enabled = True
        self.controller.request_fixed(config, 299)
        self.assertTrue(self._applied())
        applied_before = self.controller.applied_snapshot
        self.controller.on_frame_span(self._span(100, 101))
        self.controller.on_frame_span(self._span(101, 102))
        self.app.processEvents()
        self.assertIs(self.controller.applied_snapshot, applied_before)
        self.assertIsNone(self.controller.active_ticket)
        self.assertIsNone(self.controller.pending_ticket)
        snapshot = self.controller.applied_snapshot
        self.assertEqual(snapshot.processed_frames, 300)
        np.testing.assert_array_equal(snapshot.density, offline_density(self.frames, 0, 299))

    # --- wave 3: decay data-time semantics + stale-hidden audit ----------------
    def test_stale_hidden_is_audited_on_seek_rebuild(self) -> None:
        self._enable(199)
        self.assertTrue(self._applied())
        self.controller.on_frame_span(self._span(199, 75, NavigationReason.FRAME_INPUT))
        events = [name for name, _details in self.audit]
        self.assertIn("HEATMAP_STALE_HIDDEN", events)
        hidden = [details for name, details in self.audit if name == "HEATMAP_STALE_HIDDEN"]
        self.assertEqual(hidden[-1]["target_frame"], 75)

    def _enable_decay(self, target: int, half_life: float = 2.0, epsilon: float = 1e-3) -> None:
        config = PersistenceConfig(
            mode=PersistenceMode.EXPONENTIAL_DECAY,
            half_life_seconds=half_life,
            decay_cutoff_epsilon=epsilon,
            power_min_dbm=POWER_MIN,
            power_max_dbm=POWER_MAX,
            power_bins=POWER_BINS,
        )
        self.controller.enable(config, target, float(target))

    def test_decay_bounded_rebuild_reads_history_only(self) -> None:
        factory = _CountingFactory()
        self.controller._reader_factory = factory
        self._enable_decay(99, half_life=2.0)
        self.assertTrue(self._applied())
        # T_history = 2 * log2(1000) ≈ 19.93: frames 80..99 only.
        self.assertEqual(factory.reads, list(range(80, 100)))
        snapshot = self.controller.applied_snapshot
        self.assertTrue(snapshot.approximate)
        self.assertFalse(snapshot.exact)
        self.assertEqual(snapshot.half_life_seconds, 2.0)
        self.assertEqual(snapshot.decay_cutoff_epsilon, 1e-3)

    def test_decay_sequential_advance_applies_data_time_factor(self) -> None:
        self._enable_decay(99, half_life=2.0)
        self.assertTrue(self._applied())
        before = self.controller.applied_snapshot
        alpha = 2.0 ** (-1.0 / 2.0)  # dt = 1.0 s at half-life 2.0 s
        with patch.object(controller_module, "compute_heatmap") as compute_spy:
            self.controller.on_frame_span(self._span(99, 100))
            self.assertTrue(
                self._wait_until(
                    lambda: self.controller.applied_snapshot is not None
                    and self.controller.applied_snapshot.target_frame == 100
                    and self.controller.active_ticket is None
                )
            )
            compute_spy.assert_not_called()
        after = self.controller.applied_snapshot
        probe_bin = 2  # fixture bin A occupies frames 0..99
        self.assertAlmostEqual(
            float(after.density[:, probe_bin].sum()),
            float(before.density[:, probe_bin].sum()) * alpha + 1.0,
            places=6,
        )
        self.assertEqual(self.controller.diagnostics()["heatmap_sequential_updates"], 1)
        self.assertTrue(after.approximate)

    def test_decay_loop_resets_epoch_without_preloop_contribution(self) -> None:
        self._enable_decay(199, half_life=1.0)
        self.assertTrue(self._applied())
        self.controller.on_frame_span(self._span(199, 5, NavigationReason.PLAYBACK))
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 5
                and self.controller.active_ticket is None
            )
        )
        events = [name for name, _details in self.audit]
        self.assertIn("HEATMAP_LOOP_EPOCH_RESET", events)
        snapshot = self.controller.applied_snapshot
        # Reference: decay over frames 0..5 alone (dt = 1 s per step).
        accumulator = HeatmapAccumulator(FREQ_BINS, -120.0, 0.0, POWER_BINS, decay=1.0)
        for index in range(6):
            if index:
                accumulator.apply_decay_factor(0.5)
            accumulator.add_contribution(
                accumulator.make_contribution(index, float(index), self.frames[index])
            )
        np.testing.assert_allclose(snapshot.density, accumulator.density, rtol=0, atol=0)

    def test_decay_missing_timestamps_reports_error(self) -> None:
        nan_index = self._nan_index()
        self.controller.set_context(self._context_with_index(nan_index, frame_period_s=None))
        self._enable_decay(50)
        self.assertEqual(self.controller.phase, PersistencePhase.ERROR)
        self.assertIsNone(self.controller.applied_snapshot)
        events = [name for name, _details in self.audit]
        self.assertIn("HEATMAP_FAILED", events)

    def test_decay_frame_period_fallback_uses_synthetic_timeline(self) -> None:
        nan_index = self._nan_index()
        self.controller.set_context(self._context_with_index(nan_index, frame_period_s=0.5))
        self._enable_decay(99, half_life=1.0)
        self.assertTrue(self._applied())
        snapshot = self.controller.applied_snapshot
        self.assertTrue(snapshot.approximate)
        # Synthetic dt = 0.5 s per frame at half-life 1 s -> alpha = 2**-0.5.
        self.assertGreater(float(snapshot.density.sum()), 0.0)


    def test_rolling_stream_publishes_an_intermediate_deadline_snapshot(self) -> None:
        """A large logical jump is analytic-complete but not visually monolithic."""
        emitted: list[int] = []
        self.controller.set_render_fps(1000)
        self.controller.snapshot_ready.connect(lambda snapshot: emitted.append(snapshot.target_frame))
        self._enable(0)
        self.assertTrue(self._applied())
        emitted.clear()
        self.controller.on_frame_span(self._span(0, 250))
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.applied_snapshot is not None
                and self.controller.applied_snapshot.target_frame == 250
                and emitted
                and emitted[-1] == 250
            )
        )
        self.assertGreaterEqual(self.controller.diagnostics()["heatmap_stream_batches"], 2)
        stream_targets = [
            details["target_frame"]
            for event, details in self.audit
            if event == "HEATMAP_STREAM_APPLIED"
        ]
        self.assertTrue(any(0 < frame < 250 for frame in stream_targets), stream_targets)
        self.assertEqual(emitted[-1], 250)

    def test_render_submission_is_separate_from_analytical_snapshot(self) -> None:
        self._enable(99)
        self.assertTrue(self._applied())
        snapshot = self.controller.applied_snapshot
        assert snapshot is not None
        before = self.controller.diagnostics()
        self.controller.report_render_submitted(snapshot)
        after = self.controller.diagnostics()
        self.assertEqual(after["heatmap_rendered_target"], snapshot.target_frame)
        self.assertEqual(after["heatmap_render_submitted"], before["heatmap_render_submitted"] + 1)
        self.assertEqual(after["heatmap_visual_lag_frames"], 0)
        self.assertIn("HEATMAP_RENDER_SUBMITTED", [event for event, _details in self.audit])
    # --- helpers for the decay error/fallback tests -----------------------------
    def _nan_index(self) -> SpectrogramIndex:
        import dataclasses

        return dataclasses.replace(self.index, timestamps=np.full(self.index.frame_count, np.nan))

    def _context_with_index(
        self, index: SpectrogramIndex, frame_period_s: float | None
    ) -> PersistenceSourceContext:
        return PersistenceSourceContext(
            session_id="session",
            waterfall_id="waterfall",
            source_id="stream",
            source_path=self.dfl_path,
            frequencies_hz=FREQUENCIES,
            index=index,
            info=index.info,
            source_key=PersistenceSourceKey("session", "waterfall", "stream", "grid"),
            frame_period_s=frame_period_s,
        )


if __name__ == "__main__":
    unittest.main()

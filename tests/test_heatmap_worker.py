"""Worker-level cancellation tests for compute_heatmap (review P1 / HMP-PERSIST-004).

The default rolling window (500 frames) with the default ``batch_size`` (2000)
never hits a batch boundary, so cancellation must be driven by the per-frame
checks inside ``SpectrogramFrameReader.iter_frames`` — not by batch progress.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl import heatmap_worker
from esw_dfl.heatmap import HeatmapConfig, HeatmapRangeMode, density_hash
from esw_dfl.heatmap_persistence import PersistenceEngine
from esw_dfl.heatmap_worker import compute_heatmap
from esw_dfl.spectrogram import OperationCancelled
from heatmap_persistence_fixtures import (
    FREQ_BINS,
    POWER_BINS,
    POWER_MAX,
    POWER_MIN,
    BlockingFrameReader,
    SlowCountingFrameReader,
    SyntheticPersistenceSource,
    make_frame,
    make_index,
)


FREQUENCIES = np.linspace(100.0, 800.0, FREQ_BINS)


def _last_n_config(window: int = 500, batch_size: int = 2000) -> HeatmapConfig:
    return HeatmapConfig(
        range_mode=HeatmapRangeMode.LAST_N,
        window_frames=window,
        power_min_dbm=POWER_MIN,
        power_max_dbm=POWER_MAX,
        power_bins=POWER_BINS,
        batch_size=batch_size,
    )


class CancelInsideDefaultWindowTests(unittest.TestCase):
    def test_cancel_checked_during_default_500_frame_window(self) -> None:
        frames = [make_frame(3) for _ in range(500)]
        reader = SlowCountingFrameReader(frames, delay_s=0.01)
        index = make_index(len(frames))
        cancel = threading.Event()
        failure: list[BaseException] = []

        def run() -> None:
            try:
                with patch.object(
                    heatmap_worker, "SpectrogramFrameReader", lambda _path, _index: reader
                ):
                    compute_heatmap(
                        "synthetic.dfl",
                        index.info,
                        FREQUENCIES,
                        _last_n_config(window=500, batch_size=2000),
                        generation=1,
                        session_id="s",
                        waterfall_id="w",
                        source_id="src",
                        index=index,
                        cancel=cancel,
                    )
            except OperationCancelled as exc:
                failure.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        started = time.monotonic()
        worker.start()
        # Cancel once the reader began the 5th frame of the 500-frame window.
        deadline = time.monotonic() + 10.0
        while len(reader.read_indices) < 5 and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertGreaterEqual(len(reader.read_indices), 5)
        cancel.set()
        worker.join(timeout=5.0)
        latency = time.monotonic() - started
        self.assertFalse(worker.is_alive(), "worker ignored the cancellation")
        self.assertTrue(failure and isinstance(failure[0], OperationCancelled))
        # At most one already-started read/decode completes after cancel.
        self.assertLessEqual(len(reader.read_indices), 6)
        self.assertLess(latency, 5.0)

    def test_cancel_before_atomic_rebuild_commit_keeps_previous_state(self) -> None:
        frames = [make_frame(index % FREQ_BINS) for index in range(100)]
        engine = PersistenceEngine()
        builder = SyntheticPersistenceSource(frames)
        state, _snapshot = engine.rebuild_exact(
            _request_like(builder, target_frame=9, window_frames=10), builder
        )
        hash_before = density_hash(state.accumulator.density)
        weights_before = state.accumulator.normalization_weights_by_frequency.copy()
        contributions_before = len(state.contributions)

        blocking = BlockingFrameReader(frames)
        cancel = threading.Event()
        failure: list[BaseException] = []

        def run() -> None:
            try:
                # [0..9] -> [5..14] overlaps, so the advance stages reads
                # 10..14 instead of demanding a rebuild.
                engine.advance_exact(state, _target_like(blocking, 14), blocking, cancel=cancel)
            except OperationCancelled as exc:
                failure.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.assertTrue(blocking.started.wait(timeout=10.0))
        cancel.set()
        blocking.release.set()
        worker.join(timeout=5.0)
        self.assertFalse(worker.is_alive())
        self.assertTrue(failure and isinstance(failure[0], OperationCancelled))
        # The cancelled advance left the previous state bit-for-bit intact.
        self.assertEqual(density_hash(state.accumulator.density), hash_before)
        np.testing.assert_array_equal(
            state.accumulator.normalization_weights_by_frequency, weights_before
        )
        self.assertEqual(len(state.contributions), contributions_before)
        self.assertEqual(state.current_end, 9)


def _request_like(source, *, target_frame: int, window_frames: int):
    from esw_dfl.heatmap_persistence import (
        PersistenceConfig,
        PersistenceMode,
        PersistenceSourceKey,
        PersistenceWorkRequest,
    )

    config = PersistenceConfig(
        mode=PersistenceMode.ROLLING_EXACT,
        window_frames=window_frames,
        power_min_dbm=POWER_MIN,
        power_max_dbm=POWER_MAX,
        power_bins=POWER_BINS,
    )
    return PersistenceWorkRequest(
        source_key=PersistenceSourceKey("s", "w", "src", "grid"),
        config=config,
        generation=1,
        navigation_generation=1,
        target_frame=target_frame,
        target_timestamp=float(source.timestamps[target_frame]),
        frame_count=source.frame_count,
        frequencies_hz=FREQUENCIES,
    )


def _target_like(source, frame_index: int):
    from esw_dfl.frame_navigation import NavigationReason
    from esw_dfl.heatmap_persistence import PersistenceSourceKey, PersistenceTarget

    return PersistenceTarget(
        source_key=PersistenceSourceKey("s", "w", "src", "grid"),
        frame_index=frame_index,
        timestamp=float(source.timestamps[frame_index]),
        navigation_generation=2,
        persistence_generation=2,
        reason=NavigationReason.API,
    )


if __name__ == "__main__":
    unittest.main()

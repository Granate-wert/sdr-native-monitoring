"""Shared fixtures for the Heatmap persistence test files (review §9.1).

All sources are in-memory: no DFL container, no Qt, deterministic values.
``CountingFrameReader``/``SlowCountingFrameReader``/``BlockingFrameReader``
implement the ``read_frame`` protocol accepted by PersistenceEngine and
``compute_heatmap`` (the slow/blocking variants also provide ``iter_frames``
mirroring the production SpectrogramFrameReader semantics for the worker
cancellation tests).
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.heatmap import HeatmapAccumulator, HeatmapConfig, density_hash  # noqa: F401
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import OperationCancelled, SpectrogramIndex, SpectrogramRow


FREQ_BINS = 8
POWER_MIN = -120.0
POWER_MAX = 0.0
POWER_BINS = 64
SIGNAL_POWER = -50.0
NOISE_POWER = -100.0


def make_frame(
    signal_bin: int | None,
    power_dbm: float = SIGNAL_POWER,
    noise_dbm: float = NOISE_POWER,
    freq_bins: int = FREQ_BINS,
) -> np.ndarray:
    """One deterministic frame: uniform noise plus an optional signal bin."""
    values = np.full(freq_bins, noise_dbm, dtype=np.float32)
    if signal_bin is not None:
        values[signal_bin] = power_dbm
    return values


def make_segment(start: int, end: int, signal_bin: int | None, **kwargs: Any) -> list[np.ndarray]:
    """Frames ``start..end`` (inclusive) with a constant signal bin."""
    return [make_frame(signal_bin, **kwargs) for _ in range(start, end + 1)]


def make_fixture_ab(freq_bins: int = FREQ_BINS, bin_a: int = 2, bin_b: int = 5) -> list[np.ndarray]:
    """Fixture A/B per the review: frames 0..99 bin A, 100..199 bin B, 200..299 noise."""
    frames = (
        make_segment(0, 99, bin_a, freq_bins=freq_bins)
        + make_segment(100, 199, bin_b, freq_bins=freq_bins)
        + make_segment(200, 299, None, freq_bins=freq_bins)
    )
    return frames


class SyntheticPersistenceSource:
    """In-memory frame source with timestamps and a full read log."""

    def __init__(self, frames: list[np.ndarray], timestamps: np.ndarray | None = None) -> None:
        self.frames = [np.asarray(frame, dtype=np.float32) for frame in frames]
        if timestamps is None:
            timestamps = np.arange(len(frames), dtype=np.float64)
        self.timestamps = np.asarray(timestamps, dtype=np.float64)
        self.read_indices: list[int] = []

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def read_frame(self, frame_index: int) -> SpectrogramRow:
        self.read_indices.append(int(frame_index))
        return SpectrogramRow(
            int(frame_index),
            float(self.timestamps[frame_index]),
            self.frames[frame_index].copy(),
        )

    def iter_frames(
        self,
        frame_indices: Any,
        cancel: threading.Event | None = None,
    ) -> Any:
        """Mirror of SpectrogramFrameReader.iter_frames for the worker tests."""
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


class CountingFrameReader(SyntheticPersistenceSource):
    """read_frame protocol with a read counter (engine fixture)."""


class SlowCountingFrameReader(SyntheticPersistenceSource):
    """Delayed reads for the cancellation-latency tests."""

    def __init__(
        self,
        frames: list[np.ndarray],
        timestamps: np.ndarray | None = None,
        delay_s: float = 0.005,
    ) -> None:
        super().__init__(frames, timestamps)
        self.delay_s = delay_s

    def read_frame(self, frame_index: int) -> SpectrogramRow:
        time.sleep(self.delay_s)
        return super().read_frame(frame_index)


class BlockingFrameReader(SyntheticPersistenceSource):
    """Blocks every read until ``release`` is set; ``started`` signals the first read."""

    def __init__(self, frames: list[np.ndarray], timestamps: np.ndarray | None = None) -> None:
        super().__init__(frames, timestamps)
        self.started = threading.Event()
        self.release = threading.Event()

    def read_frame(self, frame_index: int) -> SpectrogramRow:
        self.started.set()
        self.release.wait(timeout=30.0)
        return super().read_frame(frame_index)


def make_index(
    frame_count: int,
    timestamps: np.ndarray | None = None,
    point_count: int = FREQ_BINS,
) -> SpectrogramIndex:
    """SpectrogramIndex with dummy offsets (only frame_count/timestamps are used)."""
    info = SpectrogramInfo(
        key="waterfall",
        title="Waterfall",
        mode="RT",
        measurement="Spectrum",
        measurement_type="Spectrogram",
        source_stream="stream",
        line_count=frame_count,
        point_count=point_count,
        start_hz=100.0,
        stop_hz=100.0 + 100.0 * (point_count - 1),
    )
    if timestamps is None:
        timestamps = np.arange(frame_count, dtype=np.float64)
    return SpectrogramIndex(
        info=info,
        line_indices=np.arange(frame_count, dtype=np.int64),
        timestamps=np.asarray(timestamps, dtype=np.float64),
        offsets=np.arange(frame_count, dtype=np.int64) * 16,
        lengths=np.ones(frame_count, dtype=np.int32),
    )


def offline_density(
    frames: list[np.ndarray],
    start: int,
    end: int,
    config: HeatmapConfig | None = None,
    *,
    power_min: float = POWER_MIN,
    power_max: float = POWER_MAX,
    power_bins: int = POWER_BINS,
) -> np.ndarray:
    """Reference exact density of frames[start..end] built through the accumulator."""
    if config is not None:
        power_min = config.power_min_dbm
        power_max = config.power_max_dbm
        power_bins = config.power_bins
    accumulator = HeatmapAccumulator(
        freq_bins=int(np.asarray(frames[0]).size),
        power_min_dbm=power_min,
        power_max_dbm=power_max,
        power_bins=power_bins,
    )
    for index in range(start, end + 1):
        accumulator.add_frame(frames[index])
    return accumulator.density.copy()

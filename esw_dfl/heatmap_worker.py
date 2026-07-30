"""Streaming Heatmap Spectrum computation over spectrogram frames (Qt-free).

Frames are read in bounded batches via random access (when a
``SpectrogramIndex`` is available) or via the streaming XML row iterator;
the full frame matrix is never materialized. Memory stays bounded at
O(batch + density) regardless of range length. The resolved position list
(and the selected-line set of the streaming fallback) grows linearly with
the range — roughly 0.8–10 MB per 100k frames — bounded independently of
the density matrix.

With an index, frames are read one by one through random access via
``SpectrogramFrameReader.iter_frames``, which performs the cancellation
checks before every blob read and after every decode; ``batch_size`` only
throttles progress reporting — a §23.3 test-suite consideration.

Frequency-grid policy: the canonical grid is the session ``frequencies_hz``
vector; its full content hash is computed once per computation, and every
frame is checked per-frame against the canonical grid width. Any mismatch
logs a ``HEATMAP_GRID_MISMATCH`` diagnostic event and aborts the computation
with ``HeatmapGridMismatchError`` — data from mismatched grids is never
mixed silently.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator

import numpy as np

from .heatmap import (
    HeatmapAccumulator,
    HeatmapConfig,
    HeatmapGridMismatchError,
    HeatmapRangeMode,
    HeatmapResult,
    HeatmapSamplingPolicy,
    frequency_grid_hash,
)
from .models import SpectrogramInfo
from .spectrogram import (
    OperationCancelled,
    SpectrogramFrameReader,
    SpectrogramIndex,
    iter_spectrogram_rows,
)


HeatmapProgress = Callable[[int, int], None]

PROGRESS_MIN_INTERVAL_S = 0.1

_logger = logging.getLogger("esw_dfl.heatmap")


def _log_grid_mismatch(frame_bins: int, grid_bins: int) -> None:
    """Emit the structured diagnostic event required before a grid-mismatch abort."""
    _logger.warning(
        "HEATMAP_GRID_MISMATCH",
        extra={"event": "HEATMAP_GRID_MISMATCH", "frame_bins": frame_bins, "grid_bins": grid_bins},
    )


def resolve_frame_range(
    config: HeatmapConfig,
    frame_count: int,
    current_frame: int | None = None,
) -> tuple[int, int]:
    """Resolve the inclusive ``[start, end]`` frame range for the range mode.

    LAST_N: the window ends at ``current_frame`` (default: last frame) and is
    clipped at the start of the file. CENTERED: ``current_frame +/- N/2``,
    shifted inward at the file boundaries. SELECTED: the configured bounds,
    clipped to the file. FULL: the whole file. EXPONENTIAL_DECAY uses LAST_N
    rolling semantics: the decaying window of N frames ends at
    ``current_frame``.

    Raises ValueError when the resolved range is empty.
    """
    if frame_count <= 0:
        raise ValueError("empty frame range: the spectrogram has no frames")
    last = frame_count - 1
    mode = config.range_mode
    if mode is HeatmapRangeMode.FULL:
        return 0, last
    if mode is HeatmapRangeMode.SELECTED:
        start_cfg = config.frame_start
        end_cfg = config.frame_end
        if start_cfg is None or end_cfg is None:
            raise ValueError("SELECTED range mode requires frame_start and frame_end")
        start = max(0, start_cfg)
        end = min(last, end_cfg)
        if start > end:
            raise ValueError(
                f"empty frame range: selected [{start_cfg}, {end_cfg}] lies outside 0..{last}"
            )
        return start, end
    current = last if current_frame is None else min(max(0, current_frame), last)
    window = config.window_frames
    if mode in (HeatmapRangeMode.LAST_N, HeatmapRangeMode.EXPONENTIAL_DECAY):
        return max(0, current - window + 1), current
    if mode is HeatmapRangeMode.CENTERED:
        start = current - window // 2
        end = start + window - 1
        if start < 0:
            end -= start
            start = 0
        if end > last:
            start = max(0, start - (end - last))
            end = last
        return start, end
    raise ValueError(f"unsupported range mode: {mode}")


def sample_positions(start: int, end: int, max_frames: int) -> np.ndarray:
    """Deterministic uniform subsample of the inclusive range ``[start, end]``.

    Returns at most ``max_frames`` positions (fewer if rounding collapses
    duplicates); the full range is returned unchanged when it already fits.
    """
    count = end - start + 1
    if count <= max(1, max_frames):
        return np.arange(start, end + 1, dtype=np.int64)
    picked = np.rint(np.linspace(start, end, max(1, max_frames))).astype(np.int64)
    return np.unique(picked)


def time_window_positions(
    index: SpectrogramIndex,
    current_frame: int | None,
    time_window_s: float,
) -> np.ndarray:
    """Positions of frames up to ``current_frame`` in ``[t_ref - window, t_ref]``.

    ``t_ref`` is the timestamp of ``current_frame`` (default: the newest
    frame); non-finite reference falls back to the newest finite timestamp.
    Frames positioned after ``current_frame`` are excluded even when their
    timestamps tie with the reference. Raises ValueError when no usable
    timestamps exist or no frame matches.
    """
    frame_count = index.frame_count
    if frame_count <= 0:
        raise ValueError("empty frame range: the spectrogram has no frames")
    current = frame_count - 1 if current_frame is None else min(max(0, current_frame), frame_count - 1)
    timestamps = np.asarray(index.timestamps, dtype=np.float64)
    t_ref = float(timestamps[current])
    if not np.isfinite(t_ref):
        finite_up_to = timestamps[: current + 1][np.isfinite(timestamps[: current + 1])]
        if finite_up_to.size == 0:
            raise ValueError("TIME_WINDOW sampling requires at least one finite timestamp")
        t_ref = float(finite_up_to[-1])
    in_window = np.isfinite(timestamps) & (timestamps >= t_ref - time_window_s) & (timestamps <= t_ref)
    in_window[current + 1 :] = False
    selected = np.flatnonzero(in_window)
    if selected.size == 0:
        raise ValueError("empty frame range: no frames inside the time window")
    return selected.astype(np.int64)


def compute_heatmap(
    source_path: str | Path,
    info: SpectrogramInfo,
    frequencies_hz: np.ndarray,
    config: HeatmapConfig,
    generation: int,
    session_id: str,
    waterfall_id: str,
    source_id: str,
    *,
    current_frame: int | None = None,
    index: SpectrogramIndex | None = None,
    progress: HeatmapProgress | None = None,
    cancel: threading.Event | None = None,
) -> HeatmapResult:
    """Compute a heatmap over the resolved frame range, streaming in batches.

    - SAMPLED_RANGE subsamples ranges larger than ``max_preview_frames`` and
      marks the result ``exact=False``; smaller ranges stay exact.
    - Exponential Decay is intentionally not accepted here. It is a stateful
      data-time half-life model and must run through ``PersistenceEngine``;
      this stateless worker must not silently revive coefficient decay.
    - ``exact=True`` additionally requires that every frame of the resolved
      range was actually processed (missing source lines force
      ``exact=False``).
    - ``progress(processed, total)`` fires at batch boundaries, throttled to
      one call per ~100 ms, plus one final call.
    - ``cancel`` is checked before start, between batches and once more
      before the result is returned; each check raises ``OperationCancelled``.
    - frequency-grid mismatches log ``HEATMAP_GRID_MISMATCH`` and raise
      ``HeatmapGridMismatchError``.

    Without an ``index`` the streaming fallback treats frame numbers as
    source ``Line`` indices of ``iter_spectrogram_rows``.
    """
    if config.range_mode is HeatmapRangeMode.EXPONENTIAL_DECAY:
        raise ValueError(
            "Exponential Decay must be computed by PersistenceEngine with a data-time half-life"
        )
    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    if frequencies.ndim != 1 or frequencies.size == 0:
        raise ValueError("frequency grid must be a non-empty 1-D array")
    frequencies = np.ascontiguousarray(frequencies)
    if info.point_count > 0 and info.point_count != frequencies.size:
        _log_grid_mismatch(frequencies.size, info.point_count)
        raise HeatmapGridMismatchError(
            f"frequency grid of {frequencies.size} bins does not match stream point_count {info.point_count}"
        )
    grid_hash = frequency_grid_hash(frequencies)
    frame_count = index.frame_count if index is not None else info.line_count

    if config.sampling_policy is HeatmapSamplingPolicy.TIME_WINDOW:
        if index is None:
            raise ValueError("TIME_WINDOW sampling requires a SpectrogramIndex")
        assert config.time_window_s is not None  # guaranteed by HeatmapConfig validation
        positions = time_window_positions(index, current_frame, config.time_window_s)
        total_in_range = int(positions.size)
        exact = True
    else:
        start, end = resolve_frame_range(config, frame_count, current_frame)
        total_in_range = end - start + 1
        if config.sampling_policy is HeatmapSamplingPolicy.SAMPLED_RANGE:
            positions = sample_positions(start, end, config.max_preview_frames)
            exact = int(positions.size) == total_in_range
        else:
            positions = np.arange(start, end + 1, dtype=np.int64)
            exact = True
    total = int(positions.size)
    if total == 0:
        raise ValueError("empty frame range: no frames to process")

    accumulator = HeatmapAccumulator(
        freq_bins=int(frequencies.size),
        power_min_dbm=config.power_min_dbm,
        power_max_dbm=config.power_max_dbm,
        power_bins=config.power_bins,
        decay=None,
    )

    def frame_values() -> Generator[np.ndarray, None, None]:
        if index is not None:
            reader = SpectrogramFrameReader(source_path, index)
            try:
                # iter_frames performs the per-frame cancellation checks
                # (before each blob read and after each decode); batch_size
                # below only throttles progress reporting.
                for row in reader.iter_frames(positions, cancel=cancel):
                    yield row.values
            finally:
                reader.close()
        else:
            wanted = {int(position) for position in positions}
            for row in iter_spectrogram_rows(source_path, info, wanted, cancel=cancel):
                yield row.values

    if cancel is not None and cancel.is_set():
        raise OperationCancelled("Операция отменена")
    processed = 0
    last_report = time.perf_counter()
    with closing(frame_values()) as frames:
        for values in frames:
            if values.size != frequencies.size:
                _log_grid_mismatch(int(values.size), int(frequencies.size))
                raise HeatmapGridMismatchError(
                    f"frame width {values.size} does not match the canonical grid of {frequencies.size} bins"
                )
            accumulator.add_frame(values)
            processed += 1
            if processed % config.batch_size == 0:
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("Операция отменена")
                now = time.perf_counter()
                if progress is not None and now - last_report >= PROGRESS_MIN_INTERVAL_S:
                    last_report = now
                    progress(processed, total)
    if cancel is not None and cancel.is_set():
        raise OperationCancelled("Операция отменена")
    if progress is not None:
        progress(processed, total)

    return HeatmapResult(
        density=accumulator.density.copy(),
        frequencies_hz=frequencies.copy(),
        power_axis_dbm=accumulator.power_axis_dbm(),
        processed_frames=processed,
        total_frames_in_range=total_in_range,
        exact=exact and processed == total_in_range,
        sampling_policy=config.sampling_policy,
        config=config,
        generation=generation,
        frequency_grid_hash=grid_hash,
        approximate=False,
        computed_at=datetime.now(timezone.utc).isoformat(),
        normalization_weights_by_frequency=accumulator.normalization_weights_by_frequency.copy(),
    )

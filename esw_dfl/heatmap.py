"""Heatmap Spectrum core: Qt-independent two-dimensional density accumulation.

A Heatmap Spectrum is a density map of "frequency (X) x power (Y)" accumulated
over many spectral frames; cell color encodes how often a power value occurred
at a given frequency bin. It is not a waterfall (waterfall maps frequency x
time; a heatmap maps frequency x power).

Memory budgets (reference grid: 1001 frequency bins x 256 power bins):

- exact density matrix: ``power_bins x freq_bins x uint32`` ~= 1 MiB, plus the
  per-frequency valid counts V(k) (``freq_bins x uint32``);
- rolling-window ring buffer: ``window_frames x freq_bins x uint16``
  (500 x 1001 x 2 B ~= 1 MB for the default 500-frame window); the ring
  stores compact per-frame power-bin indices, never full float frames;
- exponential-decay density: ``power_bins x freq_bins x float64`` ~= 2 MiB,
  plus the float64 effective weights W(k) (``freq_bins x float64``).

All structures are bounded: memory never grows with the number of processed
frames.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Sequence

import numpy as np

if TYPE_CHECKING:
    from .heatmap_persistence import FrameContribution


ALLOWED_POWER_BINS: tuple[int, ...] = (64, 128, 256, 512)

_RING_SKIP = 0xFFFF  # uint16 sentinel for skipped (non-finite) samples in the ring buffer

_logger = logging.getLogger("esw_dfl.heatmap")


class HeatmapGridMismatchError(Exception):
    """Raised when a frame or grid does not match the canonical frequency grid.

    Data from mismatched grids is never mixed silently.
    """


class HeatmapRangeMode(StrEnum):
    LAST_N = "last_n"
    CENTERED = "centered"
    SELECTED = "selected"
    FULL = "full"
    EXPONENTIAL_DECAY = "exponential_decay"


class HeatmapSamplingPolicy(StrEnum):
    FULL_RANGE = "full_range"  # exact: every frame of the range
    TIME_WINDOW = "time_window"  # frames selected by a timestamp window
    SAMPLED_RANGE = "sampled_range"  # deterministic uniform subsample (preview)


class HeatmapNormalization(StrEnum):
    COUNT = "count"
    PROBABILITY = "probability"
    LOG_DENSITY = "log_density"


@dataclass(frozen=True, slots=True)
class HeatmapConfig:
    """Immutable heatmap computation configuration.

    Visual-only styling (palette, opacity, interpolation) is intentionally
    absent: it never affects density computation or cache identity.
    """

    range_mode: HeatmapRangeMode = HeatmapRangeMode.LAST_N
    window_frames: int = 500
    frame_start: int | None = None
    frame_end: int | None = None
    time_window_s: float | None = None
    power_min_dbm: float = -120.0
    power_max_dbm: float = 0.0
    power_bins: int = 256
    normalization: HeatmapNormalization = HeatmapNormalization.LOG_DENSITY
    # Deprecated compatibility field. Exponential Decay is computed only by
    # PersistenceEngine from data-time half-life, never by compute_heatmap().
    decay: float = 0.95
    sampling_policy: HeatmapSamplingPolicy = HeatmapSamplingPolicy.FULL_RANGE
    max_preview_frames: int = 2000
    batch_size: int = 2000

    def __post_init__(self) -> None:
        if not (np.isfinite(self.power_min_dbm) and np.isfinite(self.power_max_dbm)):
            raise ValueError("power range bounds must be finite")
        if self.power_min_dbm >= self.power_max_dbm:
            raise ValueError(
                f"power_min_dbm ({self.power_min_dbm}) must be below power_max_dbm ({self.power_max_dbm})"
            )
        if self.power_bins not in ALLOWED_POWER_BINS:
            raise ValueError(f"power_bins must be one of {ALLOWED_POWER_BINS}, got {self.power_bins}")
        if self.window_frames < 1:
            raise ValueError(f"window_frames must be >= 1, got {self.window_frames}")
        if not 0.0 <= self.decay <= 1.0:
            raise ValueError(f"decay must be within [0, 1], got {self.decay}")
        if self.max_preview_frames < 1:
            raise ValueError(f"max_preview_frames must be >= 1, got {self.max_preview_frames}")
        if self.batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {self.batch_size}")
        if self.range_mode is HeatmapRangeMode.SELECTED:
            if self.frame_start is None or self.frame_end is None:
                raise ValueError("SELECTED range mode requires frame_start and frame_end")
            if self.frame_start < 0 or self.frame_end < 0:
                raise ValueError("selected frame indices must be non-negative")
            if self.frame_start > self.frame_end:
                raise ValueError(
                    f"empty selected range: frame_start ({self.frame_start}) > frame_end ({self.frame_end})"
                )
        if self.sampling_policy is HeatmapSamplingPolicy.TIME_WINDOW:
            if self.time_window_s is None or self.time_window_s <= 0:
                raise ValueError("TIME_WINDOW sampling requires a positive time_window_s")


@dataclass(frozen=True, slots=True)
class HeatmapRequest:
    """Identity of one heatmap computation request."""

    session_id: str
    waterfall_id: str
    source_id: str
    config: HeatmapConfig
    generation: int
    frequency_grid_hash: str


def frequency_grid_hash(frequencies_hz: Sequence[float] | np.ndarray) -> str:
    """Stable content hash of a frequency grid (shape + float64 values)."""
    arr = np.ascontiguousarray(np.asarray(frequencies_hz, dtype=np.float64))
    digest = hashlib.sha256()
    digest.update(f"shape={arr.shape};dtype=float64;".encode("ascii"))
    digest.update(arr.tobytes())
    return digest.hexdigest()


def density_hash(density: np.ndarray) -> str:
    """Content hash of a raw density matrix.

    The hash is computed over the unnormalized counts, so it is independent
    of normalization mode, palette and opacity.
    """
    arr = np.ascontiguousarray(density)
    digest = hashlib.sha256()
    digest.update(f"shape={arr.shape};dtype={arr.dtype};".encode("ascii"))
    digest.update(arr.tobytes())
    return digest.hexdigest()


def frequency_bin_edges(
    centers_hz: Sequence[float] | np.ndarray,
    *,
    single_bin_span_hz: float | None = None,
) -> tuple[float, float]:
    """Physical [left, right] edges of a uniform frequency grid (§3.8).

    Uniform ascending grid: ``left = centers[0] - step/2``,
    ``right = centers[-1] + step/2``. A descending grid is mirrored to
    ascending once (documented behavior). A non-uniform grid is rejected:
    the ImageItem affine transform cannot represent it, so the caller must
    regrid explicitly instead of pretending the mapping is exact. A single
    bin requires an explicit ``single_bin_span_hz`` from source metadata —
    its width is never invented.
    """
    centers = np.asarray(centers_hz, dtype=np.float64).ravel()
    if centers.size == 0 or not np.all(np.isfinite(centers)):
        raise ValueError("frequency grid must be non-empty and finite")
    if centers.size == 1:
        if single_bin_span_hz is None or single_bin_span_hz <= 0:
            raise ValueError("single-bin frequency grid requires an explicit single_bin_span_hz")
        half = single_bin_span_hz / 2.0
        return float(centers[0] - half), float(centers[0] + half)
    diffs = np.diff(centers)
    if np.all(diffs < 0):
        centers_asc = centers[::-1].copy()  # mirror to ascending once (documented)
    else:
        centers_asc = centers
    diffs = np.diff(centers_asc)
    if not np.all(diffs > 0):
        raise ValueError("frequency grid must be strict monotonic")
    step = float(diffs[0])
    if not np.allclose(diffs, step, rtol=1e-9, atol=abs(step) * 1e-9):
        raise ValueError(
            "non-uniform frequency grid is unsupported by the ImageItem transform; regrid explicitly"
        )
    return float(centers_asc[0] - step / 2.0), float(centers_asc[-1] + step / 2.0)


@dataclass(frozen=True, slots=True)
class HeatmapResult:
    """Computed density snapshot.

    ``exact`` is True only when every frame of the resolved range contributed
    with full weight (no subsampling, no decay approximation). ``approximate``
    marks the exponential-decay persistence model, which is explicitly an
    approximation rather than an exact window of N frames. ``computed_at`` is
    the UTC ISO timestamp of the computation itself (set by the worker), not
    of a later export.
    """

    density: np.ndarray
    frequencies_hz: np.ndarray
    power_axis_dbm: np.ndarray
    processed_frames: int
    total_frames_in_range: int
    exact: bool
    sampling_policy: HeatmapSamplingPolicy
    config: HeatmapConfig
    generation: int
    frequency_grid_hash: str
    approximate: bool = False
    computed_at: str | None = None  # UTC ISO timestamp of the actual computation
    # Per-frequency denominator of Probability: valid counts V(k) for exact
    # (uint32) or effective weights W(k) for decay (float64). None for results
    # built by hand without engine/accumulator provenance.
    normalization_weights_by_frequency: np.ndarray | None = None

    def normalized(self, mode: HeatmapNormalization | None = None) -> np.ndarray:
        """Return the density as a new float64 array, normalized per ``mode``.

        The result never shares memory with the internal density matrix, so
        mutating it cannot corrupt the stored snapshot.

        ``mode`` defaults to ``config.normalization``; passing an explicit
        mode restyles the same raw density without recomputation.

        COUNT: raw hit counts. PROBABILITY: counts divided per frequency
        column by V(k)/W(k) when ``normalization_weights_by_frequency`` is
        available (zero denominator yields 0); otherwise falls back to the
        legacy global denominator ``processed_frames``. LOG_DENSITY:
        log10(1 + counts).
        """
        density = np.array(self.density, dtype=np.float64)  # always a copy
        if mode is None:
            mode = self.config.normalization
        if mode is HeatmapNormalization.COUNT:
            return density
        if mode is HeatmapNormalization.PROBABILITY:
            weights = self.normalization_weights_by_frequency
            if weights is not None:
                weights64 = np.asarray(weights, dtype=np.float64)
                probability = np.zeros_like(density)
                np.divide(density, weights64, out=probability, where=weights64 > 0.0)
                return probability
            if self.processed_frames <= 0:
                return np.zeros_like(density)
            return density / float(self.processed_frames)
        return np.log10(1.0 + density)


def is_stale(result: HeatmapResult, current_generation: int) -> bool:
    """Generation guard: True when the result belongs to a superseded request."""
    return result.generation != current_generation


class HeatmapAccumulator:
    """Incremental density accumulator for one fixed frequency/power grid.

    Binning policy: finite power values are clipped into the nearest boundary
    bin, i.e. values below ``power_min_dbm`` land in bin 0 and values at or
    above ``power_max_dbm`` land in the top bin (they still count). Non-finite
    values (NaN, +/-inf) are skipped entirely.

    Binning note: the bin index is ``floor((p - power_min) / bin_width)``;
    with a bin width that is not exact in binary floating point, a value
    exactly on a bin edge may round down into bin ``k - 1``. The effect is
    limited to exact edge values and does not accumulate across frames.

    Three modes:

    - plain exact accumulation (default): uint32 density, unbounded frame count;
    - exact rolling window (``window_frames=N``): a ring buffer of per-frame
      uint16 power-bin indices (``N x freq_bins x 2 B``; 500 x 1001 ~= 1 MB)
      allows exact removal of the oldest frame without storing float frames;
    - exponential decay (``decay=d``): float64 density updated as
      ``density = density * d + contribution``; an approximate persistence
      model, not an exact window.
    """

    def __init__(
        self,
        freq_bins: int,
        power_min_dbm: float,
        power_max_dbm: float,
        power_bins: int = 256,
        *,
        window_frames: int | None = None,
        decay: float | None = None,
    ) -> None:
        if freq_bins < 1:
            raise ValueError(f"freq_bins must be >= 1, got {freq_bins}")
        if not (np.isfinite(power_min_dbm) and np.isfinite(power_max_dbm)):
            raise ValueError("power range bounds must be finite")
        if power_min_dbm >= power_max_dbm:
            raise ValueError("power_min_dbm must be below power_max_dbm")
        # uint16 ring storage with the 0xFFFF sentinel requires power_bins - 1 < 0xFFFF.
        if not 1 <= power_bins <= 0xFFFE:
            raise ValueError(f"power_bins must be within [1, 65534], got {power_bins}")
        if window_frames is not None and window_frames < 1:
            raise ValueError(f"window_frames must be >= 1, got {window_frames}")
        if decay is not None and not 0.0 <= decay <= 1.0:
            raise ValueError(f"decay must be within [0, 1], got {decay}")
        if window_frames is not None and decay is not None:
            raise ValueError("window_frames and decay are mutually exclusive")
        self.freq_bins = int(freq_bins)
        self.power_min_dbm = float(power_min_dbm)
        self.power_max_dbm = float(power_max_dbm)
        self.power_bins = int(power_bins)
        self.window_frames = window_frames
        self.decay = decay
        self._bin_width = (self.power_max_dbm - self.power_min_dbm) / self.power_bins
        if decay is not None:
            self._density = np.zeros((self.power_bins, self.freq_bins), dtype=np.float64)
            # float64 effective weights W(k) of the decayed Probability.
            self._weights = np.zeros(self.freq_bins, dtype=np.float64)
            self._ring: np.ndarray | None = None
        else:
            self._density = np.zeros((self.power_bins, self.freq_bins), dtype=np.uint32)
            # uint32 valid observation counts V(k) of the exact Probability.
            self._weights = np.zeros(self.freq_bins, dtype=np.uint32)
            self._ring = (
                np.zeros((window_frames, self.freq_bins), dtype=np.uint16) if window_frames is not None else None
            )
        self._ring_pos = 0
        self._ring_filled = 0
        self._frames_added = 0
        self._freq_index = np.arange(self.freq_bins, dtype=np.int64)

    @property
    def density(self) -> np.ndarray:
        """Internal density matrix (uint32 exact, float64 decay); copy if a snapshot is needed."""
        return self._density

    @property
    def normalization_weights_by_frequency(self) -> np.ndarray:
        """Per-frequency denominator: uint32 V(k) for exact, float64 W(k) for decay."""
        return self._weights

    @property
    def ring_buffer(self) -> np.ndarray | None:
        """Internal ring buffer of per-frame uint16 power-bin indices (diagnostics/tests)."""
        return self._ring

    @property
    def frame_count(self) -> int:
        """Frames currently represented in the density.

        For a rolling window this is ``min(frames_added, window_frames)``; for
        decay mode it is the total number of added frames (an approximate
        persistence model has no exact window membership).
        """
        if self._ring is not None:
            return self._ring_filled
        return self._frames_added

    @property
    def approximate(self) -> bool:
        """True for the exponential-decay persistence approximation."""
        return self.decay is not None

    def power_to_bin(self, power_values: Sequence[float] | np.ndarray) -> np.ndarray:
        """Map power values (dBm) to bin indices; -1 marks non-finite inputs.

        Finite values are clipped into ``[0, power_bins - 1]``: ``power_min``
        maps exactly to bin 0, ``power_max`` maps to the top bin.
        """
        values = np.asarray(power_values, dtype=np.float64).ravel()
        finite = np.isfinite(values)
        bins = np.full(values.shape, -1, dtype=np.int64)
        if np.any(finite):
            scaled = np.floor((values[finite] - self.power_min_dbm) / self._bin_width)
            bins[finite] = np.clip(scaled, 0, self.power_bins - 1).astype(np.int64)
        return bins

    def power_axis_dbm(self) -> np.ndarray:
        """Bin-center power coordinates of the density Y axis (dBm)."""
        return self.power_min_dbm + (np.arange(self.power_bins, dtype=np.float64) + 0.5) * self._bin_width

    def _check_grid_width(self, values: np.ndarray) -> None:
        if values.size != self.freq_bins:
            _logger.warning(
                "HEATMAP_GRID_MISMATCH",
                extra={
                    "event": "HEATMAP_GRID_MISMATCH",
                    "frame_bins": int(values.size),
                    "grid_bins": self.freq_bins,
                },
            )
            raise HeatmapGridMismatchError(
                f"frame width {values.size} does not match the canonical grid of {self.freq_bins} bins"
            )

    def quantize_rows_into(
        self,
        power_rows: Sequence[Sequence[float] | np.ndarray],
        destination: np.ndarray,
    ) -> None:
        """Quantize a bounded row batch directly into caller-owned uint16 storage.

        ``destination`` is normally a contiguous slice of the exact Rolling
        ring.  No per-frame uint16 arrays are allocated: non-finite samples use
        the sentinel and finite dBm values are clipped to the canonical grid.
        """
        if len(power_rows) == 0:
            if destination.shape != (0, self.freq_bins):
                raise ValueError("empty rows require an empty destination with the canonical width")
            return
        values = np.asarray(power_rows, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] != self.freq_bins:
            width = values.shape[1] if values.ndim == 2 else values.size
            self._check_grid_width(np.empty(int(width), dtype=np.float64))
            raise HeatmapGridMismatchError("batched spectrum rows must be two-dimensional")
        if destination.dtype != np.uint16 or destination.shape != values.shape or not destination.flags.writeable:
            raise ValueError("destination must be a writable uint16 matrix matching the spectral rows")
        destination[...] = _RING_SKIP
        finite = np.isfinite(values)
        if bool(np.all(finite)):
            # ESW spectrum frames are normally entirely finite. Keep this
            # dominant path contiguous: the former boolean gather allocated
            # several full-size temporaries before every 128-frame batch.
            scaled = values - self.power_min_dbm
            scaled /= self._bin_width
            np.floor(scaled, out=scaled)
            np.clip(scaled, 0, self.power_bins - 1, out=scaled)
            destination[...] = scaled
        elif np.any(finite):
            scaled = np.floor((values[finite] - self.power_min_dbm) / self._bin_width)
            destination[finite] = np.clip(scaled, 0, self.power_bins - 1).astype(np.uint16)

    def make_contribution(
        self,
        frame_index: int,
        timestamp: float | None,
        power_values: Sequence[float] | np.ndarray,
        *,
        bin_storage: np.ndarray | None = None,
    ) -> FrameContribution:
        """Build one contribution, optionally as a zero-copy view into a ring row."""
        from .heatmap_persistence import FrameContribution

        if bin_storage is None:
            bins = np.empty(self.freq_bins, dtype=np.uint16)
        else:
            bins = np.asarray(bin_storage)
            if bins.shape != (self.freq_bins,):
                raise ValueError("bin_storage must be one uint16 row of the canonical frequency width")
        self.quantize_rows_into([power_values], bins.reshape(1, -1))
        return FrameContribution(frame_index=int(frame_index), timestamp=timestamp, bin_indices=bins)

    def make_contributions_batch(
        self,
        frame_indices: Sequence[int],
        timestamps: Sequence[float | None],
        power_rows: Sequence[Sequence[float] | np.ndarray],
        *,
        bin_storage: np.ndarray | None = None,
    ) -> list[FrameContribution]:
        """Quantize same-grid spectra, optionally directly into Rolling ring rows.

        When ``bin_storage`` is supplied its rows are retained by the returned
        contributions as read-only views.  The owner remains the preallocated
        ring; callers must evict a contribution before reusing its slot.
        """
        from .heatmap_persistence import FrameContribution

        if not (len(frame_indices) == len(timestamps) == len(power_rows)):
            raise ValueError("frame_indices, timestamps and power_rows must have equal lengths")
        count = len(power_rows)
        if not count:
            return []
        if bin_storage is None:
            storage = np.empty((count, self.freq_bins), dtype=np.uint16)
        else:
            storage = np.asarray(bin_storage)
            if storage.shape != (count, self.freq_bins):
                raise ValueError("bin_storage shape must match the contribution batch")
        self.quantize_rows_into(power_rows, storage)
        return [
            FrameContribution(
                frame_index=int(frame_index),
                timestamp=timestamp,
                bin_indices=storage[row_index],
            )
            for row_index, (frame_index, timestamp) in enumerate(zip(frame_indices, timestamps, strict=True))
        ]
    def _check_contribution(self, contribution: FrameContribution) -> None:
        if contribution.bin_indices.size != self.freq_bins:
            _logger.warning(
                "HEATMAP_GRID_MISMATCH",
                extra={
                    "event": "HEATMAP_GRID_MISMATCH",
                    "frame_bins": int(contribution.bin_indices.size),
                    "grid_bins": self.freq_bins,
                },
            )
            raise HeatmapGridMismatchError(
                f"contribution width {contribution.bin_indices.size} does not match "
                f"the canonical grid of {self.freq_bins} bins"
            )

    def add_contribution(self, contribution: FrameContribution) -> None:
        """Atomically add one frame's contribution to density and V(k)/W(k)."""
        self._check_contribution(contribution)
        bins = contribution.bin_indices
        valid = bins != _RING_SKIP
        if not np.any(valid):
            return
        columns = self._freq_index[valid]
        # ``columns`` contains each frequency column at most once, therefore
        # every (power_bin, frequency_column) pair is unique. Advanced
        # indexing is consequently exact here and avoids ``np.add.at``'s
        # duplicate-index bookkeeping on the per-frame hot path.
        if self.decay is not None:
            self._density[bins[valid], columns] += 1.0
            self._weights[columns] += 1.0
        else:
            self._density[bins[valid], columns] += 1
            self._weights[columns] += 1

    def subtract_contribution(self, contribution: FrameContribution) -> None:
        """Atomically subtract one frame's contribution (exact uint32 only).

        Raises RuntimeError for a decay accumulator, and when a density or
        weight cell to be decremented is already zero — that indicates a
        corrupted add/subtract pairing, never a legitimate state.
        """
        if self.decay is not None:
            raise RuntimeError(
                "subtract_contribution is only defined for the exact uint32 accumulator"
            )
        self._check_contribution(contribution)
        bins = contribution.bin_indices
        valid = bins != _RING_SKIP
        if not np.any(valid):
            return
        columns = self._freq_index[valid]
        if np.any(self._density[bins[valid], columns] == 0):
            bad = columns[self._density[bins[valid], columns] == 0]
            raise RuntimeError(
                f"subtract_contribution would underflow density: frame {contribution.frame_index}, "
                f"columns {bad[:8].tolist()}"
            )
        if np.any(self._weights[columns] == 0):
            raise RuntimeError(
                f"subtract_contribution would underflow weights: frame {contribution.frame_index}"
            )
        np.subtract.at(self._density, (bins[valid], columns), 1)
        np.subtract.at(self._weights, columns, 1)

    def _counts_for_exact_bin_matrix(self, matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Reduce one exact uint16 matrix slice to density and column weights."""
        bins = np.asarray(matrix)
        if bins.ndim != 2 or bins.shape[1] != self.freq_bins or bins.dtype != np.uint16:
            raise ValueError("exact bin matrix must be uint16 with the canonical frequency width")
        if not bins.shape[0]:
            return np.zeros_like(self._density), np.zeros(self.freq_bins, dtype=np.uint32)
        valid = bins != _RING_SKIP
        columns = np.broadcast_to(self._freq_index, bins.shape)
        flat = bins[valid].astype(np.intp) * self.freq_bins + columns[valid]
        density = np.bincount(
            flat,
            minlength=self.power_bins * self.freq_bins,
        ).reshape(self.power_bins, self.freq_bins).astype(np.uint32, copy=False)
        return density, valid.sum(axis=0, dtype=np.uint32)

    def _counts_for_exact_bin_matrices(
        self,
        matrices: Sequence[np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Reduce bounded rows of exact bin indices without frame copies."""
        if not matrices:
            return np.zeros_like(self._density), np.zeros(self.freq_bins, dtype=np.uint32)
        if len(matrices) == 1:
            return self._counts_for_exact_bin_matrix(matrices[0])
        density = np.zeros_like(self._density)
        weights = np.zeros(self.freq_bins, dtype=np.uint32)
        for matrix in matrices:
            matrix_density, matrix_weights = self._counts_for_exact_bin_matrix(matrix)
            density += matrix_density
            weights += matrix_weights
        return density, weights
    def apply_exact_bin_matrices(
        self,
        addition_matrices: Sequence[np.ndarray],
        removal_matrices: Sequence[np.ndarray],
    ) -> None:
        """Apply exact bounded uint16 matrix slices without ``np.stack``.

        Ring-owned contiguous slices are passed directly from the rolling
        engine. The only temporary data are the required aggregate density
        counts, not one allocation per ``FrameContribution``.
        """
        if self.decay is not None:
            raise RuntimeError("apply_exact_bin_matrices is only defined for the exact accumulator")
        remove_density, remove_weights = self._counts_for_exact_bin_matrices(removal_matrices)
        if np.any(remove_density > self._density) or np.any(remove_weights > self._weights):
            raise RuntimeError("apply_exact_bin_matrices would underflow exact accumulator")
        self._density -= remove_density
        self._weights -= remove_weights
        add_density, add_weights = self._counts_for_exact_bin_matrices(addition_matrices)
        self._density += add_density
        self._weights += add_weights

    def apply_exact_batch(
        self,
        additions: Sequence["FrameContribution"],
        removals: Sequence["FrameContribution"],
    ) -> None:
        """Compatibility wrapper for object-owned exact contributions."""
        for item in (*additions, *removals):
            self._check_contribution(item)
        addition_matrix = (
            np.stack([item.bin_indices for item in additions], axis=0)
            if additions
            else np.empty((0, self.freq_bins), dtype=np.uint16)
        )
        removal_matrix = (
            np.stack([item.bin_indices for item in removals], axis=0)
            if removals
            else np.empty((0, self.freq_bins), dtype=np.uint16)
        )
        self.apply_exact_bin_matrices((addition_matrix,), (removal_matrix,))
    def apply_decay_factor(self, factor: float) -> None:
        """Multiply density and effective weights by ``factor`` (decay mode only)."""
        if self.decay is None:
            raise RuntimeError("apply_decay_factor is only defined for the decay accumulator")
        if not 0.0 <= factor <= 1.0:
            raise ValueError(f"decay factor must be within [0, 1], got {factor}")
        self._density *= factor
        self._weights *= factor

    def add_frame(self, power_values: Sequence[float] | np.ndarray) -> None:
        """Accumulate one spectral frame (one power-bin hit per frequency bin).

        Compatibility wrapper over make_contribution/add_contribution; the
        fixed-coefficient decay and ring-buffer semantics are unchanged.
        """
        contribution = self.make_contribution(self._frames_added, None, power_values)
        if self.decay is not None:
            self.apply_decay_factor(self.decay)  # decays density AND weights
            self.add_contribution(contribution)
        elif self._ring is not None:
            self._push_ring(contribution)
        else:
            self.add_contribution(contribution)
        self._frames_added += 1

    def _push_ring(self, contribution: FrameContribution) -> None:
        """Insert one frame into the ring buffer, exactly removing the oldest one."""
        assert self._ring is not None
        window = self._ring.shape[0]
        bins = contribution.bin_indices  # uint16; INVALID_POWER_BIN == _RING_SKIP
        if self._ring_filled == window:
            old = self._ring[self._ring_pos]
            old_valid = old != _RING_SKIP
            old_columns = self._freq_index[old_valid]
            np.subtract.at(self._density, (old[old_valid], old_columns), 1)
            np.subtract.at(self._weights, old_columns, 1)
        self._ring[self._ring_pos] = bins
        self._ring_pos = (self._ring_pos + 1) % window
        self._ring_filled = min(self._ring_filled + 1, window)
        valid = bins != _RING_SKIP
        columns = self._freq_index[valid]
        np.add.at(self._density, (bins[valid], columns), 1)
        np.add.at(self._weights, columns, 1)

    def clear(self) -> None:
        """Reset accumulated density; allocated buffers (and their budget) are kept."""
        self._density[...] = 0
        self._weights[...] = 0
        self._ring_pos = 0
        self._ring_filled = 0
        self._frames_added = 0

    def memory_bytes(self) -> int:
        """Estimated resident memory: density matrix + ring buffer + index scratch."""
        total = int(self._density.nbytes) + int(self._weights.nbytes) + int(self._freq_index.nbytes)
        if self._ring is not None:
            total += int(self._ring.nbytes)
        return total


@dataclass(frozen=True, slots=True)
class HeatmapCacheKey:
    """Cache identity of a computed density.

    The key covers every parameter that changes the accumulated counts:
    source identity, range mode and its parameters (``window_frames``,
    ``decay``, ``time_window_s``, ``max_preview_frames``), the frame bounds,
    the sampling policy, the power grid and the frequency grid hash.

    Normalization, palette, opacity and interpolation are deliberately not
    part of the key: cached entries hold raw, unnormalized density. The
    optional ``density_hash`` namespaces entries by content when a caller
    recomputes a density over otherwise identical parameters.
    """

    session_id: str
    waterfall_id: str
    source_id: str
    range_mode: HeatmapRangeMode
    window_frames: int
    decay: float
    time_window_s: float | None
    max_preview_frames: int
    frame_start: int | None
    frame_end: int | None
    sampling_policy: HeatmapSamplingPolicy
    power_min_dbm: float
    power_max_dbm: float
    power_bins: int
    frequency_grid_hash: str
    density_hash: str = ""


@dataclass(slots=True)
class _CacheEntry:
    result: HeatmapResult
    size_bytes: int


class HeatmapCache:
    """Memory-bounded LRU cache for heatmap results.

    Budget accounting counts the density matrix plus the frequency/power axes
    of each entry. Entries larger than the whole budget are never stored.
    Hit/miss counters are cumulative diagnostics and survive ``clear()``.

    Not thread-safe: intended for use from the GUI thread only.
    """

    DEFAULT_BUDGET_BYTES = 64 * 1024 * 1024

    def __init__(self, budget_bytes: int = DEFAULT_BUDGET_BYTES) -> None:
        if budget_bytes <= 0:
            raise ValueError(f"budget_bytes must be positive, got {budget_bytes}")
        self.budget_bytes = int(budget_bytes)
        self._entries: OrderedDict[HeatmapCacheKey, _CacheEntry] = OrderedDict()
        self._size_bytes = 0
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(
        request: HeatmapRequest,
        *,
        frame_start: int | None = None,
        frame_end: int | None = None,
        density_hash: str = "",
    ) -> HeatmapCacheKey:
        """Build a cache key from a request; resolved range bounds may be overridden."""
        config = request.config
        return HeatmapCacheKey(
            session_id=request.session_id,
            waterfall_id=request.waterfall_id,
            source_id=request.source_id,
            range_mode=config.range_mode,
            window_frames=config.window_frames,
            decay=config.decay,
            time_window_s=config.time_window_s,
            max_preview_frames=config.max_preview_frames,
            frame_start=config.frame_start if frame_start is None else frame_start,
            frame_end=config.frame_end if frame_end is None else frame_end,
            sampling_policy=config.sampling_policy,
            power_min_dbm=config.power_min_dbm,
            power_max_dbm=config.power_max_dbm,
            power_bins=config.power_bins,
            frequency_grid_hash=request.frequency_grid_hash,
            density_hash=density_hash,
        )

    @property
    def total_size_bytes(self) -> int:
        return self._size_bytes

    @property
    def cache_hit_ratio(self) -> float:
        lookups = self.hits + self.misses
        return self.hits / lookups if lookups else 0.0

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: HeatmapCacheKey) -> HeatmapResult | None:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        self._entries.move_to_end(key)
        return entry.result

    def put(self, key: HeatmapCacheKey, result: HeatmapResult) -> bool:
        """Store a result, evicting least-recently-used entries to fit the budget.

        Returns False (without storing) when the entry alone exceeds the
        budget; any existing entry under ``key`` is kept in that case.
        """
        size = int(
            result.density.nbytes + result.frequencies_hz.nbytes + result.power_axis_dbm.nbytes
        )
        if size > self.budget_bytes:
            return False
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._size_bytes -= existing.size_bytes
        while self._entries and self._size_bytes + size > self.budget_bytes:
            _evicted_key, evicted = self._entries.popitem(last=False)
            self._size_bytes -= evicted.size_bytes
        self._entries[key] = _CacheEntry(result, size)
        self._size_bytes += size
        return True

    def invalidate_session(self, session_id: str) -> int:
        """Drop every entry of a session; returns the number of removed entries."""
        doomed = [key for key in self._entries if key.session_id == session_id]
        for key in doomed:
            entry = self._entries.pop(key)
            self._size_bytes -= entry.size_bytes
        return len(doomed)

    def clear(self) -> None:
        """Remove all entries; cumulative hit/miss counters are kept."""
        self._entries.clear()
        self._size_bytes = 0




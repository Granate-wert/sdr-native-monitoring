"""Qt-free contracts and stateful engine for Heatmap Spectrum persistence.

This module owns the rolling/persistence state that the bounded rebuild
worker (``compute_heatmap``) cannot hold between requests:

- :class:`RollingExactState` keeps the exact contribution of every frame of
  the current window in a sorted deque, so a sequential ``F -> F + 1`` update
  costs one add and one subtract instead of re-reading the whole window;
- :class:`DecayState` applies the data-time half-life factor
  ``alpha_i = exp(-ln(2) * dt_i / T_half)`` per entered frame, never wall
  clock, so Pause leaves density and effective weights unchanged.

Engine boundaries (P0): the engine performs no Qt work, owns no reader
lifecycle (the caller passes a job-local reader with ``read_frame``) and
never touches widgets. Cancellation raises :class:`OperationCancelled` before
every read and after every decode; a cancelled advance discards its staged
contributions and leaves the previous state bit-for-bit intact (no atomic
commit happens after cancel).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from threading import Event
from typing import TYPE_CHECKING, Protocol

import numpy as np

from .heatmap import HeatmapAccumulator, HeatmapNormalization, HeatmapSamplingPolicy
from .spectrogram import OperationCancelled, SpectrogramRow

if TYPE_CHECKING:
    from .frame_navigation import NavigationReason


class PersistenceMode(StrEnum):
    ROLLING_EXACT = "rolling_exact"
    EXPONENTIAL_DECAY = "exponential_decay"
    SELECTED_RANGE = "selected_range"
    FULL_RECORDING = "full_recording"


class WindowUnit(StrEnum):
    FRAMES = "frames"
    SECONDS = "seconds"


class PersistencePhase(StrEnum):
    DISABLED = "disabled"
    EMPTY = "empty"
    REBUILDING = "rebuilding"
    UPDATING = "updating"
    CURRENT = "current"
    STALE = "stale"
    CANCELLED = "cancelled"
    ERROR = "error"
    SHUTTING_DOWN = "shutting_down"


class RebuildReason(StrEnum):
    INITIAL = "initial"
    SEEK = "seek"
    REVERSE = "reverse"
    LOOP = "loop"
    CONFIG_CHANGE = "config_change"
    CONTEXT_CHANGE = "context_change"
    STOP = "stop"
    CACHE_MISS = "cache_miss"
    PLAYBACK_GAP = "playback_gap"


class ColorScaleMode(StrEnum):
    AUTO_CURRENT = "auto_current"
    FIXED = "fixed"
    PERCENTILE = "percentile"
    SMOOTHED_AUTO = "smoothed_auto"


@dataclass(frozen=True, slots=True)
class PersistenceSourceKey:
    session_id: str
    waterfall_id: str
    source_id: str
    frequency_grid_hash: str

HEATMAP_RENDER_WINDOW_SAFETY_FACTOR = 1.5


@dataclass(frozen=True, slots=True)
class HeatmapRenderBudget:
    """Per-source rolling-window lower bound for a throttled Heatmap renderer.

    ``required_frames_per_refresh`` is the number of source frames that can
    arrive during one UI refresh at the selected timestamp-playback speed.
    ``recommended_window_frames`` applies the engineering safety factor so
    ordinary timer/render jitter still leaves an overlap for the exact
    add/subtract path. ``recommended_window_seconds`` is the equivalent
    timestamp-window floor for the Time (s) UI mode.
    """

    render_fps: int
    playback_speed: float
    instrument_sweep_time_s: float | None
    recorded_period_s: float | None
    effective_frame_period_s: float | None
    required_frames_per_refresh: int
    recommended_window_frames: int
    recommended_window_seconds: float | None
    safety_factor: float = HEATMAP_RENDER_WINDOW_SAFETY_FACTOR

    @property
    def available(self) -> bool:
        return self.effective_frame_period_s is not None


def heatmap_render_budget(
    render_fps: int,
    *,
    instrument_sweep_time_s: float | None = None,
    recorded_period_s: float | None = None,
    playback_speed: float = 1.0,
    safety_factor: float = HEATMAP_RENDER_WINDOW_SAFETY_FACTOR,
) -> HeatmapRenderBudget:
    """Calculate the exact rolling-window floor for one source and UI rate.

    The strict source period is ``min(T_sweep, T_recorded)`` when both are
    available: the shorter valid period is the fastest data rate that the
    analytics pipeline must tolerate. This intentionally does not infer a
    period from point count or RBW; the ESW records the selected sweep time.
    At timestamp-playback speed ``M`` the required source-frame overlap is
    ``ceil(M / (fps * period))``. Callers pass ``M = 1`` in no-skip mode,
    because the logical playhead then advances one frame at a time.
    """

    fps = max(1, int(render_fps))
    if not math.isfinite(playback_speed) or playback_speed <= 0.0:
        raise ValueError("playback_speed must be finite and > 0")
    if not math.isfinite(safety_factor) or safety_factor < 1.0:
        raise ValueError("safety_factor must be finite and >= 1")

    def _valid(value: float | None) -> float | None:
        return float(value) if value is not None and math.isfinite(value) and value > 0.0 else None

    instrument = _valid(instrument_sweep_time_s)
    recorded = _valid(recorded_period_s)
    candidates = [value for value in (instrument, recorded) if value is not None]
    if not candidates:
        return HeatmapRenderBudget(
            render_fps=fps,
            playback_speed=playback_speed,
            instrument_sweep_time_s=instrument,
            recorded_period_s=recorded,
            effective_frame_period_s=None,
            required_frames_per_refresh=1,
            recommended_window_frames=1,
            recommended_window_seconds=None,
            safety_factor=safety_factor,
        )
    period = min(candidates)
    required = max(1, math.ceil(playback_speed / (fps * period)))
    recommended = max(1, math.ceil(required * safety_factor))
    return HeatmapRenderBudget(
        render_fps=fps,
        playback_speed=playback_speed,
        instrument_sweep_time_s=instrument,
        recorded_period_s=recorded,
        effective_frame_period_s=period,
        required_frames_per_refresh=required,
        recommended_window_frames=recommended,
        recommended_window_seconds=recommended * period,
        safety_factor=safety_factor,
    )


@dataclass(frozen=True, slots=True)
class PersistenceConfig:
    mode: PersistenceMode
    window_unit: WindowUnit = WindowUnit.FRAMES
    window_frames: int = 500
    window_seconds: float | None = None
    half_life_seconds: float | None = None
    decay_cutoff_epsilon: float | None = 1e-3
    follow_playhead: bool = True
    power_min_dbm: float = -120.0
    power_max_dbm: float = 0.0
    power_bins: int = 256
    sampling_policy: HeatmapSamplingPolicy = HeatmapSamplingPolicy.FULL_RANGE
    max_preview_frames: int = 2000
    # Legacy CENTERED combo mapping: window centered on the target frame
    # instead of ending at it (boundary-shifted inward at the file edges).
    centered: bool = False
    # UI/calculation contract: Rolling Exact cannot be smaller than the
    # source-specific render-overlap budget in either available window unit.
    minimum_window_frames: int = 1
    minimum_window_seconds: float | None = None

    def __post_init__(self) -> None:
        """Validate persistence inputs before a worker receives them.

        The bounded-decay formula is defined only for ``0 < epsilon < 1``.
        Reject invalid values at the configuration boundary instead of letting
        a background worker reach a negative history or a logarithm error.
        """
        if self.window_frames < 1:
            raise ValueError("window_frames must be >= 1")
        if self.minimum_window_frames < 1:
            raise ValueError("minimum_window_frames must be >= 1")
        if (
            self.minimum_window_seconds is not None
            and (
                not math.isfinite(self.minimum_window_seconds)
                or self.minimum_window_seconds <= 0
            )
        ):
            raise ValueError("minimum_window_seconds must be finite and > 0")
        if (
            self.mode is PersistenceMode.ROLLING_EXACT
            and self.window_unit is WindowUnit.FRAMES
            and self.window_frames < self.minimum_window_frames
        ):
            raise ValueError("window_frames is below the required render-overlap budget")
        if self.window_unit is WindowUnit.SECONDS:
            if (
                self.window_seconds is None
                or not math.isfinite(self.window_seconds)
                or self.window_seconds <= 0
            ):
                raise ValueError("seconds window requires a finite positive window_seconds")
            if (
                self.mode is PersistenceMode.ROLLING_EXACT
                and self.minimum_window_seconds is not None
                and self.window_seconds is not None
                and self.window_seconds < self.minimum_window_seconds
            ):
                raise ValueError("window_seconds is below the required render-overlap budget")
        if not (math.isfinite(self.power_min_dbm) and math.isfinite(self.power_max_dbm)):
            raise ValueError("power range bounds must be finite")
        if self.power_min_dbm >= self.power_max_dbm:
            raise ValueError("power_min_dbm must be below power_max_dbm")
        if self.power_bins < 1:
            raise ValueError("power_bins must be >= 1")
        if self.mode is PersistenceMode.EXPONENTIAL_DECAY:
            if (
                self.half_life_seconds is None
                or not math.isfinite(self.half_life_seconds)
                or self.half_life_seconds <= 0
            ):
                raise ValueError("exponential decay requires a finite positive half_life_seconds")
            epsilon = self.decay_cutoff_epsilon
            if epsilon is None or not math.isfinite(epsilon) or not 0.0 < epsilon < 1.0:
                raise ValueError("decay_cutoff_epsilon must be finite and within (0, 1)")


def resolve_persistence_bounds(
    config: PersistenceConfig, target_frame: int, frame_count: int
) -> tuple[int, int]:
    """Inclusive zero-based [start, end] of the frame window around a target.

    LAST_N semantics: the window ends at the target. CENTERED semantics:
    target +/- N/2, shifted inward at the recording boundaries (mirrors
    resolve_frame_range in heatmap_worker).
    """
    if frame_count <= 0:
        raise ValueError("empty frame range: the spectrogram has no frames")
    last = frame_count - 1
    target = min(max(0, target_frame), last)
    window = max(1, config.window_frames)
    if not config.centered:
        return max(0, target - window + 1), target
    start = target - window // 2
    end = start + window - 1
    if start < 0:
        end -= start
        start = 0
    if end > last:
        start = max(0, start - (end - last))
        end = last
    return start, end


def decay_history_seconds(config: PersistenceConfig) -> float:
    """T_history: age after which a contribution weighs less than the cutoff epsilon."""
    half_life = config.half_life_seconds
    if half_life is None or half_life <= 0:
        raise ValueError("exponential decay requires a positive half_life_seconds")
    epsilon = config.decay_cutoff_epsilon or 1e-3
    return half_life * math.log2(1.0 / epsilon)


def decay_history_positions(
    config: PersistenceConfig,
    target_timestamp: float,
    timestamps: Sequence[float] | np.ndarray,
) -> np.ndarray:
    """Frames within the bounded decay history ``[target - T_history, target]``.

    Bounds are inclusive on both ends; frames without a finite timestamp or
    positioned after the target never enter. Raises ValueError when no frame
    matches (the caller decides between a fallback and an error).
    """
    values = np.asarray(timestamps, dtype=np.float64)
    if values.ndim != 1 or values.size == 0 or not np.isfinite(target_timestamp):
        raise ValueError("decay history requires a finite target timestamp")
    cutoff = target_timestamp - decay_history_seconds(config)
    selected = np.flatnonzero(np.isfinite(values) & (values >= cutoff) & (values <= target_timestamp))
    if selected.size == 0:
        raise ValueError("empty frame range: no frames inside the decay history")
    return selected.astype(np.int64)


@dataclass(frozen=True, slots=True)
class HeatmapDisplayConfig:
    normalization: HeatmapNormalization = HeatmapNormalization.LOG_DENSITY
    palette: str = "Viridis"
    opacity: float = 0.65
    color_scale_mode: ColorScaleMode = ColorScaleMode.AUTO_CURRENT
    color_min: float | None = None
    color_max: float | None = None


@dataclass(frozen=True, slots=True)
class PersistenceTarget:
    source_key: PersistenceSourceKey
    frame_index: int
    timestamp: float | None
    navigation_generation: int
    persistence_generation: int
    reason: NavigationReason


INVALID_POWER_BIN = np.uint16(0xFFFF)


@dataclass(frozen=True, slots=True)
class FrameContribution:
    """Sparse binary contribution of one frame: one power-bin index per column."""

    frame_index: int
    timestamp: float | None
    # shape=(frequency_bins,), dtype=uint16; INVALID_POWER_BIN means NaN/inf.
    bin_indices: np.ndarray

    def __post_init__(self) -> None:
        bins = np.ascontiguousarray(self.bin_indices, dtype=np.uint16)
        bins.setflags(write=False)
        object.__setattr__(self, "bin_indices", bins)


def _config_key(config: PersistenceConfig) -> tuple[object, ...]:
    return (
        config.mode,
        config.window_unit,
        config.window_frames,
        config.window_seconds,
        config.half_life_seconds,
        config.decay_cutoff_epsilon,
        config.power_min_dbm,
        config.power_max_dbm,
        config.power_bins,
        config.sampling_policy,
        config.centered,
    )


@dataclass(frozen=True, slots=True)
class PersistenceWorkRequest:
    """One atomic engine assignment: build this exact window/decay state.

    ``frequencies_hz`` is read-only provenance for the snapshot; the engine
    never mutates it. ``positions`` semantics live on the rebuild methods.
    ``timestamps`` is the effective per-frame timeline used by decay for both
    history resolution and the alpha computation; it is how the controller
    supplies the documented frame-period fallback explicitly (never a hidden
    default factor). None means "use the timestamps stored in each row".
    """

    source_key: PersistenceSourceKey
    config: PersistenceConfig
    generation: int
    navigation_generation: int
    target_frame: int
    target_timestamp: float | None
    frame_count: int
    frequencies_hz: np.ndarray
    reason: RebuildReason = RebuildReason.INITIAL
    timestamps: np.ndarray | None = None


@dataclass(slots=True)
class RollingExactState:
    source_key: PersistenceSourceKey
    config_key: tuple[object, ...]
    generation: int
    accumulator: HeatmapAccumulator
    # uint32 valid observations V(k); ownership is immutable outside engine.
    normalization_weights_by_frequency: np.ndarray
    contributions: deque[FrameContribution]
    # Exact Frames mode owns one fixed uint16 ring. FrameContribution rows are
    # non-owning read-only views into it; a row is reused only after eviction.
    contribution_ring: np.ndarray | None
    contribution_ring_cursor: int
    current_start: int
    current_end: int
    target_frame: int
    target_timestamp: float | None
    # Engine-owned extensions over the base contract (needed by advance):
    config: PersistenceConfig
    frame_count: int
    frequencies_hz: np.ndarray
    # Effective per-frame timeline for SECONDS windows. The reader's row may
    # not carry a timestamp even when the indexed source does.
    timestamps: np.ndarray | None = None

    @classmethod
    def empty(cls, request: PersistenceWorkRequest) -> "RollingExactState":
        config = request.config
        frequencies = np.asarray(request.frequencies_hz, dtype=np.float64)
        accumulator = HeatmapAccumulator(
            freq_bins=int(frequencies.size),
            power_min_dbm=config.power_min_dbm,
            power_max_dbm=config.power_max_dbm,
            power_bins=config.power_bins,
        )
        contribution_ring = (
            np.empty((config.window_frames, int(frequencies.size)), dtype=np.uint16)
            if config.window_unit is WindowUnit.FRAMES
            else None
        )
        return cls(
            source_key=request.source_key,
            config_key=_config_key(config),
            generation=request.generation,
            accumulator=accumulator,
            normalization_weights_by_frequency=accumulator.normalization_weights_by_frequency,
            contributions=deque(),
            contribution_ring=contribution_ring,
            contribution_ring_cursor=0,
            current_start=request.target_frame,
            current_end=request.target_frame - 1,  # start > end marks the empty state
            target_frame=request.target_frame,
            target_timestamp=request.target_timestamp,
            config=config,
            frame_count=int(request.frame_count),
            frequencies_hz=frequencies,
            timestamps=request.timestamps,
        )


@dataclass(slots=True)
class DecayState:
    source_key: PersistenceSourceKey
    config_key: tuple[object, ...]
    generation: int
    accumulator: HeatmapAccumulator
    # float64 effective weights W(k) for decayed Probability.
    normalization_weights_by_frequency: np.ndarray
    decay_cutoff_epsilon: float
    history: deque[FrameContribution]
    last_timestamp: float | None
    current_start: int
    current_end: int
    target_frame: int
    # Engine-owned extensions over the base contract (needed by advance):
    config: PersistenceConfig
    frame_count: int
    frequencies_hz: np.ndarray
    # Effective per-frame timeline (row timestamps or the documented
    # frame-period fallback); None means "row timestamps only".
    timestamps: np.ndarray | None = None

    @classmethod
    def empty(cls, request: PersistenceWorkRequest) -> "DecayState":
        config = request.config
        frequencies = np.asarray(request.frequencies_hz, dtype=np.float64)
        # decay=1.0 selects the float64 accumulator; the fixed coefficient is
        # never applied by the engine (it uses apply_decay_factor(alpha_i)).
        accumulator = HeatmapAccumulator(
            freq_bins=int(frequencies.size),
            power_min_dbm=config.power_min_dbm,
            power_max_dbm=config.power_max_dbm,
            power_bins=config.power_bins,
            decay=1.0,
        )
        return cls(
            source_key=request.source_key,
            config_key=_config_key(config),
            generation=request.generation,
            accumulator=accumulator,
            normalization_weights_by_frequency=accumulator.normalization_weights_by_frequency,
            decay_cutoff_epsilon=float(config.decay_cutoff_epsilon or 1e-3),
            history=deque(),
            last_timestamp=None,
            current_start=request.target_frame,
            current_end=request.target_frame - 1,  # start > end marks the empty state
            target_frame=request.target_frame,
            config=config,
            frame_count=int(request.frame_count),
            frequencies_hz=frequencies,
            timestamps=request.timestamps,
        )


@dataclass(frozen=True, slots=True)
class PersistenceSnapshot:
    source_key: PersistenceSourceKey
    config: PersistenceConfig
    density: np.ndarray
    # V(k) for exact or W(k) for decay; never a scalar frame count.
    normalization_weights_by_frequency: np.ndarray
    frequencies_hz: np.ndarray
    power_axis_dbm: np.ndarray
    phase: PersistencePhase
    generation: int
    navigation_generation: int
    target_frame: int
    applied_frame: int
    frame_start: int
    frame_end: int
    timestamp_start: float | None
    timestamp_end: float | None
    history_start_frame: int | None
    history_end_frame: int | None
    half_life_seconds: float | None
    decay_cutoff_epsilon: float | None
    processed_frames: int
    exact: bool
    approximate: bool
    stale: bool
    computed_at: str


class FrameReaderProtocol(Protocol):
    """Minimal synchronous frame source accepted by the engine."""

    def read_frame(self, frame_index: int) -> SpectrogramRow: ...


def _finite_or_none(timestamp: float) -> float | None:
    return float(timestamp) if np.isfinite(timestamp) else None


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise OperationCancelled("Операция отменена")


def _decay_alpha(previous: float | None, current: float | None, half_life_seconds: float) -> float:
    """Half-life factor for one data-time transition.

    ``dt`` is clamped at zero: a non-monotonic timestamp behaves like a zero
    delta (full contribution, no decay) and is never silently negative.
    A missing current timestamp is an explicit error, not a silent fallback.
    A missing previous timestamp means the first frame of the state and maps
    to alpha = 1 (there is nothing older to decay yet).
    """
    if current is None:
        raise ValueError("exponential decay requires finite frame timestamps")
    if previous is None:
        return 1.0
    delta_t = max(0.0, current - previous)
    return float(math.exp(-math.log(2.0) * delta_t / half_life_seconds))


class PersistenceEngine:
    """Stateful rolling/persistence engine (Qt-free, no reader ownership)."""

    # --- shared helpers ----------------------------------------------------
    def validate(self, state: RollingExactState | DecayState) -> None:
        """Validate structural invariants of an engine state."""
        if isinstance(state, RollingExactState):
            self.validate_exact_state(state)
        elif isinstance(state, DecayState):
            self.validate_decay_state(state)
        else:
            raise TypeError(f"unsupported engine state: {type(state).__name__}")

    @staticmethod
    def _validate_deque_order(
        contributions: deque[FrameContribution],
        what: str,
        *,
        check_timestamps: bool = True,
    ) -> None:
        indices = [item.frame_index for item in contributions]
        if indices != sorted(indices):
            raise ValueError(f"{what}: contributions are not sorted by frame_index")
        if not check_timestamps:
            return
        timestamps = [item.timestamp for item in contributions if item.timestamp is not None]
        if timestamps != sorted(timestamps):
            raise ValueError(f"{what}: contributions are not sorted by timestamp")

    def validate_exact_state(self, state: RollingExactState) -> None:
        density = state.accumulator.density
        weights = state.accumulator.normalization_weights_by_frequency
        if density.dtype != np.uint32:
            raise ValueError(f"exact density must be uint32, got {density.dtype}")
        if weights.dtype != np.uint32:
            raise ValueError(f"exact weights must be uint32, got {weights.dtype}")
        self._validate_deque_order(state.contributions, "RollingExactState")
        if state.contributions:
            if state.contributions[0].frame_index != state.current_start:
                raise ValueError("current_start does not match the oldest contribution")
            if state.contributions[-1].frame_index != state.current_end:
                raise ValueError("current_end does not match the newest contribution")
            if state.config.window_unit is WindowUnit.FRAMES:
                # FRAMES windows are dense: one contribution per frame in the
                # inclusive [start, end] range. SECONDS windows may be
                # caller-resolved, so contiguity is not enforced there.
                expected = state.current_end - state.current_start + 1
                if len(state.contributions) != expected:
                    raise ValueError(
                        f"RollingExactState has {len(state.contributions)} contributions "
                        f"for a dense window of {expected} frames"
                    )
        if state.config.window_unit is WindowUnit.FRAMES:
            ring = state.contribution_ring
            if ring is None or ring.dtype != np.uint16:
                raise ValueError("frame-window exact state requires a uint16 contribution ring")
            expected_shape = (state.config.window_frames, state.accumulator.freq_bins)
            if ring.shape != expected_shape:
                raise ValueError(f"contribution ring shape {ring.shape} != {expected_shape}")
            if not 0 <= state.contribution_ring_cursor < ring.shape[0]:
                raise ValueError("contribution ring cursor is outside ring capacity")
            if len(state.contributions) > ring.shape[0]:
                raise ValueError("live exact contributions exceed contribution ring capacity")
            if any(not np.shares_memory(item.bin_indices, ring) for item in state.contributions):
                raise ValueError("frame-window contribution does not reference the owned ring")
        # Invariant: per column, sum over power bins of D == V(k).
        column_sums = density.sum(axis=0)
        if not np.array_equal(column_sums, weights):
            raise ValueError("exact invariant violated: sum_b D(b,k) != V(k)")

    def validate_decay_state(self, state: DecayState) -> None:
        density = state.accumulator.density
        weights = state.accumulator.normalization_weights_by_frequency
        if density.dtype != np.float64:
            raise ValueError(f"decay density must be float64, got {density.dtype}")
        if weights.dtype != np.float64:
            raise ValueError(f"decay weights must be float64, got {weights.dtype}")
        # Decay explicitly tolerates non-monotonic timestamps (dt clamps to
        # zero), so the history is only required to be frame-ordered.
        self._validate_deque_order(state.history, "DecayState", check_timestamps=False)
        if not np.isclose(density.sum(), weights.sum(), rtol=1e-9, atol=1e-12):
            raise ValueError("decay invariant violated: sum(D) != sum(W)")

    @staticmethod
    def _take_exact_ring_storage(
        state: RollingExactState,
        count: int,
    ) -> tuple[np.ndarray, ...]:
        """Reserve contiguous ring slices for new exact frame contributions."""
        ring = state.contribution_ring
        if ring is None:
            raise RuntimeError("exact contribution ring is only available for frame windows")
        if count < 0 or count > ring.shape[0] - len(state.contributions):
            raise RuntimeError("exact contribution ring capacity would overwrite a live frame")
        if count == 0:
            return ()
        cursor = state.contribution_ring_cursor
        first_count = min(count, ring.shape[0] - cursor)
        segments: list[np.ndarray] = [ring[cursor : cursor + first_count]]
        remaining = count - first_count
        if remaining:
            segments.append(ring[:remaining])
        state.contribution_ring_cursor = (cursor + count) % ring.shape[0]
        return tuple(segments)

    @staticmethod
    def _ring_matrices_for_contributions(
        state: RollingExactState,
        contributions: Sequence[FrameContribution],
    ) -> tuple[np.ndarray, ...]:
        """Return contiguous ring slices for live contribution views."""
        if not contributions:
            return ()
        ring = state.contribution_ring
        if ring is None:
            return tuple(item.bin_indices.reshape(1, -1) for item in contributions)
        base = int(ring.__array_interface__["data"][0])
        stride = int(ring.strides[0])
        slots: list[int] = []
        for item in contributions:
            row = item.bin_indices
            pointer = int(row.__array_interface__["data"][0])
            offset = pointer - base
            if not np.shares_memory(row, ring) or offset < 0 or offset % stride:
                return tuple(entry.bin_indices.reshape(1, -1) for entry in contributions)
            slot = offset // stride
            if slot < 0 or slot >= ring.shape[0]:
                return tuple(entry.bin_indices.reshape(1, -1) for entry in contributions)
            slots.append(slot)
        matrices: list[np.ndarray] = []
        run_start = slots[0]
        previous = slots[0]
        for slot in slots[1:]:
            if slot == previous + 1:
                previous = slot
                continue
            matrices.append(ring[run_start : previous + 1])
            run_start = previous = slot
        matrices.append(ring[run_start : previous + 1])
        return tuple(matrices)

    def _copy_exact_contributions_into_ring(
        self,
        state: RollingExactState,
        contributions: Sequence[FrameContribution],
    ) -> list[FrameContribution]:
        """Rebind staged atomic-update contributions to free ring slots."""
        if not contributions:
            return []
        segments = self._take_exact_ring_storage(state, len(contributions))
        rebound: list[FrameContribution] = []
        cursor = 0
        for segment in segments:
            count = segment.shape[0]
            source = contributions[cursor : cursor + count]
            for row, contribution in zip(segment, source, strict=True):
                np.copyto(row, contribution.bin_indices)
            rebound.extend(
                FrameContribution(
                    frame_index=item.frame_index,
                    timestamp=item.timestamp,
                    bin_indices=segment[row_index],
                )
                for row_index, item in enumerate(source)
            )
            cursor += count
        return rebound
    # --- snapshots ---------------------------------------------------------
    @staticmethod
    def _snapshot_of_exact(
        state: RollingExactState,
        navigation_generation: int,
        phase: PersistencePhase = PersistencePhase.CURRENT,
    ) -> PersistenceSnapshot:
        accumulator = state.accumulator
        timestamps = [item.timestamp for item in state.contributions if item.timestamp is not None]
        return PersistenceSnapshot(
            source_key=state.source_key,
            config=state.config,
            density=accumulator.density.copy(),
            normalization_weights_by_frequency=accumulator.normalization_weights_by_frequency.copy(),
            frequencies_hz=state.frequencies_hz.copy(),
            power_axis_dbm=accumulator.power_axis_dbm(),
            phase=phase,
            generation=state.generation,
            navigation_generation=navigation_generation,
            target_frame=state.target_frame,
            applied_frame=state.current_end,
            frame_start=state.current_start,
            frame_end=state.current_end,
            timestamp_start=timestamps[0] if timestamps else None,
            timestamp_end=timestamps[-1] if timestamps else None,
            history_start_frame=None,
            history_end_frame=None,
            half_life_seconds=None,
            decay_cutoff_epsilon=None,
            processed_frames=len(state.contributions),
            exact=True,
            approximate=False,
            stale=False,
            computed_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _snapshot_of_decay(
        state: DecayState,
        navigation_generation: int,
        phase: PersistencePhase = PersistencePhase.CURRENT,
    ) -> PersistenceSnapshot:
        accumulator = state.accumulator
        timestamps = [item.timestamp for item in state.history if item.timestamp is not None]
        config = state.config
        return PersistenceSnapshot(
            source_key=state.source_key,
            config=config,
            density=accumulator.density.copy(),
            normalization_weights_by_frequency=accumulator.normalization_weights_by_frequency.copy(),
            frequencies_hz=state.frequencies_hz.copy(),
            power_axis_dbm=accumulator.power_axis_dbm(),
            phase=phase,
            generation=state.generation,
            navigation_generation=navigation_generation,
            target_frame=state.target_frame,
            applied_frame=state.current_end,
            frame_start=state.current_start,
            frame_end=state.current_end,
            timestamp_start=timestamps[0] if timestamps else None,
            timestamp_end=timestamps[-1] if timestamps else None,
            history_start_frame=state.history[0].frame_index if state.history else None,
            history_end_frame=state.history[-1].frame_index if state.history else None,
            half_life_seconds=config.half_life_seconds,
            decay_cutoff_epsilon=state.decay_cutoff_epsilon,
            processed_frames=len(state.history),
            exact=False,
            approximate=True,
            stale=False,
            computed_at=datetime.now(timezone.utc).isoformat(),
        )

    # --- exact rolling ------------------------------------------------------
    def rebuild_exact(
        self,
        request: PersistenceWorkRequest,
        reader: FrameReaderProtocol,
        cancel: Event | None = None,
        *,
        positions: Sequence[int] | None = None,
    ) -> tuple[RollingExactState, PersistenceSnapshot]:
        """Build a fresh exact state over the resolved frame window.

        ``positions`` overrides the frame-window resolution (used by the
        time-window mode, whose bounds come from index timestamps upstream).
        A cancelled build raises; no partial state escapes.
        """
        if positions is None:
            start, end = resolve_persistence_bounds(request.config, request.target_frame, request.frame_count)
            positions = range(start, end + 1)
        positions = list(positions)
        if not positions:
            raise ValueError("empty frame range: no frames to process")
        state = RollingExactState.empty(request)
        processed = 0
        for frame_index in positions:
            _check_cancel(cancel)
            row = reader.read_frame(int(frame_index))
            _check_cancel(cancel)
            timestamp = self._effective_timestamp(
                row.timestamp, int(frame_index), request.timestamps
            )
            # A seconds window is defined in data time. Frames without a
            # timestamp have no position on that axis and must never become a
            # non-evictable deque entry.
            if request.config.window_unit is WindowUnit.SECONDS and timestamp is None:
                continue
            if request.config.window_unit is WindowUnit.FRAMES:
                ring_row = self._take_exact_ring_storage(state, 1)[0][0]
                contribution = state.accumulator.make_contribution(
                    int(frame_index), timestamp, row.values, bin_storage=ring_row
                )
            else:
                contribution = state.accumulator.make_contribution(
                    int(frame_index), timestamp, row.values
                )
            state.accumulator.add_contribution(contribution)
            state.contributions.append(contribution)
            processed += 1
        if request.config.window_unit is WindowUnit.SECONDS:
            self._evict_time_window(state, request.target_timestamp)
        if state.contributions:
            state.current_start = state.contributions[0].frame_index
            state.current_end = state.contributions[-1].frame_index
        state.target_frame = request.target_frame
        state.target_timestamp = request.target_timestamp
        self.validate_exact_state(state)
        _check_cancel(cancel)
        return state, self._snapshot_of_exact(state, request.navigation_generation)

    @staticmethod
    def _evict_time_window(state: RollingExactState, target_timestamp: float | None) -> None:
        """Inclusive time-window eviction: frame at exactly the cutoff stays."""
        if target_timestamp is None:
            raise ValueError("time-window mode requires a target timestamp")
        window_seconds = state.config.window_seconds
        if window_seconds is None or window_seconds <= 0:
            raise ValueError("time-window mode requires a positive window_seconds")
        cutoff = target_timestamp - window_seconds
        contributions = state.contributions
        while contributions and contributions[0].timestamp is not None and contributions[0].timestamp < cutoff:
            state.accumulator.subtract_contribution(contributions.popleft())

    def advance_exact(
        self,
        state: RollingExactState,
        target: PersistenceTarget,
        reader: FrameReaderProtocol,
        cancel: Event | None = None,
    ) -> PersistenceSnapshot | None:
        """Advance the rolling window to ``target``; None means rebuild required.

        Only the frames entering the window are read; the frames leaving it
        are subtracted from the same contribution objects. A cancelled advance
        discards staged contributions: the passed state stays bit-for-bit
        identical (the atomic commit contains no disk I/O).
        """
        config = state.config
        bounds_start, new_end = (
            resolve_persistence_bounds(config, target.frame_index, state.frame_count)
            if config.window_unit is WindowUnit.FRAMES
            else (None, min(max(0, target.frame_index), state.frame_count - 1))
        )
        new_start = bounds_start
        if state.contributions:
            if new_end == state.current_end and target.frame_index == state.target_frame:
                # Same target: idempotent snapshot (e.g. Pause); no reads at all.
                self.validate_exact_state(state)
                return self._snapshot_of_exact(state, target.navigation_generation)
            if new_end < state.current_end:
                return None  # backward moves require a bounded rebuild
            if new_start is not None and (new_end < state.current_start or new_start > state.current_end):
                return None  # non-overlapping forward jump: bounded rebuild
            read_from = state.current_end + 1
        else:
            if new_start is None:
                # An empty time-window state cannot bound its reads without
                # index timestamps; build it via rebuild_exact with explicit
                # positions instead (policy: rebuild, never a full scan).
                return None
            # Empty state: only the final target window is read, never the
            # whole prefix of the recording.
            read_from = new_start

        staged: list[FrameContribution] = []
        for frame_index in range(read_from, new_end + 1):
            _check_cancel(cancel)
            row = reader.read_frame(frame_index)
            _check_cancel(cancel)
            timestamp = self._effective_timestamp(row.timestamp, frame_index, state.timestamps)
            if config.window_unit is WindowUnit.SECONDS:
                # A time window contains only frames with a defined timestamp
                # at or before the target. In particular, never let NaN time
                # create an entry that blocks ordered eviction from the deque.
                if timestamp is None or target.timestamp is None or timestamp > target.timestamp:
                    continue
            staged.append(state.accumulator.make_contribution(frame_index, timestamp, row.values))

        _check_cancel(cancel)
        # Atomic commit: no disk I/O below this line.
        if config.window_unit is WindowUnit.FRAMES:
            assert new_start is not None
            contributions = state.contributions
            while contributions and contributions[0].frame_index < new_start:
                state.accumulator.subtract_contribution(contributions.popleft())
        else:
            self._evict_time_window(state, target.timestamp)
        if config.window_unit is WindowUnit.FRAMES:
            staged = self._copy_exact_contributions_into_ring(state, staged)
        for contribution in staged:
            state.accumulator.add_contribution(contribution)
            state.contributions.append(contribution)
        if state.contributions:
            state.current_start = state.contributions[0].frame_index
            state.current_end = state.contributions[-1].frame_index
        else:
            state.current_start = new_end
            state.current_end = new_end - 1
        state.target_frame = target.frame_index
        state.target_timestamp = target.timestamp
        state.generation = target.persistence_generation
        self.validate_exact_state(state)
        return self._snapshot_of_exact(state, target.navigation_generation)

    def advance_exact_range(
        self,
        state: RollingExactState,
        target: PersistenceTarget,
        reader: FrameReaderProtocol,
        cancel: Event | None = None,
        *,
        publish_snapshot: bool = True,
    ) -> PersistenceSnapshot | None:
        """Advance Rolling Exact through every source frame up to ``target``.

        This is the streaming counterpart of :meth:`advance_exact`. Unlike
        repeatedly calling the one-target API, it takes one immutable snapshot
        only after the entire range has been consumed when publish_snapshot is true. The stream worker may advance bounded chunks without copying the density and publish only at its presentation deadline. That keeps the density
        matrix and the read-only DFL reader on the worker hot path rather than
        copying a multi-megabyte matrix for every acquired frame.

        The method is intentionally stateful and non-atomic between source
        frames: a cancelled stream retains the exact prefix it has consumed,
        while its generation is discarded by the controller. No partially
        processed state is ever published to the renderer.
        """
        config = state.config
        if target.frame_index < state.target_frame:
            return None
        if target.frame_index == state.target_frame:
            self.validate_exact_state(state)
            return self._snapshot_of_exact(state, target.navigation_generation) if publish_snapshot else None

        end = min(max(0, int(target.frame_index)), state.frame_count - 1)
        start = max(0, int(state.target_frame) + 1)
        if start > end:
            return self._snapshot_of_exact(state, target.navigation_generation) if publish_snapshot else None

        if config.window_unit is WindowUnit.FRAMES:
            return self._advance_exact_frame_range(
                state, target, reader, start, end, cancel, publish_snapshot=publish_snapshot
            )
        pending_additions: list[FrameContribution] = []
        pending_removals: list[FrameContribution] = []
        # A contribution may not both enter and expire in one vector batch.
        # Keeping B <= N makes batch reduction exactly equivalent to the
        # ordered rolling add/subtract sequence for frame windows.
        batch_size = max(1, min(128, config.window_frames))

        def flush_exact_batch() -> None:
            if pending_additions or pending_removals:
                state.accumulator.apply_exact_batch(pending_additions, pending_removals)
                pending_additions.clear()
                pending_removals.clear()

        for chunk_start in range(start, end + 1, batch_size):
            # Reader I/O remains cancellable per source frame. Quantisation of
            # the decoded rows is then one vectorized NumPy operation.
            chunk_stop = min(end + 1, chunk_start + batch_size)
            decoded: list[tuple[int, float | None, float | None, np.ndarray]] = []
            for frame_index in range(chunk_start, chunk_stop):
                if cancel is not None and cancel.is_set():
                    flush_exact_batch()
                    _check_cancel(cancel)
                row = reader.read_frame(frame_index)
                _check_cancel(cancel)
                timestamp = self._effective_timestamp(row.timestamp, frame_index, state.timestamps)
                target_timestamp = timestamp
                if state.timestamps is not None and 0 <= frame_index < state.timestamps.size:
                    indexed_timestamp = float(state.timestamps[frame_index])
                    if np.isfinite(indexed_timestamp):
                        target_timestamp = indexed_timestamp
                decoded.append((frame_index, timestamp, target_timestamp, row.values))

            contributions = state.accumulator.make_contributions_batch(
                [item[0] for item in decoded],
                [item[1] for item in decoded],
                [item[3] for item in decoded],
            )
            for (frame_index, timestamp, target_timestamp, _values), contribution in zip(
                decoded, contributions, strict=True
            ):
                if config.window_unit is WindowUnit.SECONDS:
                    if timestamp is None or target_timestamp is None or timestamp > target_timestamp:
                        state.target_frame = frame_index
                        state.target_timestamp = target_timestamp
                        continue
                if config.window_unit is WindowUnit.FRAMES:
                    new_start, _ = resolve_persistence_bounds(config, frame_index, state.frame_count)
                    while state.contributions and state.contributions[0].frame_index < new_start:
                        pending_removals.append(state.contributions.popleft())
                    state.contributions.append(contribution)
                    pending_additions.append(contribution)
                    if len(pending_additions) >= batch_size:
                        flush_exact_batch()
                else:
                    self._evict_time_window(state, target_timestamp)
                    state.accumulator.add_contribution(contribution)
                    state.contributions.append(contribution)
                if state.contributions:
                    state.current_start = state.contributions[0].frame_index
                    state.current_end = state.contributions[-1].frame_index
                else:
                    state.current_start = frame_index
                    state.current_end = frame_index - 1
                state.target_frame = frame_index
                state.target_timestamp = target_timestamp
        flush_exact_batch()
        state.generation = target.persistence_generation
        state.target_timestamp = target.timestamp if target.timestamp is not None else state.target_timestamp
        if publish_snapshot:
            # rebuild_exact validates the state before it enters the streaming
            # worker. Re-scanning the full density invariant for every visual
            # snapshot costs hundreds of microseconds and does not strengthen
            # the atomic batch operation; explicit validation remains public
            # for tests and diagnostic checkpoints.
            return self._snapshot_of_exact(state, target.navigation_generation)
        return None

    def _advance_exact_frame_range(
        self,
        state: RollingExactState,
        target: PersistenceTarget,
        reader: FrameReaderProtocol,
        start: int,
        end: int,
        cancel: Event | None,
        *,
        publish_snapshot: bool,
    ) -> PersistenceSnapshot | None:
        """Hot sequential Frames update backed by the preallocated uint16 ring."""
        config = state.config
        batch_size = max(1, min(128, config.window_frames))
        for chunk_start in range(start, end + 1, batch_size):
            chunk_stop = min(end + 1, chunk_start + batch_size)
            decoded: list[tuple[int, float | None, float | None, np.ndarray]] = []
            for frame_index in range(chunk_start, chunk_stop):
                _check_cancel(cancel)
                row = reader.read_frame(frame_index)
                _check_cancel(cancel)
                timestamp = self._effective_timestamp(row.timestamp, frame_index, state.timestamps)
                target_timestamp = timestamp
                if state.timestamps is not None and 0 <= frame_index < state.timestamps.size:
                    indexed_timestamp = float(state.timestamps[frame_index])
                    if np.isfinite(indexed_timestamp):
                        target_timestamp = indexed_timestamp
                decoded.append((frame_index, timestamp, target_timestamp, row.values))

            removals: list[FrameContribution] = []
            # No snapshot is observable inside this vector batch, so exact
            # final-window membership can be resolved once for the chunk
            # instead of recomputing identical bounds for every entered frame.
            new_start = max(0, chunk_stop - config.window_frames)
            while state.contributions and state.contributions[0].frame_index < new_start:
                removals.append(state.contributions.popleft())
            if removals:
                state.accumulator.apply_exact_bin_matrices(
                    (), self._ring_matrices_for_contributions(state, removals)
                )

            storage_segments = self._take_exact_ring_storage(state, len(decoded))
            additions: list[FrameContribution] = []
            addition_matrices: list[np.ndarray] = []
            cursor = 0
            for storage in storage_segments:
                count = storage.shape[0]
                entries = decoded[cursor : cursor + count]
                additions.extend(
                    state.accumulator.make_contributions_batch(
                        [item[0] for item in entries],
                        [item[1] for item in entries],
                        [item[3] for item in entries],
                        bin_storage=storage,
                    )
                )
                addition_matrices.append(storage)
                cursor += count
            state.accumulator.apply_exact_bin_matrices(addition_matrices, ())
            state.contributions.extend(additions)
            state.current_start = state.contributions[0].frame_index
            state.current_end = state.contributions[-1].frame_index
            last_frame, _timestamp, last_target_timestamp, _values = decoded[-1]
            state.target_frame = last_frame
            state.target_timestamp = last_target_timestamp

        state.generation = target.persistence_generation
        state.target_timestamp = target.timestamp if target.timestamp is not None else state.target_timestamp
        if publish_snapshot:
            # rebuild_exact validates the state before it enters the streaming
            # worker. Re-scanning the full density invariant for every visual
            # snapshot costs hundreds of microseconds and does not strengthen
            # the atomic batch operation; explicit validation remains public
            # for tests and diagnostic checkpoints.
            return self._snapshot_of_exact(state, target.navigation_generation)
        return None
    # --- exponential decay --------------------------------------------------
    @staticmethod
    def _effective_timestamp(
        row_timestamp: float, frame_index: int, timeline: np.ndarray | None
    ) -> float | None:
        """Row timestamp when finite, else the documented fallback timeline entry."""
        if np.isfinite(row_timestamp):
            return float(row_timestamp)
        if timeline is not None and 0 <= frame_index < timeline.size:
            value = float(timeline[frame_index])
            return value if np.isfinite(value) else None
        return None

    def rebuild_decay(
        self,
        request: PersistenceWorkRequest,
        reader: FrameReaderProtocol,
        cancel: Event | None = None,
        *,
        positions: Sequence[int] | None = None,
    ) -> tuple[DecayState, PersistenceSnapshot]:
        """Build a fresh decay state over the bounded history.

        Bounded history per the review model: frames older than
        ``T_history = half_life * log2(1 / epsilon)`` contribute less than
        epsilon of their initial weight. Without explicit ``positions`` the
        history is resolved from ``request.timestamps`` (or, without a
        timeline, from the frame window ending at the target).
        """
        config = request.config
        half_life = config.half_life_seconds
        if half_life is None or half_life <= 0:
            raise ValueError("exponential decay requires a positive half_life_seconds")
        timeline = request.timestamps
        if positions is None:
            if timeline is not None and request.target_timestamp is not None:
                resolved: Iterable[int] = decay_history_positions(config, request.target_timestamp, timeline)
            else:
                target = min(max(0, request.target_frame), request.frame_count - 1)
                start = max(0, target - config.window_frames + 1)
                resolved = range(start, target + 1)
            positions = list(resolved)
        else:
            positions = list(positions)
        if not positions:
            raise ValueError("empty frame range: no frames to process")
        state = DecayState.empty(request)
        previous_timestamp = state.last_timestamp
        processed = 0
        for frame_index in positions:
            _check_cancel(cancel)
            row = reader.read_frame(int(frame_index))
            _check_cancel(cancel)
            timestamp = self._effective_timestamp(row.timestamp, int(frame_index), timeline)
            alpha = _decay_alpha(previous_timestamp, timestamp, half_life)
            if alpha < 1.0:
                state.accumulator.apply_decay_factor(alpha)
            contribution = state.accumulator.make_contribution(int(frame_index), timestamp, row.values)
            state.accumulator.add_contribution(contribution)
            state.history.append(contribution)
            previous_timestamp = timestamp
            processed += 1
        state.last_timestamp = previous_timestamp
        self._trim_decay_history(state)
        if state.history:
            state.current_start = state.history[0].frame_index
            state.current_end = state.history[-1].frame_index
        state.target_frame = request.target_frame
        self.validate_decay_state(state)
        _check_cancel(cancel)
        return state, self._snapshot_of_decay(state, request.navigation_generation)

    @staticmethod
    def _trim_decay_history(state: DecayState) -> None:
        """Drop history entries whose remaining weight is below the cutoff epsilon."""
        config = state.config
        half_life = config.half_life_seconds
        if half_life is None or half_life <= 0 or state.last_timestamp is None:
            return
        t_history = half_life * math.log2(1.0 / state.decay_cutoff_epsilon)
        cutoff = state.last_timestamp - t_history
        history = state.history
        while history and history[0].timestamp is not None and history[0].timestamp < cutoff:
            history.popleft()  # weight already decayed below epsilon; nothing to subtract

    def advance_decay(
        self,
        state: DecayState,
        target: PersistenceTarget,
        reader: FrameReaderProtocol,
        cancel: Event | None = None,
    ) -> PersistenceSnapshot | None:
        """Advance the decay state to ``target``; None means rebuild required.

        Each entered frame decays the previous density by its own data-time
        factor. No wall clock: advancing to the same target twice changes
        nothing (Pause semantics).
        """
        config = state.config
        half_life = config.half_life_seconds
        if half_life is None or half_life <= 0:
            raise ValueError("exponential decay requires a positive half_life_seconds")
        new_end = min(max(0, target.frame_index), state.frame_count - 1)
        if new_end < state.current_end:
            return None  # backward moves require a bounded rebuild
        if new_end == state.current_end:
            self.validate_decay_state(state)
            return self._snapshot_of_decay(state, target.navigation_generation)

        staged: list[tuple[float, FrameContribution]] = []
        previous_timestamp = state.last_timestamp
        timeline = state.timestamps
        for frame_index in range(state.current_end + 1, new_end + 1):
            _check_cancel(cancel)
            row = reader.read_frame(frame_index)
            _check_cancel(cancel)
            timestamp = self._effective_timestamp(row.timestamp, frame_index, timeline)
            alpha = _decay_alpha(previous_timestamp, timestamp, half_life)
            contribution = state.accumulator.make_contribution(frame_index, timestamp, row.values)
            staged.append((alpha, contribution))
            previous_timestamp = timestamp

        _check_cancel(cancel)
        # Atomic commit: no disk I/O below this line.
        for alpha, contribution in staged:
            if alpha < 1.0:
                state.accumulator.apply_decay_factor(alpha)
            state.accumulator.add_contribution(contribution)
            state.history.append(contribution)
        state.last_timestamp = previous_timestamp
        self._trim_decay_history(state)
        if state.history:
            state.current_start = state.history[0].frame_index
            state.current_end = state.history[-1].frame_index
        else:
            state.current_start = new_end
            state.current_end = new_end - 1
        state.target_frame = target.frame_index
        state.generation = target.persistence_generation
        self.validate_decay_state(state)
        return self._snapshot_of_decay(state, target.navigation_generation)









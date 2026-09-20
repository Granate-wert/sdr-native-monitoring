"""Qt-free contracts for bounded completed sweep-line publication.

These contracts contain reduced spectrum data only.  They are a reference
boundary for R10-D; production-rate assembly belongs in the native data plane
and must not move raw I/Q or per-FFT work into Python or Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

import numpy as np

from .sweep import SweepBinQuality, SweepPlan
from .sweep_acquisition import SweepSegmentAcquisition, SweepSegmentPosition, validate_acquisition, validate_position
from .sweep_statistics import SweepStatisticsFrame


_VALIDATION_BATCH = 65_536


def _valid_frequency_grid(frequency: np.ndarray) -> bool:
    """Every interval, including chunk seams, without a full float diff."""
    for start in range(0, frequency.size, _VALIDATION_BATCH):
        batch = frequency[start:start + _VALIDATION_BATCH]
        if (not np.all(np.isfinite(batch)) or np.any(batch[1:] <= batch[:-1])
                or start > 0 and batch[0] <= frequency[start - 1]):
            return False
    return True


def _has_unknown_power(values: np.ndarray) -> bool:
    """NaN/+inf are unknown; -inf remains measured zero power."""
    for start in range(0, values.size, _VALIDATION_BATCH):
        batch = values[start:start + _VALIDATION_BATCH]
        if np.any(np.isnan(batch) | (batch == np.inf)):
            return True
    return False


class SweepLineState(StrEnum):
    """Whether a published line is complete or explicitly contains a gap."""

    COMPLETE = "complete"
    GAP = "gap"


class SweepQualitySchema(StrEnum):
    """Explicit bit layout; historical reference masks are not native masks."""

    REFERENCE_V1 = "sweep-reference-v1"
    NATIVE_V5 = "sdr-native-quality-v5"


class SweepLineGapReason(StrEnum):
    """Why a final line cannot be presented as continuous data."""

    MISSING_SEGMENT = "missing_segment"
    CAPACITY = "capacity"
    CANCELLATION = "cancellation"
    DISCONNECT = "disconnect"
    RECONFIGURE = "reconfigure"


@dataclass(frozen=True, slots=True)
class SweepLineDefinition:
    """Immutable one-epoch line contract with explicit finite staging capacity."""

    plan: SweepPlan
    source_id: str
    epoch: int
    expected_generation_by_segment: tuple[tuple[int, int], ...]
    target_spacing_hz: float
    max_inflight_lines: int = 4
    unit: str = "dBFS/bin"

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.unit.strip() or self.epoch < 0:
            raise ValueError("sweep-line source, unit and epoch are invalid")
        if not math.isfinite(self.target_spacing_hz) or self.target_spacing_hz <= 0.0:
            raise ValueError("sweep-line target spacing must be finite and positive")
        if not 1 <= self.max_inflight_lines <= 64:
            raise ValueError("sweep-line in-flight capacity must be in [1, 64]")
        expected = tuple(self.expected_generation_by_segment)
        planned_indices = tuple(segment.index for segment in self.plan.segments)
        if (
            len(expected) != len(planned_indices)
            or tuple(index for index, _generation in expected) != planned_indices
            or any(generation < 0 for _index, generation in expected)
        ):
            raise ValueError("sweep-line generations must exactly cover the planned segment order")

    @property
    def span_hz(self) -> float:
        return self.plan.configuration.stop_hz - self.plan.configuration.start_hz


@dataclass(frozen=True, slots=True)
class SweepLineFrame:
    """One immutable final line, never a raw-I/Q or GUI-owned buffer."""

    sequence: int
    epoch: int
    completed_at_ns: int
    source_id: str
    state: SweepLineState
    frequencies_hz: np.ndarray
    values_db: np.ndarray
    quality_flags: np.ndarray
    source_segment_indices: np.ndarray
    missing_segment_indices: tuple[int, ...]
    segment_config_generations: tuple[tuple[int, int], ...]
    gap_reasons: tuple[SweepLineGapReason, ...]
    unit: str
    analysis_window_hz: float = 0.0
    analysis_bins_per_usable_window: int = 0
    physical_fft_bin_width_hz: float = 0.0
    physical_fft_size: int = 0
    quality_schema: SweepQualitySchema = SweepQualitySchema.REFERENCE_V1
    segment_acquisition: tuple["SweepSegmentAcquisition", ...] | None = None
    statistics: SweepStatisticsFrame | None = None
    last_admitted_segment: SweepSegmentPosition | None = None

    def __post_init__(self) -> None:
        validate_position(self.last_admitted_segment, tuple(
            pair for pair in self.segment_config_generations if pair[0] not in self.missing_segment_indices))
        object.__setattr__(self, "segment_acquisition", validate_acquisition(
            self.segment_acquisition,
            tuple(pair for pair in self.segment_config_generations
                  if pair[0] not in self.missing_segment_indices),
        ))
        object.__setattr__(self, "quality_schema", SweepQualitySchema(self.quality_schema))
        if self.sequence < 0 or self.epoch < 0 or self.completed_at_ns < 0:
            raise ValueError("sweep-line sequence, epoch and timestamp must be non-negative")
        if not self.source_id.strip() or not self.unit.strip():
            raise ValueError("sweep-line source and unit must not be blank")
        if self.analysis_bins_per_usable_window:
            if (
                self.analysis_bins_per_usable_window < 256
                or self.analysis_bins_per_usable_window > 262_144
                or self.analysis_bins_per_usable_window & (self.analysis_bins_per_usable_window - 1)
            ):
                raise ValueError("sweep-line analysis bins must be a power of two in [256, 262144]")
            if not all(math.isfinite(value) and value > 0.0 for value in (
                self.analysis_window_hz, self.physical_fft_bin_width_hz
            )):
                raise ValueError("sweep-line analysis/physical geometry must be finite and positive")
            if (
                self.physical_fft_size < 256
                or self.physical_fft_size > 262_144
                or self.physical_fft_size & (self.physical_fft_size - 1)
            ):
                raise ValueError("sweep-line physical FFT size must be a power of two in [256, 262144]")
        elif (
            self.analysis_window_hz != 0.0
            or self.physical_fft_bin_width_hz != 0.0
            or self.physical_fft_size != 0
        ):
            raise ValueError("legacy sweep-line must not carry partial analysis geometry")
        frequency = np.asarray(self.frequencies_hz, dtype=np.float64)
        values = np.asarray(self.values_db, dtype=np.float32)
        raw_quality = np.asarray(self.quality_flags)
        if raw_quality.dtype.kind not in "iu" or (
            raw_quality.size and (
                (raw_quality.dtype.kind == "i" and int(np.min(raw_quality)) < 0)
                or int(np.max(raw_quality)) > np.iinfo(np.uint16).max
            )
        ):
            raise ValueError("sweep-line quality flags must be unsigned 16-bit integer masks")
        # Narrow directly into the final owned buffer. Even an already-uint16
        # input must be copied: callers may mutate their arrays after delivery.
        # Do not copy this result again in the remaining ownership pass below.
        quality = raw_quality.astype(np.uint16, copy=True)
        sources = np.asarray(self.source_segment_indices, dtype=np.int32)
        if frequency.ndim != 1 or frequency.size < 2 or frequency.size > 2_000_000:
            raise ValueError("sweep-line frequency grid must contain 2..2,000,000 bins")
        if any(item.ndim != 1 or item.size != frequency.size for item in (values, quality, sources)):
            raise ValueError("sweep-line arrays must be one-dimensional and equally sized")
        if not _valid_frequency_grid(frequency):
            raise ValueError("sweep-line frequencies must be finite and strictly increasing")
        missing = tuple(self.missing_segment_indices)
        generations = tuple(self.segment_config_generations)
        reasons = tuple(self.gap_reasons)
        if (
            any(index < 0 for index in missing)
            or len(set(missing)) != len(missing)
            or any(index < 0 or generation < 0 for index, generation in generations)
            or len({index for index, _generation in generations}) != len(generations)
            or len(set(reasons)) != len(reasons)
        ):
            raise ValueError("sweep-line gap or generation metadata is invalid")
        if self.state is SweepLineState.COMPLETE:
            if missing or reasons or _has_unknown_power(values):
                raise ValueError("complete sweep-line must not hide gaps or missing bins")
            missing_mask = (1 << 12) if self.quality_schema is SweepQualitySchema.NATIVE_V5 else int(SweepBinQuality.MISSING_SEGMENT)
            # The union tests the same bit in every bin without allocating a
            # full-width masked array. Shape/range have already been checked.
            if int(np.bitwise_or.reduce(quality, initial=np.uint16(0))) & missing_mask:
                raise ValueError("complete sweep-line must not contain missing-segment flags")
        elif self.state is SweepLineState.GAP:
            if not missing and not reasons and not _has_unknown_power(values):
                raise ValueError("gapped sweep-line requires explicit gap evidence")
        else:
            raise ValueError("unknown sweep-line state")
        for field_name, array in (
            ("frequencies_hz", frequency),
            ("values_db", values),
            ("source_segment_indices", sources),
        ):
            immutable = np.array(array, copy=True)
            immutable.setflags(write=False)
            object.__setattr__(self, field_name, immutable)
        quality.setflags(write=False)
        object.__setattr__(self, "quality_flags", quality)
        if self.statistics is not None:
            if not isinstance(self.statistics, SweepStatisticsFrame):
                raise TypeError("Sweep requires an explicit statistics contract")
            self.statistics.validate_parent(self.source_id, self.epoch, self.sequence,
                                            self.unit, self.frequencies_hz)

    @property
    def is_complete(self) -> bool:
        return self.state is SweepLineState.COMPLETE

    @property
    def aggregate_quality_flags(self) -> int:
        """Union of published bin quality, independent of coverage completeness.

        This low-rate domain summary never replaces per-bin provenance and
        does not declare a complete line calibrated or loss-free.
        """
        return int(np.bitwise_or.reduce(self.quality_flags, initial=np.uint16(0)))


@dataclass(frozen=True, slots=True)
class SweepLineAssemblyMetrics:
    """Low-rate reference metrics; analytical FFT/s remains native-owned."""

    completed_lines: int
    gapped_lines: int
    evicted_inflight_lines: int
    pending_lines: int

    def __post_init__(self) -> None:
        if any(value < 0 for value in (
            self.completed_lines,
            self.gapped_lines,
            self.evicted_inflight_lines,
            self.pending_lines,
        )):
            raise ValueError("sweep-line metrics must be non-negative")


__all__ = [
    "SweepLineAssemblyMetrics",
    "SweepLineDefinition",
    "SweepLineFrame",
    "SweepLineGapReason",
    "SweepLineState",
]

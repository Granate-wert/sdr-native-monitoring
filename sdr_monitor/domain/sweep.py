"""Qt-free contracts for planned wideband sweep operations.

The standalone model deliberately keeps the final, stitched frequency grid out
of Qt.  A widget can therefore render one immutable result but cannot hide a
failed segment by interpolating it locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag, StrEnum
import math

import numpy as np


class SweepMode(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    PRECISE = "precise"


class SweepState(StrEnum):
    IDLE = "idle"
    PLANNED = "planned"
    RUNNING = "running"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    ERROR = "error"


class SweepExecutionMode(StrEnum):
    """Explicit source selection; native hardware is never an implicit fallback."""

    SYNTHETIC = "synthetic"
    NATIVE = "native"


class SweepBinQuality(IntFlag):
    """Per-bin evidence retained after a multi-segment stitch."""

    NONE = 0
    MISSING_SEGMENT = 1 << 0
    STITCH_OVERLAP = 1 << 1
    EDGE_BIN = 1 << 2


class SweepRateEvidence(StrEnum):
    """Origin of reported sweep rates; it must never be confused with FFT LPS."""

    MEASURED = "measured"
    SYNTHETIC = "synthetic"
    NOT_MEASURED = "not_measured"


@dataclass(frozen=True, slots=True)
class SweepBandPreset:
    """A named RF span, not a claim that the selected device can acquire it."""

    preset_id: str
    title: str
    start_hz: float
    stop_hz: float
    default_mode: SweepMode

    def __post_init__(self) -> None:
        if not self.preset_id.strip() or not self.title.strip():
            raise ValueError("sweep band preset identity must not be blank")
        if not math.isfinite(self.start_hz) or not math.isfinite(self.stop_hz) or self.start_hz < 0 or self.stop_hz <= self.start_hz:
            raise ValueError("sweep band preset requires a finite increasing span")


DEFAULT_SWEEP_BAND_PRESETS: tuple[SweepBandPreset, ...] = (
    SweepBandPreset("broadcast_fm", "FM broadcast", 87.5e6, 108e6, SweepMode.BALANCED),
    SweepBandPreset("airband", "Airband", 118e6, 137e6, SweepMode.BALANCED),
    SweepBandPreset("vhf", "VHF", 136e6, 174e6, SweepMode.BALANCED),
    SweepBandPreset("uhf", "UHF", 400e6, 470e6, SweepMode.BALANCED),
    SweepBandPreset("ism_433", "ISM 433", 433.05e6, 434.79e6, SweepMode.PRECISE),
    SweepBandPreset("ism_868", "ISM 868", 863e6, 870e6, SweepMode.PRECISE),
    SweepBandPreset("ism_2400", "ISM 2.4 GHz", 2.4e9, 2.4835e9, SweepMode.PRECISE),
)


@dataclass(frozen=True, slots=True)
class SweepConfiguration:
    start_hz: float = 400e6
    stop_hz: float = 6e9
    mode: SweepMode = SweepMode.BALANCED
    overlap_fraction: float = 0.10
    dc_margin_hz: float = 100e3
    settling_s: float = 0.02
    dwell_s: float = 0.10
    discard_blocks: int = 1
    band_preset_id: str | None = None
    execution_mode: SweepExecutionMode = SweepExecutionMode.SYNTHETIC

    def __post_init__(self) -> None:
        if self.start_hz < 0 or self.stop_hz <= self.start_hz:
            raise ValueError("sweep stop frequency must exceed start frequency")
        if not 0 <= self.overlap_fraction < 0.5:
            raise ValueError("sweep overlap must be in [0, 0.5)")
        if min(self.dc_margin_hz, self.settling_s, self.dwell_s, self.discard_blocks) < 0:
            raise ValueError("sweep expert settings must be non-negative")
        if self.band_preset_id is not None and not self.band_preset_id.strip():
            raise ValueError("sweep band preset id must not be blank")


def configuration_for_band_preset(
    preset: SweepBandPreset,
    *,
    mode: SweepMode | None = None,
) -> SweepConfiguration:
    """Create an explicit configuration from a preset without device assumptions."""

    return SweepConfiguration(
        start_hz=preset.start_hz,
        stop_hz=preset.stop_hz,
        mode=preset.default_mode if mode is None else mode,
        band_preset_id=preset.preset_id,
    )


@dataclass(frozen=True, slots=True)
class SweepSegment:
    index: int
    start_hz: float
    stop_hz: float
    usable_start_hz: float
    usable_stop_hz: float


@dataclass(frozen=True, slots=True)
class SweepPlan:
    configuration: SweepConfiguration
    segments: tuple[SweepSegment, ...]
    estimated_seconds: float
    resolution_hz: float


@dataclass(frozen=True, slots=True)
class SweepProgress:
    state: SweepState
    completed_segments: int
    total_segments: int
    current_hz: float | None = None
    stage: str = ""

    @property
    def percent(self) -> float:
        return 0.0 if self.total_segments == 0 else 100.0 * self.completed_segments / self.total_segments


@dataclass(frozen=True, slots=True)
class SweepQuality:
    missing_segments: int
    seam_p95_db: float | None
    calibration_coverage_percent: float | None
    note: str | None = None
    missing_bins: int = 0
    seam_count: int = 0


@dataclass(frozen=True, slots=True)
class SweepSeamEvidence:
    """Measured mathematical agreement of two adjacent input segments."""

    left_segment_index: int
    right_segment_index: int
    overlap_start_hz: float
    overlap_stop_hz: float
    sample_count: int
    correction_db: float
    before_p95_db: float
    after_p95_db: float


@dataclass(frozen=True, slots=True)
class SweepSegmentSpectrum:
    """One already-reduced segment spectrum admitted to the stitcher.

    The arrays are copied into immutable, bounded snapshots at the service
    boundary.  This contract carries spectra only; raw I/Q is not a sweep UI
    input.
    """

    segment_index: int
    source_id: str
    config_generation: int
    frequencies_hz: np.ndarray
    values_db: np.ndarray
    unit: str = "dBFS/bin"

    def __post_init__(self) -> None:
        if self.segment_index < 0 or self.config_generation < 0:
            raise ValueError("sweep segment identity must be non-negative")
        if not self.source_id.strip() or not self.unit.strip():
            raise ValueError("sweep spectrum source and unit must not be blank")
        frequency = np.asarray(self.frequencies_hz, dtype=np.float64)
        values = np.asarray(self.values_db, dtype=np.float64)
        if frequency.ndim != 1 or values.ndim != 1 or frequency.size != values.size or frequency.size < 2:
            raise ValueError("sweep spectrum arrays must be equal one-dimensional arrays with at least two bins")
        if frequency.size > 2_000_000:
            raise ValueError("sweep segment spectrum exceeds the bounded input bin limit")
        if not np.all(np.isfinite(frequency)) or not np.all(np.diff(frequency) > 0.0):
            raise ValueError("sweep spectrum frequency grid must be finite and strictly increasing")
        if np.any(np.isposinf(values)):
            raise ValueError("sweep spectrum values must not contain positive infinity")
        frequency = np.array(frequency, dtype=np.float64, copy=True)
        values = np.array(values, dtype=np.float64, copy=True)
        frequency.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequency)
        object.__setattr__(self, "values_db", values)


@dataclass(frozen=True, slots=True)
class StitchedSweepGrid:
    """Immutable full-span grid and the evidence required to trust each bin."""

    source_id: str
    config_generation: int | None
    frequencies_hz: np.ndarray
    values_db: np.ndarray
    quality_flags: np.ndarray
    source_segment_indices: np.ndarray
    missing_segment_indices: tuple[int, ...]
    segment_config_generations: tuple[tuple[int, int], ...]
    seams: tuple[SweepSeamEvidence, ...]
    unit: str

    def __post_init__(self) -> None:
        frequency = np.asarray(self.frequencies_hz, dtype=np.float64)
        values = np.asarray(self.values_db, dtype=np.float32)
        quality = np.asarray(self.quality_flags, dtype=np.uint16)
        sources = np.asarray(self.source_segment_indices, dtype=np.int32)
        if frequency.ndim != 1 or frequency.size < 2 or frequency.size > 2_000_000:
            raise ValueError("stitched sweep grid must contain 2..2,000,000 bins")
        if any(item.ndim != 1 or item.size != frequency.size for item in (values, quality, sources)):
            raise ValueError("stitched sweep arrays must be one-dimensional and equal-sized")
        if not np.all(np.isfinite(frequency)) or not np.all(np.diff(frequency) > 0.0):
            raise ValueError("stitched sweep frequency grid must be finite and strictly increasing")
        if not self.source_id.strip() or not self.unit.strip() or (
            self.config_generation is not None and self.config_generation < 0
        ):
            raise ValueError("stitched sweep provenance is invalid")
        if any(index < 0 for index in self.missing_segment_indices) or len(set(self.missing_segment_indices)) != len(self.missing_segment_indices):
            raise ValueError("stitched sweep missing segment indices must be unique and non-negative")
        generations = tuple(self.segment_config_generations)
        if (
            any(index < 0 or generation < 0 for index, generation in generations)
            or len({index for index, _generation in generations}) != len(generations)
        ):
            raise ValueError("stitched sweep segment generations must be unique and non-negative")
        if self.config_generation is not None and any(generation != self.config_generation for _index, generation in generations):
            raise ValueError("single stitched generation must agree with every segment generation")
        for field_name, array in (("frequencies_hz", frequency), ("values_db", values), ("quality_flags", quality), ("source_segment_indices", sources)):
            immutable = np.array(array, copy=True)
            immutable.setflags(write=False)
            object.__setattr__(self, field_name, immutable)

    @property
    def missing_bin_count(self) -> int:
        return int(np.count_nonzero(self.quality_flags & int(SweepBinQuality.MISSING_SEGMENT)))


@dataclass(frozen=True, slots=True)
class SweepRateMetrics:
    """Sweep throughput evidence; analytical FFT LPS is intentionally separate."""

    evidence: SweepRateEvidence
    elapsed_seconds: float
    megahertz_per_second: float | None
    sweeps_per_second: float | None
    fft_lps: float | None

    def __post_init__(self) -> None:
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0.0:
            raise ValueError("sweep rate elapsed seconds must be finite and non-negative")
        for value in (self.megahertz_per_second, self.sweeps_per_second, self.fft_lps):
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise ValueError("sweep rate values must be finite and non-negative when known")
        if self.evidence is SweepRateEvidence.NOT_MEASURED and any(
            value is not None for value in (self.megahertz_per_second, self.sweeps_per_second, self.fft_lps)
        ):
                raise ValueError("unmeasured sweep rate evidence must not contain rate values")


class SweepSegmentState(StrEnum):
    """Terminal evidence state of one planned physical sweep segment."""

    COMPLETED = "completed"
    MISSING = "missing"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SweepSegmentEvidence:
    """Low-rate lifecycle/readback/loss evidence for one physical segment."""

    segment_index: int
    state: SweepSegmentState
    requested_center_hz: float
    applied_center_hz: float | None
    config_generation: int | None
    reconfigure_seconds: float
    settling_seconds: float
    capture_seconds: float
    accepted_spectrum_frames: int
    rejected_stale_frames: int
    transient_blocks_discarded: int
    source_blocks_dropped: int
    acquisition_blocks_dropped: int
    fft_frames_dropped: int
    analytical_fft_lps: float | None
    error: str | None = None
    # R10-C appends physical-run observations.  They are optional only for
    # synthetic/failed paths and older callers; a native physical evidence
    # writer must preserve them when its binding exposes the metric.
    observed_device_iq_sample_rate_hz: float | None = None
    observed_device_iq_samples: int | None = None
    source_short_reads: int | None = None
    source_refill_errors: int | None = None
    source_output_pool_exhaustions: int | None = None
    source_estimated_dropped_samples: int | None = None
    acquisition_queue_high_water: int | None = None
    acquisition_queue_capacity: int | None = None
    spectrum_queue_high_water: int | None = None
    spectrum_queue_capacity: int | None = None
    end_to_end_latency_ms: float | None = None
    applied_sample_rate_hz: float | None = None
    applied_analog_bandwidth_hz: float | None = None
    applied_gain_db: float | None = None

    def __post_init__(self) -> None:
        if self.segment_index < 0 or not math.isfinite(self.requested_center_hz):
            raise ValueError("sweep segment evidence identity is invalid")
        if self.applied_center_hz is not None and not math.isfinite(self.applied_center_hz):
            raise ValueError("sweep segment applied centre must be finite when known")
        if self.config_generation is not None and self.config_generation < 0:
            raise ValueError("sweep segment generation must be non-negative when known")
        for value in (
            self.reconfigure_seconds,
            self.settling_seconds,
            self.capture_seconds,
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError("sweep segment duration must be finite and non-negative")
        for value in (
            self.accepted_spectrum_frames,
            self.rejected_stale_frames,
            self.transient_blocks_discarded,
            self.source_blocks_dropped,
            self.acquisition_blocks_dropped,
            self.fft_frames_dropped,
        ):
            if value < 0:
                raise ValueError("sweep segment counters must be non-negative")
        if self.analytical_fft_lps is not None and (
            not math.isfinite(self.analytical_fft_lps) or self.analytical_fft_lps < 0.0
        ):
            raise ValueError("sweep analytical FFT LPS must be finite and non-negative when known")
        for optional_float, label in (
            (self.observed_device_iq_sample_rate_hz, "observed device I/Q sample rate"),
            (self.end_to_end_latency_ms, "end-to-end latency"),
        ):
            if optional_float is not None and (
                not math.isfinite(optional_float) or optional_float < 0.0
            ):
                raise ValueError(f"sweep {label} must be finite and non-negative when known")
        for optional_applied_value, label in (
            (self.applied_sample_rate_hz, "applied sample rate"),
            (self.applied_analog_bandwidth_hz, "applied analog bandwidth"),
        ):
            if optional_applied_value is not None and (
                not math.isfinite(optional_applied_value) or optional_applied_value <= 0.0
            ):
                raise ValueError(f"sweep {label} must be finite and positive when known")
        if self.applied_gain_db is not None and not math.isfinite(self.applied_gain_db):
            raise ValueError("sweep applied gain must be finite when known")
        for optional_counter, label in (
            (self.observed_device_iq_samples, "observed device I/Q samples"),
            (self.source_short_reads, "source short reads"),
            (self.source_refill_errors, "source refill errors"),
            (self.source_output_pool_exhaustions, "source output pool exhaustions"),
            (self.source_estimated_dropped_samples, "source estimated dropped samples"),
        ):
            if optional_counter is not None and optional_counter < 0:
                raise ValueError(f"sweep {label} must be non-negative when known")
        self._validate_queue_observation(
            self.acquisition_queue_high_water,
            self.acquisition_queue_capacity,
            "acquisition",
        )
        self._validate_queue_observation(
            self.spectrum_queue_high_water,
            self.spectrum_queue_capacity,
            "spectrum",
        )

    @staticmethod
    def _validate_queue_observation(
        high_water: int | None,
        capacity: int | None,
        label: str,
    ) -> None:
        if (high_water is None) != (capacity is None):
            raise ValueError(f"sweep {label} queue high-water and capacity must be recorded together")
        if high_water is not None and (high_water < 0 or capacity is None or capacity <= 0 or high_water > capacity):
            raise ValueError(f"sweep {label} queue observation is invalid")


@dataclass(frozen=True, slots=True)
class SweepResult:
    state: SweepState
    plan: SweepPlan
    duration_seconds: float
    quality: SweepQuality
    error: str | None = None
    stitched_grid: StitchedSweepGrid | None = None
    rate_metrics: SweepRateMetrics | None = None
    segment_evidence: tuple[SweepSegmentEvidence, ...] = ()

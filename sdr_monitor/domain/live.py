"""Immutable domain state shared by Home, Live and future device adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from .identity import (
    ConfigurationGeneration,
    FrameSequence,
    LossReason,
    SessionId,
    SourceId,
    TimestampNs,
    TimestampQuality,
    as_configuration_generation,
    as_frame_sequence,
    as_session_id,
    as_source_id,
    as_timestamp_ns,
    as_timestamp_quality,
    normalize_loss_reasons,
)
from .receiver_topology import ReceiverTopologySnapshot
from .analyzer_resources import estimate_analyzer_reduced
from .spectrum_provenance import SpectrumProvenance
from .presentation_omission import PresentationOmission


class DeviceTransport(StrEnum):
    USB = "usb"
    IP = "ip"
    MANUAL = "manual"


class BackendKind(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"
    HIP = "hip"


class LiveSessionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class LiveErrorKind(StrEnum):
    """Actionable error classification used by the standalone UI.

    The previous implementation treated every error as "device missing" and
    therefore reopened the discovery dialog for configuration/backend/stream
    failures.  Keeping the category in the immutable snapshot lets the UI
    choose the correct recovery action without parsing localized text.
    """

    DEVICE_NOT_FOUND = "device_not_found"
    CONNECTION_FAILED = "connection_failed"
    CONFIGURATION_REJECTED = "configuration_rejected"
    STREAM_START_FAILED = "stream_start_failed"
    STREAM_STALLED = "stream_stalled"
    BACKEND_FAILED = "backend_failed"
    INTERNAL = "internal"


class CalibrationQuality(StrEnum):
    UNCALIBRATED = "uncalibrated"
    CALIBRATED = "calibrated"
    MISMATCH = "mismatch"


_MEBIBYTE = 1024 * 1024
_NATIVE_BUFFER_SAMPLES = 262_144
_NATIVE_ACQUISITION_QUEUE_CAPACITY = 16
_NATIVE_SPECTRUM_QUEUE_CAPACITY = 4
_NATIVE_PERSISTENCE_SNAPSHOT_CAPACITY = 2


@dataclass(frozen=True, slots=True)
class LiveResourceEstimate:
    """Conservative upper bound for allocations owned by one live engine.

    The estimate intentionally includes the CPU fallback path even when the
    requested backend is CUDA/HIP.  A backend failure must not turn a
    previously admissible configuration into an unbounded allocation.
    """

    iq_pool_bytes: int
    dsp_working_bytes: int
    spectrum_backlog_bytes: int
    persistence_state_bytes: int
    persistence_snapshot_bytes: int
    total_bytes: int

    @property
    def persistence_bytes(self) -> int:
        return self.persistence_state_bytes + self.persistence_snapshot_bytes


@dataclass(frozen=True, slots=True)
class LiveResourceBudget:
    """Hard preflight limits for the bounded native FixedBand pipeline.

    These values mirror the native ``FixedBandConfig`` admission check.  They
    are limits, not a promise that all memory is resident: the device and DSP
    allocate lazily on configure/start, but a request outside this envelope is
    rejected before either side effect occurs.
    """

    max_iq_pool_bytes: int = 128 * _MEBIBYTE
    max_dsp_working_bytes: int = 128 * _MEBIBYTE
    max_spectrum_backlog_bytes: int = 128 * _MEBIBYTE
    max_persistence_bytes: int = 256 * _MEBIBYTE
    max_total_bytes: int = 512 * _MEBIBYTE

    def estimate(self, configuration: "LiveConfiguration") -> LiveResourceEstimate:
        hop_size = max(1, int(round(configuration.fft_size * (1.0 - configuration.overlap_ratio))))
        output_capacity = max(
            _NATIVE_BUFFER_SAMPLES // hop_size + 3,
            _NATIVE_SPECTRUM_QUEUE_CAPACITY,
        )
        iq_pool_bytes = (
            _NATIVE_BUFFER_SAMPLES
            * 4
            * (_NATIVE_ACQUISITION_QUEUE_CAPACITY + 3)
        )
        # CpuDspBackend currently allocates f32 and f64 rings/staging/output
        # buffers plus both accumulator families.  The 32 bytes/bin margin
        # covers vector and FFT-provider bookkeeping without relying on an
        # allocator-specific estimate.
        dsp_working_bytes = configuration.fft_size * (116 + 48 * 1)
        # Each pending frame owns an axis/value pair plus a small per-frame
        # allowance.  This covers the backend's internal output deque, whose
        # capacity is derived from one device block rather than the public
        # latest-wins queue.
        spectrum_backlog_bytes = estimate_analyzer_reduced(
            "rtbw", configuration.fft_size, output_capacity,
        ).output_bytes

        persistence_state_bytes = 0
        persistence_snapshot_bytes = 0
        if configuration.persistence_enabled:
            cells = configuration.fft_size * configuration.persistence_power_bins
            # Native persistence stores one float32 histogram.  Exact rolling
            # mode also retains its explicit uint32 row history; exponential
            # mode has only scalar epoch/normalization state between frames.
            persistence_state_bytes = cells * 4
            if configuration.persistence_mode == "rolling-exact":
                persistence_state_bytes += configuration.persistence_window_frames * configuration.fft_size * 4
            # Native owns a constructing snapshot and two latest-wins queue
            # entries.  The service can concurrently retain one immutable
            # zero-copy NumPy view.  Axes are shared, but conservative
            # admission includes their retained lifetime, not a fake copy.
            persistence_snapshot_bytes = (
                cells * 4 * (_NATIVE_PERSISTENCE_SNAPSHOT_CAPACITY + 2)
                + configuration.fft_size * 8 * (_NATIVE_PERSISTENCE_SNAPSHOT_CAPACITY + 2)
            )
        total_bytes = (
            iq_pool_bytes
            + dsp_working_bytes
            + spectrum_backlog_bytes
            + persistence_state_bytes
            + persistence_snapshot_bytes
        )
        return LiveResourceEstimate(
            iq_pool_bytes=iq_pool_bytes,
            dsp_working_bytes=dsp_working_bytes,
            spectrum_backlog_bytes=spectrum_backlog_bytes,
            persistence_state_bytes=persistence_state_bytes,
            persistence_snapshot_bytes=persistence_snapshot_bytes,
            total_bytes=total_bytes,
        )

    def validate(self, configuration: "LiveConfiguration") -> LiveResourceEstimate:
        estimate = self.estimate(configuration)
        limits = (
            ("I/Q pool", estimate.iq_pool_bytes, self.max_iq_pool_bytes),
            ("DSP working set", estimate.dsp_working_bytes, self.max_dsp_working_bytes),
            ("spectrum backlog", estimate.spectrum_backlog_bytes, self.max_spectrum_backlog_bytes),
            ("persistence", estimate.persistence_bytes, self.max_persistence_bytes),
            ("total live-engine memory", estimate.total_bytes, self.max_total_bytes),
        )
        for label, actual, limit in limits:
            if actual > limit:
                raise ValueError(
                    f"live resource budget exceeded for {label}: "
                    f"{actual / _MEBIBYTE:.1f} MiB > {limit / _MEBIBYTE:.1f} MiB"
                )
        return estimate


DEFAULT_LIVE_RESOURCE_BUDGET = LiveResourceBudget()


@dataclass(frozen=True, slots=True)
class LiveDisplayResourceEstimate:
    """Peak storage estimate for the waterfall's preallocated UI images."""

    rows: int
    columns: int
    ring_bytes: int
    render_copy_bytes: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class LiveDisplayResourceBudget:
    """Hard waterfall-image bounds shared by the domain and UI controls.

    ``WaterfallRingBuffer`` retains a data image and a compatibility staging
    image.  The active R06 renderer maps at most two cyclic views directly,
    including bottom-up orientation, so it needs no full render copy.  The
    estimate remains a conservative fixed peak for either direction.
    """

    max_rows: int = 4096
    max_columns: int = 2048
    max_total_bytes: int = 96 * _MEBIBYTE

    def estimate(self, rows: int, columns: int, *, bottom_up: bool = True) -> LiveDisplayResourceEstimate:
        if rows < 1 or columns < 1:
            raise ValueError("waterfall dimensions must be positive")
        if rows > self.max_rows or columns > self.max_columns:
            raise ValueError(
                f"waterfall dimensions exceed display budget: {rows}x{columns} "
                f"> {self.max_rows}x{self.max_columns}"
            )
        image_bytes = rows * columns * np.dtype(np.float32).itemsize
        del bottom_up
        total = image_bytes * 2
        if total > self.max_total_bytes:
            raise ValueError(
                f"waterfall display budget exceeded: {total / _MEBIBYTE:.1f} MiB "
                f"> {self.max_total_bytes / _MEBIBYTE:.1f} MiB"
            )
        return LiveDisplayResourceEstimate(
            rows=rows,
            columns=columns,
            ring_bytes=image_bytes * 2,
            render_copy_bytes=0,
            total_bytes=total,
        )

    def max_history_seconds(self, rows_per_second: int, *, columns: int | None = None) -> int:
        if rows_per_second < 1:
            raise ValueError("waterfall rows per second must be positive")
        active_columns = self.max_columns if columns is None else columns
        self.estimate(1, active_columns)
        return max(1, self.max_rows // rows_per_second)

    def waterfall_dimensions(self, history_seconds: int, rows_per_second: int, columns: int) -> tuple[int, int]:
        if history_seconds < 1 or rows_per_second < 1:
            raise ValueError("waterfall history and rows per second must be positive")
        rows = history_seconds * rows_per_second
        self.estimate(rows, columns)
        return rows, columns


DEFAULT_LIVE_DISPLAY_RESOURCE_BUDGET = LiveDisplayResourceBudget()


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    sample_rates_hz: tuple[float, ...]
    gain_range_db: tuple[float, float]
    analog_bandwidths_hz: tuple[float, ...] = ()
    supported_backends: tuple[BackendKind, ...] = (BackendKind.AUTO, BackendKind.CPU)
    # R10-E0: optional, read-only topology facts.  ``None`` means the active
    # adapter did not enumerate them; it never implies single-RX hardware.
    receiver_topology: ReceiverTopologySnapshot | None = None
    # Presets are not an exhaustive list when the producer publishes ranges.
    # Triples are (inclusive minimum, inclusive maximum, step); zero step
    # denotes a continuous range. Native configure/readback remains authoritative.
    sample_rate_ranges_hz: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    device_id: str
    label: str
    uri: str
    transport: DeviceTransport
    capabilities: DeviceCapabilities
    # A physical Pluto can be visible through USB and its network gadget at
    # the same time.  Discovery exposes one logical device and retains the
    # alternate routes for failover instead of forcing the operator to guess
    # which duplicate row is the same radio.
    alternate_uris: tuple[str, ...] = ()
    serial: str | None = None
    identity_key: str | None = None


@dataclass(frozen=True, slots=True)
class LiveConfiguration:
    center_hz: float = 2.4e9
    sample_rate_hz: float = 20e6
    gain_db: float = 18.0
    # Independent RF front-end passband (Pluto: 0.2–56 MHz). This is NOT the
    # sample rate: the AD936x decimates a 61.44 MSPS stream down to
    # sample_rate_hz, while analog_bandwidth_hz controls the analog/RF filter
    # passband. Keeping them separate avoids confusing the two.
    analog_bandwidth_hz: float | None = None
    # Spectral analysis settings (P07 DSP). Overlap ratio in [0, 0.95) maps to
    # hop = round(fft_size * (1 - overlap_ratio)); averaging_frames >= 1.
    fft_size: int = 4096
    overlap_ratio: float = 0.5
    detector: str = "sample"
    window: str = "hann"
    averaging_frames: int = 1
    # Native spectrum publication is deliberately independent from GUI FPS.
    # The DSP computes every admitted FFT; this value controls only the
    # bounded latest-wins snapshots crossing into Python.
    # R10-D6 selected 240/s from controlled offscreen full-widget evidence as
    # the responsive default. It is not a visible-desktop acceptance claim.
    # Analytical DSP still processes every admitted FFT; 480/720 remain
    # explicit high-temporal-publication choices.
    snapshot_rate_hz: float = 240.0
    backend: BackendKind = BackendKind.AUTO
    # Native persistence configuration.  The UI may hide these fields in
    # Basic mode, but they belong to the applied DSP configuration rather than
    # to PyQtGraph.
    persistence_enabled: bool = True
    persistence_mode: str = "exponential-decay"
    persistence_power_min_db: float = -140.0
    persistence_power_max_db: float = 20.0
    persistence_power_bins: int = 256
    persistence_window_frames: int = 500
    persistence_half_life_s: float = 1.0
    persistence_snapshot_rate_hz: float = 30.0
    profile_id: str | None = None

    def __post_init__(self) -> None:
        if self.center_hz <= 0 or self.sample_rate_hz <= 0:
            raise ValueError("center and sample rate must be positive")
        if self.analog_bandwidth_hz is not None and self.analog_bandwidth_hz <= 0:
            raise ValueError("analog bandwidth must be positive when provided")
        if self.fft_size < 256 or self.fft_size > 262144 or (self.fft_size & (self.fft_size - 1)):
            raise ValueError("fft_size must be a power of two in [256, 262144]")
        if not 0.0 <= self.overlap_ratio < 0.95:
            raise ValueError("overlap_ratio must be in [0, 0.95)")
        if self.averaging_frames < 1:
            raise ValueError("averaging_frames must be at least 1")
        if not 1.0 <= self.snapshot_rate_hz <= 1000.0:
            raise ValueError("snapshot_rate_hz must be in [1, 1000]")
        if self.persistence_power_max_db <= self.persistence_power_min_db:
            raise ValueError("persistence power range must be ordered")
        if self.persistence_power_bins < 16 or self.persistence_power_bins > 4096:
            raise ValueError("persistence_power_bins must be in [16, 4096]")
        normalized_persistence_mode = self.persistence_mode.strip().casefold().replace("_", "-")
        if normalized_persistence_mode not in {"disabled", "rolling-exact", "exponential-decay"}:
            raise ValueError("persistence_mode must be disabled, rolling-exact or exponential-decay")
        object.__setattr__(self, "persistence_mode", normalized_persistence_mode)
        if self.persistence_enabled != (normalized_persistence_mode != "disabled"):
            raise ValueError("persistence_enabled and persistence_mode must agree")
        if self.persistence_window_frames < 1:
            raise ValueError("persistence_window_frames must be positive")
        if self.persistence_half_life_s <= 0.0:
            raise ValueError("persistence_half_life_s must be positive")
        if not 10.0 <= self.persistence_snapshot_rate_hz <= 30.0:
            raise ValueError("persistence_snapshot_rate_hz must be in [10, 30]")
        DEFAULT_LIVE_RESOURCE_BUDGET.validate(self)


@dataclass(frozen=True, slots=True)
class AppliedLiveConfiguration:
    requested: LiveConfiguration
    applied: LiveConfiguration
    adjustments: tuple[str, ...] = ()
    # Empty means prepared/legacy-unknown, not hardware confirmation.
    readback_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveQuality:
    calibration: CalibrationQuality = CalibrationQuality.UNCALIBRATED
    backend: BackendKind = BackendKind.CPU
    fallback_reason: str | None = None
    # True only for the currently published frame when native failover could
    # not replay a complete contiguous history.  It must never be inferred as
    # a device, transport or raw-I/Q continuity claim.
    backend_discontinuity: bool = False
    dropped_blocks: int = 0
    loss_reasons: tuple[LossReason, ...] = ()

    def __post_init__(self) -> None:
        if self.dropped_blocks < 0:
            raise ValueError("dropped_blocks must not be negative")
        object.__setattr__(self, "loss_reasons", normalize_loss_reasons(self.loss_reasons))


@dataclass(frozen=True, slots=True)
class LivePerformance:
    """Coherent native performance snapshot for diagnostics and UI readout."""

    # ``4`` adds R10-D8 scalar refill threshold/gap timing. R03 appends an explicit
    # live-loss taxonomy while preserving the schema's append-only rule. New
    # optional fields are
    # appended only; consumers must use ``stage_timing_mask`` before treating
    # a zero duration as a measured result.
    metrics_schema_version: int = 4
    analytical_fft_rate_hz: float = 0.0
    spectrum_snapshot_rate_hz: float = 0.0
    iq_sample_rate_hz: float = 0.0
    fft_frames_computed: int = 0
    fft_frames_dropped: int = 0
    iq_samples_dropped: int = 0
    iq_blocks_dropped: int = 0
    source_samples_dropped: int = 0
    source_blocks_dropped: int = 0
    acquisition_queue_samples_dropped: int = 0
    acquisition_queue_blocks_dropped: int = 0
    snapshots_emitted: int = 0
    snapshots_superseded: int = 0
    persistence_updates: int = 0
    persistence_snapshots_superseded: int = 0
    acquisition_queue_depth: int = 0
    spectrum_queue_depth: int = 0
    cpu_processing_ms: float = 0.0
    gpu_processing_ms: float = 0.0
    h2d_ms: float = 0.0
    d2h_ms: float = 0.0
    end_to_end_latency_ms: float = 0.0
    stage_timing_mask: int = 0
    input_unpack_ms: float = 0.0
    window_ms: float = 0.0
    fft_ms: float = 0.0
    detector_ms: float = 0.0
    persistence_processing_ms: float = 0.0
    publication_processing_ms: float = 0.0
    # R10-D6: Python bridge observability.  These counters describe
    # latest-wins coalescing after the bounded native output queue; they are
    # not analytical FFT drops and never change native engine semantics.
    bridge_native_frames_polled: int = 0
    bridge_frames_coalesced: int = 0
    bridge_frames_published: int = 0
    source_sequence_discontinuities: int = 0
    source_sample_index_discontinuities: int = 0
    source_timestamp_regressions: int = 0
    source_estimated_timestamp_blocks: int = 0
    hardware_overflow_counter_available: bool = False
    # Cumulative host-wall timings from the native source adapter. They are
    # diagnostics for transport/refill versus canonicalization work, not RF
    # timestamps, device-overflow counters or raw-I/Q payloads.
    source_refill_wait_ms: float = 0.0
    source_canonicalization_ms: float = 0.0
    source_refill_calls: int = 0
    source_refill_wait_over_nominal_period: int = 0
    source_refill_wait_over_two_nominal_periods: int = 0
    source_inter_refill_gap_ms: float = 0.0
    source_inter_refill_gap_count: int = 0
    # Append-only: None distinguishes default zeros from observed rates.
    rate_observation_interval_s: float | None = None


# Keep the established public name while making the snapshot role explicit in
# new code and reporting documents. R02 extends these metrics without a second
# parallel telemetry type.
PerformanceSnapshot = LivePerformance


@dataclass(frozen=True, slots=True)
class LiveSpectrumFrame:
    """One bounded, immutable spectrum publication for the Live Monitor.

    Arrays are copied read-only at construction; the frame carries native
    drop/gap counters so the UI never hides loss on the acquisition path.
    """

    sequence: FrameSequence
    timestamp_ns: TimestampNs
    center_frequency_hz: float
    sample_rate_hz: float
    fft_size: int
    hop_size: int
    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str = "dBFS/bin"
    dropped_samples_before: int = 0
    dropped_iq_blocks_before: int = 0
    dropped_fft_frames_before: int = 0
    source_id: SourceId = SourceId("unknown")
    config_generation: ConfigurationGeneration = ConfigurationGeneration(0)
    timestamp_quality: TimestampQuality = TimestampQuality.UNKNOWN
    loss_reasons: tuple[LossReason, ...] = ()
    backend_fallback: bool = False
    backend_discontinuity: bool = False
    # Full native QualityFlag wire mask (schema v5). None means unavailable,
    # never a clean measurement. Derived loss/fallback fields remain compatible.
    native_quality_flags: int | None = None
    receiver_id: str | None = None
    acquisition_epoch: int | None = None
    clock_domain: str | None = None
    accumulation_id: str | None = None
    numerical_provenance: SpectrumProvenance | None = None

    def __post_init__(self) -> None:
        if self.numerical_provenance is not None and not isinstance(self.numerical_provenance, SpectrumProvenance):
            raise ValueError("numerical provenance must be immutable SpectrumProvenance or unknown")
        frequencies = np.asarray(self.frequencies_hz, dtype=np.float64).reshape(-1)
        values = np.asarray(self.values, dtype=np.float32).reshape(-1)
        if frequencies.size == 0 or values.size != frequencies.size:
            raise ValueError("spectrum frame requires equal non-empty frequency/value arrays")
        frequencies.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "sequence", as_frame_sequence(self.sequence))
        object.__setattr__(self, "timestamp_ns", as_timestamp_ns(self.timestamp_ns))
        object.__setattr__(self, "source_id", as_source_id(self.source_id))
        object.__setattr__(self, "config_generation", as_configuration_generation(self.config_generation))
        object.__setattr__(self, "timestamp_quality", as_timestamp_quality(self.timestamp_quality))
        object.__setattr__(self, "loss_reasons", normalize_loss_reasons(self.loss_reasons))
        object.__setattr__(self, "backend_fallback", bool(self.backend_fallback))
        object.__setattr__(self, "backend_discontinuity", bool(self.backend_discontinuity))
        if self.native_quality_flags is not None and (
            type(self.native_quality_flags) is not int or not 0 <= self.native_quality_flags <= 0xFFFFFFFF
        ):
            raise ValueError("native quality flags must be a uint32 mask or unknown")
        if min(self.dropped_samples_before, self.dropped_iq_blocks_before, self.dropped_fft_frames_before) < 0:
            raise ValueError("spectrum drop counters must not be negative")
        object.__setattr__(self, "fft_size", int(self.fft_size))
        object.__setattr__(self, "hop_size", int(self.hop_size))
        object.__setattr__(self, "unit", self.unit or "dBFS/bin")


@dataclass(frozen=True, slots=True)
class LivePersistenceFrame:
    """One bounded, immutable native persistence (density) publication.

    ``density`` is a row-major float32 image shaped
    ``(power_bins, frequency_bins)``. It is renderer-ready: frequency is the
    horizontal axis and power is the vertical axis, so normal rendering needs
    neither a transpose nor a copy. The immutable raw histogram is normalized
    for probability by ``probability_scale`` and for decayed hit estimates by
    ``count_scale``. Arrays retain native ownership and are marked read-only.
    """

    update_sequence: FrameSequence
    timestamp_ns: TimestampNs
    source_frame_sequence: FrameSequence
    power_min_db: float
    power_max_db: float
    power_bins: int
    frequency_bins: int
    processed_frames: int
    exponential_decay: bool
    frequencies_hz: np.ndarray
    density: np.ndarray
    probability_scale: float = 1.0
    count_scale: float = 1.0
    source_id: SourceId = SourceId("unknown")
    config_generation: ConfigurationGeneration = ConfigurationGeneration(0)
    timestamp_quality: TimestampQuality = TimestampQuality.UNKNOWN
    unit: str | None = None
    producer_identity_available: bool = False
    receiver_id: str | None = None
    acquisition_epoch: int | None = None
    clock_domain: str | None = None
    accumulation_id: str | None = None

    def __post_init__(self) -> None:
        frequencies = np.asarray(self.frequencies_hz, dtype=np.float64).reshape(-1)
        density = np.asarray(self.density, dtype=np.float32).reshape(self.power_bins, self.frequency_bins)
        if frequencies.size != self.frequency_bins or density.size == 0:
            raise ValueError("persistence frame requires matching frequency/density arrays")
        if self.probability_scale < 0.0 or self.count_scale < 0.0:
            raise ValueError("persistence scales must not be negative")
        frequencies.setflags(write=False)
        density.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "density", density)
        object.__setattr__(self, "update_sequence", as_frame_sequence(self.update_sequence))
        object.__setattr__(self, "source_frame_sequence", as_frame_sequence(self.source_frame_sequence))
        object.__setattr__(self, "timestamp_ns", as_timestamp_ns(self.timestamp_ns))
        object.__setattr__(self, "source_id", as_source_id(self.source_id))
        object.__setattr__(self, "config_generation", as_configuration_generation(self.config_generation))
        object.__setattr__(self, "timestamp_quality", as_timestamp_quality(self.timestamp_quality))
        object.__setattr__(self, "probability_scale", float(self.probability_scale))
        object.__setattr__(self, "count_scale", float(self.count_scale))


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    generation: ConfigurationGeneration
    sequence: FrameSequence
    state: LiveSessionState
    device: DeviceDescriptor | None = None
    applied: AppliedLiveConfiguration | None = None
    quality: LiveQuality = field(default_factory=LiveQuality)
    unit: str = "dBFS/bin"
    error: str | None = None
    error_kind: LiveErrorKind | None = None
    spectrum: LiveSpectrumFrame | None = None
    persistence: LivePersistenceFrame | None = None
    performance: LivePerformance = field(default_factory=LivePerformance)
    session_id: SessionId = SessionId("unknown")

    # Application lifecycle metadata, not an acquisition/continuity claim.
    # Remains true after a dispatched failure until explicit Stop succeeds.
    stop_required: bool = False
    active_source_id: SourceId | None = None
    receiver_id: str | None = None
    acquisition_epoch: int | None = None
    clock_domain: str | None = None
    active_config_generation: int | None = None
    # Application display projection only; never changes quality or RX state.
    presentation_omission: "PresentationOmission | None" = None

    def __post_init__(self) -> None:
        if self.presentation_omission is not None and not isinstance(self.presentation_omission, PresentationOmission):
            raise TypeError("invalid Live presentation omission")
        if self.presentation_omission is not None and (self.spectrum is not None or self.persistence is not None):
            raise ValueError("omitted Live presentation cannot retain measurement arrays")
        object.__setattr__(self, "generation", as_configuration_generation(self.generation))
        object.__setattr__(self, "sequence", as_frame_sequence(self.sequence))
        object.__setattr__(self, "session_id", as_session_id(self.session_id))
        if self.active_source_id is not None:
            object.__setattr__(self, "active_source_id", as_source_id(self.active_source_id))

    @property
    def reports_dbm(self) -> bool:
        return self.unit.casefold().startswith("dbm") and self.quality.calibration is CalibrationQuality.CALIBRATED


class LiveAdmissionRejected(RuntimeError):
    """Atomic backend refusal before this caller acquires any RX resources."""

"""Shared reduced-data accounting; excludes acquisition and DSP engine memory."""
from dataclasses import dataclass
import math


def _finite_positive(value: object, name: str, *, allow_zero: bool = False) -> None:
    """Reject bools and malformed frozen payloads at the public boundary."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite numeric value")
    if not math.isfinite(value) or value < 0.0 or (not allow_zero and value == 0.0):
        raise ValueError(f"{name} must be finite and positive")


def _bounded_int(value: object, name: str, *, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside its bounded integer range")


@dataclass(frozen=True, slots=True)
class AnalyzerReducedEstimate:
    mode: str
    output_bins: int
    output_bytes: int
    assembly_bytes: int
    retained_segment_bytes: int

    def __post_init__(self) -> None:
        if self.mode not in ("rtbw", "sweep"):
            raise ValueError("unsupported analyzer strategy")
        _bounded_int(self.output_bins, "output bins", minimum=2, maximum=2_000_000)
        for name, value in (
            ("output bytes", self.output_bytes),
            ("assembly bytes", self.assembly_bytes),
            ("retained segment bytes", self.retained_segment_bytes),
        ):
            _bounded_int(value, name, minimum=0, maximum=(1 << 63) - 1)

    @property
    def total_bytes(self) -> int:
        return self.output_bytes + self.assembly_bytes + self.retained_segment_bytes


@dataclass(frozen=True, slots=True)
class AnalyzerGeometryPreflight:
    """Planned geometry, not applied readback or an RF-performance promise.

    ``physical_bin_spacing_hz`` is the transform bin spacing ``Fs / F``.
    It is deliberately distinct from ``output_spacing_hz`` (which can be
    ``W / N`` for a Sweep plan), and from RBW/ENBW.  This application layer
    has no native window-calibration/readback contract that could make an RBW
    or ENBW claim, so those fields are explicit ``None`` rather than aliases
    for either grid spacing.

    New fields are appended with defaults to retain compatibility with the
    existing seven-position construction sites.
    """
    mode: str
    sample_rate_hz: float
    physical_fft_size: int
    output_spacing_hz: float
    segment_count: int
    segment_stride_hz: float
    reduced: AnalyzerReducedEstimate
    usable_window_hz: float = 0.0
    analysis_bins_per_usable_window: int = 0
    physical_bin_spacing_hz: float = 0.0
    rbw_hz: float | None = None
    enbw_hz: float | None = None
    statistics_payload_bytes: int = 0
    fft_averaging_frames: int = 1
    minimum_samples_per_spectrum: int = 0

    def __post_init__(self) -> None:
        if self.mode not in ("rtbw", "sweep"):
            raise ValueError("unsupported analyzer strategy")
        _finite_positive(self.sample_rate_hz, "sample rate")
        _bounded_int(self.physical_fft_size, "physical FFT size", minimum=2, maximum=262_144)
        _finite_positive(self.output_spacing_hz, "output spacing")
        _bounded_int(self.segment_count, "segment count", minimum=1, maximum=64)
        _finite_positive(self.segment_stride_hz, "segment stride", allow_zero=True)
        _finite_positive(self.usable_window_hz, "usable window", allow_zero=True)
        if self.mode == "sweep" and (self.segment_stride_hz == 0.0 or self.usable_window_hz == 0.0):
            raise ValueError("Sweep geometry requires a usable window and positive stride")
        if self.mode == "rtbw" and self.segment_count != 1:
            raise ValueError("RTBW geometry has exactly one segment")
        if self.analysis_bins_per_usable_window != 0:
            _bounded_int(
                self.analysis_bins_per_usable_window,
                "analysis bins per usable window",
                minimum=256,
                maximum=262_144,
            )
            if self.analysis_bins_per_usable_window & (self.analysis_bins_per_usable_window - 1):
                raise ValueError("analysis bins per usable window must be a power of two")
        _finite_positive(self.physical_bin_spacing_hz, "physical bin spacing", allow_zero=True)
        for name, value in (("RBW", self.rbw_hz), ("ENBW", self.enbw_hz)):
            if value is not None:
                _finite_positive(value, name)
        if not isinstance(self.reduced, AnalyzerReducedEstimate) or self.reduced.mode != self.mode:
            raise ValueError("reduced estimate must match analyzer strategy")
        _bounded_int(self.statistics_payload_bytes, "statistics payload", minimum=0, maximum=512 * 1024 * 1024)
        _bounded_int(self.fft_averaging_frames, "FFT averaging", minimum=1, maximum=(1 << 32) - 1)
        _bounded_int(self.minimum_samples_per_spectrum, "spectrum sample support", minimum=0, maximum=(1 << 63) - 1)


def estimate_analyzer_reduced(
    mode: str, output_bins: int, retained_outputs: int, *,
    physical_fft_size: int = 0, segment_count: int = 0,
) -> AnalyzerReducedEstimate:
    if mode not in ("rtbw", "sweep"):
        raise ValueError("unsupported analyzer strategy")
    if (type(output_bins) is not int or not 2 <= output_bins <= 2_000_000
            or type(retained_outputs) is not int or retained_outputs < 1):
        raise ValueError("analyzer output exceeds bounded bin limit or retention")
    if mode == "rtbw":
        if physical_fft_size or segment_count:
            raise ValueError("RTBW reduced budget cannot contain sweep segments")
        # Preserve the established Live axis/value + per-frame margin.
        return AnalyzerReducedEstimate(mode, output_bins, output_bins * 16 * retained_outputs, 0, 0)
    if (type(physical_fft_size) is not int or not 2 <= physical_fft_size <= 262144
            or type(segment_count) is not int or not 1 <= segment_count <= 64):
        raise ValueError("invalid sweep retained-segment geometry")
    # Output: f64 axis + f32 values + u32 quality + i32 segment index.
    # Assembly: f64 power + u32 coverage/quality + i32 source + shared f64 axis.
    # Retained input spectra: conservative 16 bytes/bin and one f64 scratch.
    return AnalyzerReducedEstimate(
        mode, output_bins, output_bins * 20 * retained_outputs,
        output_bins * 28,
        segment_count * physical_fft_size * 16 + physical_fft_size * 8,
    )

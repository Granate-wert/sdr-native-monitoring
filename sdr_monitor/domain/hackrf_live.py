"""Typed no-I/O HackRF RX/DSP profile; compatible with the existing request API."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from numbers import Real

from .identity import SourceId, as_source_id
from .live import BackendKind
from .layer_ready import DENSITY_LAYER_SCALAR_RESERVATION_BYTES

_HACKRF_FILTER_BANDWIDTHS_HZ = frozenset(
    (
        1_750_000,
        2_500_000,
        3_500_000,
        5_000_000,
        5_500_000,
        6_000_000,
        7_000_000,
        8_000_000,
        9_000_000,
        10_000_000,
        12_000_000,
        14_000_000,
        15_000_000,
        20_000_000,
        24_000_000,
        28_000_000,
    )
)
_WINDOWS = frozenset(
    (
        "rectangular",
        "hann",
        "blackman_harris_4term",
        "flat_top",
        "nuttall",
        "kaiser",
    )
)
_DETECTORS = frozenset(("sample", "peak", "negative_peak", "rms", "average_power"))
_MAX_QUEUE_CAPACITY = 64
# The qualified official runtime supplies 262144-byte CI8 transfers; the native
# pipeline drains CPU output after each transfer. A different runtime transfer
# size needs requalification against its reported source.slot_bytes. This is
# a default for the qualified runtime, not an inferred hardware guarantee.
# Keep the analytical burst buffer distinct from
# the small freshest-window presentation queue. These are bounded scalar
# policy values, not runtime discovery or a process-RSS guarantee.
_TRANSFER_SAMPLES = 262_144 // 2
_MAX_DSP_OUTPUT_CAPACITY = 4096
_SPECTRUM_QUEUE_BUDGET_BYTES = 64 * 1024 * 1024
_SPECTRUM_FRAME_OVERHEAD_ALLOWANCE_BYTES = 1024
_MAX_GENERATION = (1 << 63) - 1


def _finite_positive(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be finite and positive")  # noqa: TRY004 - preserve the existing public request API.
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return normalized


def _bounded_integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _opaque_source_id(value: object) -> SourceId:
    source_id = as_source_id(value)
    if len(source_id) > 128 or any(token in source_id.casefold() for token in ("usb:", "ip:", "\\", "/")):
        raise ValueError("source_id must be a route-free opaque identifier")
    return source_id


@dataclass(frozen=True, slots=True)
class HackrfLiveRequest:
    """Complete no-side-effect RX/DSP request for one future HackRF owner."""

    center_frequency_hz: float
    sample_rate_hz: float
    baseband_filter_hz: int
    lna_gain_db: int
    vga_gain_db: int
    rf_amplifier_enabled: bool = False
    bias_tee_enabled: bool = False
    fft_size: int = 4096
    hop_size: int = 2048
    window: str = "hann"
    detector: str = "sample"
    backend: BackendKind = BackendKind.CPU
    persistence_enabled: bool = False
    slot_count: int = 32
    ready_capacity: int = 24
    # None selects enough outputs for one transfer, including FFT overlap
    # carried from the preceding transfer. Keep the automatic policy as None
    # so dataclasses.replace(..., fft_size=..., hop_size=...) recalculates it.
    # Explicit smaller queues remain
    # available for bounded lossy/diagnostic profiles and are never increased.
    dsp_output_capacity: int | None = None
    presentation_capacity: int = 4
    configuration_generation: int = 1
    source_id: SourceId = field(default_factory=lambda: SourceId("native.hackrf.live"))
    # Detector groups consume consecutive analytical FFTs, not display frames.
    # Optional native DSP profile protocol1 is required for non-default groups.
    averaging_frames: int = 1
    persistence_mode: str = "disabled"
    persistence_power_min_db: float = -140.0
    persistence_power_max_db: float = 20.0
    persistence_power_bins: int = 256
    persistence_window_frames: int = 500
    persistence_half_life_s: float = 1.0
    persistence_snapshot_rate_hz: float = 15.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "center_frequency_hz", _finite_positive(self.center_frequency_hz, "center_frequency_hz"))
        object.__setattr__(self, "sample_rate_hz", _finite_positive(self.sample_rate_hz, "sample_rate_hz"))
        if (
            isinstance(self.baseband_filter_hz, bool)
            or not isinstance(self.baseband_filter_hz, int)
            or self.baseband_filter_hz not in _HACKRF_FILTER_BANDWIDTHS_HZ
        ):
            raise ValueError("baseband_filter_hz must be a documented HackRF setting")
        if self.baseband_filter_hz > self.sample_rate_hz:
            raise ValueError("baseband_filter_hz must not exceed sample_rate_hz")
        if (
            isinstance(self.lna_gain_db, bool)
            or not isinstance(self.lna_gain_db, int)
            or not 0 <= self.lna_gain_db <= 40
            or self.lna_gain_db % 8 != 0
        ):
            raise ValueError("lna_gain_db must be a HackRF 0..40 dB step of 8")
        if (
            isinstance(self.vga_gain_db, bool)
            or not isinstance(self.vga_gain_db, int)
            or not 0 <= self.vga_gain_db <= 62
            or self.vga_gain_db % 2 != 0
        ):
            raise ValueError("vga_gain_db must be a HackRF 0..62 dB step of 2")
        if self.rf_amplifier_enabled or self.bias_tee_enabled:
            raise ValueError("R11-M requires RF amplifier and bias tee disabled")
        fft_size = _bounded_integer(self.fft_size, "fft_size", 256, 262_144)
        if fft_size & (fft_size - 1):
            raise ValueError("fft_size must be a power of two")
        object.__setattr__(self, "fft_size", fft_size)
        hop_size = _bounded_integer(self.hop_size, "hop_size", 1, fft_size)
        object.__setattr__(self, "hop_size", hop_size)
        window = self.window.strip().casefold().replace("-", "_") if isinstance(self.window, str) else ""
        detector = self.detector.strip().casefold().replace("-", "_") if isinstance(self.detector, str) else ""
        if window not in _WINDOWS:
            raise ValueError("window must be a canonical CPU DSP window")
        if detector not in _DETECTORS:
            raise ValueError("detector must be a canonical CPU DSP detector")
        object.__setattr__(self, "window", window)
        object.__setattr__(self, "detector", detector)
        _bounded_integer(self.averaging_frames, "averaging_frames", 1, 256)
        if BackendKind(self.backend) is not BackendKind.CPU:
            raise ValueError("R11-M admits only the CPU DSP backend")
        object.__setattr__(self, "backend", BackendKind.CPU)
        if type(self.persistence_enabled) is not bool:
            raise ValueError("persistence_enabled must be an explicit boolean")
        mode = self.persistence_mode.strip().casefold().replace("_", "-") if isinstance(self.persistence_mode, str) else ""
        if mode not in {"disabled", "rolling-exact", "exponential-decay"}:
            raise ValueError("invalid persistence mode")
        if self.persistence_enabled != (mode != "disabled"):
            raise ValueError("persistence_enabled and persistence_mode must agree")
        object.__setattr__(self, "persistence_mode", mode)
        for name in ("persistence_power_min_db", "persistence_power_max_db"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
                raise ValueError("persistence power bounds must be finite numbers")
            object.__setattr__(self, name, float(value))
        if self.persistence_power_max_db <= self.persistence_power_min_db:
            raise ValueError("persistence power bounds must be ordered")
        _bounded_integer(self.persistence_power_bins, "persistence_power_bins", 16, 4096)
        _bounded_integer(self.persistence_window_frames, "persistence_window_frames", 1, 1_000_000)
        object.__setattr__(self, "persistence_half_life_s",
            _finite_positive(self.persistence_half_life_s, "persistence_half_life_s"))
        rate = _finite_positive(self.persistence_snapshot_rate_hz, "persistence_snapshot_rate_hz")
        if not 10.0 <= rate <= 30.0:
            raise ValueError("persistence_snapshot_rate_hz must be in [10, 30]")
        object.__setattr__(self, "persistence_snapshot_rate_hz", rate)
        if self.persistence_allocation_bytes > 256 * 1024 * 1024:
            raise ValueError("HackRF persistence exceeds the 256 MiB allocation policy")
        slot_count = _bounded_integer(self.slot_count, "slot_count", 1, _MAX_QUEUE_CAPACITY)
        ready_capacity = _bounded_integer(self.ready_capacity, "ready_capacity", 1, slot_count)
        object.__setattr__(self, "slot_count", slot_count)
        object.__setattr__(self, "ready_capacity", ready_capacity)
        output_capacity = self.dsp_output_capacity
        if output_capacity is None:
            output_capacity = (_TRANSFER_SAMPLES + hop_size - 1) // hop_size
        output_capacity = _bounded_integer(
            output_capacity, "dsp_output_capacity", 1, _MAX_DSP_OUTPUT_CAPACITY)
        presentation_capacity = _bounded_integer(
            self.presentation_capacity, "presentation_capacity", 1, _MAX_QUEUE_CAPACITY)
        # Worst-case independent float64 frequencies + float32 values per frame;
        # sharing a frequency grid may reduce actual memory, never raise the cap.
        frame_bytes = fft_size * 12 + _SPECTRUM_FRAME_OVERHEAD_ALLOWANCE_BYTES
        if (output_capacity + presentation_capacity) * frame_bytes > _SPECTRUM_QUEUE_BUDGET_BYTES:
            raise ValueError("HackRF spectrum queues exceed the 64 MiB allocation policy")
        object.__setattr__(self, "presentation_capacity", presentation_capacity)
        object.__setattr__(self, "configuration_generation", _bounded_integer(self.configuration_generation, "configuration_generation", 1, _MAX_GENERATION))
        object.__setattr__(self, "source_id", _opaque_source_id(self.source_id))

    @property
    def persistence_allocation_bytes(self) -> int:
        """Native histogram/ring plus bounded snapshots; NOT a process RSS cap.

        Same conservative accounting as LiveCapacityLimits: histogram, two
        queued immutable images, one constructing image and one service view.
        UI holders remain governed separately by PresentationAllocationBudget.
        """
        if not self.persistence_enabled:
            return 0
        cells = self.fft_size * self.persistence_power_bins
        ring = self.fft_size * self.persistence_window_frames * 4 if self.persistence_mode == "rolling-exact" else 0
        return cells * 4 * 5 + self.fft_size * 8 * 4 + ring + DENSITY_LAYER_SCALAR_RESERVATION_BYTES

    @property
    def resolved_dsp_output_capacity(self) -> int:
        """Validated, deterministic native value; no runtime lookup or allocation."""
        if self.dsp_output_capacity is not None:
            return self.dsp_output_capacity
        return (_TRANSFER_SAMPLES + self.hop_size - 1) // self.hop_size

    @property
    def spectrum_stall_timeout_s(self) -> float:
        """Bounded host watchdog, NOT observed RF/display period or Fs readback.

        Large detector groups intentionally delay the first reduced frame.
        Preserve the old two-second floor; allow three requested group periods.
        """
        group_samples = self.fft_size + (self.averaging_frames - 1) * self.hop_size
        return min(120.0, max(2.0, 3.0 * group_samples / self.sample_rate_hz))


@dataclass(frozen=True, slots=True)
class HackrfConfigurationPatch:
    """Explicit staged draft with optimistic selection/profile conflict guards."""

    request: HackrfLiveRequest
    selection_revision: int
    expected_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.request, HackrfLiveRequest):
            raise TypeError("HackRF patch requires its typed profile")
        _bounded_integer(self.selection_revision, "selection_revision", 0, (1 << 64) - 1)
        _bounded_integer(self.expected_generation, "expected_generation", 0, _MAX_GENERATION)


__all__ = ["HackrfConfigurationPatch", "HackrfLiveRequest"]

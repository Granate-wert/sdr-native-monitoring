"""Immutable producer-reported numerical semantics; absence is never a default."""

from dataclasses import dataclass
from math import isfinite

_ENUM_VALUES = {
    "window": {"rectangular", "hann", "blackman_harris_4term", "flat_top", "nuttall", "kaiser"},
    "detector": {"sample", "peak", "negative_peak", "rms", "average_power"},
    "precision_mode": {"reference_f64", "accurate_f32_f64_accum", "fast_f32"},
    "calibration_status": {"not_applicable", "uncalibrated", "applied", "interpolated", "extrapolated", "invalid"},
}

@dataclass(frozen=True, slots=True)
class SpectrumProvenance:
    window: str | None = None
    detector: str | None = None
    precision_mode: str | None = None
    fft_bin_width_hz: float | None = None
    enbw_hz: float | None = None
    nominal_rbw_hz: float | None = None
    averaging_frames: int | None = None
    calibration_status: str | None = None
    calibration_profile_id: str | None = None
    estimated_uncertainty_db: float | None = None

    def __post_init__(self) -> None:
        for name, allowed in _ENUM_VALUES.items():
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or value not in allowed):
                raise ValueError(f"unsupported {name}")
        for name in ("window", "detector", "precision_mode", "calibration_status", "calibration_profile_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 128):
                raise ValueError(f"{name} must be bounded non-empty text or unknown")
        for name in ("fft_bin_width_hz", "enbw_hz", "nominal_rbw_hz", "estimated_uncertainty_db"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not isfinite(value) or value < 0
                                      or value == 0 and name != "estimated_uncertainty_db"):
                raise ValueError(f"{name} must be finite positive (uncertainty nonnegative) or unknown")
        if self.averaging_frames is not None and (
            type(self.averaging_frames) is not int or self.averaging_frames < 1
        ):
            raise ValueError("averaging_frames must be a positive integer or unknown")

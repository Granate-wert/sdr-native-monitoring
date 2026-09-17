"""Native scalar metadata mapper. No configuration fallback or DSP in Python."""

from math import isnan
from typing import Any

from ..domain.spectrum_provenance import SpectrumProvenance


def native_spectrum_provenance(frame: Any) -> SpectrumProvenance:
    def enum(name: str) -> str | None:
        value = getattr(frame, name, None)
        if value is None:
            return None
        token = getattr(value, "name", value)
        if not isinstance(token, str) or not token.strip():
            raise ValueError(f"invalid native {name}")
        return token.lower()

    def number(name: str, *, unavailable_nan: bool = False) -> float | None:
        value = getattr(frame, name, None)
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"invalid native {name}")
        converted = float(value)
        if unavailable_nan and isnan(converted):
            return None
        return converted

    profile = getattr(frame, "calibration_profile_id", None)
    averaging = getattr(frame, "averaging_frames", None)
    if profile is not None and not isinstance(profile, str):
        raise ValueError("invalid native calibration_profile_id")
    if averaging is not None and type(averaging) is not int:
        raise ValueError("invalid native averaging_frames")
    return SpectrumProvenance(
        window=enum("window"), detector=enum("detector"), precision_mode=enum("precision_mode"),
        fft_bin_width_hz=number("fft_bin_width_hz"), enbw_hz=number("enbw_hz"),
        nominal_rbw_hz=number("nominal_rbw_hz"), averaging_frames=None if averaging == 0 else averaging,
        calibration_status=enum("calibration_status"), calibration_profile_id=profile or None,
        estimated_uncertainty_db=number("estimated_uncertainty_db", unavailable_nan=True),
    )


def validate_absolute_unit(unit: str, provenance: SpectrumProvenance) -> None:
    # Current CPU/CUDA engines normalize digital power only. Configuration
    # status/profile is not evidence that an absolute correction was applied.
    # This guard is scoped to native Live, not device-calibrated tinySA traces.
    if unit.startswith("dBm"):
        raise ValueError("native Live absolute dBm is unavailable: verified calibration correction is not implemented")
    if provenance.calibration_status in {"applied", "interpolated", "extrapolated"}:
        raise ValueError("native Live cannot claim applied calibration from configuration metadata")

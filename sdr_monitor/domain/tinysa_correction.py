"""External frequency correction of device dBm using the EXISTING profile model.

No SDK, store or Qt. Declared input/settings and post-pass RBW/attenuation are
conditions, not RF-state attestation. Uncertainty is the curve author's value,
not a combined uncertainty of the instrument. Original device values survive.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .calibration import (
    CalibrationProfile,
    CalibrationProfileError,
    CalibrationSignature,
    CalibrationStatus,
    InstrumentCalibrationContext,
    check_applicability,
)
from .tinysa_settings import TinySaInputMode

if TYPE_CHECKING:
    from .tinysa_analyzer import TinySaSweepRequest


def tinysa_settings_fingerprint(request: TinySaSweepRequest) -> str:
    payload = {name: getattr(request.settings, name) for name in request.settings.__dataclass_fields__}
    payload["input_mode"] = request.input_mode.value
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tinysa_correction_signature(request: TinySaSweepRequest, *, rbw_hz: float,
                                attenuation_db: float) -> CalibrationSignature:
    """Same stable source facts; all SDR-only fields are explicitly absent."""
    identity = request.source.binding.calibration_identity
    snapshot = request.source.binding.snapshot
    if identity is None or snapshot is None or request.input_mode is TinySaInputMode.PRESERVE:
        raise CalibrationProfileError("tinySA correction requires an explicit input and observed source")
    return CalibrationSignature(
        # The binding exposes an opaque identity, not a raw device serial.
        device_serial="unknown", backend="instrument", rf_port_path=request.input_mode.value,
        sample_rate_hz=None, analog_bandwidth_hz=None, gain_mode=None, manual_gain_db=None,
        window_normalization_version=None, fft_unit_convention=None,
        frontend_chain=request.frontend_chain,
        reference_plane=(request.external_correction.reference_plane if request.external_correction is not None else "rf_input"),
        device_family="tinysa", adapter_id=request.source.binding.adapter_id,
        device_identity_key=identity.device_identity_key, firmware_fingerprint=identity.firmware_fingerprint,
        instrument_context=InstrumentCalibrationContext(snapshot.model_id or "", request.input_mode.value,
            tinysa_settings_fingerprint(request), rbw_hz, attenuation_db),
    )


def admit_tinysa_correction(request: TinySaSweepRequest) -> None:
    """Pre-open static guard. Actual post-pass readouts are checked separately."""
    profile = request.external_correction
    if profile is None:
        return
    if not isinstance(profile, CalibrationProfile) or profile.signature.instrument_context is None:
        raise CalibrationProfileError("tinySA correction requires an instrument profile, not an SDR conversion")
    if not request.readback_settings or request.input_mode is TinySaInputMode.PRESERVE:
        raise CalibrationProfileError("tinySA correction requires explicit input and post-pass readback")
    expected = profile.signature.instrument_context
    current = tinysa_correction_signature(request, rbw_hz=expected.rbw_hz, attenuation_db=expected.attenuation_db)
    if not check_applicability(profile, current).applicable:
        raise CalibrationProfileError("tinySA correction profile does not match source/firmware/input/intent/chain")
    last = request.start_hz + (request.points - 1) * ((request.stop_hz - request.start_hz) // request.points)
    if (not request.allow_correction_extrapolation and
            not profile.points[0].frequency_hz <= request.start_hz <= last <= profile.points[-1].frequency_hz):
        raise CalibrationProfileError("tinySA correction curve does not cover the actual scan grid")


def _freeze(values: np.ndarray) -> np.ndarray:
    return np.frombuffer(np.ascontiguousarray(values, dtype=np.float32).tobytes(), dtype=np.float32)


@dataclass(frozen=True, slots=True)
class InstrumentCorrectionObservation:
    profile: CalibrationProfile
    current_signature: CalibrationSignature
    status: CalibrationStatus
    reason: str
    device_values_dbm: np.ndarray = field(compare=False)
    correction_db: np.ndarray | None = field(default=None, compare=False)
    curve_uncertainty_db: np.ndarray | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.profile, CalibrationProfile) or self.profile.signature.instrument_context is None
                or not isinstance(self.current_signature, CalibrationSignature)
                or self.current_signature.instrument_context is None
                or not isinstance(self.status, CalibrationStatus)
                or self.status not in {CalibrationStatus.CALIBRATED, CalibrationStatus.INTERPOLATED,
                                       CalibrationStatus.EXTRAPOLATED, CalibrationStatus.INVALID}
                or self.reason not in {"applied", "settings_mismatch", "range_not_covered", "invalid_curve"}):
            raise CalibrationProfileError("instrument correction observation has invalid typed provenance")
        raw = self.device_values_dbm
        if (not isinstance(raw, np.ndarray) or raw.dtype != np.float32 or raw.ndim != 1
                or not 2 <= raw.size <= 10001 or raw.flags.writeable or np.any(np.isinf(raw))):
            raise CalibrationProfileError("instrument original values must be immutable and bounded")
        if self.applied:
            if self.reason != "applied" or not check_applicability(self.profile, self.current_signature).applicable:
                raise CalibrationProfileError("instrument correction cannot claim applied on mismatched conditions")
            for array in (self.correction_db, self.curve_uncertainty_db):
                if (not isinstance(array, np.ndarray) or array.shape != raw.shape or array.flags.writeable
                        or array.dtype != np.float32 or not np.all(np.isfinite(array))):
                    raise CalibrationProfileError("instrument correction arrays must be immutable and finite")
            assert self.curve_uncertainty_db is not None
            if np.any(self.curve_uncertainty_db < 0):
                raise CalibrationProfileError("curve uncertainty cannot be negative")
        elif self.reason == "applied" or self.correction_db is not None or self.curve_uncertainty_db is not None:
            raise CalibrationProfileError("unapplied correction cannot fabricate correction/uncertainty arrays")

    @property
    def applied(self) -> bool:
        return self.status is not CalibrationStatus.INVALID

    @property
    def display_key(self) -> str | None:
        return "external:" + self.profile.fingerprint if self.applied else None

    def display_values(self) -> np.ndarray:
        if self.correction_db is None:
            return self.device_values_dbm
        return _freeze(self.device_values_dbm + self.correction_db)


def correct_tinysa_values(request: TinySaSweepRequest, frequencies_hz: np.ndarray,
                         values_dbm: np.ndarray, *, rbw_hz: float,
                         attenuation_db: float) -> InstrumentCorrectionObservation:
    """Bounded vector interpolation, not N Python evaluations of M points."""
    profile = request.external_correction
    if profile is None:
        raise CalibrationProfileError("external correction was not requested")
    # Bound input before any dtype conversion or bytes-backed copy. The
    # function consumes the request's grid, never a resampled display grid.
    if (not isinstance(values_dbm, np.ndarray) or values_dbm.ndim != 1 or values_dbm.dtype.kind != "f"
            or not 2 <= values_dbm.size <= 10001 or values_dbm.size != request.points
            or not isinstance(frequencies_hz, np.ndarray) or frequencies_hz.shape != values_dbm.shape):
        raise CalibrationProfileError("instrument correction needs bounded request-sized arrays")
    raw = _freeze(values_dbm)
    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    expected = request.start_hz + np.arange(request.points, dtype=np.float64) * (
        (request.stop_hz - request.start_hz) // request.points)
    if not np.array_equal(frequencies, expected):
        raise CalibrationProfileError("instrument correction needs the exact ascending measurement grid")
    current = tinysa_correction_signature(request, rbw_hz=rbw_hz, attenuation_db=attenuation_db)
    def refused(reason: str) -> InstrumentCorrectionObservation:
        return InstrumentCorrectionObservation(profile, current, CalibrationStatus.INVALID, reason, raw)
    if not check_applicability(profile, current).applicable:
        return refused("settings_mismatch")  # preserve device dBm; no fake corrected measurement
    grid = np.asarray([p.frequency_hz for p in profile.points])
    correction = np.asarray([p.correction_db for p in profile.points])
    uncertainty = np.asarray([p.uncertainty_db for p in profile.points])
    outside = (frequencies < grid[0]) | (frequencies > grid[-1])
    if np.any(outside) and not request.allow_correction_extrapolation:
        return refused("range_not_covered")
    result = np.interp(frequencies, grid, correction)
    errors = np.interp(frequencies, grid, uncertainty)
    if np.any(outside):
        for mask, left, right in ((frequencies < grid[0], 0, 1), (frequencies > grid[-1], -2, -1)):
            fraction = (frequencies[mask] - grid[left]) / (grid[right] - grid[left])
            result[mask] = correction[left] + fraction * (correction[right] - correction[left])
            errors[mask] = uncertainty[left] + fraction * (uncertainty[right] - uncertainty[left])
        status = CalibrationStatus.EXTRAPOLATED
    else:
        status = (CalibrationStatus.CALIBRATED if np.all(np.isin(frequencies, grid))
                  else CalibrationStatus.INTERPOLATED)
    with np.errstate(over="ignore", invalid="ignore"):
        applied, errors32 = _freeze(result), _freeze(errors)
        levels = raw + applied
    if (not np.all(np.isfinite(applied)) or not np.all(np.isfinite(errors32))
            or np.any(errors32 < 0) or np.any(np.isinf(levels))):
        return refused("invalid_curve")
    return InstrumentCorrectionObservation(profile, current, status, "applied", raw, applied, errors32)

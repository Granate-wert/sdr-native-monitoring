"""Native scalar metadata mapper. No configuration fallback or DSP in Python."""

from math import isnan
from typing import Any

from ..domain.spectrum_provenance import SpectrumProvenance
from ..domain.processing_policy import DC_REMOVED_MASK, DspProcessingRecipeObservationV1, HostDcMode


# Two immutable stage observations, not an owner capability declaration. No
# per-publication JSON parsing, hashing, DSP or allocation of recipe payloads.
_CPU_RECIPES = {mode.value: DspProcessingRecipeObservationV1(mode) for mode in HostDcMode}
_MISSING_RECIPE_FIELD = object()
_EXPECTED_RECIPE_FIELDS = {
    mode: (("schema_version", observed.schema_version), ("scope", observed.scope),
           ("spur_mode", "off"), ("canonical_policy_json", observed.canonical_policy_json),
           ("policy_digest", observed.policy_digest),
           ("whole_frame_modified", observed.whole_frame_modified),
           ("comparison_applied", False), ("hardware_dc_tracking", None))
    for mode, observed in _CPU_RECIPES.items()
}


class _DensityMetadataView:
    """Borrow actual bounded native metadata; no latest-spectrum/request join."""

    __slots__ = ("_metadata", "quality_flags")

    def __init__(self, metadata: Any, quality_flags: int) -> None:
        if type(quality_flags) is not int or not 0 <= quality_flags < (1 << 32):
            raise ValueError("density metadata requires exact native quality flags")
        self._metadata = metadata
        self.quality_flags = quality_flags

    def __getattr__(self, name: str) -> Any:
        return getattr(self._metadata, name)


def native_persistence_provenance(value: Any, *, native_quality_flags: int) -> SpectrumProvenance | None:
    """Contributing FFT observation ONLY, not authenticated density owner authority."""
    metadata = getattr(value, "processing_metadata", None)
    if metadata is None:
        return None  # Legacy/unsupported absence stays UNKNOWN, not inferred OFF.
    result = native_spectrum_provenance(_DensityMetadataView(metadata, native_quality_flags))
    token = str(getattr(value.unit, "name", value.unit))
    validate_absolute_unit("dBm" if token.lower().startswith("dbm") else token, result)
    return result


def _processing_recipe(frame: Any) -> DspProcessingRecipeObservationV1 | None:
    recipe = getattr(frame, "dsp_processing_recipe", None)
    if recipe is None:
        return None  # Legacy quality bits cannot invent algorithm provenance.
    mode = getattr(recipe, "dc_mode", None)
    expected = _CPU_RECIPES.get(mode) if type(mode) is str else None
    if expected is None:
        raise ValueError("unsupported native DSP recipe")
    for name, value in _EXPECTED_RECIPE_FIELDS[expected.dc_mode.value]:
        actual = getattr(recipe, name, _MISSING_RECIPE_FIELD)
        if type(actual) is not type(value) or actual != value:
            raise ValueError(f"invalid native DSP recipe {name}")
    flags = getattr(frame, "quality_flags", None)
    if type(flags) is not int or not 0 <= flags < (1 << 32):
        raise ValueError("native DSP recipe requires exact quality flags")
    if expected.dc_mode is HostDcMode.BLOCK_MEAN and not flags & DC_REMOVED_MASK:
        raise ValueError("native BlockMean recipe lacks DC_REMOVED")
    return expected


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
        window_normalization_version=getattr(frame, "window_normalization_version", None),
        processing_recipe=_processing_recipe(frame),
    )


def validate_absolute_unit(unit: str, provenance: SpectrumProvenance) -> None:
    # Current CPU/CUDA engines normalize digital power only. Configuration
    # status/profile is not evidence that an absolute correction was applied.
    # This guard is scoped to native Live, not device-calibrated tinySA traces.
    if unit.startswith("dBm"):
        raise ValueError("native Live absolute dBm is unavailable: verified calibration correction is not implemented")
    if provenance.calibration_status in {"applied", "interpolated", "extrapolated"}:
        raise ValueError("native Live cannot claim applied calibration from configuration metadata")

"""Instrument Sweep intent/provenance, never a synthetic SDR/FFT profile."""

import math
from dataclasses import dataclass, field

from .analyzer_sources import AnalyzerSourceChoice
from .calibration import CalibrationProfile
from .device_capabilities import AdapterRuntimeAvailability, DeviceFamily
from .tinysa_correction import InstrumentCorrectionObservation, admit_tinysa_correction
from .tinysa_settings import (
    TinySaInputMode,
    TinySaSettingsObservation,
    TinySaSweepSettingsPlan,
    compile_tinysa_runtime_settings,
)


@dataclass(frozen=True, slots=True)
class TinySaSweepRequest:
    source: AnalyzerSourceChoice
    selection_revision: int
    start_hz: int
    stop_hz: int
    points: int = 3100
    timeout_s: float = 60.0
    epoch: int = 0
    repeat_until_stop: bool = False
    interval_s: float = 0.1
    settings: TinySaSweepSettingsPlan = field(default_factory=TinySaSweepSettingsPlan)
    input_mode: TinySaInputMode = TinySaInputMode.PRESERVE
    readback_settings: bool = False
    external_correction: CalibrationProfile | None = None
    frontend_chain: str = "unknown"
    allow_correction_extrapolation: bool = False

    def __post_init__(self) -> None:
        source = self.source
        if (not isinstance(source, AnalyzerSourceChoice) or source.family is not DeviceFamily.TINYSA
                or source.binding.snapshot is None or source.binding.calibration_identity is None
                or source.runtime is None or source.runtime.availability is not AdapterRuntimeAvailability.AVAILABLE):
            raise ValueError("tinySA Sweep requires an observed instrument/runtime")
        if source.binding.snapshot.model_id not in {"tinysa_basic", "tinysa_ultra"}:
            raise ValueError("tinySA model is unverified")
        for value in (self.selection_revision, self.epoch):
            if type(value) is not int or not 0 <= value <= (1 << 64) - 1:
                raise ValueError("tinySA selection/epoch must fit uint64")
        if (type(self.points) is not int or not 2 <= self.points <= 10001
                or type(self.start_hz) is not int or type(self.stop_hz) is not int
                or not 100_000 <= self.start_hz < self.stop_hz
                or (self.stop_hz - self.start_hz) // self.points < 1
                or not any(r.minimum <= self.start_hz < self.stop_hz <= r.maximum
                           for r in source.binding.snapshot.tuning_ranges_hz)):
            raise ValueError("tinySA Sweep points/range are invalid for the observed input ranges")
        if (isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, (int, float))
                or not math.isfinite(self.timeout_s) or not 0.05 <= self.timeout_s <= 120):
            raise ValueError("tinySA Sweep deadline must be in [0.05, 120] seconds")
        if (type(self.repeat_until_stop) is not bool or isinstance(self.interval_s, bool)
                or not isinstance(self.interval_s, (int, float)) or not math.isfinite(self.interval_s)
                or not 0.05 <= self.interval_s <= 60):
            raise ValueError("tinySA explicit repeat/interval is invalid")
        compile_tinysa_runtime_settings(self.settings, self.input_mode, model_id=source.binding.snapshot.model_id,
            control_contract=source.binding.snapshot.runtime_control_contract, start_hz=self.start_hz,
            stop_hz=self.stop_hz, readback=self.readback_settings)
        if (not isinstance(self.frontend_chain, str) or not 1 <= len(self.frontend_chain) <= 128
                or any(ord(c) < 32 or ord(c) == 127 for c in self.frontend_chain)
                or self.frontend_chain != self.frontend_chain.strip()
                or type(self.allow_correction_extrapolation) is not bool
                or self.external_correction is None and self.allow_correction_extrapolation):
            raise ValueError("tinySA correction chain/extrapolation intent is invalid")
        admit_tinysa_correction(self)


@dataclass(frozen=True, slots=True)
class TinySaSweepRunIdentity:
    request: TinySaSweepRequest  # exact controller-assigned epoch/selection references
    configuration_generation: int

    def __post_init__(self) -> None:
        if (not isinstance(self.request, TinySaSweepRequest) or type(self.configuration_generation) is not int
                or not 1 <= self.configuration_generation <= (1 << 64) - 1):
            raise ValueError("instrument run requires the actual admitted request/generation")


@dataclass(frozen=True, slots=True)
class TinySaSweepProvenance:
    selection_revision: int
    configuration_generation: int
    model_id: str
    device_identity_key: str
    firmware_fingerprint: str
    start_hz: int
    stop_hz: int
    points: int
    # None on a cancelled/unreceived trace: never invent observed calibration.
    observed_zero_db: float | None
    host_elapsed_s: float
    value_provenance: str = "device_reported_trace"
    calibration_provenance: str = "device_reported_builtin"
    clock_domain: str = "host_steady_completion"
    settings: TinySaSettingsObservation | None = None
    external_correction: InstrumentCorrectionObservation | None = None
    gap_value_context: str | None = None

    def __post_init__(self) -> None:
        if any(type(v) is not int or not 0 <= v <= (1 << 64) - 1
               for v in (self.selection_revision, self.configuration_generation)):
            raise ValueError("instrument provenance revisions must fit uint64")
        if self.model_id not in {"tinysa_basic", "tinysa_ultra"}:
            raise ValueError("instrument model is invalid")
        for value in (self.device_identity_key, self.firmware_fingerprint):
            if (not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71
                    or any(c not in "0123456789abcdef" for c in value[7:])):
                raise ValueError("instrument identity/firmware must be opaque fingerprints")
        if (type(self.start_hz) is not int or type(self.stop_hz) is not int
                or type(self.points) is not int or not 2 <= self.points <= 10001
                or not 100_000 <= self.start_hz < self.stop_hz
                or (self.stop_hz - self.start_hz) // self.points < 1):
            raise ValueError("instrument provenance grid is invalid")
        if (isinstance(self.host_elapsed_s, bool) or not math.isfinite(self.host_elapsed_s)
                or self.host_elapsed_s < 0 or self.observed_zero_db is not None
                and (isinstance(self.observed_zero_db, bool) or not math.isfinite(self.observed_zero_db))):
            raise ValueError("instrument timing/calibration observation is invalid")
        if (self.value_provenance != "device_reported_trace"
                or self.calibration_provenance != "device_reported_builtin"
                or self.clock_domain != "host_steady_completion"):
            raise ValueError("instrument semantics cannot imply FFT, external correction or RF time")
        if self.settings is not None and not isinstance(self.settings, TinySaSettingsObservation):
            raise TypeError("instrument settings must be a typed same-owner observation")
        if self.external_correction is not None and not isinstance(self.external_correction, InstrumentCorrectionObservation):
            raise TypeError("instrument external correction must be a typed observation")
        if self.gap_value_context is not None and (
                not self.gap_value_context.startswith("external:") or len(self.gap_value_context) != 73
                or any(c not in "0123456789abcdef" for c in self.gap_value_context[9:])):
            raise ValueError("instrument gap display context is invalid")

    @property
    def value_context_key(self) -> str | None:
        return self.external_correction.display_key if self.external_correction is not None else self.gap_value_context

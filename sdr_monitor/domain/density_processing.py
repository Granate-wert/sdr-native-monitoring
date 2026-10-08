"""Density-specific HOST evidence; never an analytical-spectrum producer receipt."""

from dataclasses import dataclass

from .layer_ready import DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt
from .processing_policy import DC_REMOVED_MASK, HostDcMode, _digest, _text, _uint
from .source_processing import RfProcessingValue, RfValueAuthority
from .spectrum_provenance import SpectrumProvenance


@dataclass(frozen=True, slots=True)
class DensityProcessingContextV1:
    family: str
    resource_id: str
    layer_ready: LayerReadyReceipt
    grid_digest: str
    policy_digest: str
    processing_revision: int
    numerical_provenance: SpectrumProvenance
    center: RfProcessingValue
    sample_rate: RfProcessingValue
    analog_bandwidth: RfProcessingValue
    fft_size: int
    hop_size: int
    native_quality_flags: int

    def __post_init__(self) -> None:
        if self.family not in ("ad936x", "hackrf") or type(self.family) is not str:
            raise ValueError("qualified density family required")
        _text(self.resource_id, "density resource")
        _digest(self.grid_digest, "density grid")
        _digest(self.policy_digest, "density policy")
        _uint(self.processing_revision, "density processing revision")
        _uint(self.fft_size, "density FFT", 1 << 20)
        _uint(self.hop_size, "density hop", self.fft_size)
        _uint(self.native_quality_flags, "density quality", (1 << 32) - 1, 0)
        ready = self.layer_ready
        if (type(ready) is not LayerReadyReceipt or ready.kind is not LayerReadyKind.DENSITY
                or type(ready.identity) is not DensityLayerIdentity or ready.owner_run_id is None
                or ready.session_id != ready.identity.accumulation_id
                or ready.identity.native_accumulation_sequence is None):
            raise ValueError("authenticated original density creation required")
        if (self.family == "ad936x" and ready.identity.receiver_id not in ("RX1", "RX2")
                or self.family == "hackrf" and ready.identity.receiver_id is not None):
            raise ValueError("density receiver authority differs from family")
        p = self.numerical_provenance
        if (type(p) is not SpectrumProvenance or p.processing_recipe is None
                or p.processing_recipe.policy_digest != self.policy_digest
                or p.window_normalization_version != "power-norm-v1"
                or p.precision_mode != ("accurate_f32_f64_accum" if self.family == "ad936x" else "reference_f64")
                or p.calibration_status != "uncalibrated"):
            raise ValueError("density recipe/numerical semantics are unqualified")
        if p.processing_recipe.dc_mode is HostDcMode.BLOCK_MEAN and not self.native_quality_flags & DC_REMOVED_MASK:
            raise ValueError("density BlockMean lacks native DC_REMOVED")
        authority = RfValueAuthority.READBACK if self.family == "ad936x" else RfValueAuthority.SDK_APPLIED
        if any(type(value) is not RfProcessingValue or value.authority is not authority
               for value in (self.center, self.sample_rate, self.analog_bandwidth)):
            raise ValueError("density RF authority differs from the actual owner family")

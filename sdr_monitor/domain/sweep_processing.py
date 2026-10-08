"""Original AD/HackRF Sweep owner context; per-family RF authority stays explicit."""
from dataclasses import dataclass
from math import isclose, isfinite, ceil, floor

import numpy as np

from .layer_ready import LayerReadyKind, LayerReadyReceipt, SweepLayerIdentity
from .processing_policy import SdrProcessingPolicyV1, HostSpurMode, HostDcMode, DC_REMOVED_MASK, _text, _uint
from .source_processing import RfValueAuthority
from .sweep_acquisition import SweepSegmentAcquisition
from .continuous_sweep_request import ContinuousSweepPlanRequest
from .continuous_sweep_geometry import sweep_segment_count, sweep_step_geometry


@dataclass(frozen=True, slots=True)
class HackrfSweepProcessingContextV1:
    resource_id: str
    selection_revision: int
    layer_ready: LayerReadyReceipt
    policy: SdrProcessingPolicyV1
    start_hz: int
    stop_hz: int
    fft_size: int
    rf_authority: RfValueAuthority = RfValueAuthority.SDK_APPLIED

    def __post_init__(self) -> None:
        _text(self.resource_id, "Sweep resource", 4096)
        _uint(self.selection_revision, "selection revision", minimum=0)
        if (type(self.layer_ready) is not LayerReadyReceipt
                or self.layer_ready.kind not in (LayerReadyKind.SWEEP_PROGRESS, LayerReadyKind.SWEEP_TERMINAL)
                or type(self.layer_ready.identity) is not SweepLayerIdentity
                or self.layer_ready.owner_run_id is None
                or self.layer_ready.identity.receiver_id is not None
                or self.layer_ready.identity.source_id != self.resource_id):
            raise ValueError("Sweep context requires original selected SAME owner creation")
        if (type(self.policy) is not SdrProcessingPolicyV1 or self.policy.spur_mode is not HostSpurMode.OFF
                or self.policy.compare_raw or self.rf_authority is not RfValueAuthority.SDK_APPLIED):
            raise ValueError("Sweep processing policy or RF authority is unqualified")
        for name in ("start_hz", "stop_hz", "fft_size"):
            _uint(getattr(self, name), name)
        if (self.start_hz < 1_000_000 or self.stop_hz > 6_000_000_000
                or self.stop_hz - self.start_hz < 20_000_000
                or self.start_hz % 1_000_000 or self.stop_hz % 1_000_000
                or self.fft_size not in (1024, 2048, 4096)):
            raise ValueError("Sweep context geometry is unqualified")

    def validate(self, ready: LayerReadyReceipt | None, records: tuple[SweepSegmentAcquisition, ...] | None,
                 frequencies: np.ndarray, values: np.ndarray, flags: np.ndarray, unit: str) -> None:
        if ready != self.layer_ready or records is None or unit != "dBFS/bin":
            raise ValueError("Sweep context differs from original receipt/acquisition/unit")
        key = self.layer_ready.identity
        assert isinstance(key, SweepLayerIdentity)
        expected = key.acquired_segment_generations
        if tuple((r.segment_index, r.config_generation) for r in records) != expected:
            raise ValueError("Sweep processing context lacks exact contributing coverage")
        count = (self.stop_hz - self.start_hz + 4_999_999) // 5_000_000
        if sorted([i for i, _ in expected] + list(key.pending_segment_indices)) != list(range(count)):
            raise ValueError("Sweep context coverage differs from admitted full plan")
        rate, spacing = 20_000_000., 20_000_000. / self.fft_size
        # Complete output grid, including missing bins. Chunked scratch only.
        bins = int(np.ceil((self.stop_hz - self.start_hz) / spacing - 1. - 1e-12))
        if frequencies.size != bins:
            raise ValueError("Sweep context output grid length differs from plan")
        for offset in range(0, bins, 65536):
            actual = frequencies[offset:offset + 65536]
            grid = self.start_hz + spacing * np.arange(offset + 1, offset + 1 + actual.size)
            if not np.array_equal(actual, grid):
                raise ValueError("Sweep context output grid differs from contributing plan")
            known = ~np.isnan(values[offset:offset + 65536])
            modified = (flags[offset:offset + 65536] & DC_REMOVED_MASK) != 0
            if np.any(known & (modified != (self.policy.dc_mode is HostDcMode.BLOCK_MEAN))):
                raise ValueError("Sweep context measured-bin DC quality differs from policy")
        for record in records:
            m = record.processing_metadata
            p = None if m is None else m.numerical_provenance
            # A firmware FFT yields two crops with the SAME +7.5 MHz center.
            center = self.start_hz + (record.segment_index // 4) * 20_000_000 + (record.segment_index % 2) * 5_000_000 + 7_500_000
            if (record.config_generation != key.epoch or m is None or p is None
                    or m.center_frequency_hz != center or m.sample_rate_hz != rate
                    or m.analog_bandwidth_hz != 15_000_000 or m.fft_size != self.fft_size
                    or m.hop_size != self.fft_size or p.processing_recipe is None
                    or p.processing_recipe.policy != self.policy or p.window != "hann" or p.detector != "sample"
                    or p.averaging_frames != 1 or p.precision_mode != "accurate_f32_f64_accum"
                    or p.fft_bin_width_hz != spacing or p.enbw_hz is None
                    or not isclose(p.enbw_hz, 1.5 * rate / (self.fft_size - 1), rel_tol=1e-12, abs_tol=0)
                    or p.nominal_rbw_hz != p.enbw_hz
                    or p.window_normalization_version != "power-norm-v1" or p.calibration_status != "uncalibrated"):
                raise ValueError("Sweep contributing FFT/recipe differs from admitted owner")


@dataclass(frozen=True, slots=True)
class AdSweepProcessingPlanV1:
    """Frozen selected owner/expected profile; only contributing records prove RF readback."""
    resource_id: str
    session_id: str
    revision: int
    revision_kind: str
    source_id: str
    receiver_id: str
    request: ContinuousSweepPlanRequest
    policy: SdrProcessingPolicyV1
    sample_rate_hz: float
    analog_bandwidth_hz: float
    fft_size: int
    hop_size: int
    averaging_frames: int
    window: str
    detector: str

    def __post_init__(self) -> None:
        for name in ("resource_id", "session_id", "source_id"):
            _text(getattr(self, name), name, 4096)
        _uint(self.revision, "owner revision", minimum=1)
        if self.revision_kind not in ("native_processing", "application_selection") or self.receiver_id not in ("RX1", "RX2"):
            raise ValueError("AD Sweep requires exact revision authority and receiver")
        if (type(self.request) is not ContinuousSweepPlanRequest or type(self.policy) is not SdrProcessingPolicyV1
                or self.policy.spur_mode is not HostSpurMode.OFF or self.policy.compare_raw):
            raise ValueError("AD Sweep plan policy or geometry is unqualified")
        sweep_segment_count(self.request)
        for value in (self.sample_rate_hz, self.analog_bandwidth_hz):
            if type(value) is not float or not isfinite(value) or value <= 0:
                raise ValueError("AD Sweep requires a positive selected RF profile")
        if (type(self.fft_size) is not int or not 256 <= self.fft_size <= 262144 or self.fft_size & (self.fft_size - 1)
                or type(self.hop_size) is not int or not 1 <= self.hop_size <= self.fft_size
                or type(self.averaging_frames) is not int or self.averaging_frames < 1
                or type(self.window) is not str or not self.window or type(self.detector) is not str or not self.detector):
            raise ValueError("AD Sweep numerical profile is invalid")
        _text(self.window, "native window", 256)
        _text(self.detector, "native detector", 256)

    @property
    def spacing_hz(self) -> float:
        return (self.request.usable_window_hz / self.request.analysis_bins_per_usable_window
                if self.request.analysis_bins_per_usable_window else self.sample_rate_hz / self.fft_size)

    @property
    def output_bins(self) -> int:
        ratio = (self.request.stop_hz - self.request.start_hz) / self.spacing_hz
        return ceil(ratio - 1e-12) if self.request.analysis_bins_per_usable_window else floor(ratio) + 1


@dataclass(frozen=True, slots=True)
class AdSweepProcessingContextV1:
    plan: AdSweepProcessingPlanV1
    layer_ready: LayerReadyReceipt
    rf_authority: RfValueAuthority = RfValueAuthority.READBACK

    def __post_init__(self) -> None:
        ready, plan = self.layer_ready, self.plan
        if (type(plan) is not AdSweepProcessingPlanV1 or type(ready) is not LayerReadyReceipt
                or ready.kind not in (LayerReadyKind.SWEEP_PROGRESS, LayerReadyKind.SWEEP_TERMINAL)
                or type(ready.identity) is not SweepLayerIdentity or ready.owner_run_id is None
                or ready.session_id != plan.session_id or ready.identity.source_id != plan.source_id
                or ready.identity.receiver_id != plan.receiver_id or ready.identity.epoch < plan.request.epoch
                or self.rf_authority is not (RfValueAuthority.READBACK if ready.identity.acquired_segment_generations
                                            else RfValueAuthority.UNKNOWN)):
            raise ValueError("AD Sweep requires original selected SAME owner/native creation")

    def validate(self, ready: LayerReadyReceipt | None, records: tuple[SweepSegmentAcquisition, ...] | None,
                 frequencies: np.ndarray, values: np.ndarray, flags: np.ndarray, unit: str) -> None:
        plan, key = self.plan, self.layer_ready.identity
        assert isinstance(key, SweepLayerIdentity)
        if ready != self.layer_ready or records is None or unit != "dBFS/bin":
            raise ValueError("AD Sweep context differs from original receipt/acquisition/unit")
        expected = key.acquired_segment_generations
        if (tuple((r.segment_index, r.config_generation) for r in records) != expected
                or sorted([i for i, _ in expected] + list(key.pending_segment_indices)) != list(range(sweep_segment_count(plan.request)))):
            raise ValueError("AD Sweep context lacks exact contributing plan coverage")
        if frequencies.size != plan.output_bins:
            raise ValueError("AD Sweep output grid length differs from admitted plan")
        for offset in range(0, frequencies.size, 65536):
            actual = frequencies[offset:offset + 65536]
            grid = plan.request.start_hz + plan.spacing_hz * np.arange(offset, offset + actual.size)
            if not np.array_equal(actual, grid):
                raise ValueError("AD Sweep output grid differs from admitted plan")
            known = ~np.isnan(values[offset:offset + 65536])
            modified = (flags[offset:offset + 65536] & DC_REMOVED_MASK) != 0
            if np.any(known & (modified != (plan.policy.dc_mode is HostDcMode.BLOCK_MEAN))):
                raise ValueError("AD Sweep measured-bin DC quality differs from admitted policy")
        for record in records:
            metadata = record.processing_metadata
            p = None if metadata is None else metadata.numerical_provenance
            geometry = sweep_step_geometry(plan.request, record.segment_index)
            if (metadata is None or p is None or p.processing_recipe is None
                    or metadata.center_frequency_hz != geometry.center_hz
                    or metadata.sample_rate_hz != plan.sample_rate_hz or record.sample_rate_hz != plan.sample_rate_hz
                    or metadata.analog_bandwidth_hz != plan.analog_bandwidth_hz
                    or metadata.fft_size != plan.fft_size or record.fft_size != plan.fft_size
                    or metadata.hop_size != plan.hop_size or p.processing_recipe.policy != plan.policy
                    or p.window != plan.window or p.detector != plan.detector
                    or p.averaging_frames != plan.averaging_frames or p.precision_mode != "accurate_f32_f64_accum"
                    or p.fft_bin_width_hz != plan.sample_rate_hz / plan.fft_size
                    or p.enbw_hz is None or p.nominal_rbw_hz != p.enbw_hz
                    or p.window_normalization_version != "power-norm-v1" or p.calibration_status != "uncalibrated"):
                raise ValueError("AD Sweep contributing RF/FFT/recipe differs from SAME admitted owner")


SweepProcessingContextV1 = HackrfSweepProcessingContextV1 | AdSweepProcessingContextV1

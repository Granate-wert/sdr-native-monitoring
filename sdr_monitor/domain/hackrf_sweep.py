"""Immutable HackRF bounded-Sweep intent; no receiver or hardware ownership."""

from dataclasses import dataclass
from math import ceil

from .analyzer_sources import AnalyzerSourceChoice
from .analyzer_resources import AnalyzerGeometryPreflight, estimate_analyzer_reduced
from .live import DEFAULT_LIVE_RESOURCE_BUDGET
from .processing_policy import SdrProcessingPolicyV1, HostSpurMode


@dataclass(frozen=True, slots=True)
class HackrfSweepRequest:
    source: AnalyzerSourceChoice
    selection_revision: int
    start_hz: int
    stop_hz: int
    fft_size: int
    lna_gain: int
    vga_gain: int
    preview_rate_hz: int
    epoch: int = 0
    processing_policy: SdrProcessingPolicyV1 = SdrProcessingPolicyV1()

    def __post_init__(self) -> None:
        if (type(self.processing_policy) is not SdrProcessingPolicyV1
                or self.processing_policy.spur_mode is not HostSpurMode.OFF
                or self.processing_policy.compare_raw):
            raise ValueError("HackRF Sweep supports typed OFF/BlockMean only; spur/comparison unqualified")
        if not isinstance(self.source, AnalyzerSourceChoice):
            raise TypeError("HackRF Sweep requires the exact selected source choice")
        for name, value in (("selection revision", self.selection_revision), ("epoch", self.epoch)):
            if type(value) is not int or not 0 <= value <= (1 << 64) - 1:
                raise ValueError(f"HackRF Sweep {name} must fit uint64")
        if (type(self.start_hz) is not int or type(self.stop_hz) is not int
                or self.start_hz < 0 or self.stop_hz <= self.start_hz
                or self.start_hz % 1_000_000 or self.stop_hz % 1_000_000):
            raise ValueError("HackRF Sweep frequencies must be whole MHz")
        span = self.stop_hz - self.start_hz
        if not 20_000_000 <= span or self.stop_hz > 6_000_000_000:
            raise ValueError("HackRF Sweep requires a >=20 MHz span within 1..6000 MHz")
        if self.start_hz < 1_000_000:
            raise ValueError("HackRF Sweep requires frequencies within 1..6000 MHz")
        if self.hardware_stop_hz - 7_500_000 > 6_000_000_000:
            raise ValueError("HackRF rounded capture would tune outside its qualified RF envelope")
        if type(self.fft_size) is not int or self.fft_size not in (1024, 2048, 4096):
            raise ValueError("HackRF Sweep FFT size must be 1024, 2048 or 4096")
        if type(self.lna_gain) is not int or not 0 <= self.lna_gain <= 40 or self.lna_gain % 8:
            raise ValueError("HackRF LNA gain must be 0..40 dB in 8 dB steps")
        if type(self.vga_gain) is not int or not 0 <= self.vga_gain <= 62 or self.vga_gain % 2:
            raise ValueError("HackRF VGA gain must be 0..62 dB in 2 dB steps")
        if type(self.preview_rate_hz) is not int or not 1 <= self.preview_rate_hz <= 100:
            raise ValueError("HackRF preview rate must be in 1..100 Hz")
        if self.geometry.reduced.total_bytes > DEFAULT_LIVE_RESOURCE_BUDGET.max_spectrum_backlog_bytes:
            raise ValueError("HackRF Sweep reduced spectrum backlog exceeds memory budget")

    @property
    def hardware_stop_hz(self) -> int:
        # Match official host whole tuning-step planning; analysis stop stays
        # EXACT. Padding is shown in UI preview, never relabelled as coverage.
        span = self.stop_hz - self.start_hz
        return self.start_hz + ((span + 19_999_999) // 20_000_000) * 20_000_000

    @property
    def requires_extended_geometry(self) -> bool:
        return (self.stop_hz - self.start_hz > 320_000_000
                or self.hardware_stop_hz != self.stop_hz)

    @property
    def geometry(self) -> AnalyzerGeometryPreflight:
        spacing = 20_000_000.0 / self.fft_size
        segments = ceil((self.stop_hz - self.start_hz) / 5_000_000)
        bins = ceil((self.stop_hz - self.start_hz) / spacing - 1.0 - 1e-12)
        reduced = estimate_analyzer_reduced(
            "sweep", bins, 5, physical_fft_size=self.fft_size, segment_count=segments)
        return AnalyzerGeometryPreflight(
            "sweep", 20_000_000.0, self.fft_size, spacing, segments, 5_000_000.0,
            reduced, usable_window_hz=5_000_000.0,
            analysis_bins_per_usable_window=self.fft_size // 4,
            physical_bin_spacing_hz=spacing)


__all__ = ["HackrfSweepRequest"]

"""Immutable HackRF bounded-Sweep intent; no receiver or hardware ownership."""

from dataclasses import dataclass

from .analyzer_sources import AnalyzerSourceChoice


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

    def __post_init__(self) -> None:
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
        if not 20_000_000 <= span <= 320_000_000 or span % 20_000_000:
            raise ValueError("HackRF Sweep span must be 20..320 MHz in 20 MHz steps")
        if type(self.fft_size) is not int or self.fft_size not in (1024, 2048, 4096):
            raise ValueError("HackRF Sweep FFT size must be 1024, 2048 or 4096")
        if type(self.lna_gain) is not int or not 0 <= self.lna_gain <= 40 or self.lna_gain % 8:
            raise ValueError("HackRF LNA gain must be 0..40 dB in 8 dB steps")
        if type(self.vga_gain) is not int or not 0 <= self.vga_gain <= 62 or self.vga_gain % 2:
            raise ValueError("HackRF VGA gain must be 0..62 dB in 2 dB steps")
        if type(self.preview_rate_hz) is not int or not 1 <= self.preview_rate_hz <= 100:
            raise ValueError("HackRF preview rate must be in 1..100 Hz")


__all__ = ["HackrfSweepRequest"]

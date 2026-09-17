"""Deterministic Sweep acquisition presets, not rendering or throughput promises.

Only the number of analytical FFTs per emitted spectrum changes. FFT size,
sample rate, buffer geometry and post-retune discard policy remain explicit
and unchanged. 'Averaged' reduces variance, not calibrated amplitude error.
"""
from enum import StrEnum


class SweepSpeedProfile(StrEnum):
    APPLIED = "applied"
    QUICK = "quick"
    BALANCED = "balanced"
    AVERAGED = "averaged"

    def averaging_frames(self, applied: int) -> int:
        if type(applied) is not int or applied < 1:
            raise ValueError("applied FFT averaging must be a positive integer")
        return {
            self.APPLIED: applied,
            self.QUICK: 1,
            self.BALANCED: 4,
            self.AVERAGED: 16,
        }[self]

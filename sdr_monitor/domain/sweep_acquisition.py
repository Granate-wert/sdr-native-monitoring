"""Scalar producer provenance, without inferred hardware clock semantics."""
from dataclasses import dataclass
from collections.abc import Iterable
import math


@dataclass(frozen=True, slots=True)
class SweepSegmentPosition:
    """Last successful assembler admission; NEVER the current RF position."""
    segment_index: int
    config_generation: int
    usable_start_hz: float
    usable_stop_hz: float

    def __post_init__(self) -> None:
        if (type(self.segment_index) is not int or self.segment_index < 0
                or type(self.config_generation) is not int or self.config_generation <= 0
                or not math.isfinite(self.usable_start_hz) or not math.isfinite(self.usable_stop_hz)
                or self.usable_start_hz < 0 or self.usable_stop_hz <= self.usable_start_hz):
            raise ValueError("invalid last admitted segment position")


def validate_position(position: SweepSegmentPosition | None, generations: Iterable[tuple[int, int]]) -> None:
    if position is None:
        return  # Historical/unsupported producer; no inferred cursor.
    if not isinstance(position, SweepSegmentPosition):
        raise TypeError("last admitted segment requires a typed immutable position")
    if dict(generations).get(position.segment_index) != position.config_generation:
        raise ValueError("last admitted segment must refer to an acquired generation")


@dataclass(frozen=True, slots=True)
class SweepSegmentAcquisition:
    segment_index: int
    config_generation: int
    frame_sequence: int
    first_sample_index: int
    timestamp_ns: int
    sample_rate_hz: float
    fft_size: int
    quality_flags: int

    def __post_init__(self) -> None:
        for value in (self.segment_index, self.config_generation, self.frame_sequence,
                      self.first_sample_index, self.timestamp_ns, self.fft_size, self.quality_flags):
            if type(value) is not int or value < 0:
                raise ValueError("acquisition counters must be nonnegative integers")
        if (not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0
                or self.fft_size == 0 or self.quality_flags > 0xFFFFFFFF):
            raise ValueError("invalid acquisition rate, FFT size or quality mask")


def validate_acquisition(
    records: Iterable[SweepSegmentAcquisition] | None,
    generations: Iterable[tuple[int, int]],
) -> tuple[SweepSegmentAcquisition, ...] | None:
    """None is unsupported; an empty tuple supplies no segment timing evidence."""
    if records is None:
        return None
    immutable_records = tuple(records)
    expected = dict(generations)
    seen: set[int] = set()
    for item in immutable_records:
        if not isinstance(item, SweepSegmentAcquisition):
            raise TypeError("acquisition record must be a typed immutable value")
        if (item.segment_index in seen
                or expected.get(item.segment_index) != item.config_generation):
            raise ValueError("acquisition provenance disagrees with segment generation")
        seen.add(item.segment_index)
    return immutable_records

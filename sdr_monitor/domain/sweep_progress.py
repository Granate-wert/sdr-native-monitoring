"""Nonterminal reduced Sweep publication. No completion or RF-time inference."""
from dataclasses import dataclass

import numpy as np
from .sweep_acquisition import SweepSegmentAcquisition, SweepSegmentPosition, validate_acquisition, validate_position
from .sweep_statistics import SweepStatisticsFrame


_VALIDATION_BATCH = 65_536


def _validate_progress_arrays(arrays: tuple[np.ndarray, ...], indices: list[int]) -> None:
    """Check every bin with bounded scratch, preserving error precedence.

    Do not gather full-grid source arrays through boolean masks. Chunk-local
    masks stay cache-sized, and adjacent comparisons avoid a full float diff.
    Missing bins remain explicit; -inf is measured zero power, not missing.
    """
    grid_ok = values_ok = coverage_ok = owners_ok = True
    frequencies, values, quality, sources = arrays
    for start in range(0, frequencies.size, _VALIDATION_BATCH):
        stop = start + _VALIDATION_BATCH
        frequency = frequencies[start:stop]
        value = values[start:stop]
        flags = quality[start:stop]
        owner = sources[start:stop]
        grid_ok = (grid_ok and bool(np.all(np.isfinite(frequency)))
                   and not bool(np.any(frequency[1:] <= frequency[:-1]))
                   and (start == 0 or bool(frequency[0] > frequencies[start - 1])))
        values_ok = values_ok and not bool(np.any(np.isposinf(value)))
        missing = np.isnan(value)
        flagged_missing = (flags & np.uint32(1 << 12)) != 0
        coverage_ok = coverage_ok and np.array_equal(missing, flagged_missing)
        allowed = owner == indices[0] if len(indices) == 1 else np.isin(owner, indices)
        owners_ok = (owners_ok and not bool(np.any(missing & (owner != -1)))
                     and bool(np.all(missing | allowed)))
    if not grid_ok:
        raise ValueError("invalid progress frequency grid")
    if not values_ok:
        raise ValueError("progress values must be finite, zero power (-inf dB), or explicit NaN gaps")
    if not coverage_ok:
        raise ValueError("progress NaN coverage must match native MissingSegment flags")
    if not owners_ok:
        raise ValueError("progress bin provenance must refer only to acquired segments")


@dataclass(frozen=True, slots=True)
class SweepProgressFrame:
    source_id: str
    sequence: int
    epoch: int
    revision: int
    unit: str
    frequencies_hz: np.ndarray
    values_db: np.ndarray
    quality_flags: np.ndarray
    source_segment_indices: np.ndarray
    acquired_segment_generations: tuple[tuple[int, int], ...]
    pending_segment_indices: tuple[int, ...]
    segment_acquisition: tuple[SweepSegmentAcquisition, ...] | None = None
    statistics: SweepStatisticsFrame | None = None
    last_admitted_segment: SweepSegmentPosition | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.unit.strip():
            raise ValueError("progress requires explicit source and unit")
        for number in (self.sequence, self.epoch, self.revision):
            if type(number) is not int or number < 0:
                raise ValueError("progress identity must use nonnegative integers")
        acquired = tuple(tuple(pair) for pair in self.acquired_segment_generations)
        pending = tuple(self.pending_segment_indices)
        if not acquired or not pending or self.revision != len(acquired):
            raise ValueError("progress must contain acquired and pending segments")
        if any(len(pair) != 2 or any(type(x) is not int for x in pair)
               or pair[0] < 0 or pair[1] <= 0 for pair in acquired):
            raise ValueError("progress requires actual segment generations")
        indices = [pair[0] for pair in acquired]
        if (any(type(x) is not int or x < 0 for x in pending)
                or len(set(indices + list(pending))) != len(indices) + len(pending)):
            raise ValueError("progress segment identities overlap or repeat")
        arrays = (self.frequencies_hz, self.values_db, self.quality_flags, self.source_segment_indices)
        if any(not isinstance(a, np.ndarray) or a.ndim != 1 or a.flags.writeable for a in arrays):
            raise ValueError("progress arrays must be immutable one-dimensional native views")
        if not 2 <= arrays[0].size <= 2_000_000 or any(a.size != arrays[0].size for a in arrays):
            raise ValueError("progress arrays must have matching bounded geometry")
        if (arrays[0].dtype.kind != "f" or arrays[1].dtype.kind != "f"
                or arrays[2].dtype != np.dtype("uint32") or arrays[3].dtype != np.dtype("int32")):
            raise ValueError("invalid native progress array types")
        _validate_progress_arrays(arrays, indices)
        generation_pairs = tuple((pair[0], pair[1]) for pair in acquired)
        validate_position(self.last_admitted_segment, generation_pairs)
        object.__setattr__(self, "acquired_segment_generations", generation_pairs)
        object.__setattr__(self, "pending_segment_indices", pending)
        object.__setattr__(self, "segment_acquisition",
                           validate_acquisition(self.segment_acquisition, generation_pairs))
        if self.statistics is not None:
            if not isinstance(self.statistics, SweepStatisticsFrame):
                raise TypeError("Sweep requires an explicit statistics contract")
            self.statistics.validate_parent(self.source_id, self.epoch, self.sequence,
                                            self.unit, self.frequencies_hz)

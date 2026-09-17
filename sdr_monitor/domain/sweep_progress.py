"""Nonterminal reduced Sweep publication. No completion or RF-time inference."""
from dataclasses import dataclass

import numpy as np
from .sweep_acquisition import SweepSegmentAcquisition, validate_acquisition
from .sweep_statistics import SweepStatisticsFrame


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
        if not np.all(np.isfinite(arrays[0])) or np.any(np.diff(arrays[0]) <= 0):
            raise ValueError("invalid progress frequency grid")
        if np.any(np.isinf(arrays[1])):
            raise ValueError("progress values must be finite or explicit NaN gaps")
        missing = np.isnan(arrays[1])
        flagged_missing = (arrays[2] & np.uint32(1 << 12)) != 0
        if not np.array_equal(missing, flagged_missing):
            raise ValueError("progress NaN coverage must match native MissingSegment flags")
        if np.any(arrays[3][missing] != -1) or not np.all(np.isin(arrays[3][~missing], indices)):
            raise ValueError("progress bin provenance must refer only to acquired segments")
        generation_pairs = tuple((pair[0], pair[1]) for pair in acquired)
        object.__setattr__(self, "acquired_segment_generations", generation_pairs)
        object.__setattr__(self, "pending_segment_indices", pending)
        object.__setattr__(self, "segment_acquisition",
                           validate_acquisition(self.segment_acquisition, generation_pairs))
        if self.statistics is not None:
            if not isinstance(self.statistics, SweepStatisticsFrame):
                raise TypeError("Sweep requires an explicit statistics contract")
            self.statistics.validate_parent(self.source_id, self.epoch, self.sequence,
                                            self.unit, self.frequencies_hz)

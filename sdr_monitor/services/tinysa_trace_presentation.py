"""Peak-preserving visual reduction for immutable tinySA analyzer traces."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .tinysa_trace_parser import MAX_TINYSA_TRACE_POINTS, TinySaSpectrumTrace

MAX_TINYSA_PRESENTATION_WIDTH = 4_096


@dataclass(frozen=True, slots=True)
class TinySaTracePresentation:
    """Bounded display-only extrema; never the analytical trace itself."""

    source_point_count: int
    pixel_width: int
    bucket_count: int
    source_indices: np.ndarray
    frequencies_hz: np.ndarray
    values_dbm: np.ndarray
    peak_preserving: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.source_point_count <= MAX_TINYSA_TRACE_POINTS:
            raise ValueError("tinySA presentation source count is outside the product bound")
        if not 1 <= self.pixel_width <= MAX_TINYSA_PRESENTATION_WIDTH:
            raise ValueError("tinySA presentation pixel width is outside the fixed bound")
        if not 1 <= self.bucket_count <= min(self.source_point_count, self.pixel_width):
            raise ValueError("tinySA presentation bucket count is inconsistent")
        indices = np.array(self.source_indices, dtype=np.int32, copy=True).reshape(-1)
        frequencies = np.array(self.frequencies_hz, dtype=np.float64, copy=True).reshape(-1)
        values = np.array(self.values_dbm, dtype=np.float32, copy=True).reshape(-1)
        if not 1 <= indices.size <= 2 * self.bucket_count:
            raise ValueError("tinySA presentation output exceeds two extrema per bucket")
        if frequencies.size != indices.size or values.size != indices.size:
            raise ValueError("tinySA presentation axes have inconsistent lengths")
        if np.any(indices < 0) or np.any(indices >= self.source_point_count):
            raise ValueError("tinySA presentation source index is outside the trace")
        if np.any(indices[1:] <= indices[:-1]):
            raise ValueError("tinySA presentation source indices must be strictly increasing")
        if not np.isfinite(frequencies).all() or not np.isfinite(values).all():
            raise ValueError("tinySA presentation values must be finite")
        if not self.peak_preserving:
            raise ValueError("tinySA presentation must retain bucket extrema")
        indices.setflags(write=False)
        frequencies.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "source_indices", indices)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values_dbm", values)

    @property
    def display_point_count(self) -> int:
        return int(self.values_dbm.size)


def reduce_tinysa_trace_for_width(
    trace: TinySaSpectrumTrace,
    pixel_width: int,
) -> TinySaTracePresentation:
    """Keep min/max extrema in source order for each horizontal pixel bucket."""

    if not isinstance(trace, TinySaSpectrumTrace):
        raise TypeError("tinySA presentation requires an immutable analyzer trace")
    if isinstance(pixel_width, bool) or not isinstance(pixel_width, int):
        raise TypeError("tinySA presentation width must be an integer")
    if not 1 <= pixel_width <= MAX_TINYSA_PRESENTATION_WIDTH:
        raise ValueError("tinySA presentation width is outside the fixed bound")

    source_count = int(trace.values_dbm.size)
    bucket_count = min(source_count, pixel_width)
    selected = np.empty(2 * bucket_count, dtype=np.int32)
    selected_count = 0
    for bucket in range(bucket_count):
        start = bucket * source_count // bucket_count
        stop = (bucket + 1) * source_count // bucket_count
        values = trace.values_dbm[start:stop]
        minimum = start + int(np.argmin(values))
        maximum = start + int(np.argmax(values))
        if minimum == maximum:
            selected[selected_count] = minimum
            selected_count += 1
            continue
        first, second = sorted((minimum, maximum))
        selected[selected_count] = first
        selected[selected_count + 1] = second
        selected_count += 2

    source_indices = selected[:selected_count]
    return TinySaTracePresentation(
        source_point_count=source_count,
        pixel_width=pixel_width,
        bucket_count=bucket_count,
        source_indices=source_indices,
        frequencies_hz=trace.frequencies_hz[source_indices],
        values_dbm=trace.values_dbm[source_indices],
    )


__all__ = [
    "MAX_TINYSA_PRESENTATION_WIDTH",
    "TinySaTracePresentation",
    "reduce_tinysa_trace_for_width",
]

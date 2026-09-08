"""Peak-preserving bounded conversion from a public frame to paint data."""

from __future__ import annotations

import math

import numpy as np

from .contracts import EnvelopeTrace, SpectrumFrameView


def peak_preserving_envelope(view: SpectrumFrameView, pixel_width: int) -> EnvelopeTrace:
    """Return a bounded envelope that preserves a min/max extremum per column.

    The scene retains the source frame separately for marker lookup.  This
    function creates only a paint representation; it neither modifies nor
    takes ownership of the source arrays.
    """

    width = max(1, int(pixel_width))
    values = view.values
    frequencies = view.frequencies_hz
    if values.size <= width * 4:
        return EnvelopeTrace(frequencies, values, view.point_count, peak_preserving=True)

    bucket_size = math.ceil(values.size / width)
    display_frequencies: list[float] = []
    display_values: list[float] = []
    inserted_gap = False
    for start in range(0, values.size, bucket_size):
        stop = min(values.size, start + bucket_size)
        bucket_values = values[start:stop]
        finite_offsets = np.flatnonzero(np.isfinite(bucket_values))
        if finite_offsets.size == 0:
            if not inserted_gap:
                _append_gap(display_frequencies, display_values)
                inserted_gap = True
            continue
        if inserted_gap:
            _append_gap(display_frequencies, display_values)
        inserted_gap = bool(finite_offsets.size != bucket_values.size)
        finite_indices = start + finite_offsets
        local_values = values[finite_indices]
        extrema = (int(np.argmin(local_values)), int(np.argmax(local_values)))
        selected = {int(finite_indices[0]), int(finite_indices[-1])}
        selected.update(int(finite_indices[offset]) for offset in extrema)
        for index in sorted(selected):
            display_frequencies.append(float(frequencies[index]))
            display_values.append(float(values[index]))

    return EnvelopeTrace(
        frequencies_hz=np.asarray(display_frequencies, dtype=np.float64),
        values=np.asarray(display_values, dtype=np.float64),
        source_point_count=view.point_count,
        peak_preserving=True,
    )


def _append_gap(frequencies: list[float], values: list[float]) -> None:
    if values and math.isnan(values[-1]):
        return
    frequencies.append(float("nan"))
    values.append(float("nan"))

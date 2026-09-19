"""Peak-preserving bounded conversion from a public frame to paint data."""

from __future__ import annotations

import math

import numpy as np

from .contracts import EnvelopeTrace, SpectrumFrameView
from .envelope_batch import bucket_batches, extrema_rows
from .cancellation import CancelCheck, check_cancelled


def peak_preserving_envelope(view: SpectrumFrameView, pixel_width: int,
                             *, cancelled: CancelCheck = None) -> EnvelopeTrace:
    """Return a bounded envelope that preserves a min/max extremum per column.

    The scene retains the source frame separately for marker lookup.  This
    function creates only a paint representation; it neither modifies nor
    takes ownership of the source arrays. Each column contributes at most four
    finite points plus five gap sentinels. Unknown samples between retained
    points always break the line, including gaps inside a single column.
    """

    check_cancelled(cancelled)
    width = max(1, int(pixel_width))
    values = view.values
    frequencies = view.frequencies_hz
    if values.size <= width * 4:
        return EnvelopeTrace(frequencies, values, view.point_count, peak_preserving=True)

    bucket_size = math.ceil(values.size / width)
    display_frequencies: list[np.ndarray] = []
    display_values: list[np.ndarray] = []
    for start, rows, size in bucket_batches(values.size, bucket_size):
        check_cancelled(cancelled)
        stop = start + rows * size
        block = values[start:stop].reshape(rows, size)
        x, y = extrema_rows(frequencies[start:stop].reshape(rows, size), block, np.isfinite(block))
        display_frequencies.append(x)
        display_values.append(y)
    check_cancelled(cancelled)
    x = np.concatenate(display_frequencies)
    y = np.concatenate(display_values)
    # The original reducer suppresses adjacent gap sentinels across buckets.
    keep = np.r_[True, ~(np.isnan(y[1:]) & np.isnan(y[:-1]))]

    return EnvelopeTrace(
        frequencies_hz=x[keep],
        values=y[keep],
        source_point_count=view.point_count,
        peak_preserving=True,
    )

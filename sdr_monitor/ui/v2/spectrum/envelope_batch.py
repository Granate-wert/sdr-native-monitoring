"""Batched extrema selection with explicit holes and bounded temporary storage."""
from collections.abc import Iterator

import numpy as np

# Batch scratch is O(max(BATCH_SAMPLES, one bucket)), not O(full source grid).
BATCH_SAMPLES = 65_536


def bucket_batches(count: int, bucket_size: int) -> Iterator[tuple[int, int, int]]:
    """(flat start, row count, row size); the short last bucket stays separate."""
    full, tail = divmod(count, bucket_size)
    rows_per_batch = max(1, BATCH_SAMPLES // bucket_size)
    for first in range(0, full, rows_per_batch):
        yield first * bucket_size, min(rows_per_batch, full - first), bucket_size
    if tail:
        yield full * bucket_size, 1, tail


def extrema_rows(frequencies: np.ndarray, values: np.ndarray, finite: np.ndarray,
                 *, keep_small: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Reduce equally sized 2D buckets, up to four finite points + five holes.

    Finite is explicit so history can exclude current samples without copying
    a full masked trace. First/last/min/max are selected in SOURCE order.
    Prefix counts of absent bins forbid joining two extrema across a hole.
    No source data is mutated; this is display LOD, never averaging or DSP.
    """
    rows, size = values.shape
    if keep_small and size <= 4:
        return frequencies.reshape(-1), np.where(finite, values, np.nan).reshape(-1)
    present = np.asarray(np.any(finite, axis=1))
    if not np.any(present):
        # Each absent bucket contributes only its gap sentinel. Avoid extrema
        # and prefix scans for empty current frames or fully replaced history.
        return np.full(rows, np.nan), np.full(rows, np.nan)
    first = np.argmax(finite, axis=1)
    last = size - 1 - np.argmax(finite[:, ::-1], axis=1)
    minimum = np.argmin(np.where(finite, values, np.inf), axis=1)
    maximum = np.argmax(np.where(finite, values, -np.inf), axis=1)
    indices = np.sort(np.stack((first, minimum, maximum, last), axis=1), axis=1)
    unique = np.ones((rows, 4), dtype=bool)
    unique[:, 1:] = indices[:, 1:] != indices[:, :-1]
    unique &= present[:, None]
    absent_prefix = np.cumsum(~finite, axis=1, dtype=np.int32)
    gaps = np.take_along_axis(absent_prefix, indices, axis=1)
    keep = np.zeros((rows, 9), dtype=bool)
    keep[:, 1:8:2] = unique
    keep[:, 0] = ~present | (first != 0)
    keep[:, 2:8:2] = unique[:, 1:] & (gaps[:, 1:] != gaps[:, :-1])
    keep[:, 8] = present & (last != size - 1)
    x = np.full((rows, 9), np.nan, dtype=np.float64)
    y = np.full((rows, 9), np.nan, dtype=np.float64)
    x[:, 1:8:2] = np.take_along_axis(frequencies, indices, axis=1)
    y[:, 1:8:2] = np.take_along_axis(values, indices, axis=1)
    return x[keep], y[keep]

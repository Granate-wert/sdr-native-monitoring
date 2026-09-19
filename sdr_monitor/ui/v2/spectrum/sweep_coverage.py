"""Bounded display-only Sweep coverage/history, never an analytical input."""
from dataclasses import dataclass
from math import ceil
from typing import TypeAlias

import numpy as np

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from .contracts import EnvelopeTrace
from .envelope_batch import bucket_batches, extrema_rows
from .cancellation import CancelCheck, check_cancelled

CURRENT = 1
PREVIOUS = 2
MISSING = 4
MAX_COLUMNS = 2048
SweepFrame: TypeAlias = SweepLineFrame | SweepProgressFrame


@dataclass(frozen=True, slots=True)
class CoverageProjection:
    """Each bounded column is a bitwise union, not a claim of full coverage.

    Edges are physical midpoints between adjacent frequency centres. Mixed
    columns retain all states. History contains only bins absent from current;
    NaN separators prevent a line across newly acquired or missing samples.
    """
    edges_hz: np.ndarray
    states: np.ndarray
    history: EnvelopeTrace


def _compatible(a: SweepFrame, b: SweepFrame) -> bool:
    return ((a.source_id, a.epoch, a.unit) == (b.source_id, b.epoch, b.unit)
            and (a.frequencies_hz is b.frequencies_hz
                 or np.array_equal(a.frequencies_hz, b.frequencies_hz)))


class SweepCoverageState:
    """Retain at most current + one older published terminal, without copies.

    Missing intermediate passes are never reconstructed. The previous label
    names the actual observed sequence, not an invented sequence-1 or RF age.
    This state cannot feed markers, average, density, Waterfall or export.
    """
    def __init__(self) -> None:
        self.current: SweepFrame | None = None
        self.previous: SweepLineFrame | None = None

    def clear(self) -> None:
        self.current = self.previous = None

    def accept(self, snapshot: ContinuousSweepDisplaySnapshot) -> bool:
        if not isinstance(snapshot, ContinuousSweepDisplaySnapshot):
            raise TypeError("coverage requires a validated Sweep snapshot")
        frame: SweepFrame | None = snapshot.line
        if snapshot.progress is not None and (frame is None or snapshot.progress.sequence > frame.sequence):
            frame = snapshot.progress
        if frame is None:
            changed = self.current is not None
            self.clear()
            return changed
        if self.current is frame and (snapshot.line is None or snapshot.line is frame
                                      or snapshot.line is self.previous):
            return False
        candidates = (snapshot.line, self.current, self.previous)
        previous = max((value for value in candidates
                        if isinstance(value, SweepLineFrame) and value.sequence < frame.sequence
                        and _compatible(value, frame)), key=lambda value: value.sequence, default=None)
        changed = self.current is not frame or self.previous is not previous
        self.current, self.previous = frame, previous
        return changed

    def project(self, left: float, right: float, width: int,
                *, cancelled: CancelCheck = None) -> CoverageProjection:
        """O(visible bins), O(columns + one bucket) scratch, no full-grid copy.

        At most 2,048 columns and nine history points per column. The existing
        extrema reducer keeps peaks and explicit holes. A zoom reprojects the
        immutable sources, never the already reduced screen representation.
        """
        check_cancelled(cancelled)
        frame = self.current
        if frame is None:
            raise ValueError("no Sweep measurement for coverage")
        frequencies = frame.frequencies_hz
        count = frequencies.size
        start = max(0, int(np.searchsorted(frequencies, left)) - 1)
        stop = min(count, int(np.searchsorted(frequencies, right, side="right")) + 1)
        edges: list[float] = []
        states: list[int] = []
        history_x: list[np.ndarray] = []
        history_y: list[np.ndarray] = []
        if stop > start:
            bucket_size = ceil((stop - start) / max(1, min(MAX_COLUMNS, int(width))))
            edges.append(_edge(frequencies, start))
            for offset, rows, size in bucket_batches(stop - start, bucket_size):
                check_cancelled(cancelled)
                lower, upper = start + offset, start + offset + rows * size
                # Measured zero power (-inf dB) owns coverage even though the
                # finite display axis cannot draw it. Never fill it with old RF.
                current = frame.values_db[lower:upper].reshape(rows, size) < np.inf
                current_count = np.count_nonzero(current, axis=1)
                flags = (current_count > 0).astype(np.uint8) * CURRENT
                if self.previous is not None:
                    if np.all(current_count == size):
                        # Every bin in this batch already belongs to this pass,
                        # including measured -inf. No previous value can be
                        # displayed here, so do not rescan/reduce the old array.
                        # Keep exactly the reducer's empty-bucket sentinels:
                        # omitting them would join history across current RF.
                        history_count = 0
                        if size <= 4:
                            x = frequencies[lower:upper]
                            y = np.full(rows * size, np.nan, dtype=self.previous.values_db.dtype)
                        else:
                            x, y = np.full(rows, np.nan), np.full(rows, np.nan)
                    else:
                        old = self.previous.values_db[lower:upper].reshape(rows, size)
                        historical = ~current & (old < np.inf)
                        history_count = np.count_nonzero(historical, axis=1)
                        flags |= (history_count > 0).astype(np.uint8) * PREVIOUS
                        x, y = extrema_rows(frequencies[lower:upper].reshape(rows, size), old,
                                            historical & np.isfinite(old), keep_small=True)
                    history_x.append(x)
                    history_y.append(y)
                else:
                    history_count = 0
                flags |= (current_count + history_count < size).astype(np.uint8) * MISSING
                states.extend(flags)
                edges.extend(_edge(frequencies, index) for index in range(lower + size, upper + 1, size))
        check_cancelled(cancelled)
        arrays = (np.asarray(edges, dtype=np.float64), np.asarray(states, dtype=np.uint8),
                  np.concatenate(history_x) if history_x else np.empty(0, dtype=np.float64),
                  np.concatenate(history_y) if history_y else np.empty(0, dtype=np.float64))
        for array in arrays:
            array.setflags(write=False)
        return CoverageProjection(arrays[0], arrays[1], EnvelopeTrace(
            arrays[2], arrays[3], max(0, stop - start), peak_preserving=True))


def _edge(frequencies: np.ndarray, index: int) -> float:
    if index == 0:
        return float(frequencies[0] - (frequencies[1] - frequencies[0]) / 2)
    if index == frequencies.size:
        return float(frequencies[-1] + (frequencies[-1] - frequencies[-2]) / 2)
    return float(frequencies[index - 1] + (frequencies[index] - frequencies[index - 1]) / 2)

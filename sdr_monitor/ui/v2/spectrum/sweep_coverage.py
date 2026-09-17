"""Bounded display-only Sweep coverage/history, never an analytical input."""
from dataclasses import dataclass
from math import ceil
from typing import TypeAlias

import numpy as np

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from .contracts import EnvelopeTrace, SpectrumFrameView
from .envelope import peak_preserving_envelope

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

    def project(self, left: float, right: float, width: int) -> CoverageProjection:
        """O(visible bins), O(columns + one bucket) scratch, no full-grid copy.

        At most 2,048 columns and nine history points per column. The existing
        extrema reducer keeps peaks and explicit holes. A zoom reprojects the
        immutable sources, never the already reduced screen representation.
        """
        frame = self.current
        if frame is None:
            raise ValueError("no Sweep measurement for coverage")
        frequencies = frame.frequencies_hz
        count = frequencies.size
        start = max(0, int(np.searchsorted(frequencies, left)) - 1)
        stop = min(count, int(np.searchsorted(frequencies, right, side="right")) + 1)
        edges: list[float] = []
        states: list[int] = []
        history_x: list[float] = []
        history_y: list[float] = []
        if stop > start:
            bucket_size = ceil((stop - start) / max(1, min(MAX_COLUMNS, int(width))))
            edges.append(_edge(frequencies, start))
            for lower in range(start, stop, bucket_size):
                upper = min(stop, lower + bucket_size)
                current = np.isfinite(frame.values_db[lower:upper])
                old = (self.previous.values_db[lower:upper] if self.previous is not None
                       else np.full(upper - lower, np.nan, dtype=np.float32))
                historical = ~current & np.isfinite(old)
                absent = ~current & ~historical
                states.append((CURRENT if np.any(current) else 0)
                              | (PREVIOUS if np.any(historical) else 0)
                              | (MISSING if np.any(absent) else 0))
                edges.append(_edge(frequencies, upper))
                if self.previous is not None:
                    view = SpectrumFrameView(self.previous, frequencies[lower:upper],
                                             np.where(historical, old, np.nan), frame.unit)
                    envelope = peak_preserving_envelope(view, 1)
                    history_x.extend(envelope.frequencies_hz)
                    history_y.extend(envelope.values)
        arrays = (np.asarray(edges, dtype=np.float64), np.asarray(states, dtype=np.uint8),
                  np.asarray(history_x, dtype=np.float64), np.asarray(history_y, dtype=np.float64))
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

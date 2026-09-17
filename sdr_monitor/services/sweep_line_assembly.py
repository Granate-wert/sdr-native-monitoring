"""Deterministic bounded reference assembler for R10-D sweep-line contracts.

This module is deliberately not a production high-rate path: it receives
already-reduced ``SweepSegmentSpectrum`` objects and exists to lock the domain
semantics with synthetic tests.  The future production implementation must be
native and preserve these publication/gap invariants without a Python callback
per FFT or raw-I/Q crossing.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from ..domain.sweep import SweepSegmentSpectrum
from ..domain.sweep_lines import (
    SweepLineAssemblyMetrics,
    SweepLineDefinition,
    SweepLineFrame,
    SweepLineGapReason,
    SweepLineState,
)
from .sweep_stitching import SweepStitchOptions, stitch_sweep_segments


@dataclass(slots=True)
class _PendingLine:
    completed_at_ns: int
    spectra: dict[int, SweepSegmentSpectrum] = field(default_factory=dict)


class SweepLineReferenceAssembler:
    """Bounded synthetic oracle; never silently overwrites or fills a gap."""

    def __init__(self, definition: SweepLineDefinition) -> None:
        self._definition = definition
        self._pending: OrderedDict[int, _PendingLine] = OrderedDict()
        self._completed_lines = 0
        self._gapped_lines = 0
        self._evicted_inflight_lines = 0

    @property
    def metrics(self) -> SweepLineAssemblyMetrics:
        return SweepLineAssemblyMetrics(
            self._completed_lines,
            self._gapped_lines,
            self._evicted_inflight_lines,
            len(self._pending),
        )

    def admit(
        self,
        sequence: int,
        completed_at_ns: int,
        spectrum: SweepSegmentSpectrum,
    ) -> tuple[SweepLineFrame, ...]:
        """Admit one reduced segment and return every newly finalised line.

        A capacity eviction finalises the oldest incomplete line as an explicit
        gap before the new line is staged. Source/generation/duplicate errors
        are rejected before they can corrupt a staged line.
        """

        if sequence < 0 or completed_at_ns < 0:
            raise ValueError("sweep-line sequence and timestamp must be non-negative")
        expected_generation = dict(self._definition.expected_generation_by_segment).get(spectrum.segment_index)
        if expected_generation is None:
            raise ValueError("sweep-line segment is outside its definition")
        if spectrum.source_id != self._definition.source_id or spectrum.unit != self._definition.unit:
            raise ValueError("sweep-line source or unit differs from its definition")
        if spectrum.config_generation != expected_generation:
            raise ValueError("sweep-line segment generation differs from its epoch definition")

        finished: list[SweepLineFrame] = []
        pending = self._pending.get(sequence)
        if pending is None:
            if len(self._pending) >= self._definition.max_inflight_lines:
                evicted_sequence, evicted = self._pending.popitem(last=False)
                finished.append(self._finalise_gap(evicted_sequence, evicted, SweepLineGapReason.CAPACITY))
                self._evicted_inflight_lines += 1
            pending = _PendingLine(completed_at_ns)
            self._pending[sequence] = pending
        else:
            pending.completed_at_ns = max(pending.completed_at_ns, completed_at_ns)
        if spectrum.segment_index in pending.spectra:
            raise ValueError("sweep-line segment was admitted more than once")
        pending.spectra[spectrum.segment_index] = spectrum
        if len(pending.spectra) == len(self._definition.plan.segments):
            self._pending.pop(sequence)
            finished.append(self._finalise_complete(sequence, pending))
        return tuple(finished)

    def flush(self, reason: SweepLineGapReason) -> tuple[SweepLineFrame, ...]:
        """Terminate every staged line as a visible, ordered control gap."""

        finished = tuple(
            self._finalise_gap(sequence, pending, reason)
            for sequence, pending in self._pending.items()
        )
        self._pending.clear()
        return finished

    def _stitch(self, spectra: tuple[SweepSegmentSpectrum, ...]):
        return stitch_sweep_segments(
            self._definition.plan,
            spectra,
            SweepStitchOptions(
                target_spacing_hz=self._definition.target_spacing_hz,
                expected_generation_by_segment=self._definition.expected_generation_by_segment,
            ),
        )

    def _finalise_complete(self, sequence: int, pending: _PendingLine) -> SweepLineFrame:
        grid = self._stitch(tuple(pending.spectra.values()))
        state = (
            SweepLineState.COMPLETE
            if not grid.missing_segment_indices and np.all(np.isfinite(grid.values_db))
            else SweepLineState.GAP
        )
        reasons = () if state is SweepLineState.COMPLETE else (SweepLineGapReason.MISSING_SEGMENT,)
        frame = SweepLineFrame(
            sequence,
            self._definition.epoch,
            pending.completed_at_ns,
            grid.source_id,
            state,
            grid.frequencies_hz,
            grid.values_db,
            grid.quality_flags,
            grid.source_segment_indices,
            grid.missing_segment_indices,
            grid.segment_config_generations,
            reasons,
            grid.unit,
        )
        if frame.is_complete:
            self._completed_lines += 1
        else:
            self._gapped_lines += 1
        return frame

    def _finalise_gap(
        self,
        sequence: int,
        pending: _PendingLine,
        reason: SweepLineGapReason,
    ) -> SweepLineFrame:
        grid = self._stitch(tuple(pending.spectra.values()))
        frame = SweepLineFrame(
            sequence,
            self._definition.epoch,
            pending.completed_at_ns,
            grid.source_id,
            SweepLineState.GAP,
            grid.frequencies_hz,
            grid.values_db,
            grid.quality_flags,
            grid.source_segment_indices,
            grid.missing_segment_indices,
            grid.segment_config_generations,
            (reason,),
            grid.unit,
        )
        self._gapped_lines += 1
        return frame


__all__ = ["SweepLineReferenceAssembler"]

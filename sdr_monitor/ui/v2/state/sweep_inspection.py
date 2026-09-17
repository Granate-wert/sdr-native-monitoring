"""Scalar-only Sweep inspection: no bin reductions, retained frame or RF clock."""
from dataclasses import dataclass

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_acquisition import SweepSegmentAcquisition, SweepSegmentPosition
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame


@dataclass(frozen=True, slots=True)
class SegmentInspection:
    index: int
    state: str
    generation: int | None
    acquisition: SweepSegmentAcquisition | None
    previous_generation: int | None = None
    previous_acquisition: SweepSegmentAcquisition | None = None


@dataclass(frozen=True, slots=True)
class SweepInspection:
    source: str
    epoch: int
    sequence: int
    revision: int | None
    unit: str
    terminal: bool
    complete: bool
    previous_sequence: int | None
    segments: tuple[SegmentInspection, ...]
    last_admitted_segment: SweepSegmentPosition | None = None


def inspect_sweep(snapshot: ContinuousSweepDisplaySnapshot | None) -> SweepInspection | None:
    """Read existing segment scalars only; never scan/copy spectrum arrays.

    A retained older terminal is history, NOT current coverage. The acquisition
    tuple is not an arrival-order contract, so neither max(index), tuple[-1] nor
    max(timestamp) is used to invent a scan cursor. Segment count is not a bin
    coverage percentage and a received segment is not necessarily good quality.
    """
    if snapshot is None:
        return None
    frame: SweepLineFrame | SweepProgressFrame | None = snapshot.line
    if snapshot.progress is not None and (frame is None or snapshot.progress.sequence > frame.sequence):
        frame = snapshot.progress
    if frame is None:
        return None
    partial = isinstance(frame, SweepProgressFrame)
    if isinstance(frame, SweepProgressFrame):
        generations = dict(frame.acquired_segment_generations)
        absent = set(frame.pending_segment_indices)
    else:
        absent = set(frame.missing_segment_indices)
        generations = {index: generation for index, generation in frame.segment_config_generations
                       if index not in absent}
    indices = sorted(set(generations) | absent)
    # Matches application preflight. Refuse, never silently truncate evidence.
    if len(indices) > 64:
        raise ValueError("Sweep inspection exceeds the 64-segment application limit")
    records = {record.segment_index: record for record in frame.segment_acquisition or ()}
    previous = snapshot.line if snapshot.line is not None and snapshot.line.sequence < frame.sequence else None
    previous_generations = ({index: generation for index, generation in previous.segment_config_generations
                             if index not in previous.missing_segment_indices} if previous else {})
    previous_records = {record.segment_index: record for record in previous.segment_acquisition or ()} if previous else {}
    return SweepInspection(
        frame.source_id, frame.epoch, frame.sequence,
        frame.revision if isinstance(frame, SweepProgressFrame) else None,
        frame.unit, not partial, isinstance(frame, SweepLineFrame) and frame.is_complete,
        previous.sequence if previous else None,
        tuple(SegmentInspection(index, "received" if index in generations else "pending" if partial else "missing",
                                generations.get(index), records.get(index), previous_generations.get(index),
                                previous_records.get(index)) for index in indices),
        frame.last_admitted_segment,
    )

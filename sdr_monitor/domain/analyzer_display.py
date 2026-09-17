"""Immutable reduced Sweep publication shared by infrastructure and application."""
from dataclasses import dataclass

import numpy as np

from .analyzer import AnalyzerFrameBundle, bundle_from_sweep
from .sweep_lines import SweepLineFrame
from .sweep_progress import SweepProgressFrame


@dataclass(frozen=True, slots=True)
class ContinuousSweepDisplayMetrics:
    """Publication counters, not analytical FFT/s or UI paint FPS."""
    completed_line_lps: float = 0.0
    completed_lines: int = 0
    gapped_lines: int = 0
    native_line_relay_superseded: int = 0
    native_line_relay_queue_capacity: int = 0
    native_line_relay_queue_high_water: int = 0
    terminal_control_gaps: int = 0
    native_output_superseded: int = 0
    ui_snapshots_superseded: int = 0
    native_queue_depth: int = 0
    native_queue_capacity: int = 0
    has_error: bool = False


@dataclass(frozen=True, slots=True)
class ContinuousSweepDisplaySnapshot:
    """Latest progressive/terminal measurement and separate native counters."""
    line: SweepLineFrame | None
    metrics: ContinuousSweepDisplayMetrics
    progress: SweepProgressFrame | None = None

    def __post_init__(self) -> None:
        if self.line is not None and not isinstance(self.line, SweepLineFrame):
            raise TypeError("Sweep terminal publication must be a SweepLineFrame")
        if self.progress is not None and not isinstance(self.progress, SweepProgressFrame):
            raise TypeError("Sweep preview publication must be a SweepProgressFrame")
        if self.line is not None and self.progress is not None:
            line, progress = self.line, self.progress
            if ((line.source_id, line.epoch, line.unit) != (progress.source_id, progress.epoch, progress.unit)
                    or not np.array_equal(line.frequencies_hz, progress.frequencies_hz)):
                raise ValueError("Sweep terminal and preview must share source, epoch, unit and grid")

    @property
    def analyzer_bundle(self) -> AnalyzerFrameBundle | None:
        # Arrival order is not acquisition order. A terminal event wins over
        # the same/older preview; a later preview must not erase the separate
        # terminal event consumed through ``line`` (e.g. future history layers).
        measurement: SweepLineFrame | SweepProgressFrame | None = self.line
        if self.progress is not None and (measurement is None or self.progress.sequence > measurement.sequence):
            measurement = self.progress
        return bundle_from_sweep(measurement) if measurement is not None else None

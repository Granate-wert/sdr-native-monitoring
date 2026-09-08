"""Immutable reduced Sweep publication shared by infrastructure and application."""
from dataclasses import dataclass

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

    @property
    def analyzer_bundle(self) -> AnalyzerFrameBundle | None:
        measurement = self.progress if self.progress is not None else self.line
        return bundle_from_sweep(measurement) if measurement is not None else None

"""Pure, bounded presentation preparation for the existing Sweep worker.

This performs display LOD, not analytical FFT/detection. No Qt object, receiver,
timer or extra executor is owned here. Terminal and next-pass preview stay in
one packet, with the exact originating domain snapshot retained for coherence.
"""

from dataclasses import dataclass

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle

from ..waterfall.contracts import SweepWaterfallLine
from .analyzer_layers import waterfall_line_from_sweep


@dataclass(frozen=True, slots=True)
class PreparedSweepSnapshot:
    snapshot: ContinuousSweepDisplaySnapshot
    analyzer_bundle: AnalyzerFrameBundle | None
    waterfall_rows: tuple[SweepWaterfallLine, ...]
    waterfall_error: str | None = None

    def __post_init__(self) -> None:
        terminal = self.snapshot.line
        preview = self.snapshot.progress
        expected = (preview if preview is not None and
                    (terminal is None or preview.sequence > terminal.sequence) else terminal)
        if ((expected is None) != (self.analyzer_bundle is None)
                or self.analyzer_bundle is not None and self.analyzer_bundle.spectrum is not expected):
            raise ValueError("Prepared bundle must retain its exact snapshot measurement")
        if not isinstance(self.waterfall_rows, tuple) or len(self.waterfall_rows) > 2:
            raise ValueError("Prepared Sweep admits at most two immutable display rows")
        if self.waterfall_error is not None and self.waterfall_rows:
            raise ValueError("Failed preparation cannot retain stale display rows")


def prepare_sweep_snapshot(snapshot: ContinuousSweepDisplaySnapshot,
                           bundle: AnalyzerFrameBundle | None) -> PreparedSweepSnapshot:
    """Validate/reduce at most two rows off GUI; preserve old fail-closed UX."""
    if not isinstance(snapshot, ContinuousSweepDisplaySnapshot):
        raise TypeError("Sweep preparation requires a domain snapshot")
    try:
        rows = tuple(waterfall_line_from_sweep(frame)
                     for frame in (snapshot.line, snapshot.progress) if frame is not None)
    except (ValueError, TypeError) as error:
        # A malformed display grid clears Waterfall and reports its reason;
        # it must not be silently replaced by the preceding successful rows.
        return PreparedSweepSnapshot(snapshot, bundle, (), str(error))
    return PreparedSweepSnapshot(snapshot, bundle, rows)

"""Pure, bounded presentation preparation for the existing Sweep worker.

This performs display LOD, not analytical FFT/detection. No Qt object, receiver,
timer or extra executor is owned here. Terminal and next-pass preview stay in
one packet, with the exact originating domain snapshot retained for coherence.
"""

from dataclasses import dataclass

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle

from ..waterfall.contracts import SweepWaterfallLine
from ..spectrum.contracts import PreparedSpectrumFrame
from .analyzer_layers import waterfall_line_from_sweep
from ..spectrum.allocation_budget import PresentationAllocationBudget, PresentationBudgetExceeded
from ..spectrum.grid_baseline import MeasurementGridCache
from .source_admission import PresentationSourceAdmission
from ..spectrum.cancellation import CancelCheck, check_cancelled


@dataclass(frozen=True, slots=True)
class PreparedSweepSnapshot:
    snapshot: ContinuousSweepDisplaySnapshot
    analyzer_bundle: AnalyzerFrameBundle | None
    waterfall_rows: tuple[SweepWaterfallLine, ...]
    waterfall_error: str | None = None
    spectrum: PreparedSpectrumFrame | None = None
    memory_limited: bool = False

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
        if ((self.analyzer_bundle is None) != (self.spectrum is None)
                or self.spectrum is not None and self.spectrum.view.source_frame is not self.analyzer_bundle):
            raise ValueError("Prepared spectrum must retain the exact Analyzer bundle")


def prepare_sweep_snapshot(snapshot: ContinuousSweepDisplaySnapshot,
                           bundle: AnalyzerFrameBundle | None, *,
                           spectrum: PreparedSpectrumFrame | None = None,
                           grid_cache: MeasurementGridCache | None = None) -> PreparedSweepSnapshot:
    """Validate/reduce at most two rows off GUI; preserve old fail-closed UX."""
    if not isinstance(snapshot, ContinuousSweepDisplaySnapshot):
        raise TypeError("Sweep preparation requires a domain snapshot")
    spectrum = spectrum if spectrum is not None else None if bundle is None else PreparedSpectrumFrame(bundle)
    try:
        rows = tuple(waterfall_line_from_sweep(frame, grid_cache=grid_cache)
                     for frame in (snapshot.line, snapshot.progress) if frame is not None)
    except (ValueError, TypeError) as error:
        # A malformed display grid clears Waterfall and reports its reason;
        # it must not be silently replaced by the preceding successful rows.
        return PreparedSweepSnapshot(snapshot, bundle, (), str(error), spectrum)
    return PreparedSweepSnapshot(snapshot, bundle, rows, spectrum=spectrum)


class SweepSnapshotPreparer:
    """Use the same derived-array ledger without turning display pressure into Stop."""

    def __init__(self, allocation_budget: PresentationAllocationBudget) -> None:
        self.allocation_budget = allocation_budget
        self._grid = MeasurementGridCache(allocation_budget)

    def __call__(self, snapshot: ContinuousSweepDisplaySnapshot,
                 bundle: AnalyzerFrameBundle | None) -> PreparedSweepSnapshot:
        return self.prepare_cancellable(snapshot, bundle)

    def clear(self) -> None:
        """Release owned geometry only after the preparation owner is terminal."""
        self._grid.clear()

    def prepare_cancellable(self, snapshot: ContinuousSweepDisplaySnapshot,
                            bundle: AnalyzerFrameBundle | None, *,
                            cancelled: CancelCheck = None) -> PreparedSweepSnapshot:
        """Cooperative optional preview work; no terminal or device cancellation."""
        if (snapshot.line is not None or snapshot.progress is None
                or snapshot.presentation_omission is not None or snapshot.metrics.has_error):
            cancelled = None
        check_cancelled(cancelled)
        if snapshot.presentation_omission is not None:
            self._grid.clear()
            return PreparedSweepSnapshot(snapshot, None, (), "presentation_memory_budget", memory_limited=True)
        self.allocation_budget.observe(snapshot, bundle)
        if bundle is None:
            self._grid.clear()
        try:
            spectrum = None if bundle is None else PreparedSpectrumFrame(
                bundle, grid_cache=self._grid, cancelled=cancelled)
        except PresentationBudgetExceeded:
            self._grid.clear()
            omitted = PresentationSourceAdmission(self.allocation_budget).omit_sweep(snapshot)
            return PreparedSweepSnapshot(omitted, None, (), "presentation_memory_budget", memory_limited=True)
        check_cancelled(cancelled)
        size = sum(min(2048, frame.values_db.size) * 12 + 8
                   for frame in (snapshot.line, snapshot.progress) if frame is not None)
        try:
            with self.allocation_budget.reserve(int(size)) as allocation:
                prepared = prepare_sweep_snapshot(snapshot, bundle, spectrum=spectrum, grid_cache=self._grid)
                check_cancelled(cancelled)
                allocation.commit(*prepared.waterfall_rows)
                return prepared
        except PresentationBudgetExceeded as error:
            # Preserve the exact spectrum and terminal lifecycle publication;
            # only the optional derived Waterfall rows are unavailable.
            return PreparedSweepSnapshot(snapshot, bundle, (), str(error),
                                         spectrum, memory_limited=True)

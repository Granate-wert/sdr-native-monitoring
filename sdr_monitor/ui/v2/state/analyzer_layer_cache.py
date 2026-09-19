"""One-entry-per-layer cache owned by one Live presentation adapter."""

from __future__ import annotations

from sdr_monitor.domain.live import LiveSpectrumFrame

from ..spectrum.persistence_contracts import PersistenceDensityFrame
from ..waterfall.contracts import WaterfallLineFrame
from .analyzer_layers import persistence_density_from_native, waterfall_line_from_spectrum
from ..spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.domain.live import LivePersistenceFrame


class AnalyzerLayerCache:
    """Reuse only identical immutable inputs, after the domain coherence gate.

    No global storage, numerical identity guess, history, or device ownership.
    A new input replaces the previous retained input/output, bounding storage
    to one converted density and one display row plus their source references.
    """

    def __init__(self, allocation_budget: PresentationAllocationBudget | None = None) -> None:
        self.allocation_budget = allocation_budget
        self.clear()

    def clear(self) -> None:
        self._density_source: object | None = None
        self._density: PersistenceDensityFrame | None = None
        self._waterfall_source: LiveSpectrumFrame | None = None
        self._waterfall: WaterfallLineFrame | None = None

    def persistence(self, frame: object) -> PersistenceDensityFrame | None:
        if frame is not self._density_source:
            # Release the old pair even if conversion of the new input fails.
            self._density_source, self._density = None, None
            if self.allocation_budget is not None and isinstance(frame, LivePersistenceFrame):
                size = int(frame.density.size * 4 + (frame.frequencies_hz.size + 1) * 8
                           + (frame.power_bins + 1) * 8)
                with self.allocation_budget.reserve(size, frame) as allocation:
                    converted = persistence_density_from_native(frame)
                    allocation.commit(converted)
            else:
                converted = persistence_density_from_native(frame)
            self._density_source, self._density = frame, converted
        return self._density

    def waterfall(self, frame: LiveSpectrumFrame) -> WaterfallLineFrame:
        if frame is not self._waterfall_source or self._waterfall is None:
            self._waterfall_source, self._waterfall = None, None
            if self.allocation_budget is not None:
                columns = min(2048, frame.values.size)
                with self.allocation_budget.reserve(int(columns * 12 + 8), frame) as allocation:
                    converted = waterfall_line_from_spectrum(frame)
                    allocation.commit(converted)
            else:
                converted = waterfall_line_from_spectrum(frame)
            self._waterfall_source, self._waterfall = frame, converted
        return self._waterfall

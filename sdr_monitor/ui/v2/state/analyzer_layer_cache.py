"""One-entry-per-layer cache owned by one Live presentation adapter."""

from __future__ import annotations

from sdr_monitor.domain.live import LiveSpectrumFrame

from ..spectrum.persistence_contracts import PersistenceDensityFrame
from ..waterfall.contracts import WaterfallLineFrame
from .analyzer_layers import (
    _validate_native_persistence_display, persistence_density_from_native, waterfall_line_from_spectrum,
)
from ..spectrum.allocation_budget import PresentationAllocationBudget
from ..spectrum.grid_baseline import MeasurementGridCache
from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.domain.persistence_storage import is_persistence_storage_receipt


class AnalyzerLayerCache:
    """Reuse only identical immutable inputs, after the domain coherence gate.

    No global storage, numerical identity guess, history, or device ownership.
    A new input replaces the previous retained input/output, bounding storage
    to one converted density and one display row plus their source references.
    """

    def __init__(self, allocation_budget: PresentationAllocationBudget | None = None, *,
                 grid_cache: MeasurementGridCache | None = None) -> None:
        self.allocation_budget = allocation_budget
        self._grid = grid_cache
        self.clear()

    def clear(self) -> None:
        self._density_source: object | None = None
        self._density: PersistenceDensityFrame | None = None
        self._waterfall_source: LiveSpectrumFrame | None = None
        self._waterfall: WaterfallLineFrame | None = None
        self._range_source: LivePersistenceFrame | None = None
        self._range_receipt: object | None = None
        self._range_validated = False

    def validate_persistence(self, frame: object) -> bool:
        """Validate every publication, reusing only exact native FP-clean range work."""
        trusted = (type(frame) is LivePersistenceFrame
                   and is_persistence_storage_receipt(frame.immutable_storage)
                   and frame.immutable_storage.matches(frame.density, frame.frequencies_hz))
        cached = (trusted and frame is self._range_source
                  and frame.immutable_storage is self._range_receipt and self._range_validated)
        if not trusted or (self._range_source is not None and frame is not self._range_source):
            self._clear_range()
        try:
            result, fp_clean = _validate_native_persistence_display(
                frame, self.allocation_budget, trusted_storage=trusted, range_cached=cached,
                grid_cache=self._grid)
        except Exception:
            self._clear_range()
            raise
        if trusted and result and fp_clean:
            self._range_source = frame
            self._range_receipt = frame.immutable_storage
            self._range_validated = True
        else:
            self._clear_range()
        return result

    def _clear_range(self) -> None:
        self._range_source = None
        self._range_receipt = None
        self._range_validated = False

    def persistence(self, frame: object) -> PersistenceDensityFrame | None:
        if frame is not self._density_source:
            # Release the old pair even if conversion of the new input fails.
            self._density_source, self._density = None, None
            if frame is not self._range_source:
                self._clear_range()
            try:
                if self.allocation_budget is not None and isinstance(frame, LivePersistenceFrame):
                    size = int(frame.density.size * 4 + (frame.frequencies_hz.size + 1) * 8
                               + (frame.power_bins + 1) * 8)
                    with self.allocation_budget.reserve(size, frame) as allocation:
                        converted = persistence_density_from_native(frame)
                        allocation.commit(converted)
                else:
                    converted = persistence_density_from_native(frame)
            except Exception:
                self._clear_range()
                raise
            self._density_source, self._density = frame, converted
        return self._density

    def clear_density(self, incoming: object = None) -> None:
        """Release a replaced pair; an identical original source keeps reuse.

        This never certifies payload validity. Ordered validation still scans
        every incoming input; calling without a source forces release.
        """
        if incoming is None or incoming is not self._density_source:
            self._density_source, self._density = None, None
        if incoming is None or incoming is not self._range_source:
            self._clear_range()

    def waterfall(self, frame: LiveSpectrumFrame) -> WaterfallLineFrame:
        if frame is not self._waterfall_source or self._waterfall is None:
            self._waterfall_source, self._waterfall = None, None
            if self.allocation_budget is not None:
                columns = min(2048, frame.values.size)
                with self.allocation_budget.reserve(int(columns * 12 + 8), frame) as allocation:
                    converted = waterfall_line_from_spectrum(frame, grid_cache=self._grid)
                    allocation.commit(converted)
            else:
                converted = waterfall_line_from_spectrum(frame, grid_cache=self._grid)
            self._waterfall_source, self._waterfall = frame, converted
        return self._waterfall

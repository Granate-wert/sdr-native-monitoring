"""One-entry-per-layer cache owned by one Live presentation adapter."""

from __future__ import annotations

from sdr_monitor.domain.live import LiveSpectrumFrame

from ..spectrum.persistence_contracts import PersistenceDensityFrame
from ..waterfall.contracts import WaterfallLineFrame
from .analyzer_layers import persistence_density_from_native, waterfall_line_from_spectrum


class AnalyzerLayerCache:
    """Reuse only identical immutable inputs, after the domain coherence gate.

    No global storage, numerical identity guess, history, or device ownership.
    A new input replaces the previous retained input/output, bounding storage
    to one converted density and one display row plus their source references.
    """

    def __init__(self) -> None:
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
            converted = persistence_density_from_native(frame)
            self._density_source, self._density = frame, converted
        return self._density

    def waterfall(self, frame: LiveSpectrumFrame) -> WaterfallLineFrame:
        if frame is not self._waterfall_source or self._waterfall is None:
            self._waterfall_source, self._waterfall = None, None
            converted = waterfall_line_from_spectrum(frame)
            self._waterfall_source, self._waterfall = frame, converted
        return self._waterfall

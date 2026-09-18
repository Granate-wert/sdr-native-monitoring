"""Pure Live display preparation invoked by the existing presenter worker."""
from dataclasses import replace

from sdr_monitor.domain.live import LiveSnapshot

from ..spectrum.contracts import PreparedSpectrumFrame
from .analyzer_layer_cache import AnalyzerLayerCache
from .live_view_state import LiveViewState, build_live_view_state


class LiveSnapshotPreparer:
    """One worker-owned bounded cache, no Qt, device or executor ownership.

    The result retains the exact coherent snapshot/bundle and converted layers.
    GUI rebuilds cheap labels, age and controls from that same snapshot, never
    trusts worker-time labels as current UI state, and never validates it twice.
    """

    def __init__(self) -> None:
        self._layers = AnalyzerLayerCache()

    def __call__(self, snapshot: LiveSnapshot) -> LiveViewState:
        if not isinstance(snapshot, LiveSnapshot):
            raise TypeError("Live preparation requires an immutable domain snapshot")
        state = build_live_view_state(snapshot, layer_cache=self._layers)
        bundle = state.analyzer_bundle
        return replace(state, prepared_spectrum=None if bundle is None else PreparedSpectrumFrame(bundle))

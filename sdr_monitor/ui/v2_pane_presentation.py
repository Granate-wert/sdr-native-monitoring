"""Worker-side, resource-scoped preparation for independent Analyzer panes.

This is not a second acquisition or DSP path. The input is an already
admitted PaneResourceSession delivery; existing spectrum, persistence and
waterfall presentation adapters are reused without changing its measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneCrop, PaneLayout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.pane_resource_session import PaneDelivery

from .v2.spectrum.allocation_budget import PresentationAllocationBudget
from .v2.spectrum.contracts import PreparedSpectrumFrame
from .v2.spectrum.grid_baseline import MeasurementGridCache
from .v2.spectrum.persistence_contracts import PersistenceDensityFrame
from .v2.state.analyzer_layer_cache import AnalyzerLayerCache
from .v2.state.analyzer_layers import persistence_density_from_sweep, waterfall_line_from_sweep
from .v2.view_models.analyzer_view_model import AnalyzerMode
from .v2.waterfall.contracts import SweepWaterfallLine, WaterfallLineFrame


@dataclass(frozen=True, slots=True)
class PanePresentationBinding:
    slot_number: int
    pane_id: str
    physical_stream_resource_id: str
    capture_id: str
    receiver_endpoint_id: str
    source_id: str
    crop: PaneCrop
    capture_start_hz: float
    capture_stop_hz: float
    mode: AnalyzerMode
    measurement_mode: CaptureMeasurementMode
    unit: str


@dataclass(frozen=True, slots=True)
class PreparedPaneDelivery:
    """One exact immutable bundle and its optional worker-prepared layers."""

    binding: PanePresentationBinding
    delivery: PaneDelivery
    spectrum: PreparedSpectrumFrame
    waterfall: WaterfallLineFrame | SweepWaterfallLine | None
    persistence: PersistenceDensityFrame | None

    def __post_init__(self) -> None:
        if self.spectrum.view.source_frame is not self.delivery.bundle:
            raise ValueError("pane spectrum preparation belongs to another publication")
        if type(self.delivery.host_run_serial) is not int or self.delivery.host_run_serial < 0:
            raise ValueError("pane publication requires an explicit non-negative host run serial")
        binding, delivery = self.binding, self.delivery
        if (delivery.pane_id != binding.pane_id
                or delivery.physical_stream_resource_id != binding.physical_stream_resource_id
                or delivery.capture_id != binding.capture_id
                or delivery.receiver_endpoint_id != binding.receiver_endpoint_id
                or delivery.crop != binding.crop
                or delivery.bundle.identity is None
                or delivery.bundle.identity.source_id != binding.source_id
                or delivery.bundle.mode != binding.mode.value
                or delivery.bundle.unit != binding.unit):
            raise ValueError("prepared delivery differs from its exact pane binding")

    @property
    def bundle(self) -> AnalyzerFrameBundle:
        return self.delivery.bundle


class PaneDeliveryPreparer:
    """One bounded comparison/layer cache per occupied pane, no Qt or RX I/O.

    The caller owns worker scheduling and must serialize calls for a given
    pane. A shared budget charges all four pane presentation roots together.
    """

    def __init__(self, layout: PaneLayout, groups: tuple[AcquisitionGroup, ...],
                 allocation_budget: PresentationAllocationBudget) -> None:
        if not isinstance(layout, PaneLayout) or not isinstance(allocation_budget, PresentationAllocationBudget):
            raise TypeError("pane presentation needs a compiled layout and shared allocation budget")
        expected_resources = (set() if layout.schedule is None else
                              {item.physical_stream_resource_id for item in layout.schedule.resources})
        if (len({group.physical_stream_resource_id for group in groups}) != len(groups)
                or {group.physical_stream_resource_id for group in groups} != expected_resources):
            raise ValueError("pane presentation resources differ from the admitted layout")
        endpoints = {}
        for group in groups:
            for endpoint in group.endpoints:
                if endpoint.endpoint_id in endpoints:
                    raise ValueError("pane presentation has duplicate receiver endpoints")
                endpoints[endpoint.endpoint_id] = endpoint
        jobs = {}
        if layout.schedule is not None:
            for resource in layout.schedule.resources:
                for job in resource.jobs:
                    for crop in job.crops:
                        if crop.pane_id in jobs:
                            raise ValueError("pane presentation has multiple capture jobs for one pane")
                        jobs[crop.pane_id] = (resource.physical_stream_resource_id, job, crop)
        bindings = {}
        for slot in layout.slots:
            request = slot.request
            if request is None:
                continue
            if request.pane_id not in jobs or request.receiver_endpoint_id not in endpoints:
                raise ValueError("occupied pane lacks its exact capture or endpoint")
            resource_id, job, crop = jobs[request.pane_id]
            endpoint = endpoints[request.receiver_endpoint_id]
            if (endpoint.physical_stream_resource_id != resource_id
                    or crop.receiver_endpoint_id != request.receiver_endpoint_id
                    or crop.start_hz != request.start_hz or crop.stop_hz != request.stop_hz):
                raise ValueError("pane presentation request differs from admitted capture crop")
            mode = (AnalyzerMode.RTBW if job.profile.measurement_mode is CaptureMeasurementMode.RTBW
                    else AnalyzerMode.SWEEP)
            bindings[request.pane_id] = PanePresentationBinding(
                slot.number, request.pane_id, resource_id, job.capture_id,
                request.receiver_endpoint_id, endpoint.source_id, crop,
                job.start_hz, job.stop_hz, mode,
                job.profile.measurement_mode, job.profile.unit)
        if set(bindings) != set(jobs):
            raise ValueError("pane presentation has unmatched occupied slots")
        self.layout = layout
        self.bindings = bindings
        self.allocation_budget = allocation_budget
        self._grids = {pane_id: MeasurementGridCache(allocation_budget) for pane_id in bindings}
        self._layers = {pane_id: AnalyzerLayerCache(allocation_budget, grid_cache=self._grids[pane_id])
                        for pane_id in bindings}

    def clear(self) -> None:
        """Release worker-owned comparison/layer roots after its worker joins."""
        for layers in self._layers.values():
            layers.clear()
        for grid in self._grids.values():
            grid.clear()

    def clear_resource(self, resource_id: str) -> None:
        """Reset only the stopped worker's layers before its explicit new run.

        Retained immutable GUI frames are not mutated or called new data.
        The caller serializes this with preparation on this resource alone.
        """
        pane_ids = tuple(pane_id for pane_id, binding in self.bindings.items()
                         if binding.physical_stream_resource_id == resource_id)
        if not pane_ids:
            raise ValueError("unknown pane resource cannot clear peer preparation")
        for pane_id in pane_ids:
            self._layers[pane_id].clear()
            self._grids[pane_id].clear()

    def prepare(self, delivery: PaneDelivery) -> PreparedPaneDelivery:
        if not isinstance(delivery, PaneDelivery):
            raise TypeError("pane presentation requires an admitted pane delivery")
        binding = self.bindings.get(delivery.pane_id)
        if (binding is None or delivery.physical_stream_resource_id != binding.physical_stream_resource_id
                or delivery.capture_id != binding.capture_id
                or delivery.receiver_endpoint_id != binding.receiver_endpoint_id
                or delivery.crop != binding.crop):
            raise ValueError("pane delivery does not belong to this exact slot and capture")
        bundle = delivery.bundle
        identity = bundle.identity
        if (identity is None or identity.source_id != binding.source_id
                or bundle.mode != binding.mode.value or bundle.unit != binding.unit):
            raise ValueError("pane delivery source, mode or unit differs from the selected binding")
        grid = bundle.frequencies_hz
        if grid.size < 2:
            raise ValueError("pane delivery has no usable physical frequency grid")
        # tinySA's scanraw Stop is exclusive, and integer-Hz stepping may
        # leave up to N-1 Hz between the last center's nominal next step and
        # that Stop. Trust its validated provenance, not an extrapolated bin.
        trace = (bundle.spectrum.instrument if binding.measurement_mode is CaptureMeasurementMode.INSTRUMENT_TRACE
                 and isinstance(bundle.spectrum, SweepLineFrame) else None)
        if binding.measurement_mode is CaptureMeasurementMode.INSTRUMENT_TRACE and (
                trace is None or trace.start_hz != binding.capture_start_hz
                or trace.stop_hz != binding.capture_stop_hz):
            raise ValueError("instrument trace span differs from the selected capture")
        lower = float(trace.start_hz) if trace is not None else float(grid[0])
        upper = float(trace.stop_hz) if trace is not None else float(grid[-1])
        if binding.measurement_mode is CaptureMeasurementMode.RTBW:
            bounds = bundle.rtbw_frequency_bounds_hz
            if bounds is None:
                raise ValueError("RTBW pane requires its validated FFT coverage")
            lower, upper = bounds
        if (binding.crop.start_hz < lower or binding.crop.stop_hz > upper):
            raise ValueError(f"pane crop for {binding.pane_id} is outside the delivered physical frequency grid")
        if not self.allocation_budget.admit_sources(bundle):
            raise ValueError("pane source exceeds the shared presentation allocation budget")
        prepared = PreparedSpectrumFrame(bundle, grid_cache=self._grids[binding.pane_id])
        waterfall: WaterfallLineFrame | SweepWaterfallLine | None
        if binding.mode is AnalyzerMode.RTBW:
            if not isinstance(bundle.spectrum, LiveSpectrumFrame):
                raise ValueError("RTBW pane requires a Live spectrum publication")
            layers = self._layers[binding.pane_id]
            persistence = layers.persistence(bundle.persistence)
            waterfall = layers.waterfall(bundle.spectrum)
        else:
            persistence = (None if bundle.sweep_statistics is None
                           else persistence_density_from_sweep(bundle.sweep_statistics))
            frame = bundle.spectrum
            if not isinstance(frame, (SweepLineFrame, SweepProgressFrame)):
                raise ValueError("Sweep pane requires a Sweep publication")
            columns = min(2048, frame.values_db.size)
            with self.allocation_budget.reserve(int(columns * 12 + 8), frame) as allocation:
                waterfall = waterfall_line_from_sweep(frame, grid_cache=self._grids[binding.pane_id])
                allocation.commit(waterfall)
        return PreparedPaneDelivery(binding, delivery, prepared, waterfall, persistence)


__all__ = ["PanePresentationBinding", "PreparedPaneDelivery", "PaneDeliveryPreparer"]

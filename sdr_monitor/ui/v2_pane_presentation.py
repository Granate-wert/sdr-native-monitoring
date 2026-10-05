"""Worker-side, resource-scoped preparation for independent Analyzer panes.

This is not a second acquisition or DSP path. The input is an already
admitted PaneResourceSession delivery; existing spectrum, persistence and
waterfall presentation adapters are reused without changing its measurement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneCrop, PaneLayout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup, ReceiverChainSelection, ReceiverEndpoint
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView
from .v2_pane_obligation_refs import delivery_obligation_refs

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
    # Logical chain from the exact AcquisitionGroup, never an endpoint suffix
    # or evidence of a verified physical RF connector.
    receiver_selection: ReceiverChainSelection | None = None

    def __post_init__(self) -> None:
        if self.receiver_selection is not None and not isinstance(self.receiver_selection, ReceiverChainSelection):
            raise TypeError("pane receiver selection must be an explicit typed chain")


@dataclass(frozen=True, slots=True)
class PreparedPaneDelivery:
    """One exact immutable bundle and its optional worker-prepared layers."""

    binding: PanePresentationBinding
    delivery: PaneDelivery
    spectrum: PreparedSpectrumFrame
    waterfall: WaterfallLineFrame | SweepWaterfallLine | None
    persistence: PersistenceDensityFrame | None
    # The selected device route remains binding.source_id. A paired native
    # frame instead carries its admitted per-RX producer SourceDescriptor.
    producer_source_id: str | None = None
    # Only refs whose requested layer exists in this exact worker preparation.
    # None preserves legacy facade behavior when optional metadata is absent.
    active_obligation_refs: tuple[PaneDeliveryObligationRef, ...] | None = None

    def __post_init__(self) -> None:
        if self.spectrum.view.source_frame is not self.delivery.bundle:
            raise ValueError("pane spectrum preparation belongs to another publication")
        if type(self.delivery.host_run_serial) is not int or self.delivery.host_run_serial < 0:
            raise ValueError("pane publication requires an explicit non-negative host run serial")
        binding, delivery = self.binding, self.delivery
        active_refs = self.active_obligation_refs
        if active_refs is not None:
            if (not isinstance(active_refs, tuple) or len(active_refs) > 3
                    or any(not isinstance(ref, PaneDeliveryObligationRef) for ref in active_refs)):
                raise ValueError("prepared custody refs must be a bounded immutable tuple")
            source_refs = delivery_obligation_refs(delivery)
            seen_views: set[PaneDeliveryView] = set()
            prepared_refs: list[PaneDeliveryObligationRef] = []
            for ref in active_refs:
                if not any(ref is source for source in source_refs) or ref.view in seen_views:
                    raise ValueError("prepared custody refs must be exact unique-view source refs")
                layer_prepared = (ref.view is PaneDeliveryView.SPECTRUM and self.spectrum is not None
                                  or ref.view is PaneDeliveryView.WATERFALL and self.waterfall is not None
                                  or ref.view is PaneDeliveryView.PERSISTENCE and self.persistence is not None)
                if not layer_prepared:
                    raise ValueError("prepared custody ref has no corresponding prepared layer")
                seen_views.add(ref.view)
                prepared_refs.append(ref)
            expected_refs = tuple(
                ref for ref in source_refs
                if (ref.view is PaneDeliveryView.SPECTRUM and self.spectrum is not None
                    or ref.view is PaneDeliveryView.WATERFALL and self.waterfall is not None
                    or ref.view is PaneDeliveryView.PERSISTENCE and self.persistence is not None))
            if (len(expected_refs) != len(prepared_refs)
                    or any(not any(ref is source_ref for source_ref in expected_refs)
                           for ref in prepared_refs)):
                raise ValueError("prepared custody refs must retain each prepared source view")
        producer = self.producer_source_id
        if (producer is not None and (not isinstance(producer, str) or not producer
                or producer != producer.strip())
                or (delivery.bundle.paired_capture is not None or delivery.bundle.paired_sweep is not None)
                and producer is None):
            raise ValueError("paired pane delivery requires an explicit admitted producer source")
        if (delivery.bundle.paired_capture is not None or delivery.bundle.paired_sweep is not None) and (
                binding.receiver_selection not in {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}
                or delivery.bundle.receiver_id != binding.receiver_selection.name):
            raise ValueError("paired pane delivery differs from its typed receiver selection")
        if delivery.bundle.paired_sweep is not None:
            request = delivery.bundle.paired_sweep.run.request
            paired_expected_source_id = (
                request.pair.primary_source_id if binding.receiver_selection is ReceiverChainSelection.RX1
                else request.pair.secondary_source_id)
            if (not delivery.bundle.paired_sweep.steps
                    or request.resource_id != binding.physical_stream_resource_id
                    or request.pair.device_id != binding.source_id
                    or paired_expected_source_id != producer):
                raise ValueError("paired Sweep delivery differs from its selected producer and resource")
        if (delivery.pane_id != binding.pane_id
                or delivery.physical_stream_resource_id != binding.physical_stream_resource_id
                or delivery.capture_id != binding.capture_id
                or delivery.receiver_endpoint_id != binding.receiver_endpoint_id
                or delivery.crop != binding.crop
                or delivery.bundle.identity is None
                or delivery.bundle.identity.source_id != (producer or binding.source_id)
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
                 allocation_budget: PresentationAllocationBudget, *,
                 admitted_producer_source_id: Callable[[str, str], str | None] | None = None) -> None:
        if not isinstance(layout, PaneLayout) or not isinstance(allocation_budget, PresentationAllocationBudget):
            raise TypeError("pane presentation needs a compiled layout and shared allocation budget")
        bindings = self._bindings_for_layout(layout, groups)
        self.layout = layout
        self.bindings = bindings
        self.allocation_budget = allocation_budget
        self._admitted_producer_source_id = admitted_producer_source_id
        self._paired_resource_ids = frozenset(group.physical_stream_resource_id for group in groups
            if {endpoint.selection for endpoint in group.endpoints if isinstance(endpoint, ReceiverEndpoint)}
            == {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2})
        # Sweep gaps are cumulative only within one line; native DSP resets
        # its count for the next LO sequence. Keep the line identity beside it.
        self._paired_sync_context: dict[str, tuple[
            int, int, str | None, int | None, CaptureMeasurementMode, int, tuple[int, int] | None]] = {}
        self._grids = {pane_id: MeasurementGridCache(allocation_budget) for pane_id in bindings}
        self._layers = {pane_id: AnalyzerLayerCache(allocation_budget, grid_cache=self._grids[pane_id])
                        for pane_id in bindings}

    @property
    def paired_resource_ids(self) -> frozenset[str]:
        """Logical paired groups from the compiled plan, not RF-path evidence."""
        return self._paired_resource_ids

    @staticmethod
    def _bindings_for_layout(layout: PaneLayout, groups: tuple[AcquisitionGroup, ...]) -> dict[str, PanePresentationBinding]:
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
                job.profile.measurement_mode, job.profile.unit,
                endpoint.selection if isinstance(endpoint, ReceiverEndpoint) else None)
        if set(bindings) != set(jobs):
            raise ValueError("pane presentation has unmatched occupied slots")
        return bindings

    def preview_resource_layout(self, layout: PaneLayout, groups: tuple[AcquisitionGroup, ...],
                                resource_id: str) -> dict[str, PanePresentationBinding]:
        """Validate the next routing without allocating layers or changing peers."""
        proposed = self._bindings_for_layout(layout, groups)
        if (proposed.keys() != self.bindings.keys()
                or any(proposed[key] != binding for key, binding in self.bindings.items()
                       if binding.physical_stream_resource_id != resource_id)
                or any(proposed[key].physical_stream_resource_id != binding.physical_stream_resource_id
                       or proposed[key].slot_number != binding.slot_number
                       or proposed[key].receiver_endpoint_id != binding.receiver_endpoint_id
                       or proposed[key].receiver_selection != binding.receiver_selection
                       or proposed[key].source_id != binding.source_id
                       for key, binding in self.bindings.items())):
            raise ValueError("RF change cannot replace pane identities or peer presentation")
        # Preserve exact peer binding objects, not just their equal values.
        return {key: proposed[key] if binding.physical_stream_resource_id == resource_id else binding
                for key, binding in self.bindings.items()}

    def commit_resource_layout(self, layout: PaneLayout, bindings: dict[str, PanePresentationBinding],
                               resource_id: str) -> None:
        """Only the stopped target worker commits its prevalidated routing.

        No GUI frame is relabelled or mutated. The GUI refreshes captions from
        this receipt BEFORE exposing the explicit next Start.
        """
        self.clear_resource(resource_id)
        self.bindings = bindings
        self.layout = layout

    def clear(self) -> None:
        """Release worker-owned comparison/layer roots after its worker joins."""
        self._paired_sync_context.clear()
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
        self._paired_sync_context.pop(resource_id, None)
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
        producer = (binding.source_id if self._admitted_producer_source_id is None else
                    self._admitted_producer_source_id(binding.physical_stream_resource_id,
                                                      binding.receiver_endpoint_id))
        paired_capture = bundle.paired_capture
        paired_sweep = bundle.paired_sweep
        paired = paired_capture is not None or paired_sweep is not None
        if (not isinstance(producer, str) or not producer or producer != producer.strip()
                or paired and self._admitted_producer_source_id is None
                or identity is None or identity.source_id != producer
                or bundle.mode != binding.mode.value or bundle.unit != binding.unit):
            raise ValueError("pane delivery source, mode or unit differs from the selected binding")
        if ((binding.physical_stream_resource_id in self._paired_resource_ids) != paired
                or paired_capture is not None and binding.measurement_mode is not CaptureMeasurementMode.RTBW
                or paired_sweep is not None and binding.measurement_mode is not CaptureMeasurementMode.SWEEP):
            raise ValueError("pane delivery pair metadata differs from its typed acquisition group")
        if paired and (
                binding.receiver_selection not in {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}
                or bundle.receiver_id != binding.receiver_selection.name):
            raise ValueError("pane delivery differs from its typed receiver selection")
        if paired_sweep is not None:
            request = paired_sweep.run.request
            expected = (request.pair.primary_source_id if binding.receiver_selection is ReceiverChainSelection.RX1
                        else request.pair.secondary_source_id)
            if (not paired_sweep.steps or request.resource_id != binding.physical_stream_resource_id
                    or request.pair.device_id != binding.source_id
                    or expected != producer):
                raise ValueError("paired Sweep delivery lacks acquired prefix or selected producer")
        if paired:
            # Sweep steps retune under one admitted attempt. Their per-step
            # synchronization epochs change normally and are NOT gap signals.
            if paired_capture is not None:
                marker = paired_capture.synchronization_epoch
            else:
                assert paired_sweep is not None
                marker = sum(step.primary.shared_input_gaps_before for step in paired_sweep.steps)
            sweep_line = (None if paired_sweep is None else
                          (paired_sweep.primary.epoch, paired_sweep.primary.sequence))
            context = (delivery.host_run_serial, delivery.host_activation_serial, bundle.session_id,
                       bundle.acquisition_epoch, binding.measurement_mode, marker, sweep_line)
            resource_id = binding.physical_stream_resource_id
            previous = self._paired_sync_context.get(resource_id)
            clear = previous is None or context[:2] != previous[:2]
            if paired_sweep is not None and previous is not None:
                if (context[:2] < previous[:2]
                        or context[:2] == previous[:2] and context[2:5] != previous[2:5]):
                    raise ValueError("pane paired publication is stale or differs from the current attempt")
                if context[:2] == previous[:2]:
                    prior_line = previous[6]
                    assert sweep_line is not None and prior_line is not None
                    if (sweep_line < prior_line or sweep_line == prior_line and marker < previous[5]):
                        raise ValueError("pane paired Sweep line or observed gap prefix regressed")
                    clear = ((sweep_line == prior_line and marker > previous[5])
                             or (sweep_line > prior_line and marker > 0))
            elif previous is not None:
                clear = context != previous
            if clear:
                # One shared input gap invalidates both RX histories before
                # either new-epoch frame enters a pane comparison cache.
                self.clear_resource(resource_id)
            self._paired_sync_context[resource_id] = context
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
        ready_views = {PaneDeliveryView.SPECTRUM}
        if waterfall is not None:
            ready_views.add(PaneDeliveryView.WATERFALL)
        if persistence is not None:
            ready_views.add(PaneDeliveryView.PERSISTENCE)
        source_refs = delivery_obligation_refs(delivery)
        active_refs = tuple(dict.fromkeys(ref for ref in source_refs if ref.view in ready_views))
        return PreparedPaneDelivery(binding, delivery, prepared, waterfall, persistence, producer, active_refs)


__all__ = ["PanePresentationBinding", "PreparedPaneDelivery", "PaneDeliveryPreparer"]

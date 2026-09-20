"""On-demand GUI-side inventory of retained presentation array owners.

No timer, device call, deep sample traversal, or retained publication in the
returned report. This is an exposed-array inventory, not RSS or an atomic
snapshot of concurrently allocating workers/native/Qt caches.
"""
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from ..spectrum.retained_bytes import retained_arrays, union_bytes
from ..spectrum.allocation_budget import AllocationBudgetSnapshot


@dataclass(frozen=True, slots=True)
class ArrayOwnerBytes:
    name: str
    bytes: int


@dataclass(frozen=True, slots=True)
class PresentationMemorySnapshot:
    owners: tuple[ArrayOwnerBytes, ...]
    unique_array_bytes: int
    shared_alias_bytes: int
    projection_reserved_bytes: int
    in_flight: tuple[str, ...]
    missing: tuple[str, ...]
    allocation_budget: AllocationBudgetSnapshot | None = None

    @property
    def exposed_plus_reserved_bytes(self) -> int:
        return self.unique_array_bytes + self.projection_reserved_bytes


def presentation_memory_snapshot(composition: Any, workspace: Any = None) -> PresentationMemorySnapshot:
    """Sample already-created owners only; never construct a deferred page.

    Per-owner totals overlap; unique_array_bytes is their deduplicated union.
    Future results are inspected ONLY after done(), while they await GUI ack.
    Concurrent worker scratch/intermediate results remain explicitly unknown.
    """
    app = QApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise RuntimeError("presentation memory inventory requires the GUI thread")
    owners: dict[str, dict[int, int]] = {}
    in_flight: list[str] = []
    missing = ["native allocator capacity", "backend queues/service snapshots", "worker scratch",
               "queued control delivery payloads", "Qt raster/cache", "Python object overhead"]

    def add(name: str, *values: object) -> None:
        owners[name] = retained_arrays(*values)

    def result(name: str, future: Any) -> object:
        if future is None:
            return None
        if not future.done():
            in_flight.append(name)
            return None
        if future.cancelled():
            return None
        try:
            return future.result()  # terminal check above, never a wait
        except Exception:
            return None  # error strings are not array storage

    live = composition._presenter
    pending = getattr(live, "_pending_preparation", None)
    add("live.presenter", getattr(live, "_last_polled", None),
        getattr(live, "_active_preparation_snapshot", None),
        None if pending is None else pending[0],
        getattr(getattr(live, "_display_scheduler", None), "_pending", None),
        result("live.prepare", getattr(live, "_preparation_future", None)))
    if getattr(live, "_pending_commands", 0):
        in_flight.append("live.control: publication not yet acknowledged")
    cache = getattr(getattr(live, "_snapshot_preparer", None), "_layers", None)
    add("live.grid-cache", getattr(getattr(getattr(live, "_snapshot_preparer", None), "_grid", None), "baseline", None))
    add("live.preparation-cache", *(getattr(cache, name, None) for name in
        ("_density_source", "_density", "_waterfall_source", "_waterfall")))
    add("live.view-model", composition.view_model.state)
    model_cache = getattr(composition.view_model, "_layer_cache", None)
    add("live.view-model-cache", *(getattr(model_cache, name, None) for name in
        ("_density_source", "_density", "_waterfall_source", "_waterfall")))
    analyzer = composition.analyzer_view_model
    add("analyzer.view-model", None if analyzer is None else analyzer.state)
    presenter = composition.analyzer_presenter
    add("sweep.grid-cache", getattr(getattr(getattr(presenter, "_snapshot_preparer", None), "_grid", None), "baseline", None))
    packets = tuple(result("sweep." + name, getattr(presenter, "_" + name + "_future", None))
                    for name in ("start", "poll", "stop"))
    add("sweep.presenter", *packets,
        *(row for packet in packets for row in
          getattr(getattr(packet, "presentation", None), "waterfall_rows", ())))
    state = None if analyzer is None else analyzer.state
    add("sweep.prepared-rows", *getattr(getattr(state, "prepared_sweep", None), "waterfall_rows", ()))
    projector = composition.spectrum_projector
    reserved = 0
    if projector is not None:
        requests = (projector._active, projector._pending)
        add("viewport.requests", *(view for request in requests if request is not None for _, view in request.traces),
            *(value for request in requests if request is not None for value in
              (request.current, request.previous, request.prepared)))
        projection = result("viewport.worker", projector._future)
        add("viewport.result", *(trace for _, trace in getattr(projection, "traces", ())),
            getattr(projection, "coverage", None))
        # A completed result is already counted as an actual array; retaining
        # the reservation too would double-count it. Pending is still reserved.
        reserved = projector._pending_reserve + (projector._active_reserve if projection is None else 0)
    if workspace is None:
        missing.append("workspace not supplied")
    else:
        scene, pane = workspace.visualization.spectrum_scene, workspace.visualization.waterfall_pane
        add("workspace.latest", *(getattr(workspace, name) for name in
            ("_last_bundle", "_last_waterfall", "_last_persistence", "_last_identity", "_last_sweep_snapshot")))
        add("spectrum.sources", scene._latest_view, scene._displayed_view, scene._prepared_spectrum,
            scene._measurement_grid, *scene._trace_views.values(), *scene._envelopes.values())
        coverage = scene.sweep_coverage
        add("spectrum.coverage", coverage.state.current, coverage.state.previous,
            coverage._display_previous, coverage.projection)
        add("spectrum.plot-arrays", *(array for curve in (*scene._curves.values(), coverage.history)
                                      for layer in (curve, getattr(curve, "curve", None))
                                      for array in (getattr(layer, "xData", None), getattr(layer, "yData", None))))
        density = scene._persistence
        add("persistence", density._latest_view, density._pending_view, density._uploaded_density,
            density._visual_buffer, density._row_scratch, density.image_item.image)
        ring = pane._renderer.buffer
        add("waterfall", None if ring is None else ring._data,
            None if ring is None else ring._timestamps_ns,
            *(item.image for item in pane._image_items))
    per_owner = tuple(ArrayOwnerBytes(name, sum(storage.values())) for name, storage in owners.items())
    unique = union_bytes(*owners.values())
    return PresentationMemorySnapshot(per_owner, unique, sum(item.bytes for item in per_owner) - unique,
                                      reserved, tuple(in_flight), tuple(missing),
                                      composition.allocation_budget.snapshot())

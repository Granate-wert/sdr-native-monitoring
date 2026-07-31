"""Typed references to existing application services; this facade performs no work."""

from __future__ import annotations

from collections.abc import MutableMapping

from PySide6.QtCore import QThreadPool

from ..adapter import DflMeasurementAdapter
from ..heatmap_persistence_controller import HeatmapPersistenceController
from ..repository import MeasurementRepository
from ..time_gated_power import TimeGatedChannelPowerService


class ApplicationServices:
    """Compatibility facade over existing service instances, not a service locator."""

    __slots__ = (
        "repository",
        "dfl_adapter",
        "thread_pool",
        "time_gated_service",
        "heatmap_controller",
        "live_controllers",
        "live_adapters",
    )

    def __init__(
        self,
        *,
        repository: MeasurementRepository,
        dfl_adapter: DflMeasurementAdapter,
        thread_pool: QThreadPool,
        time_gated_service: TimeGatedChannelPowerService,
        heatmap_controller: HeatmapPersistenceController,
        live_controllers: MutableMapping[str, object],
        live_adapters: MutableMapping[str, object],
    ) -> None:
        self.repository = repository
        self.dfl_adapter = dfl_adapter
        self.thread_pool = thread_pool
        self.time_gated_service = time_gated_service
        self.heatmap_controller = heatmap_controller
        self.live_controllers = live_controllers
        self.live_adapters = live_adapters

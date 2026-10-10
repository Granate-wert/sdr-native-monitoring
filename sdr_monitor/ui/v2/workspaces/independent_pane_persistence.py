"""Applied-layout density lanes; no receiver or executor ownership.

Retirement polls only already-done Futures and acknowledges them on Qt. The
application joins its existing optional executor off Qt on terminal shutdown.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread

from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle
from ..spectrum.allocation_budget import PresentationAllocationBudget
from ..spectrum.persistence_projector import PersistenceProjector
from ..spectrum.scene import SpectrumScene

if TYPE_CHECKING:
    from .independent_pane_session import IndependentPaneSessionV2


class IndependentPanePersistenceLanes:
    def __init__(self, handle: PaneProductSessionHandle, *,
                 submit: Callable[[Callable[[], object]], Future],
                 allocation_budget: PresentationAllocationBudget) -> None:
        if not isinstance(handle, PaneProductSessionHandle) or not handle.applied:
            raise TypeError("density lanes require an applied pane handle")
        if not callable(submit):
            raise TypeError("independent async density requires the optional submitter")
        if (not isinstance(allocation_budget, PresentationAllocationBudget)
                or handle.preparer.allocation_budget is not allocation_budget):
            raise ValueError("independent density refuses a foreign presentation ledger")
        if not 1 <= len(handle.preparer.bindings) <= 4:
            raise ValueError("independent density admits one through four occupied panes")
        self.handle = handle
        self.allocation_budget = allocation_budget
        self._submit = submit
        self._thread = QThread.currentThread()
        self._lanes: dict[str, tuple[SpectrumScene, PersistenceProjector]] = {}
        self._retiring = False
        self._released: set[str] = set()
        self._failed_surface: IndependentPaneSessionV2 | None = None

    def _check_thread(self) -> None:
        if QThread.currentThread() != self._thread:
            raise RuntimeError("density lane lifecycle requires its Qt owner thread")

    def attach(self, pane_id: str, scene: SpectrumScene) -> None:
        self._check_thread()
        if (self._retiring or pane_id not in self.handle.preparer.bindings
                or pane_id in self._lanes or not isinstance(scene, SpectrumScene)
                or any(scene is item[0] for item in self._lanes.values())
                or scene._density_port is not None or scene._projector is not None
                or scene._density_admitted or scene._graphics_terminal_released):
            raise ValueError("density lane requires one exact occupied pane scene")
        port = PersistenceProjector(self._submit, allocation_budget=self.allocation_budget)
        self._lanes[pane_id] = (scene, port)
        try:
            scene.set_persistence_projection_port(port)
        except Exception as original_error:
            cleanup_errors: list[Exception] = []
            for operation in (self.quiesce, self.release_after_shutdown):
                try:
                    operation()  # No publication was admitted.
                except Exception as error:
                    cleanup_errors.append(error)
            if cleanup_errors:
                raise original_error from cleanup_errors[0]
            raise

    def quiesce(self) -> None:
        self._check_thread()
        self._retiring = True
        errors: list[Exception] = []
        for scene, port in self._lanes.values():
            try:
                scene.quiesce_persistence_projection()
            except Exception as error:
                errors.append(error)
            try:
                port.dispose()
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def poll_retired(self) -> bool:
        """Nonblocking done + GUI acknowledgement; settled is not a close ack."""
        self._check_thread()
        if not self._retiring:
            raise RuntimeError("density retirement requires quiesce first")
        errors: list[Exception] = []
        for pane_id, (scene, port) in self._lanes.items():
            if pane_id in self._released:
                continue
            future = port._future
            if future is not None and not future.done():
                continue
            try:
                port.release_after_shutdown()
                scene.disconnect_persistence_projection_after_shutdown()
                self._released.add(pane_id)
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]
        return len(self._released) == len(self._lanes)

    def release_after_shutdown(self) -> None:
        if not self.poll_retired():
            raise RuntimeError("optional density Futures have not acknowledged shutdown")

    @property
    def retired(self) -> bool:
        return self._retiring and len(self._released) == len(self._lanes)


__all__ = ["IndependentPanePersistenceLanes"]

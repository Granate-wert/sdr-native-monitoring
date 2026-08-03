"""Thread-safe presenter for discovery and live-session control."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from ...domain import LiveConfiguration, LiveSnapshot
from ...services.interfaces import LiveSdrService


class LivePresenter(QObject):
    """Moves all potentially blocking service methods off the Qt GUI thread."""

    devices_discovered = Signal(object)
    snapshot_changed = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)
    render_ready = Signal(object)

    def __init__(self, service: LiveSdrService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-live")
        self._closed = False
        self._pending_render: LiveSnapshot | None = None
        self._render_generation = -1
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(16)
        self._render_timer.timeout.connect(self._flush_render)

    def discover_devices(self) -> None:
        self._submit(self._service.discover_devices, self.devices_discovered.emit)

    def select_device(self, device_id: str) -> None:
        self._submit(lambda: self._service.select_device(device_id), self._emit_snapshot)

    def select_manual_uri(self, uri: str) -> None:
        self._submit(lambda: self._service.select_manual_uri(uri), self._emit_snapshot)
    def apply_configuration(self, configuration: LiveConfiguration) -> None:
        self._submit(lambda: self._service.apply_configuration(configuration), self._emit_snapshot)

    def start(self) -> None:
        self._submit(self._service.start, self._emit_snapshot)

    def stop(self) -> None:
        self._submit(self._service.stop, self._emit_snapshot)

    def offer_snapshot_for_render(self, snapshot: LiveSnapshot) -> None:
        """Coalesce producer-rate publications into a bounded 60 Hz UI stream."""
        if snapshot.generation < self._render_generation:
            return
        self._pending_render = snapshot
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _flush_render(self) -> None:
        snapshot = self._pending_render
        self._pending_render = None
        self._render_timer.stop()
        if snapshot is None or snapshot.generation < self._render_generation:
            return
        self._render_generation = snapshot.generation
        self.render_ready.emit(snapshot)
    def shutdown(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _submit(self, operation: Callable[[], Any], on_success: Callable[[Any], None]) -> None:
        if self._closed:
            return
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(lambda result: self._complete(result, on_success))

    def _complete(self, future: Future[Any], on_success: Callable[[Any], None]) -> None:
        try:
            value = future.result()
        except Exception as error:  # pragma: no cover - adapter-dependent branch
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            self.busy_changed.emit(False)

    def _emit_snapshot(self, snapshot: LiveSnapshot) -> None:
        self.snapshot_changed.emit(snapshot)

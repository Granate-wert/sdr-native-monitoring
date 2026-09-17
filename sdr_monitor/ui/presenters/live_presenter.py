"""Thread-safe presenter for discovery and live-session control."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from ...application import LiveSessionUseCases
from ...domain import LiveConfiguration, LiveSnapshot
from ...domain.live_configuration_patch import LiveConfigurationPatch
from ...domain.analyzer_resources import AnalyzerGeometryPreflight
from ...domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..display_scheduler import DisplayScheduler, DisplaySchedulerMetrics


def _poll_interval_ms(display_fps: int) -> int:
    """Bound GUI polling to twice the requested presentation cadence.

    The service exposes only one immutable latest snapshot, so polling faster
    than this cannot improve queue depth or preserve an intermediate frame. A
    two-times cadence leaves one early observation opportunity per render
    period while avoiding a permanent 2-ms (500 wakeups/s) GUI timer at every
    selected display rate.
    """

    return max(1, round(1000.0 / (2.0 * display_fps)))


class LivePresenter(QObject):
    """Moves all potentially blocking service methods off the Qt GUI thread."""

    devices_discovered = Signal(object)
    snapshot_changed = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)
    render_ready = Signal(object)
    analyzer_ready = Signal(object)

    def __init__(self, use_cases: LiveSessionUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._use_cases = use_cases
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-live")
        self._closed = False
        self._closing = False
        self._shutdown_use_cases_complete = False
        self._shutdown_executor_complete = False
        self._shutdown_presentation_complete = False
        self._publication_lock = threading.Lock()
        self._control_revision = 0
        self._offered_control_revision = 0
        # GUI-rate coalescing intentionally has no service reference.  This
        # keeps acquisition/control ownership in the presenter and makes the
        # display clock independently testable and bounded to one snapshot.
        self._display_scheduler = DisplayScheduler(parent=self)
        self._display_scheduler.frame_ready.connect(self._emit_render)
        self._poll_started = False
        self._last_polled: LiveSnapshot | None = None
        self._poll_timer = QTimer(self)
        # Polling is intentionally faster than rendering.  The service owns a
        # latest immutable snapshot, so this timer never builds a backlog; it
        # merely notices native publications while the display clock
        # coalesces them to the requested GUI FPS.
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.setInterval(_poll_interval_ms(self._display_scheduler.fps))
        self._poll_timer.timeout.connect(self._poll_frames)
        self._start_polling()

    def discover_devices(self, *, startup: bool = False) -> None:
        self._submit(lambda: self._use_cases.discover(startup=startup), self.devices_discovered.emit)

    def select_device(self, device_id: str) -> None:
        self._submit(lambda: self._use_cases.select_device(device_id), self._emit_snapshot)

    def select_manual_uri(self, uri: str) -> None:
        self._submit(lambda: self._use_cases.select_manual_uri(uri), self._emit_snapshot)

    def preview_sweep(self, configuration: LiveConfiguration,
                      request: ContinuousSweepPlanRequest) -> AnalyzerGeometryPreflight:
        """Bounded scalar-only calculation; no control worker, lease or I/O.

        The application repeats admission against applied readback at Start.
        A preview neither reserves resources nor authorizes a receiver.
        """
        if self._closed or self._closing:
            raise RuntimeError("Live presenter is closing")
        self._use_cases.preflight_configuration(configuration)
        return self._use_cases.preflight_sweep(configuration, request)

    def refresh_snapshot(self) -> None:
        """Replay the current immutable service state to a newly created UI."""
        if self._closed or self._closing:
            return
        self._emit_snapshot(self._use_cases.current_snapshot())

    def apply_configuration(self, configuration: LiveConfiguration | LiveConfigurationPatch) -> None:
        self._submit(lambda: self._use_cases.apply_configuration(configuration), self._emit_snapshot)

    def reconfigure(self, configuration: LiveConfiguration, *, restart: bool = True) -> None:
        """Delegate the session transaction to the Qt-free application layer."""
        self._submit(
            lambda: self._use_cases.reconfigure(configuration, restart=restart),
            self._emit_snapshot,
        )

    def start_with_configuration(self, configuration: LiveConfiguration) -> None:
        """Apply requested settings and start in one worker transaction."""

        self._submit(
            lambda: self._use_cases.start_with_configuration(configuration),
            self._emit_snapshot,
        )

    def set_display_fps(self, fps: int) -> None:
        value = int(fps)
        if value not in (15, 30, 60, 120, 144, 240):
            raise ValueError("display FPS must be 15, 30, 60, 120, 144 or 240")
        self._display_scheduler.set_fps(value)
        self._poll_timer.setInterval(_poll_interval_ms(value))

    @property
    def display_fps(self) -> int:
        return self._display_scheduler.fps

    @property
    def superseded_renders(self) -> int:
        return self._display_scheduler.superseded

    @property
    def display_metrics(self) -> DisplaySchedulerMetrics:
        """Scalar presentation-boundary accounting for R10-D6 evidence."""

        return self._display_scheduler.metrics

    def start(self) -> None:
        self._submit(self._use_cases.start, self._emit_snapshot)

    def stop(self) -> None:
        self._submit(self._use_cases.stop, self._emit_snapshot)

    def offer_snapshot_for_render(self, snapshot: LiveSnapshot) -> None:
        """Coalesce producer-rate publications into a bounded configured-FPS stream."""
        with self._publication_lock:
            self._offered_control_revision = self._control_revision
        self._display_scheduler.offer(snapshot)

    def reset_display_metrics(self) -> None:
        """Begin a controlled UI measurement without changing render state."""

        self._display_scheduler.reset_metrics()

    def _start_polling(self) -> None:
        if self._poll_started:
            return
        self._poll_started = True
        self._poll_timer.start()

    def _poll_frames(self) -> None:
        if not self._use_cases.is_running():
            return
        frames = self._use_cases.poll_published_snapshots()
        if not frames:
            return
        snapshot = frames[-1]
        if snapshot is self._last_polled:
            return
        self._last_polled = snapshot
        self.offer_snapshot_for_render(snapshot)

    def shutdown(self, timeout_s: float = 5.0) -> None:
        if self._closed:
            return
        self._closing = True
        if not self._shutdown_presentation_complete:
            self._display_scheduler.shutdown()
            self._poll_timer.stop()
            self._poll_started = False
            self._shutdown_presentation_complete = True
        # Stop the native stream before waiting for queued operations.  The
        # service owns cancellation/join semantics, so no worker survives a
        # closed Qt shell merely because it was blocked in a device call.
        try:
            if not self._shutdown_use_cases_complete:
                self._use_cases.shutdown(timeout_s)
                self._shutdown_use_cases_complete = True
        finally:
            # Even a service cleanup error must not leave queued presenter
            # commands alive. The service cleanup itself remains retryable.
            if not self._shutdown_executor_complete:
                self._executor.shutdown(wait=True, cancel_futures=True)
                self._shutdown_executor_complete = True
        self._closed = True

    def _submit(self, operation: Callable[[], Any], on_success: Callable[[Any], None]) -> None:
        if self._closed or self._closing:
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
        with self._publication_lock:
            self._control_revision += 1
        self.snapshot_changed.emit(snapshot)
        self._emit_analyzer(snapshot)

    def _emit_render(self, snapshot: LiveSnapshot) -> None:
        with self._publication_lock:
            if self._offered_control_revision != self._control_revision:
                return  # A queued old RUNNING frame cannot undo Stop/Apply.
        self.render_ready.emit(snapshot)
        self._emit_analyzer(snapshot)

    def _emit_analyzer(self, snapshot: LiveSnapshot) -> None:
        # Validate only the coalesced delivered snapshot, not discarded frames.
        # APP-02 can consume the same signal/contract in either strategy.
        try:
            bundle = self._use_cases.analyzer_bundle_for_snapshot(snapshot)
        except (TypeError, ValueError) as error:
            self.analyzer_ready.emit(None)
            self.task_failed.emit(f"Analyzer publication rejected: {error}")
            return
        self.analyzer_ready.emit(bundle)

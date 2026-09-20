"""Thread-safe presenter for discovery and live-session control."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot

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


@dataclass(frozen=True, slots=True)
class _PreparedDelivery:
    snapshot: LiveSnapshot
    revision: int
    render: bool
    value: object | None = None
    error: str | None = None


class LivePresenter(QObject):
    """Moves all potentially blocking service methods off the Qt GUI thread."""

    devices_discovered = Signal(object)
    snapshot_changed = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)
    render_ready = Signal(object)
    analyzer_ready = Signal(object)
    prepared_snapshot_ready = Signal(object)
    _prepared_control_ready = Signal(object)
    _prepared_render_done = Signal(object)
    _prepared_command_done = Signal(object)

    def __init__(self, use_cases: LiveSessionUseCases, parent: QObject | None = None, *,
                 snapshot_preparer: Callable[[LiveSnapshot], object] | None = None,
                 snapshot_admitter: Callable[[LiveSnapshot], LiveSnapshot] | None = None) -> None:
        super().__init__(parent)
        # Capture affinity without QObject.thread(): PySide 6.11.1 assigns the
        # borrowed QThread wrapper a binding-level parent of that receiver.
        # A later presenter deletion can invalidate the shared GUI wrapper.
        # This presenter and its timers keep their construction-thread affinity.
        self._presentation_thread = QThread.currentThread()
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
        self._snapshot_preparer = snapshot_preparer
        self._snapshot_admitter = snapshot_admitter
        self._preparation_future: Future[_PreparedDelivery] | None = None
        self._active_preparation_snapshot: LiveSnapshot | None = None
        self._pending_preparation: tuple[LiveSnapshot, int, bool] | None = None
        self._projection_in_flight = False
        self._preparation_superseded = 0
        self._preparation_stale = 0
        self._pending_commands = 0
        self._prepared_control_ready.connect(self._deliver_prepared, Qt.ConnectionType.QueuedConnection)
        self._prepared_render_done.connect(self._finish_preparation, Qt.ConnectionType.QueuedConnection)
        self._prepared_command_done.connect(self._finish_prepared_command, Qt.ConnectionType.QueuedConnection)
        # GUI-rate coalescing intentionally has no service reference.  This
        # keeps acquisition/control ownership in the presenter and makes the
        # display clock independently testable and bounded to one snapshot.
        self._display_scheduler = DisplayScheduler(parent=self)
        self._display_scheduler.frame_ready.connect(self._emit_render)
        self._poll_started = False
        self._last_polled: LiveSnapshot | None = None
        self._last_poll_key: tuple[int, object, object] | None = None
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
    def prepares_snapshots(self) -> bool:
        return self._snapshot_preparer is not None

    @property
    def preparation_superseded(self) -> int:
        """Replaced waiting display requests, never analytical FFT loss."""
        return self._preparation_superseded

    @property
    def preparation_stale(self) -> int:
        """Prepared display results rejected after a control revision changed."""
        return self._preparation_stale

    @property
    def superseded_renders(self) -> int:
        return self._display_scheduler.superseded + self._preparation_superseded

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
        snapshot = self._admit_snapshot(snapshot)
        with self._publication_lock:
            self._offered_control_revision = self._control_revision
        self._display_scheduler.offer(snapshot)

    def reset_display_metrics(self) -> None:
        """Begin a controlled UI measurement without changing render state."""

        self._display_scheduler.reset_metrics()
        self._preparation_superseded = 0
        self._preparation_stale = 0

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
        key = (id(snapshot), snapshot.generation, snapshot.sequence)
        if key == self._last_poll_key:
            return
        self._last_poll_key = key
        self._last_polled = self._admit_snapshot(snapshot)
        self.offer_snapshot_for_render(self._last_polled)

    def _admit_snapshot(self, snapshot: LiveSnapshot) -> LiveSnapshot:
        return snapshot if self._snapshot_admitter is None else self._snapshot_admitter(snapshot)

    def submit_display_task(self, operation: Callable[[], Any]) -> Future:
        """Reuse this owner's worker for one externally bounded viewport job.

        The composition must dispose its projection port before shutdown. This
        does not discover/configure/start a device or create another executor.
        """
        if self._closing or self._closed:
            raise RuntimeError("Live presentation worker is closing")
        return self._executor.submit(operation)

    def set_projection_in_flight(self, active: bool) -> None:
        """GUI-only backpressure from this owner's shared viewport worker.

        Keep the newest unprepared render until the preceding projection is
        acknowledged. Commands and explicit state refreshes bypass this gate.
        Each acknowledgement releases one preparation even during viewport churn.
        """
        self._projection_in_flight = bool(active)
        if not active and not self._closed and not self._closing:
            self._dispatch_preparation()

    def prepare_shutdown(self) -> None:
        """GUI quiesce only; finish_shutdown owns potentially blocking cleanup."""
        if self._closed:
            return
        self._closing = True
        self._pending_preparation = None
        if not self._shutdown_presentation_complete:
            self._display_scheduler.shutdown()
            self._poll_timer.stop()
            self._poll_started = False
            self._shutdown_presentation_complete = True

    def shutdown(self, timeout_s: float = 5.0) -> None:
        self.prepare_shutdown()
        self.finish_shutdown(timeout_s)

    def finish_shutdown(self, timeout_s: float = 5.0) -> None:
        """Run on the lifecycle worker after GUI quiesce, never on its executor."""
        if self._closed:
            return
        if not self._shutdown_presentation_complete:
            raise RuntimeError("Live must be quiesced on GUI before cleanup")
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
        if self.prepares_snapshots:
            # Invalidate at command acceptance, not only after a slow Stop/Apply
            # finishes. At most the one already-running preparation precedes it.
            with self._publication_lock:
                self._control_revision += 1
                revision = self._control_revision
            if self._pending_preparation is not None:
                self._preparation_stale += 1
                self._pending_preparation = None
            self._pending_commands += 1
            self.busy_changed.emit(True)
            # Preparation is part of the command future. Even an immediately
            # completed operation cannot release Busy before its prepared state
            # is acknowledged on GUI (Future callbacks may run inline).
            def run():
                value = operation()
                return (self._prepare(value, revision, render=False)
                        if on_success == self._emit_snapshot else value)

            future = self._executor.submit(run)
            future.add_done_callback(lambda result: self._prepared_command_done.emit(
                (result, on_success, revision)))
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
        snapshot = self._admit_snapshot(snapshot)
        with self._publication_lock:
            self._control_revision += 1
            revision = self._control_revision
        if self.prepares_snapshots:
            if QThread.currentThread() == self._presentation_thread:
                # Explicit refresh/test seam may run on GUI; never prepare there.
                # Reuse the bounded presentation slot instead of queuing work.
                self._offer_preparation(snapshot, revision, render=False)
            else:
                self._prepared_control_ready.emit(self._prepare(snapshot, revision, render=False))
            return
        self.snapshot_changed.emit(snapshot)
        self._emit_analyzer(snapshot)

    def _emit_render(self, snapshot: LiveSnapshot) -> None:
        with self._publication_lock:
            if self._offered_control_revision != self._control_revision:
                return  # A queued old RUNNING frame cannot undo Stop/Apply.
            revision = self._control_revision
        if self.prepares_snapshots:
            self._offer_preparation(snapshot, revision)
            return
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

    def _offer_preparation(self, snapshot: LiveSnapshot, revision: int, *, render: bool = True) -> None:
        if self._closing or self._closed:
            return
        snapshot = self._admit_snapshot(snapshot)
        if self._pending_preparation is not None:
            self._preparation_superseded += 1
        self._pending_preparation = (snapshot, revision, render)
        self._dispatch_preparation()

    def _dispatch_preparation(self) -> None:
        if self._pending_commands or self._preparation_future is not None or self._pending_preparation is None:
            return
        snapshot, revision, render = self._pending_preparation
        if render and self._projection_in_flight:
            return
        self._pending_preparation = None
        with self._publication_lock:
            current = revision == self._control_revision
        if not current:
            self._preparation_stale += 1
            return
        if render and self._offered_control_revision == revision:
            # The existing cadence ticket may have waited for projection/GUI
            # acknowledgement while a fresher publication entered the scheduler.
            # Consume it for THIS task, not a second task at the next timer tick.
            replacement = self._display_scheduler.take_pending_replacement(snapshot)
            if replacement is not None and replacement is not snapshot:
                self._preparation_superseded += 1
                snapshot = replacement
        future = self._executor.submit(self._prepare, snapshot, revision, render)
        self._preparation_future = future
        self._active_preparation_snapshot = snapshot
        # Keep the slot occupied until GUI acknowledgement, even after work
        # finishes. A stalled GUI cannot accumulate prepared packets/signals.
        future.add_done_callback(self._prepared_render_done.emit)

    def _prepare(self, snapshot: LiveSnapshot, revision: int, render: bool) -> _PreparedDelivery:
        assert self._snapshot_preparer is not None
        snapshot = self._admit_snapshot(snapshot)
        with self._publication_lock:
            if revision != self._control_revision:
                return _PreparedDelivery(snapshot, revision, render)
        def obsolete() -> bool:
            with self._publication_lock:
                return self._closing or self._closed or revision != self._control_revision
        try:
            preparer = self._snapshot_preparer
            # Explicit opt-in on the callable's type, not dynamic instance
            # attributes (injected adapters/mocks remain one-argument callables).
            cancellable = getattr(type(preparer), "prepare_cancellable", None)
            value = (cancellable(preparer, snapshot, cancelled=obsolete)
                     if render and callable(cancellable) else preparer(snapshot))
            projected = getattr(value, "snapshot", None)
            if isinstance(projected, LiveSnapshot):
                snapshot = projected
            return _PreparedDelivery(snapshot, revision, render, value)
        except Exception as error:
            return _PreparedDelivery(snapshot, revision, render, error=str(error))

    @Slot(object)
    def _finish_prepared_command(self, completion: tuple[Future[Any], Callable[[Any], None], int]) -> None:
        future, on_success, revision = completion
        try:
            if self._closed or self._closing or future.cancelled():
                return
            with self._publication_lock:
                current = revision == self._control_revision
                if current:
                    # Polls made while the command was busy observed its OLD
                    # service state. Seal that interval before terminal delivery,
                    # including failure, so none can overwrite this acknowledgement.
                    self._control_revision += 1
                acknowledged_revision = self._control_revision
            if not current:
                self._preparation_stale += 1
                return
            value = future.result()  # completion notification, never a GUI wait
            if isinstance(value, _PreparedDelivery):
                self._deliver_prepared(replace(value, revision=acknowledged_revision))
            else:
                on_success(value)
        except Exception as error:
            self.task_failed.emit(str(error))
        finally:
            self._pending_commands -= 1
            if not self._closing and not self._closed:
                self.busy_changed.emit(self._pending_commands > 0)
                self._dispatch_preparation()

    @Slot(object)
    def _finish_preparation(self, future: Future[_PreparedDelivery]) -> None:
        if future is not self._preparation_future:
            return
        try:
            if not future.cancelled():
                self._deliver_prepared(future.result())  # already complete; never GUI wait
        finally:
            self._preparation_future = None
            self._active_preparation_snapshot = None
        if not self._closed and not self._closing:
            self._dispatch_preparation()

    @Slot(object)
    def _deliver_prepared(self, delivery: _PreparedDelivery) -> None:
        if self._closed or self._closing:
            return
        with self._publication_lock:
            current = delivery.revision == self._control_revision
        if not current:
            self._preparation_stale += 1
            return
        if delivery.error is not None:
            self.task_failed.emit("Live display preparation failed: " + delivery.error)
            return
        self.prepared_snapshot_ready.emit(delivery.value)
        # Compatibility observers receive the same snapshot; V2 subscribes only
        # to prepared_snapshot_ready, so no deep GUI conversion is repeated.
        signal = self.render_ready if delivery.render else self.snapshot_changed
        signal.emit(delivery.snapshot)
        self.analyzer_ready.emit(getattr(delivery.value, "analyzer_bundle", None))

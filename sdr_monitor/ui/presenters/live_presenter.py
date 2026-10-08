"""Thread-safe presenter for discovery and live-session control."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from ...application import LiveSessionUseCases
from ...domain import LiveConfiguration, LiveSnapshot
from ...domain.analyzer_resources import AnalyzerGeometryPreflight
from ...domain.analyzer_sources import AnalyzerSourceSelection
from ...domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ...domain.hackrf_live import HackrfConfigurationPatch
from ...domain.live_configuration_patch import LiveConfigurationPatch
from ...application.analyzer_rf_change import AnalyzerRfApplyReceipt, AnalyzerRfShiftProposal
from ..v2.state.analyzer_rf_control import RfControlCompletion
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


@dataclass(frozen=True, slots=True)
class _RfDelivery:
    completion: RfControlCompletion
    snapshot: LiveSnapshot | None = None
    prepared: _PreparedDelivery | None = None


class LivePresenter(QObject):
    """Moves all potentially blocking service methods off the Qt GUI thread."""

    devices_discovered = Signal(object)
    source_selection_changed = Signal(object)
    snapshot_changed = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)
    render_ready = Signal(object)
    analyzer_ready = Signal(object)
    prepared_snapshot_ready = Signal(object)
    preparation_active_changed = Signal(bool)
    rf_command_ready = Signal(object)
    control_accepted = Signal()
    calibration_worker_available = Signal()
    calibration_slot_released = Signal(object)
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
        # Optional density mapping must not occupy the required spectrum /
        # snapshot / command lane. Its owner still submits at most one active
        # plus one replaceable latest task through SpectrumProjector.
        self._persistence_executor: ThreadPoolExecutor | None = None
        self._closed = False
        self._closing = False
        self._shutdown_use_cases_complete = False
        self._shutdown_executor_complete = False
        self._shutdown_persistence_executor_complete = False
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
        self._calibration_pending = False
        self._calibration_in_flight = False
        self._calibration_turn = True
        self._calibration_serial = 0
        self._worker_handoff_active = False
        self._preparation_superseded = 0
        self._preparation_stale = 0
        self._pending_commands = 0
        self._prepared_control_ready.connect(self._deliver_prepared, Qt.ConnectionType.QueuedConnection)
        self._prepared_render_done.connect(self._finish_preparation, Qt.ConnectionType.QueuedConnection)
        self._prepared_command_done.connect(self._finish_prepared_command, Qt.ConnectionType.QueuedConnection)
        self.calibration_slot_released.connect(self.release_calibration_slot, Qt.ConnectionType.QueuedConnection)
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

    def discover_devices(self, *, startup: bool = False, local_only: bool = False) -> None:
        if startup and local_only:
            raise ValueError("startup and local-only discovery cannot be combined")
        if local_only:
            self._submit(lambda: self._use_cases.discover(local_only=True), self.devices_discovered.emit)
        else:
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

    def rtl_candidate_stage_available(self, source_id: str, selection_revision: int) -> bool:
        """Read-only cached-candidate check, never a selected-graph Start permit."""
        if self._closed or self._closing:
            return False
        operation = getattr(self._use_cases, "rtl_candidate_stage_available", None)
        if not callable(operation):
            return False
        try:
            return operation(source_id, selection_revision) is True
        except Exception:
            return False

    def apply_configuration(self, configuration: LiveConfiguration | LiveConfigurationPatch) -> None:
        self._submit(lambda: self._use_cases.apply_configuration(configuration), self._emit_snapshot)

    def stage_hackrf_configuration(self, patch: HackrfConfigurationPatch) -> None:
        operation = getattr(self._use_cases, "stage_hackrf_configuration", None)
        if not callable(operation):
            raise RuntimeError("Common HackRF RTBW is unavailable")  # noqa: TRY004 - absent optional port, not an invalid patch type.
        self._submit(lambda: operation(patch), self._emit_snapshot)

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
    def source_selection(self) -> AnalyzerSourceSelection | None:
        """Cached control state for a new subscriber; never discover or probe."""
        getter = getattr(self._use_cases, "current_source_selection", None)
        value = getter() if callable(getter) else None
        return value if isinstance(value, AnalyzerSourceSelection) else None

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

    @property
    def rf_controls_available(self) -> bool:
        """No SDK inspection; only this composed use-case surface is checked."""
        return all(callable(getattr(self._use_cases, name, None)) for name in (
            "preview_rf_shift", "stop_rf_rtbw", "apply_rf_shift", "acknowledge_rf_apply",
            "start_rf_rtbw", "capture_rf_context"))

    def preview_rf_shift(self, offset_hz: float, token: object, *, cancelled: Callable[[], bool]) -> None:
        self._rf_command(token, "preview", "preview_rf_shift", offset_hz, cancelled=cancelled)

    def stop_rf_rtbw(self, proposal: AnalyzerRfShiftProposal, token: object, *, cancelled: Callable[[], bool]) -> None:
        self._rf_command(token, "stop", "stop_rf_rtbw", proposal, cancelled=cancelled, pass_cancel=True)

    def apply_rf_shift(self, proposal: AnalyzerRfShiftProposal, token: object, *, cancelled: Callable[[], bool]) -> None:
        self._rf_command(token, "apply", "apply_rf_shift", proposal, cancelled=cancelled, pass_cancel=True)

    def acknowledge_rf_apply(self, receipt: AnalyzerRfApplyReceipt, token: object, *, cancelled: Callable[[], bool]) -> None:
        self._rf_command(token, "ack", "acknowledge_rf_apply", receipt, cancelled=cancelled)

    def start_rf_rtbw(self, receipt: AnalyzerRfApplyReceipt, token: object, *, cancelled: Callable[[], bool]) -> None:
        self._rf_command(token, "start", "start_rf_rtbw", receipt, cancelled=cancelled, pass_cancel=True)

    def observe_rf_context(self, token: object, *, cancelled: Callable[[], bool]) -> None:
        self._rf_command(token, "observe", "capture_rf_context", cancelled=cancelled)

    def stop_rf_cleanup(self, token: object) -> None:
        self._rf_command(token, "cleanup", "stop", cancelled=lambda: False)

    def _rf_command(self, token: object, phase: str, name: str, *arguments: object,
                    cancelled: Callable[[], bool], pass_cancel: bool = False) -> None:
        """SAME serial control lane, with an always-queued Qt acknowledgement.

The unprepared compatibility path must not call a widget/coordinator from a
Future callback. There is no new executor or unbounded display-task loophole.
"""
        if self._closed or self._closing or not self.rf_controls_available:
            raise RuntimeError("Shared RF control is unavailable or closing")

        def run() -> _RfDelivery:
            try:
                if cancelled():
                    raise RuntimeError("RF command was cancelled")
                operation = getattr(self._use_cases, name)
                value = operation(*arguments, cancelled=cancelled) if pass_cancel else operation(*arguments)
                if phase == "cleanup" and isinstance(value, LiveSnapshot) and (value.error is not None or value.stop_required):
                    return _RfDelivery(RfControlCompletion(token, phase, error=value.error or "RF cleanup was not confirmed"), value)
                snapshot = value.snapshot if isinstance(value, AnalyzerRfApplyReceipt) else value if isinstance(value, LiveSnapshot) else None
                return _RfDelivery(RfControlCompletion(token, phase, value), snapshot)
            except Exception as error:
                return _RfDelivery(RfControlCompletion(token, phase, error=str(error)))

        self._submit(run, self._deliver_rf_command, force_gui=True)

    def _deliver_rf_command(self, delivery: _RfDelivery) -> None:
        completion = delivery.completion
        if delivery.prepared is not None:
            if delivery.prepared.error is not None:
                completion = replace(completion, error="RF snapshot preparation failed")
            else:
                self._deliver_prepared(delivery.prepared)
        elif delivery.snapshot is not None and completion.error is None:
            self.snapshot_changed.emit(delivery.snapshot)
            self._emit_analyzer(delivery.snapshot)
        self.rf_command_ready.emit(completion)

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

    @property
    def calibration_worker_ready(self) -> bool:
        """Optional analytics cannot queue ahead of accepted controls/render work."""
        return (not self._closed and not self._closing and not self._pending_commands
                and self._preparation_future is None and not self._projection_in_flight
                and not self._calibration_in_flight
                and (self._calibration_turn or self._pending_preparation is None))

    @property
    def calibration_slot_serial(self) -> int:
        return self._calibration_serial

    def set_calibration_pending(self, pending: bool) -> None:
        """Scalar notification only; the analytics presenter owns its ONE request."""
        self._calibration_pending = bool(pending)
        if pending:
            self._dispatch_preparation()

    def submit_calibration_task(self, operation: Callable[[], Any]) -> Future:
        if not self.calibration_worker_ready:
            raise RuntimeError("Live worker has higher-priority work")
        self._calibration_in_flight = True
        self._calibration_pending = False
        self._calibration_turn = False  # One raw turn is owed when raw is pending.
        self._calibration_serial += 1
        try:
            return self._executor.submit(operation)
        except Exception:
            self._calibration_in_flight = False
            raise

    @Slot(object)
    def release_calibration_slot(self, serial: int) -> None:
        """GUI acknowledgement, or queued terminal disposal, of this exact slot."""
        if serial != self._calibration_serial or not self._calibration_in_flight:
            return
        self._calibration_in_flight = False
        self._dispatch_preparation()

    def submit_persistence_task(self, operation: Callable[[], Any]) -> Future:
        """Submit optional density preparation outside control/spectrum work."""
        if self._closing or self._closed:
            raise RuntimeError("Live persistence worker is closing")
        if self._persistence_executor is None:
            self._persistence_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-persistence")
        return self._persistence_executor.submit(operation)

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
        self.control_accepted.emit()
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
            try:
                if not self._shutdown_executor_complete:
                    self._executor.shutdown(wait=True, cancel_futures=True)
                    self._shutdown_executor_complete = True
            finally:
                if not self._shutdown_persistence_executor_complete:
                    executor = self._persistence_executor
                    if executor is not None:
                        executor.shutdown(wait=True, cancel_futures=True)
                    self._shutdown_persistence_executor_complete = True
        self._closed = True

    def release_presentation_after_shutdown(self) -> None:
        """V2 GUI finalization, after successful service/executor acknowledgement.

        Never called by Stop or prepare_shutdown. Queued preparation callbacks
        see a detached Future and cannot republish data into a closed product.
        This releases UI holders, not snapshots owned by an external service.
        """
        if (not self._closed or not self._shutdown_executor_complete
                or not self._shutdown_persistence_executor_complete):
            raise RuntimeError("Live presentation release requires completed shutdown")
        if self._preparation_future is not None and not self._preparation_future.done():
            raise RuntimeError("Live preparation still active after shutdown")
        self._last_polled = None
        self._pending_preparation = None
        self._active_preparation_snapshot = None
        self._preparation_future = None
        clear = getattr(self._snapshot_preparer, "clear", None)
        if callable(clear):
            clear()

    def _submit(self, operation: Callable[[], Any], on_success: Callable[[Any], None], *, force_gui: bool = False) -> None:
        if self._closed or self._closing:
            return
        self.control_accepted.emit()
        if self.prepares_snapshots or force_gui:
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
                if isinstance(value, _RfDelivery) and self.prepares_snapshots and value.snapshot is not None:
                    try:
                        return replace(value, prepared=self._prepare(value.snapshot, revision, render=False))
                    except Exception:
                        # Admission/preparation can fail before its own error
                        # packet exists. The RF token still MUST finish on Qt.
                        return _RfDelivery(replace(value.completion, value=None,
                            error="RF snapshot preparation failed"))
                return (self._prepare(value, revision, render=False)
                        if on_success == self._emit_snapshot else value)

            try:
                future = self._executor.submit(run)
            except Exception:
                self._pending_commands -= 1
                self.busy_changed.emit(self._pending_commands > 0)
                raise
            future.add_done_callback(lambda result: self._prepared_command_done.emit(
                (result, on_success, revision)))
            return
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(lambda result: self._complete(result, on_success))

    def _complete(self, future: Future[Any], on_success: Callable[[Any], None]) -> None:
        try:
            self._emit_source_selection()
            value = future.result()
        except Exception as error:  # pragma: no cover - adapter-dependent branch
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            self.busy_changed.emit(False)

    def _emit_snapshot(self, snapshot: LiveSnapshot) -> None:
        if not self.prepares_snapshots:
            self._emit_source_selection()
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

    def _emit_source_selection(self) -> None:
        """Control completion only; never read catalog/format per FFT/render."""
        self.source_selection_changed.emit(self.source_selection)

    def _offer_preparation(self, snapshot: LiveSnapshot, revision: int, *, render: bool = True) -> None:
        if self._closing or self._closed:
            return
        snapshot = self._admit_snapshot(snapshot)
        if self._pending_preparation is not None:
            self._preparation_superseded += 1
        self._pending_preparation = (snapshot, revision, render)
        self._dispatch_preparation()

    def _dispatch_preparation(self) -> None:
        """Bounded alternating handoff at raw/projection/calibration releases.

        Controls always precede either display lane. With both lanes pending,
        at most one raw preparation bypasses an explicit calibration command OR
        optional CURRENT, and at most one calibration job bypasses pending raw.
        Calibration remains occupied through GUI acknowledgement, not Future
        completion. Existing projection backpressure remains authoritative.
        """
        if (self._closing or self._closed or self._pending_commands or self._preparation_future is not None
                or self._calibration_in_flight or self._worker_handoff_active):
            return
        self._worker_handoff_active = True
        try:
            if self._calibration_pending and self.calibration_worker_ready:
                self.calibration_worker_available.emit()
                if self._calibration_in_flight:
                    return
            self._dispatch_raw_preparation()
        finally:
            self._worker_handoff_active = False

    def _dispatch_raw_preparation(self) -> None:
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
        self._calibration_turn = True
        self._active_preparation_snapshot = snapshot
        self.preparation_active_changed.emit(True)
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
                if on_success == self._deliver_rf_command:
                    value = future.result()
                    if isinstance(value, _RfDelivery):
                        # Supersession invalidates the publication, not the
                        # requirement to finish this RF command's GUI phase.
                        self.rf_command_ready.emit(replace(value.completion, value=None,
                            error="RF command acknowledgement was superseded"))
                return
            self._emit_source_selection()
            value = future.result()  # completion notification, never a GUI wait
            if isinstance(value, _PreparedDelivery):
                self._deliver_prepared(replace(value, revision=acknowledged_revision))
            else:
                if isinstance(value, _RfDelivery) and value.prepared is not None:
                    value = replace(value, prepared=replace(value.prepared, revision=acknowledged_revision))
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
            self.preparation_active_changed.emit(False)
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
        if not delivery.render:
            self._emit_source_selection()
        self.prepared_snapshot_ready.emit(delivery.value)
        # Compatibility observers receive the same snapshot; V2 subscribes only
        # to prepared_snapshot_ready, so no deep GUI conversion is repeated.
        signal = self.render_ready if delivery.render else self.snapshot_changed
        signal.emit(delivery.snapshot)
        self.analyzer_ready.emit(getattr(delivery.value, "analyzer_bundle", None))

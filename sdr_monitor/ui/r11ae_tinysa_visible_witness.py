"""Human-visible tinySA witness observer for the one-shot R11-AE UI cell.

It observes the existing normal product flow.  It does not synthesize input,
discover devices, create a serial backend, or start a trace itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from ..application.tinysa_analyzer import TinySaTraceApplicationResult
from ..r11ae_tinysa_visible_ui_evidence import (
    R11AEVisibleUiEvidence,
    R11AEVisibleUiProfile,
)
from ..services.tinysa_capability_adapter import TinySaModel
from ..services.tinysa_serial_trace_collector import TinySaScanRawRequest
from .presenters.tinysa_analyzer_presenter import TinySaAnalyzerPresenterMetrics
from .presenters.tinysa_source_activation_presenter import (
    TinySaSourceActivationWitness,
)
from .tinysa_trace_canvas import TinySaTraceCanvasMetrics
from .workspaces.tinysa_analyzer import TinySaAnalyzerWorkspace


@dataclass(frozen=True, slots=True)
class _TraceCapture:
    result: TinySaTraceApplicationResult
    presenter: TinySaAnalyzerPresenterMetrics
    canvas: TinySaTraceCanvasMetrics
    window_visible: bool
    window_active: bool
    device_pixel_ratio: float
    logical_dpi_x: float
    logical_dpi_y: float
    logical_width: int
    logical_height: int


class R11AEVisibleUiWitness(QObject):
    """Observe one operator-driven AppShell flow and retain scalar evidence."""

    def __init__(
        self,
        profile: R11AEVisibleUiProfile,
        *,
        draft_preflight_sha256: str,
        ready_preflight_sha256: str,
        display_preflight_sha256: str,
        source_id: str,
        identity_assurance: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._draft_preflight_sha256 = draft_preflight_sha256
        self._ready_preflight_sha256 = ready_preflight_sha256
        self._display_preflight_sha256 = display_preflight_sha256
        self._source_id = source_id
        self._identity_assurance = identity_assurance
        self._app: QApplication | None = None
        self._shell: QWidget | None = None
        self._workspace: TinySaAnalyzerWorkspace | None = None
        self._workspace_source_id: str | None = None
        self._workspace_identity_assurance: str | None = None
        self._activation: TinySaSourceActivationWitness | None = None
        self._capture: _TraceCapture | None = None
        self._pending_trace_result: TinySaTraceApplicationResult | None = None
        self._trace_paint_baseline = 0
        self._capture_scheduled = False
        self._failure: str | None = None
        self._shell_close_observed = False
        self._mouse_button_presses = 0
        self._keyboard_key_presses = 0
        self._maximum_resize_delta_logical_px = 0
        self._heartbeat_samples_while_busy = 0
        self._maximum_heartbeat_stall_ms = 0.0
        self._busy = False
        self._busy_size: tuple[int, int] | None = None
        self._last_heartbeat_ns = 0
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._heartbeat_timer.setInterval(10)
        self._heartbeat_timer.timeout.connect(self._heartbeat)
        self._interaction_deadline_timer = QTimer(self)
        self._interaction_deadline_timer.setSingleShot(True)
        self._interaction_deadline_timer.timeout.connect(self._expire_interaction)

    @property
    def failure(self) -> str | None:
        return self._failure

    def attach(self, shell: QWidget) -> None:
        """Attach before the operator clicks the normal tinySA entry."""

        if self._shell is not None:
            raise RuntimeError("R11-AE visible witness is already attached")
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            raise TypeError("R11-AE visible witness requires QApplication")
        if QGuiApplication.platformName().casefold() != "windows":
            raise RuntimeError("R11-AE accepts only the visible Windows Qt platform")
        if not callable(getattr(shell, "show", None)):
            raise TypeError("R11-AE visible witness requires the normal AppShell")
        activation_signal = getattr(shell, "tinysa_activation_completed", None)
        workspace_signal = getattr(shell, "tinysa_analyzer_workspace_ready", None)
        activation_connect = getattr(activation_signal, "connect", None)
        workspace_connect = getattr(workspace_signal, "connect", None)
        if not callable(activation_connect) or not callable(workspace_connect):
            raise TypeError("R11-AE AppShell witness signals are unavailable")
        self._app = app
        self._shell = shell
        app.installEventFilter(self)
        shell.installEventFilter(self)
        activation_connect(self._on_activation_completed)
        workspace_connect(self._on_workspace_ready)
        self._heartbeat_timer.start()
        self._interaction_deadline_timer.start(
            round(self._profile.interaction_timeout_seconds * 1_000.0)
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        shell = self._shell
        if shell is None:
            return super().eventFilter(watched, event)
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and shell.isVisible():
            self._mouse_button_presses += 1
        elif event_type == QEvent.Type.KeyPress and shell.isVisible():
            self._keyboard_key_presses += 1
        elif watched is shell and event_type == QEvent.Type.Resize and self._busy:
            size = shell.size()
            baseline = self._busy_size
            if baseline is not None:
                self._maximum_resize_delta_logical_px = max(
                    self._maximum_resize_delta_logical_px,
                    abs(size.width() - baseline[0]),
                    abs(size.height() - baseline[1]),
                )
        elif watched is shell and event_type == QEvent.Type.Close:
            self._shell_close_observed = True
        return super().eventFilter(watched, event)

    def build_evidence(self) -> R11AEVisibleUiEvidence:
        """Build only after the operator has cleanly closed the normal shell."""

        self._heartbeat_timer.stop()
        self._interaction_deadline_timer.stop()
        if self._failure is not None:
            raise RuntimeError(self._failure)
        if not self._shell_close_observed:
            raise RuntimeError("R11-AE normal AppShell close was not observed")
        shell = self._shell
        if shell is None or shell.isVisible():
            raise RuntimeError("R11-AE normal AppShell is still visible after event-loop exit")
        activation = self._activation
        capture = self._capture
        if activation is None or capture is None:
            raise RuntimeError("R11-AE visible witness did not complete one normal product flow")
        if activation.source.source_id != self._source_id:
            raise RuntimeError("R11-AE activated source differs from the retained ready preflight")
        if activation.source.identity_assurance.value != self._identity_assurance:
            raise RuntimeError("R11-AE source assurance differs from the retained ready preflight")
        if (
            self._workspace_source_id != self._source_id
            or self._workspace_identity_assurance != self._identity_assurance
        ):
            raise RuntimeError("R11-AE workspace source binding differs from the ready preflight")
        result = capture.result
        presentation = result.presentation
        acquisition = result.acquisition
        return R11AEVisibleUiEvidence(
            profile=self._profile,
            draft_preflight_sha256=self._draft_preflight_sha256,
            ready_preflight_sha256=self._ready_preflight_sha256,
            display_preflight_sha256=self._display_preflight_sha256,
            source_id=self._source_id,
            identity_assurance=self._identity_assurance,
            observed_at_utc=datetime.now(UTC).isoformat(),
            platform_name=QGuiApplication.platformName(),
            window_visible=capture.window_visible,
            window_active=capture.window_active,
            device_pixel_ratio=capture.device_pixel_ratio,
            logical_dpi_x=capture.logical_dpi_x,
            logical_dpi_y=capture.logical_dpi_y,
            logical_width=capture.logical_width,
            logical_height=capture.logical_height,
            mouse_button_presses=self._mouse_button_presses,
            keyboard_key_presses=self._keyboard_key_presses,
            maximum_resize_delta_logical_px=self._maximum_resize_delta_logical_px,
            discover_actions=activation.discover_actions,
            select_actions=activation.select_actions,
            version_verify_actions=activation.version_verify_actions,
            compose_actions=activation.compose_actions,
            collect_actions=capture.presenter.trace_operations_started,
            point_count=acquisition.point_count,
            finite_point_count=acquisition.finite_point_count,
            unit=result.unit,
            value_provenance="device_reported_trace",
            calibration_provenance="device_reported_builtin",
            full_analytical_trace_retained=True,
            presentation_width=presentation.pixel_width,
            presentation_point_count=presentation.display_point_count,
            peak_preserving_presentation=presentation.peak_preserving,
            trace_elapsed_seconds=result.elapsed_seconds,
            observed_points_per_second=result.observed_points_per_second,
            heartbeat_samples_while_busy=self._heartbeat_samples_while_busy,
            maximum_heartbeat_stall_ms=self._maximum_heartbeat_stall_ms,
            worker_to_gui_latency_ms=(
                capture.presenter.maximum_worker_to_gui_latency_ns / 1_000_000.0
            ),
            canvas_trace_paint_events=capture.canvas.trace_paint_events,
            maximum_canvas_paint_ms=capture.canvas.maximum_paint_duration_ns / 1_000_000.0,
            version_commands=activation.version_verify_actions,
            preflight_pnp_revalidations=1,
            measurement_commands=acquisition.measurement_commands,
            readback_commands=acquisition.readback_commands,
            settings_writes=0,
            settings_apply_attempts=capture.presenter.settings_confirmation_operations_started,
            resets=0,
            firmware_writes=0,
            persistent_configuration_writes=0,
            retries=acquisition.retries,
            port_closed=acquisition.port_closed,
            shell_closed=True,
        )

    def _on_activation_completed(self, witness: object) -> None:
        if not isinstance(witness, TinySaSourceActivationWitness):
            self._failure = "R11-AE AppShell emitted an invalid activation witness"
            return
        self._activation = witness

    def _on_workspace_ready(self, workspace: object) -> None:
        if not isinstance(workspace, TinySaAnalyzerWorkspace):
            self._failure = "R11-AE AppShell emitted an invalid analyzer workspace"
            return
        source = workspace.verified_source
        if (
            source is None
            or source.source_id != self._source_id
            or source.identity_assurance.value != self._identity_assurance
        ):
            self._failure = "R11-AE workspace source differs from the ready preflight"
            return
        if self._workspace is not None:
            self._failure = "R11-AE AppShell published more than one analyzer workspace"
            return
        request = TinySaScanRawRequest(
            TinySaModel.ULTRA,
            87_500_000,
            108_000_000,
            self._profile.point_count,
            deadline_seconds=self._profile.trace_deadline_seconds,
        )
        try:
            workspace.lock_unchanged_trace_for_visible_evidence(
                request,
                self._profile.presentation_width,
            )
        except (RuntimeError, ValueError) as error:
            self._failure = f"R11-AE could not lock the unchanged trace: {error}"
            return
        workspace.trace_busy_changed.connect(self._on_trace_busy_changed)
        workspace.trace_observed.connect(self._on_trace_observed)
        workspace.trace_painted.connect(self._on_trace_painted)
        self._workspace = workspace
        self._workspace_source_id = source.source_id
        self._workspace_identity_assurance = source.identity_assurance.value
        self._trace_paint_baseline = workspace.canvas_metrics.trace_paint_events

    def _on_trace_busy_changed(self, busy: object) -> None:
        self._busy = busy is True
        if self._busy and self._shell is not None:
            self._busy_size = (self._shell.width(), self._shell.height())
            self._last_heartbeat_ns = time.perf_counter_ns()
        elif not self._busy:
            self._busy_size = None
            self._last_heartbeat_ns = 0

    def _on_trace_observed(self, result: object) -> None:
        if not isinstance(result, TinySaTraceApplicationResult):
            self._failure = "R11-AE analyzer emitted an invalid trace result"
            return
        if self._capture is not None or self._pending_trace_result is not None:
            self._failure = "R11-AE observed more than one trace"
            return
        self._pending_trace_result = result
        self._schedule_capture_after_trace_paint()

    def _on_trace_painted(self, metrics: object) -> None:
        if not isinstance(metrics, TinySaTraceCanvasMetrics):
            self._failure = "R11-AE canvas emitted invalid paint metrics"
            return
        self._schedule_capture_after_trace_paint()

    def _schedule_capture_after_trace_paint(self) -> None:
        """Capture after the reduced trace has actually painted, never by a guessed delay."""

        workspace = self._workspace
        if (
            self._pending_trace_result is None
            or workspace is None
            or workspace.canvas_metrics.trace_paint_events <= self._trace_paint_baseline
            or self._capture_scheduled
        ):
            return
        self._capture_scheduled = True
        QTimer.singleShot(0, self._capture_after_paint)

    def _capture_after_paint(self) -> None:
        self._capture_scheduled = False
        shell = self._shell
        workspace = self._workspace
        result = self._pending_trace_result
        if shell is None or workspace is None or result is None or self._failure is not None:
            return
        if workspace.canvas_metrics.trace_paint_events <= self._trace_paint_baseline:
            return
        screen = shell.screen()
        if screen is None:
            self._failure = "R11-AE visible shell has no screen"
            return
        self._capture = _TraceCapture(
            result=result,
            presenter=workspace.presenter_metrics,
            canvas=workspace.canvas_metrics,
            window_visible=shell.isVisible(),
            window_active=shell.isActiveWindow(),
            device_pixel_ratio=shell.devicePixelRatioF(),
            logical_dpi_x=screen.logicalDotsPerInchX(),
            logical_dpi_y=screen.logicalDotsPerInchY(),
            logical_width=shell.width(),
            logical_height=shell.height(),
        )
        self._pending_trace_result = None
        status_bar = getattr(shell, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage("R11-AE trace painted; close the normal shell to finalize this cell.")

    def _heartbeat(self) -> None:
        if not self._busy:
            return
        now_ns = time.perf_counter_ns()
        previous_ns = self._last_heartbeat_ns
        self._last_heartbeat_ns = now_ns
        if previous_ns <= 0:
            return
        self._heartbeat_samples_while_busy += 1
        self._maximum_heartbeat_stall_ms = max(
            self._maximum_heartbeat_stall_ms,
            (now_ns - previous_ns) / 1_000_000.0,
        )

    def _expire_interaction(self) -> None:
        """Fail closed instead of letting an incomplete visible cell run indefinitely."""

        if not self._shell_close_observed:
            self._failure = "R11-AE visible interaction deadline expired before normal shell close"
            shell = self._shell
            status_bar = getattr(shell, "statusBar", None)
            if callable(status_bar):
                status_bar().showMessage("R11-AE deadline expired; close the normal shell.")


__all__ = ["R11AEVisibleUiWitness"]

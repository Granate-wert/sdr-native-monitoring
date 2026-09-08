"""Application-owned progressive Sweep facade for the shared Analyzer session.

The facade deliberately owns no receiver, native service, lease or Qt object.
Lifecycle commands go through the same ``LiveSessionApplicationService`` that
owns RTBW admission; the display port is publication-only so it cannot bypass
that ownership boundary.
"""

from __future__ import annotations

import threading
from typing import Protocol

from .analyzer_session import AnalyzerMode, AnalyzerPhase, AnalyzerSessionState
from ..domain.live import LiveSnapshot
from ..domain.analyzer_display import ContinuousSweepDisplaySnapshot
from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest


class AnalyzerContinuousSweepDisplayPort(Protocol):
    """Read-only reduced publication path owned by the existing Sweep service."""

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot: ...


class AnalyzerContinuousSweepControlPort(Protocol):
    """Shared lifecycle subset supplied by ``LiveSessionApplicationService``."""

    @property
    def analyzer_state(self) -> AnalyzerSessionState | None: ...

    def start_sweep(self, request: ContinuousSweepPlanRequest) -> AnalyzerSessionState: ...

    def stop(self) -> LiveSnapshot: ...


class AnalyzerContinuousSweepApplicationService:
    """Adapt shared Analyzer lifecycle plus Sweep publications for its presenter.

    ``ContinuousSweepPresenter`` performs an explicit Stop followed by one poll
    for the terminal line/control gap.  Consequently Stop never consumes the
    display snapshot and Close never polls it a second time.
    """

    def __init__(
        self,
        application: AnalyzerContinuousSweepControlPort,
        display: AnalyzerContinuousSweepDisplayPort,
    ) -> None:
        self._application = application
        self._display = display
        self._operation_lock = threading.RLock()
        self._owns_sweep_attempt = False
        self._terminal_poll_pending = False
        self._closed = False
        self._attempt_id: int | None = None

    @property
    def stop_required(self) -> bool:
        return self._owns_sweep_attempt

    def start(self, request: ContinuousSweepPlanRequest) -> None:
        with self._operation_lock:
            if self._closed:
                raise RuntimeError("continuous Sweep application is closed")
            if self._owns_sweep_attempt:
                raise RuntimeError("continuous Sweep already has an owned start attempt")
            if self._terminal_poll_pending:
                raise RuntimeError("consume the terminal Sweep publication before restarting")
            # Mark the attempt before entering a potentially partially mutating
            # backend call.  A pre-admission rejection remains safe to clean up:
            # the shared controller knows that it must not stop a foreign owner.
            before = self._application.analyzer_state
            previous_id = before.operation_id if before is not None else None
            self._owns_sweep_attempt = True
            try:
                state = self._application.start_sweep(request)
                self._attempt_id = state.operation_id
            except Exception:
                # If admission failed before this operation selected Sweep, it
                # cannot own cleanup.  In particular, never Stop a later/current
                # RTBW owner merely because an earlier Sweep request failed.
                failed_state = self._application.analyzer_state
                if (failed_state is None or failed_state.mode is not AnalyzerMode.SWEEP
                        or failed_state.operation_id == previous_id):
                    self._owns_sweep_attempt = False
                else:
                    self._attempt_id = failed_state.operation_id
                raise

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        with self._operation_lock:
            if self._closed:
                raise RuntimeError("continuous Sweep application is closed")
            if not self._owns_sweep_attempt and not self._terminal_poll_pending:
                raise RuntimeError("continuous Sweep has no owned start attempt")
            state = self._application.analyzer_state
            if state is None or state.operation_id != self._attempt_id:
                raise RuntimeError("Sweep publication belongs to a superseded operation")
            snapshot = self._display.poll_latest()
            self._terminal_poll_pending = False
            return snapshot

    def stop(self) -> None:
        with self._operation_lock:
            self._stop_owned_attempt()

    def close(self) -> None:
        with self._operation_lock:
            if self._closed:
                return
            self._stop_owned_attempt()
            self._closed = True

    def _stop_owned_attempt(self) -> None:
        if not self._owns_sweep_attempt:
            return
        state = self._application.analyzer_state
        if state is None:
            # Losing the common owner is an uncertain cleanup state.  Preserve
            # the local obligation so a repaired composition may retry Close.
            raise RuntimeError("shared Analyzer state is unavailable during Sweep cleanup")
        if state.mode is not AnalyzerMode.SWEEP or state.operation_id != self._attempt_id:
            # Another completed Stop may already have released Sweep and a new
            # RTBW operation may now be running.  Never stop that later owner.
            self._owns_sweep_attempt = False
            self._terminal_poll_pending = False
            return
        if state.phase is AnalyzerPhase.IDLE:
            self._owns_sweep_attempt = False
            self._terminal_poll_pending = True
            return
        snapshot = self._application.stop()
        if snapshot.stop_required:
            # LiveSessionApplicationService translates controller exceptions to
            # immutable error snapshots.  Retain cleanup ownership for retry.
            raise RuntimeError(snapshot.error or "continuous Sweep cleanup was not confirmed")
        self._owns_sweep_attempt = False
        self._terminal_poll_pending = True


__all__ = [
    "AnalyzerContinuousSweepApplicationService",
    "AnalyzerContinuousSweepControlPort",
    "AnalyzerContinuousSweepDisplayPort",
]

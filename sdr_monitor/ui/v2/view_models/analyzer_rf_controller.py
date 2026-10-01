"""GUI-only RF sequence over the TWO EXISTING serial presenter lanes.

No SDK, future wait, executor, DSP, owner or native quality mutation here.
Every potentially blocking command has a queued Qt acknowledgement. The
workspace explicitly acknowledges all view histories before any Start.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from sdr_monitor.application.analyzer_rf_change import (
    AnalyzerRfApplyReceipt, AnalyzerRfContext, AnalyzerRfShiftProposal,
)
from sdr_monitor.application.analyzer_session import AnalyzerPhase

from ..i18n import text
from ..state.analyzer_rf_control import RfControlCompletion

if TYPE_CHECKING:
    from .analyzer_view_model import AnalyzerViewModel


@dataclass(frozen=True, slots=True)
class AnalyzerRfBoundary:
    before: AnalyzerRfContext
    after: AnalyzerRfContext
    host_control_elapsed_s: float | None
    reason: str = "PROFILE_OR_RF_PLAN_CHANGE"


class AnalyzerRfController(QObject):
    """One pending chain; a user Stop outranks all queued RF continuations."""

    preview_ready = Signal(object, object)
    apply_ready = Signal(object)
    cancelled = Signal()
    boundary_ready = Signal(object)

    def __init__(self, model: AnalyzerViewModel, live: Any, sweep: Any) -> None:
        super().__init__()
        self._model, self._live, self._sweep = model, live, sweep
        self._phase: str | None = None
        self._token: object | None = None
        self._cancel = Event()
        self._proposal: AnalyzerRfShiftProposal | None = None
        self._arm: AnalyzerRfApplyReceipt | None = None
        self._anchor: object | None = None
        self._restart = False
        self._fault = False
        self._error_key: str | None = None
        self._user_stop_pending = False
        self._started_at: float | None = None
        self._closed = False
        self._connections = (
            (live.rf_command_ready, self._complete),
            (sweep.rf_command_ready, self._complete),
            (live.busy_changed, self._wake_cleanup),
            (sweep.starting_changed, self._wake_cleanup),
            (sweep.stopping_changed, self._wake_cleanup),
        )
        for signal, slot in self._connections:
            signal.connect(slot)

    @property
    def pending(self) -> bool:
        return self._phase is not None

    @property
    def armed(self) -> bool:
        return self._arm is not None and self._phase is None and not self._fault

    @property
    def fault(self) -> bool:
        return self._fault

    @property
    def phase(self) -> str | None:
        return self._phase

    @property
    def error(self) -> str | None:
        return None if self._error_key is None else text(self._error_key)

    def _phase_changed(self, phase: str | None) -> None:
        self._phase = phase
        self._model.notify_rf_control()

    def _dispatch(self, phase: str, operation: Any, *, fault: bool = True) -> bool:
        """Submission itself can fail; never strand a GUI phase or enable Start."""
        self._phase_changed(phase)
        try:
            operation()
        except Exception:
            self._fail("analyzer.rf.refused", fault=fault)
            return False
        return True

    def begin_entry(self, anchor: object) -> bool:
        if self._closed or self.pending or self.armed or self._fault:
            return False
        self._error_key = None
        self._cancel = Event()
        self._token = object()
        self._anchor = anchor
        self._phase_changed("entry")
        return True

    def preview(self, offset_hz: float, anchor: object) -> bool:
        if self._closed or self._fault or self.armed or self._phase not in (None, "entry"):
            return False
        if self._phase is None and not self.begin_entry(anchor):
            return False
        if self._anchor != anchor or not isfinite(offset_hz) or offset_hz == 0:
            self.cancel()
            return False
        return self._dispatch("preview", lambda: self._live.preview_rf_shift(
            offset_hz, self._token, cancelled=self._cancel.is_set), fault=False)

    def approve(self, proposal: AnalyzerRfShiftProposal) -> bool:
        if self._closed or self._phase != "confirm" or proposal is not self._proposal:
            return False
        self._restart = proposal.expected.state.phase is AnalyzerPhase.RUNNING
        self._started_at = monotonic()
        if self._restart:
            if proposal.expected.state.mode.value == "sweep":
                return self._dispatch("stop", lambda: self._sweep.stop_rf(
                    proposal, self._token, cancelled=self._cancel.is_set))
            else:
                return self._dispatch("stop", lambda: self._live.stop_rf_rtbw(
                    proposal, self._token, cancelled=self._cancel.is_set))
        else:
            return self._apply()

    def _apply(self) -> bool:
        return self._dispatch("apply", lambda: self._live.apply_rf_shift(
            self._proposal, self._token, cancelled=self._cancel.is_set))

    def acknowledge_views(self, receipt: AnalyzerRfApplyReceipt) -> None:
        if self._phase != "receipt" or receipt is not self._arm or self._cancel.is_set():
            return
        self._dispatch("ack", lambda: self._live.acknowledge_rf_apply(
            receipt, self._token, cancelled=self._cancel.is_set))

    def reject_views(self) -> None:
        self._fail("analyzer.rf.receipt_failed", fault=True)

    def start_armed(self) -> bool:
        if not self.armed or self._closed or self._model.live.state.busy:
            return False
        self._cancel = Event()
        self._token = object()
        self._started_at = monotonic()
        return self._start()

    def _start(self) -> bool:
        receipt = self._arm
        if receipt is None or self._cancel.is_set():
            return False
        try:
            self._model.prepare_rf_start(receipt)
        except Exception:
            self.reject_views()
            return False
        if receipt.proposal.expected.state.mode.value == "sweep":
            return self._dispatch("start", lambda: self._sweep.start_rf(
                receipt, self._token, cancelled=self._cancel.is_set))
        else:
            return self._dispatch("start", lambda: self._live.start_rf_rtbw(
                receipt, self._token, cancelled=self._cancel.is_set))

    def cancel(self) -> None:
        if self._closed:
            return
        self._cancel.set()
        self._token = None
        if self._phase not in (None, "entry", "preview", "confirm") or self._arm is not None:
            self._fault = True  # A receipt/control may already have mutated.
        self._arm = None
        self._proposal = None
        self._phase_changed(None)
        self.cancelled.emit()

    def request_user_stop(self) -> bool:
        """Exactly one explicit cleanup request, after in-flight commands finish."""
        if self._closed:
            return False
        if self._user_stop_pending or self._phase in {"cleanup_wait", "cleanup"}:
            return True
        self.cancel()
        self._user_stop_pending = True
        self._phase_changed("cleanup_wait")
        self._wake_cleanup(False)
        return True

    def _wake_cleanup(self, _busy: bool) -> None:
        if (not self._user_stop_pending or self._closed or self._model.live.state.busy
                or self._sweep.is_starting or self._sweep.is_stopping):
            return
        self._user_stop_pending = False
        self._token = object()
        if not self._sweep.can_close():
            self._dispatch("cleanup", lambda: self._sweep.stop_rf_cleanup(self._token))
        else:
            self._dispatch("cleanup", lambda: self._live.stop_rf_cleanup(self._token))

    def _fail(self, key: str, *, fault: bool = False) -> None:
        self._cancel.set()
        self._token = None
        self._fault = self._fault or fault
        self._arm = None
        self._error_key = key
        self._phase_changed(None)
        self.cancelled.emit()

    def _complete(self, result: object) -> None:
        if (self._closed or not isinstance(result, RfControlCompletion)
                or result.token is not self._token or result.phase != self._phase):
            return
        if result.error is not None:
            self._fail("analyzer.rf.refused", fault=result.phase in {"stop", "apply", "ack", "start", "observe", "cleanup"})
            return
        value = result.value
        if result.phase == "preview":
            if not isinstance(value, AnalyzerRfShiftProposal) or value.expected.state.mode.value != self._model.state.mode.value:
                self._fail("analyzer.rf.refused")
                return
            self._proposal = value
            self._phase_changed("confirm")
            self.preview_ready.emit(value, self._anchor)
        elif result.phase == "stop":
            self._apply()
        elif result.phase == "apply":
            if not isinstance(value, AnalyzerRfApplyReceipt):
                self.reject_views()
                return
            self._arm = value
            self._phase_changed("receipt")
            self.apply_ready.emit(value)  # NO Start until the workspace ACKs.
        elif result.phase == "ack":
            if self._restart:
                self._start()
            else:
                self._phase_changed(None)  # Arms only; next Start is explicit.
        elif result.phase == "start":
            self._dispatch("observe", lambda: self._live.observe_rf_context(
                self._token, cancelled=self._cancel.is_set))
        elif result.phase == "observe":
            if not isinstance(value, AnalyzerRfContext) or self._proposal is None:
                self._fail("analyzer.rf.refused", fault=True)
                return
            elapsed = None if self._started_at is None else monotonic() - self._started_at
            if elapsed is not None and (not isfinite(elapsed) or elapsed < 0):
                elapsed = None
            boundary = AnalyzerRfBoundary(self._proposal.expected, value, elapsed)
            self._arm = None
            self._fault = False
            self._phase_changed(None)
            self.boundary_ready.emit(boundary)
        elif result.phase == "cleanup":
            self._fault = False
            self._error_key = None
            self._phase_changed(None)

    def dispose(self) -> None:
        if self._closed:
            return
        self.cancel()
        self._closed = True
        for signal, slot in self._connections:
            signal.disconnect(slot)


__all__ = ["AnalyzerRfController", "AnalyzerRfBoundary"]

"""Inert workspace for the explicit R11-Q/R11-R HackRF activation path."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ...application import (
    HackrfActivationApplicationSnapshot,
    HackrfActivationApplicationState,
)
from ...services import HackrfLiveActivationPlan
from ..components import EmptyState, SectionCard, StatusChip
from ..design_tokens import StatusTone
from ..presenters.hackrf_activation_presenter import HackrfActivationPresenter


class HackrfActivationWorkspace(QWidget):
    """Shows an already admitted plan; creation itself cannot access hardware."""

    def __init__(self, presenter: HackrfActivationPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("HackRF activation workspace")
        self._presenter = presenter
        self._plan: HackrfLiveActivationPlan | None = None
        self._build_ui()
        presenter.snapshot_changed.connect(self._apply_snapshot)
        presenter.busy_changed.connect(self._set_busy)
        presenter.latest_frame_ready.connect(self._show_frame)
        presenter.metrics_ready.connect(self._show_metrics)
        self._apply_snapshot(presenter.current_snapshot)

    def set_activation_plan(self, plan: HackrfLiveActivationPlan) -> None:
        """Supply an already admitted plan only; no preflight is started here."""

        if plan is None:
            raise ValueError("activation plan is required")
        self._plan = plan
        self._plan_detail.setText("An admitted profile is ready for explicit identity preflight.")
        self._preflight.setEnabled(True)

    def shutdown(self) -> None:
        self._presenter.shutdown()

    def blocks_shell_close(self) -> bool:
        """Keep explicit ownership visible until Live has stopped.

        Shell closure must not silently discard the only presentation path while
        the coordinator reports an active or transitional owner.  This query
        does not invoke stop or any runtime operation.
        """

        return self._presenter.current_snapshot.state in {
            HackrfActivationApplicationState.ACTIVE,
            HackrfActivationApplicationState.BUSY,
        }

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("HackRF Live activation")
        title.setProperty("role", "heading")
        layout.addWidget(title)
        self._status = StatusChip("No admitted HackRF plan", StatusTone.NEUTRAL)
        layout.addWidget(self._status)
        self._empty = EmptyState(
            "Explicit activation required",
            "Supplying a plan, checking identity and confirming start are separate actions. This workspace never auto-starts a receiver.",
        )
        layout.addWidget(self._empty)
        card = SectionCard("Activation workflow")
        self._plan_detail = QLabel("An external capability/admission workflow must provide a plan.")
        self._plan_detail.setWordWrap(True)
        self._reason = QLabel("No activation operation has been requested.")
        self._reason.setWordWrap(True)
        self._frame = QLabel("No reduced SpectrumFrame is available.")
        self._frame.setProperty("role", "secondary")
        self._metrics = QLabel("No native scalar metrics are available.")
        self._metrics.setProperty("role", "secondary")
        for widget in (self._plan_detail, self._reason, self._frame, self._metrics):
            card.content.addWidget(widget)
        layout.addWidget(card)
        actions = QHBoxLayout()
        self._preflight = QPushButton("Check current HackRF identity")
        self._preflight.setAccessibleDescription("Runs the explicit identity preflight for the supplied plan only.")
        self._preflight.setEnabled(False)
        self._preflight.clicked.connect(self._request_preflight)
        self._start = QPushButton("Start Live")
        self._start.setAccessibleDescription("Requires a separate visible confirmation before starting one owner.")
        self._start.setEnabled(False)
        self._start.clicked.connect(self._confirm_start)
        self._stop = QPushButton("Stop Live")
        self._stop.setAccessibleDescription("Requests an explicit bounded stop for the active HackRF owner.")
        self._stop.setEnabled(False)
        self._stop.clicked.connect(self._presenter.stop)
        for button in (self._preflight, self._start, self._stop):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)

    def _request_preflight(self) -> None:
        if self._plan is not None:
            self._presenter.request_preflight(self._plan)

    def _confirm_start(self) -> None:
        answer = QMessageBox.question(
            self,
            "Start HackRF Live",
            "Start the explicitly preflighted HackRF Live receiver now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        self._presenter.confirm_start(user_confirmed=answer == QMessageBox.StandardButton.Yes)

    def _show_frame(self, _frame: object) -> None:
        self._frame.setText("Latest reduced SpectrumFrame received.")

    def _show_metrics(self, _metrics: object) -> None:
        self._metrics.setText("Latest bounded native scalar metrics received.")

    def _apply_snapshot(self, snapshot: HackrfActivationApplicationSnapshot) -> None:
        tone = {
            HackrfActivationApplicationState.READY_FOR_PREFLIGHT: StatusTone.INFO,
            HackrfActivationApplicationState.AWAITING_CONFIRMATION: StatusTone.WARNING,
            HackrfActivationApplicationState.BUSY: StatusTone.WARNING,
            HackrfActivationApplicationState.ACTIVE: StatusTone.SUCCESS,
            HackrfActivationApplicationState.FAULTED: StatusTone.ERROR,
        }[snapshot.state]
        self._status.set_status(snapshot.state.value.replace("_", " ").title(), tone)
        self._reason.setText("No refusal reason." if snapshot.reason is None else snapshot.reason.value)
        self._preflight.setEnabled(snapshot.can_request_preflight and self._plan is not None)
        self._start.setEnabled(snapshot.can_confirm_start)
        self._stop.setEnabled(snapshot.active)
        self._empty.setVisible(snapshot.state is HackrfActivationApplicationState.READY_FOR_PREFLIGHT)

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self._preflight.setEnabled(False)
            self._start.setEnabled(False)
            self._stop.setEnabled(False)


__all__ = ["HackrfActivationWorkspace"]

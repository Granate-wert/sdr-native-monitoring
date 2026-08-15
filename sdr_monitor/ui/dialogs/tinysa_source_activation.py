"""Accessible explicit tinySA discover/select/verify/compose dialog."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services.tinysa_source_composition import (
    TinySaComposedSource,
    TinySaSourcePhase,
    TinySaSourceSnapshot,
)
from ..components import EmptyState, SectionCard, StatusChip
from ..design_tokens import StatusTone
from ..presenters.tinysa_source_activation_presenter import (
    TinySaSourceActivationPresenter,
    TinySaSourceActivationWitness,
)
from ..tinysa_analyzer_registration import TinySaAnalyzerWorkspaceRegistration


class TinySaSourceActivationDialog(QDialog):
    """No discovery or version command occurs until its named button is used."""

    registration_ready = Signal(object)
    activation_completed = Signal(object)

    def __init__(
        self,
        presenter: TinySaSourceActivationPresenter,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(presenter, TinySaSourceActivationPresenter):
            raise TypeError("tinySA activation dialog requires its bounded presenter")
        self._presenter = presenter
        self.setWindowTitle("Activate tinySA analyzer")
        self.setModal(False)
        self.setMinimumSize(560, 420)
        self.setAccessibleName("tinySA source activation dialog")
        self._build_ui()
        presenter.snapshot_changed.connect(self._apply_snapshot)
        presenter.composition_ready.connect(self._accept_composition)
        presenter.busy_changed.connect(self._set_busy)
        presenter.task_failed.connect(self._show_error)
        self._apply_snapshot(presenter.current_snapshot)

    @property
    def blocks_shell_close(self) -> bool:
        return self._presenter.busy

    def shutdown(self) -> None:
        if not self._presenter.busy:
            self._presenter.shutdown()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Activate a tinySA source")
        title.setProperty("role", "heading")
        title.setAccessibleName("Activate a tinySA source")
        self._status = StatusChip("Ready", StatusTone.INFO)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._status)
        layout.addLayout(header)

        helper = QLabel(
            "Nothing happens on open. Discover lists USB-CDC endpoints without opening them; "
            "Verify identity sends one version command only after selection."
        )
        helper.setWordWrap(True)
        helper.setProperty("role", "secondary")
        layout.addWidget(helper)

        self._empty = EmptyState(
            "No tinySA sources discovered",
            "Press Discover devices when you are ready to enumerate matching USB endpoints.",
        )
        layout.addWidget(self._empty)

        source_card = SectionCard("Source identity")
        self._candidates = QComboBox()
        self._candidates.setAccessibleName("Discovered tinySA source")
        self._candidates.setAccessibleDescription(
            "Route-redacted tinySA candidates from the last explicit discovery"
        )
        source_card.content.addWidget(self._candidates)

        actions = QHBoxLayout()
        self._discover = QPushButton("Discover devices")
        self._discover.setAccessibleDescription(
            "Enumerates matching USB PnP endpoints without opening a serial port"
        )
        self._discover.clicked.connect(self._presenter.discover)
        self._select = QPushButton("Select source")
        self._select.setAccessibleDescription(
            "Selects the highlighted opaque source without opening it"
        )
        self._select.clicked.connect(self._select_candidate)
        self._verify = QPushButton("Verify identity")
        self._verify.setAccessibleDescription(
            "Sends one read-only version command to the selected source"
        )
        self._verify.clicked.connect(self._presenter.verify_selected)
        self._compose = QPushButton("Use verified source")
        self._compose.setAccessibleDescription(
            "Creates inert bound collector and settings adapters, then registers the analyzer"
        )
        self._compose.clicked.connect(self._presenter.compose_selected)
        for button in (self._discover, self._select, self._verify, self._compose):
            actions.addWidget(button)
        source_card.content.addLayout(actions)
        layout.addWidget(source_card)

        self._identity = QLabel("No source identity has been verified.")
        self._identity.setWordWrap(True)
        self._identity.setAccessibleName("tinySA verified identity status")
        layout.addWidget(self._identity)

        self._busy = QLabel("Source operation in progress…")
        self._busy.setAccessibleName("tinySA source operation in progress")
        self._busy.setVisible(False)
        layout.addWidget(self._busy)
        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setProperty("statusTone", "error")
        self._error.setAccessibleName("tinySA source activation error")
        self._error.setVisible(False)
        layout.addWidget(self._error)
        layout.addStretch(1)

    def _select_candidate(self) -> None:
        source_id = self._candidates.currentData()
        if not isinstance(source_id, str):
            self._show_error("Select a discovered tinySA source first")
            return
        self._presenter.select(source_id)

    def _apply_snapshot(self, snapshot: TinySaSourceSnapshot) -> None:
        if not isinstance(snapshot, TinySaSourceSnapshot):
            self._show_error("Invalid tinySA source state")
            return
        tone = {
            TinySaSourcePhase.READY: StatusTone.INFO,
            TinySaSourcePhase.DISCOVERED: StatusTone.INFO,
            TinySaSourcePhase.SELECTED: StatusTone.WARNING,
            TinySaSourcePhase.VERIFIED: StatusTone.SUCCESS,
            TinySaSourcePhase.COMPOSED: StatusTone.SUCCESS,
            TinySaSourcePhase.FAULTED: StatusTone.ERROR,
        }[snapshot.phase]
        self._status.set_status(snapshot.phase.value.title(), tone)
        selected = self._candidates.currentData()
        self._candidates.clear()
        selected_index = -1
        for index, candidate in enumerate(snapshot.candidates):
            self._candidates.addItem(
                f"{candidate.label} — {candidate.identity_assurance.value}",
                candidate.source_id,
            )
            if candidate.source_id == (snapshot.selected_source_id or selected):
                selected_index = index
        if selected_index >= 0:
            self._candidates.setCurrentIndex(selected_index)
        self._empty.setVisible(not snapshot.candidates)
        self._discover.setEnabled(snapshot.can_discover)
        self._select.setEnabled(snapshot.can_select and bool(snapshot.candidates))
        self._verify.setEnabled(snapshot.can_verify)
        self._compose.setEnabled(snapshot.can_compose)
        if snapshot.verified is not None:
            source = snapshot.verified
            assurance = source.identity_assurance.value.replace("_", " ")
            self._identity.setText(
                f"{source.label}; identity assurance: {assurance}; "
                "device continuity remains unverified."
            )
        if snapshot.reason is not None:
            self._show_error(snapshot.reason.value.replace("_", " "))
        elif snapshot.phase is not TinySaSourcePhase.FAULTED:
            self._error.setVisible(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy.setVisible(busy)
        if busy:
            for button in (self._discover, self._select, self._verify, self._compose):
                button.setEnabled(False)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setAccessibleDescription(message)
        self._error.setVisible(True)

    def _accept_composition(self, composed: TinySaComposedSource) -> None:
        try:
            registration = TinySaAnalyzerWorkspaceRegistration.from_composed_source(
                composed
            )
        except ValueError:
            self._show_error("tinySA source registration failed")
            return
        witness = self._presenter.completed_witness()
        if not isinstance(witness, TinySaSourceActivationWitness):
            self._show_error("tinySA source activation transcript failed")
            return
        self.activation_completed.emit(witness)
        self.registration_ready.emit(registration)
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._presenter.busy:
            self._show_error("Wait for the current tinySA source operation to finish")
            event.ignore()
            return
        self._presenter.shutdown()
        super().closeEvent(event)


__all__ = ["TinySaSourceActivationDialog"]

"""One-tab UI V2 surface for an externally previewed/applied pane plan.

This widget never discovers, selects or applies device settings. Commands
are explicit and enqueue on the per-resource workers; Qt only polls their
small state snapshots and drains the bounded presentation queue. The product
controller must own source Stage/Apply and terminal graph close outside Qt.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase

from ..design import ThemeId, stylesheet_for_theme
from ..i18n import text
from .independent_pane_board import IndependentPaneBoardV2
from .independent_pane_delivery import IndependentPaneDeliveryPort


class IndependentPaneSessionV2(QWidget):
    """One selected-pane command bar plus distinct 1–4 graph pairs."""

    def __init__(self, handle: PaneProductSessionHandle, *,
                 confirm_shared_stop: Callable[[tuple[str, ...]], bool] | None = None,
                 close_layout: Callable[[PaneProductSessionHandle], None] | None = None,
                 parent: QWidget | None = None) -> None:
        if not isinstance(handle, PaneProductSessionHandle) or not handle.applied:
            raise ValueError("independent pane UI needs one explicitly applied product plan")
        super().__init__(parent)
        self.setObjectName("independentPaneSessionV2")
        self.setProperty("ui2Root", True)
        self.handle = handle
        self._confirm_shared_stop = confirm_shared_stop or self._ask_shared_stop
        self._close_layout = close_layout
        self._futures: list[Future[Any]] = []
        self._error_key: str | None = None
        self._error_pane: str | None = None
        self._terminal_released = False
        schedule = handle.layout.schedule
        assert schedule is not None  # The applied product handle requires a nonempty plan.
        self._pane_resources = {
            item.pane_id: item.physical_stream_resource_id
            for item in schedule.pane_revisits
        }
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        commands = QHBoxLayout()
        commands.setSpacing(4)
        self.title = QLabel(self)
        self.title.setProperty("ui2Role", "secondary")
        commands.addWidget(self.title, 1)
        self.start_selected = self._button(self._start_selected)
        self.start_all = self._button(self._start_all)
        self.stop_selected = self._button(self._stop_selected)
        self.stop_all = self._button(self._stop_all)
        for control in (self.start_selected, self.start_all, self.stop_selected, self.stop_all):
            commands.addWidget(control)
        self.close_layout = self._button(self._request_close_layout)
        self.close_layout.setVisible(close_layout is not None)
        commands.addWidget(self.close_layout)
        layout.addLayout(commands)
        self.board = IndependentPaneBoardV2(handle.preparer, source_labels=handle.source_labels, parent=self)
        self.board.selected_slot_changed.connect(self._refresh)
        layout.addWidget(self.board, 1)
        self.status = QLabel(self)
        self.status.setProperty("ui2Role", "secondary")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.error = QLabel(self)
        self.error.setProperty("ui2Tone", "error")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.delivery = IndependentPaneDeliveryPort(self.board, handle.queue, parent=self)
        self.delivery.render_failed.connect(self._render_failed)
        self.delivery.start()
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(100)
        self._state_timer.timeout.connect(self._refresh)
        self._state_timer.start()
        self.set_theme(ThemeId.DARK)
        self.set_locale()

    def _button(self, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(self)
        button.setProperty("ui2Role", "utility-action")
        button.clicked.connect(callback)
        return button

    def _selected_resource(self) -> tuple[str, str] | None:
        slot = self.handle.layout.slots[self.board.selected_slot - 1]
        if slot.request is None:
            return None
        return slot.request.pane_id, self._pane_resources[slot.request.pane_id]

    def _start_selected(self) -> None:
        selected = self._selected_resource()
        if selected is None:
            return
        self._error_key = None
        try:
            self._futures.append(self.handle.pump.start_resource(selected[1]))
        except (RuntimeError, ValueError):
            self._error_key = "analyzer.independent.operation_failed"
        self._refresh()

    def _start_all(self) -> None:
        self._error_key = None
        try:
            for item in self.handle.pump.snapshot():
                if item.phase is PanePumpPhase.IDLE:
                    self._futures.append(self.handle.pump.start_resource(item.physical_stream_resource_id))
        except (RuntimeError, ValueError):
            self._error_key = "analyzer.independent.operation_failed"
        self._refresh()

    def _stop_selected(self) -> None:
        selected = self._selected_resource()
        if selected is None:
            return
        try:
            impact = self.handle.session.stop_impact(selected[0])
            if len(impact) > 1 and not self._confirm_shared_stop(impact):
                return
            _, future = self.handle.pump.stop_selected(selected[0], acknowledge_shared=True)
            self._futures.append(future)
        except (RuntimeError, ValueError):
            self._error_key = "analyzer.independent.operation_failed"
        self._refresh()

    def _stop_all(self) -> None:
        try:
            self._futures.extend(self.handle.pump.stop_all().values())
        except (RuntimeError, ValueError):
            self._error_key = "analyzer.independent.operation_failed"
        self._refresh()

    def _request_close_layout(self) -> None:
        if self._close_layout is not None and self.handle.can_close():
            self._close_layout(self.handle)

    def _ask_shared_stop(self, impact: tuple[str, ...]) -> bool:
        answer = QMessageBox.question(self, text("analyzer.independent.shared_stop.title"),
            text("analyzer.independent.shared_stop.detail", panes=", ".join(impact)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer is QMessageBox.StandardButton.Yes

    def _render_failed(self, pane_id: str, _detail: str) -> None:
        self._error_key = "analyzer.independent.render_failed"
        self._error_pane = pane_id
        self._refresh()

    def _refresh(self, _selected_slot: int | None = None) -> None:
        if self._terminal_released:
            return
        waiting: list[Future[Any]] = []
        for future in self._futures:
            if future.done():
                if future.exception() is not None:
                    self._error_key = "analyzer.independent.operation_failed"
            else:
                waiting.append(future)
        self._futures = waiting
        states = {item.physical_stream_resource_id: item.phase for item in self.handle.pump.snapshot()}
        selected = self._selected_resource()
        selected_phase = None if selected is None else states[selected[1]]
        self.start_selected.setEnabled(selected_phase is PanePumpPhase.IDLE)
        self.stop_selected.setEnabled(selected_phase is not None and selected_phase is not PanePumpPhase.STOPPED)
        self.start_all.setEnabled(any(phase is PanePumpPhase.IDLE for phase in states.values()))
        self.stop_all.setEnabled(any(phase is not PanePumpPhase.STOPPED for phase in states.values()))
        self.close_layout.setEnabled(self._close_layout is not None and self.handle.can_close())
        running = sum(phase is PanePumpPhase.RUNNING for phase in states.values())
        starting = sum(phase in {PanePumpPhase.STARTING, PanePumpPhase.STOPPING} for phase in states.values())
        failed = sum(phase is PanePumpPhase.STOP_REQUIRED for phase in states.values())
        stopped = sum(phase is PanePumpPhase.STOPPED for phase in states.values())
        summary = text("analyzer.independent.summary", running=running, starting=starting,
                       failed=failed, stopped=stopped)
        if self.status.text() != summary:
            self.status.setText(summary)
        detail = ("" if self._error_key is None else text(self._error_key, pane=self._error_pane or ""))
        if self.error.text() != detail:
            self.error.setText(detail)
        self.error.setVisible(bool(detail))

    def set_theme(self, theme: ThemeId) -> None:
        self.setStyleSheet(stylesheet_for_theme(theme))
        self.board.set_theme(theme)

    def set_locale(self) -> None:
        self.title.setText(text("analyzer.independent.title"))
        for control, key in (
            (self.start_selected, "analyzer.independent.start_selected"),
            (self.start_all, "analyzer.independent.start_all"),
            (self.stop_selected, "analyzer.independent.stop_selected"),
            (self.stop_all, "analyzer.independent.stop_all"),
            (self.close_layout, "analyzer.pane.setup.close_layout"),
        ):
            control.setText(text(key))
            control.setAccessibleName(text(key))
        self.board.set_locale()
        self._refresh()

    def release_presentation_after_shutdown(self) -> None:
        if self._terminal_released:
            return
        if not self.handle.shutdown_complete:
            raise RuntimeError("independent pane owner has not confirmed terminal shutdown")
        self._state_timer.stop()
        self.delivery.stop()
        self.board.release_presentation_after_shutdown()
        self._terminal_released = True

    def closeEvent(self, event) -> None:
        if not self.handle.can_close():
            event.ignore()
            return
        super().closeEvent(event)


__all__ = ["IndependentPaneSessionV2"]

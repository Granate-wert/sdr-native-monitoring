"""One-tab UI V2 surface for an externally previewed/applied pane plan.

This widget never discovers, selects or applies device settings. Commands
are explicit and enqueue on the per-resource workers; Qt only polls their
small state snapshots and drains the bounded presentation queue. The product
controller must own source Stage/Apply and terminal graph close outside Qt.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from math import floor, isfinite
from typing import Any
from weakref import ref as weak_ref

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, RtlTunerGainReceipt
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.domain.pane_scheduler import PaneControlGapReason, RtlRtbwPaneProfile
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.services.pane_resource_session import PaneActivation, PaneHostTiming
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef, PaneDeliveryStage
from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from sdr_monitor.ui.v2_pane_runtime import PanePumpResourceState
from sdr_monitor.ui.v2_pane_presentation import PanePresentationBinding
from sdr_monitor.ui.v2_pane_rf_plan import PaneRfChangePreview

from ..design import ThemeId, stylesheet_for_theme
from ..design.tokens import tokens_for_theme
from ..i18n import text
from ..state.analyzer_readouts import tinysa_settings_readout
from .independent_pane_board import IndependentPaneBoardV2
from .independent_pane_delivery import IndependentPaneDeliveryPort
from .pane_failure_text import pane_failure_text
from .rf_shift_dialog import RfImpactDialog, RfShiftEntryDialog, pane_rf_impact_summary, pane_rf_preview_text


class IndependentPaneSessionV2(QWidget):
    """One selected-pane command bar plus distinct 1–4 graph pairs."""

    stop_boundary = Signal(object)

    def __init__(self, handle: PaneProductSessionHandle, *,
                 confirm_shared_stop: Callable[[tuple[str, ...]], bool] | None = None,
                 confirm_paired_start: Callable[[tuple[int, ...]], bool] | None = None,
                 close_layout: Callable[[PaneProductSessionHandle], None] | None = None,
                 parent: QWidget | None = None) -> None:
        if not isinstance(handle, PaneProductSessionHandle) or not handle.applied:
            raise ValueError("independent pane UI needs one explicitly applied product plan")
        super().__init__(parent)
        self.setObjectName("independentPaneSessionV2")
        self.setProperty("ui2Root", True)
        self.handle = handle
        self._confirm_shared_stop = confirm_shared_stop or self._ask_shared_stop
        self._confirm_paired_start = confirm_paired_start or self._ask_paired_start
        self._close_layout = close_layout
        self._futures: list[Future[Any]] = []
        self._error_key: str | None = None
        self._error_pane: str | None = None
        self._terminal_released = False
        self._rf_phase: str | None = None
        self._rf_future: Future[Any] | None = None
        self._rf_preview: PaneRfChangePreview | None = None
        self._rf_dialog: QDialog | None = None
        self._rf_cancelled = False
        self._rf_fault_resource: str | None = None
        self._rf_boundaries: dict[str, PaneActivation] = {}
        self.stop_boundary.connect(self._on_stop_boundary, Qt.ConnectionType.QueuedConnection)
        schedule = handle.layout.schedule
        assert schedule is not None  # The applied product handle requires a nonempty plan.
        self._pane_resources = {
            item.pane_id: item.physical_stream_resource_id
            for item in schedule.pane_revisits
        }
        self._pane_revisits = {item.pane_id: item for item in schedule.pane_revisits}
        self._rtl_pane_ids = {
            crop.pane_id for resource in schedule.resources for job in resource.jobs
            if isinstance(job.profile, RtlRtbwPaneProfile) for crop in job.crops
        }
        self._time_sliced_resources = {item.physical_stream_resource_id
                                       for item in schedule.resources if len(item.jobs) > 1}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        commands = QVBoxLayout()
        commands.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        self.title = QLabel(self)
        self.title.setProperty("ui2Role", "secondary")
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_row.addWidget(self.title, 1)
        self.rf_shift = self._button(self._enter_rf_shift)
        self.rf_shift.setObjectName("independentRfShiftV2")
        title_row.addWidget(self.rf_shift)
        self.close_layout = self._button(self._request_close_layout)
        self.close_layout.setVisible(close_layout is not None)
        title_row.addWidget(self.close_layout)
        commands.addLayout(title_row)
        control_row = QHBoxLayout()
        control_row.setSpacing(4)
        self.start_selected = self._button(self._start_selected)
        self.start_all = self._button(self._start_all)
        self.stop_selected = self._button(self._stop_selected)
        self.stop_all = self._button(self._stop_all)
        for control in (self.start_selected, self.start_all, self.stop_selected, self.stop_all):
            control.setMinimumWidth(0)
            control.setMaximumWidth(220)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            control_row.addWidget(control, 1)
        commands.addLayout(control_row)
        layout.addLayout(commands)
        self.board_scroll = QScrollArea(self)
        self.board_scroll.setObjectName("independentPaneBoardScrollV2")
        self.board_scroll.setProperty("ui2Role", "panel-scroll")
        self.board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board_scroll.setWidgetResizable(True)
        self.board_scroll.setMinimumSize(0, 0)
        self.board_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.board = IndependentPaneBoardV2(
            handle.preparer, source_labels=handle.source_labels,
            stage_callback=handle.report_delivery_stage, parent=self.board_scroll)
        self.board_scroll.setWidget(self.board)
        self.board.selected_slot_changed.connect(self._refresh)
        self.board.set_rf_control_available(self._rf_eligible)
        self.board.rf_shift_requested.connect(self._request_rf_shift)
        layout.addWidget(self.board_scroll, 1)
        self.status = QLabel(self)
        self.status.setProperty("ui2Role", "secondary")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
        if (selected is None or self._rf_phase is not None
                or self._terminal_released or selected[1] == self._rf_fault_resource):
            return
        resource_id = selected[1]
        if resource_id not in self.handle.pump.startable_resource_ids():
            return
        if resource_id in self.handle.preparer.paired_resource_ids:
            impact = self._paired_start_impact((resource_id,))
            if not self._confirm_paired_start(impact):
                return
            if (self._terminal_released or self._rf_phase is not None
                    or resource_id == self._rf_fault_resource
                    or resource_id not in self.handle.pump.startable_resource_ids()):
                return
        self._error_key = None
        try:
            self._futures.append(self.handle.pump.start_resource(resource_id))
        except (RuntimeError, ValueError):
            self._error_key = "analyzer.independent.operation_failed"
        self._refresh()

    def _start_all(self) -> None:
        if self._rf_phase is not None or self._terminal_released:
            return
        startable = tuple(item for item in self.handle.pump.startable_resource_ids()
                          if item != self._rf_fault_resource)
        if not startable:
            return
        impact = self._paired_start_impact(startable)
        if impact:
            if not self._confirm_paired_start(impact):
                return
            # A modal confirmation must not authorize a different later set
            # of resources, especially a pair that was not in its summary.
            if (self._terminal_released or self._rf_phase is not None
                    or startable != tuple(item for item in self.handle.pump.startable_resource_ids()
                                          if item != self._rf_fault_resource)):
                return
        self._error_key = None
        try:
            for resource_id in startable:
                self._futures.append(self.handle.pump.start_resource(resource_id))
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
            preview = self._rf_preview
            # Stop on an independent peer does not cancel this target's
            # approved RF change. Stop of the target outranks the chain.
            if preview is None or preview.proposal.physical_stream_resource_id == selected[1]:
                self._cancel_rf_change()
            _, future = self.handle.pump.stop_selected(selected[0], acknowledge_shared=True)
            self._watch_stop_boundary(future, selected[1])
            self._futures.append(future)
        except (RuntimeError, ValueError):
            self._error_key = "analyzer.independent.operation_failed"
        self._refresh()

    def _stop_all(self) -> None:
        self._cancel_rf_change()
        try:
            futures = self.handle.pump.stop_all()
            for resource_id, future in futures.items():
                self._watch_stop_boundary(future, resource_id)
            self._futures.extend(futures.values())
        except (RuntimeError, ValueError):
            self._error_key = "analyzer.independent.operation_failed"
        self._refresh()

    def _rf_eligible(self, number: int) -> bool:
        if (type(number) is not int or not 1 <= number <= len(self.handle.layout.slots)
                or self._terminal_released or self._rf_phase is not None or self.handle.rf_context is None
                or self.handle.pump.control_pending() or any(not future.done() for future in self._futures)):
            return False
        slot = self.handle.layout.slots[number - 1]
        if slot.request is None:
            return False
        resource_id = self._pane_resources[slot.request.pane_id]
        state = next(item for item in self.handle.pump.snapshot()
                     if item.physical_stream_resource_id == resource_id)
        return (resource_id != self._rf_fault_resource
                and state.phase in {PanePumpPhase.RUNNING, PanePumpPhase.STOPPED}
                and self.board.rf_anchor(number) is not None)

    def _enter_rf_shift(self) -> None:
        number = self.board.selected_slot
        if not self._rf_eligible(number):
            return
        anchor = self.board.rf_anchor(number)
        dialog = RfShiftEntryDialog(self)
        self._rf_dialog = dialog
        self._rf_phase = "entry"
        self.handle.set_rf_presentation_pending(True)

        def finished(result: int) -> None:
            if self._rf_dialog is not dialog:
                return
            delta = dialog.offset.value() * 1e6
            self._finish_rf_change()
            if result == QDialog.DialogCode.Accepted:
                self._request_rf_shift(number, delta, anchor)
            self._refresh()

        dialog.finished.connect(finished)
        dialog.open()
        self._refresh()

    def _request_rf_shift(self, number: int, delta: float, anchor: object) -> None:
        if (not self._rf_eligible(number) or anchor != self.board.rf_anchor(number)
                or not isfinite(delta) or delta == 0):
            return
        self._error_key = None
        self._rf_cancelled = False
        self._rf_phase = "preview"
        self.handle.set_rf_presentation_pending(True)
        try:
            self._rf_future = self.handle.preview_rf_shift(number, delta)
        except (RuntimeError, ValueError):
            self._error_key = "analyzer.rf.refused"
            self._finish_rf_change()
        self._refresh()

    def _show_rf_preview(self, preview: PaneRfChangePreview) -> None:
        self._rf_preview = preview
        self._rf_phase = "confirm"
        dialog = RfImpactDialog(pane_rf_preview_text(preview),
                                restart_required=preview.resource.restart_required,
                                impact_summary=pane_rf_impact_summary(preview), parent=self)
        self._rf_dialog = dialog

        def finished(result: int) -> None:
            if self._rf_dialog is not dialog:
                return
            self._rf_dialog = None
            dialog.deleteLater()
            if result != QDialog.DialogCode.Accepted:
                self._finish_rf_change()
            else:
                try:
                    self._rf_phase = "stop"
                    self._rf_future = self.handle.stop_for_rf_shift(preview)
                    self._watch_stop_boundary(
                        self._rf_future, preview.proposal.physical_stream_resource_id)
                except (RuntimeError, ValueError):
                    self._error_key = "analyzer.rf.refused"
                    self._finish_rf_change()
            self._refresh()

        dialog.finished.connect(finished)
        dialog.open()

    def _finish_rf_change(self) -> None:
        dialog = self._rf_dialog
        self._rf_dialog = None
        if dialog is not None:
            dialog.reject()
            dialog.deleteLater()
        self._rf_phase = None
        self._rf_future = None
        self._rf_preview = None
        self._rf_cancelled = False
        self.handle.set_rf_presentation_pending(False)

    def _cancel_rf_change(self) -> None:
        if self._rf_phase is None:
            return
        self._rf_cancelled = True
        dialog = self._rf_dialog
        self._rf_dialog = None
        if dialog is not None:
            dialog.reject()
            dialog.deleteLater()
        if self._rf_future is None:
            self._finish_rf_change()
        # A running Future is not detached or cancelled. Observe its terminal
        # receipt; if Apply committed, update GUI metadata but NEVER Start.

    def _install_rf_receipt(self, resource_id: str) -> None:
        try:
            self.board.refresh_resource_plan(resource_id)
        except Exception:
            self._rf_fault_resource = resource_id
            self._error_key = "analyzer.rf.receipt_failed"
            raise RuntimeError("RF GUI receipt failed; Stop and layout close required") from None
        schedule = self.handle.layout.schedule
        assert schedule is not None
        self._pane_resources = {item.pane_id: item.physical_stream_resource_id
                                for item in schedule.pane_revisits}
        self._pane_revisits = {item.pane_id: item for item in schedule.pane_revisits}
        self._time_sliced_resources = {item.physical_stream_resource_id
                                       for item in schedule.resources if len(item.jobs) > 1}

    def _advance_rf_change(self) -> None:
        future = self._rf_future
        if future is None or not future.done():
            return
        phase = self._rf_phase
        self._rf_future = None
        try:
            result = future.result()  # Done only: no blocking Qt/SDK operation.
            preview = self._rf_preview
            if phase == "apply":
                assert preview is not None
                self._install_rf_receipt(preview.proposal.physical_stream_resource_id)
            if self._rf_cancelled:
                self._finish_rf_change()
            elif phase == "preview":
                if not isinstance(result, PaneRfChangePreview):
                    raise RuntimeError("RF worker returned an invalid preview")
                self._show_rf_preview(result)
            elif phase == "stop":
                assert preview is not None
                self._rf_phase = "apply"
                self._rf_future = self.handle.apply_rf_shift(preview)
            elif phase == "apply" and preview is not None and preview.resource.restart_required:
                self._rf_phase = "start"
                self._rf_future = self.handle.pump.start_resource(preview.proposal.physical_stream_resource_id)
            else:
                self._finish_rf_change()
        except Exception:
            if not self._rf_cancelled and self._rf_fault_resource is None:
                self._error_key = "analyzer.rf.refused"
            self._finish_rf_change()

    def _request_close_layout(self) -> None:
        if self._close_layout is not None and self.handle.can_close():
            self._close_layout(self.handle)

    def _watch_stop_boundary(self, future: Future[Any], resource_id: str) -> None:
        owner_ref = weak_ref(self)

        def completed(result: Future[Any]) -> None:
            try:
                if result.exception() is not None:
                    return
                owner = owner_ref()
                if owner is None:
                    return
                snapshot = owner.handle.session.pane_delivery_ledger_snapshot()
                refs = tuple(record.ref for record in snapshot.records
                             if record.stage in {PaneDeliveryStage.QUEUE_DRAINED,
                                                 PaneDeliveryStage.UI_ADMITTED,
                                                 PaneDeliveryStage.PAINT_SCHEDULED}
                             and record.ref.identity.physical_stream_resource_id == resource_id)
                owner.stop_boundary.emit(refs)
            except Exception:
                # A cached-ledger read or a late deleted QObject is telemetry
                # only; it must not perturb the already-completed Stop future.
                return
        future.add_done_callback(completed)

    def _on_stop_boundary(self, payload: object) -> None:
        if not isinstance(payload, tuple) or any(
                not isinstance(ref, PaneDeliveryObligationRef) for ref in payload):
            return
        for slot in self.handle.layout.slots:
            if slot.request is None:
                continue
            pane = self.board.pane(slot.number)
            if pane is not None:
                pane.spectrum_scene.stop_delivery_custody(payload)

    def _ask_shared_stop(self, impact: tuple[str, ...]) -> bool:
        answer = QMessageBox.question(self, text("analyzer.independent.shared_stop.title"),
            text("analyzer.independent.shared_stop.detail", panes=", ".join(impact)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        # Compare the returned Qt button value, not Python wrapper identity.
        return answer == QMessageBox.StandardButton.Yes

    def _paired_start_impact(self, resource_ids: tuple[str, ...]) -> tuple[int, ...]:
        paired = self.handle.preparer.paired_resource_ids.intersection(resource_ids)
        return tuple(sorted(binding.slot_number for binding in self.handle.preparer.bindings.values()
                            if binding.physical_stream_resource_id in paired))

    def _ask_paired_start(self, impact: tuple[int, ...]) -> bool:
        answer = QMessageBox.question(
            self, text("analyzer.independent.paired_start.title"),
            text("analyzer.independent.paired_start.detail",
                 panes=", ".join(str(number) for number in impact)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Yes

    def _render_failed(self, pane_id: str, _detail: str) -> None:
        self._error_key = "analyzer.independent.render_failed"
        self._error_pane = pane_id
        self._refresh()

    @staticmethod
    def _age_text(age_s: float | None) -> str | None:
        if age_s is None or not isfinite(age_s) or age_s < 0:
            return None
        if age_s < 1:
            return text("analyzer.independent.timing.under_one")
        if age_s >= 999:
            return text("analyzer.independent.timing.age_over_limit")
        return text("analyzer.independent.timing.seconds", value=floor(age_s))

    @staticmethod
    def _interval_text(interval_s: float) -> str:
        if interval_s < 0.01:
            return text("analyzer.independent.timing.under_hundredth")
        return text("analyzer.independent.timing.seconds", value=f"{interval_s:.2f}")

    def _host_input_age_text(self, pane_id: str, phase: PanePumpPhase) -> str:
        if phase is not PanePumpPhase.RUNNING:
            age = None
        else:
            try:
                timing = self.handle.session.pane_host_timing(pane_id)
            except (RuntimeError, ValueError):
                timing = PaneHostTiming(None, None)
                self._error_key = "analyzer.independent.operation_failed"
            age = self._age_text(timing.frame_age_s)
        return text("analyzer.independent.timing.host_input_age",
                    age=age or text("analyzer.independent.timing.age_unknown"))

    def _freshness_prefix(self, slot_number: int, pane_id: str,
                          state: PanePumpResourceState) -> str:
        plot_age = self._age_text(self.board.plot_update_age_s(slot_number))
        return (self._host_input_age_text(pane_id, state.phase) + " · " +
            text("analyzer.independent.timing.plot_last_updated",
                 age=plot_age or text("analyzer.independent.timing.age_unknown")))

    def _retained_prior_activation(self, slot_number: int, state: PanePumpResourceState,
                                   *, has_retained_frame: bool) -> bool:
        if not has_retained_frame:
            return False
        accepted_serial = self.board.accepted_activation_serial(slot_number)
        activation_serial = (None if state.activation is None else
                             state.activation.host_activation_serial)
        return ((activation_serial is None and state.phase in {
                    PanePumpPhase.STARTING, PanePumpPhase.RUNNING})
                or (activation_serial is not None and accepted_serial != activation_serial))

    def _timing_summary_text(self, slot_number: int, pane_id: str,
                             state: PanePumpResourceState, *, has_retained_frame: bool,
                             details: str) -> str:
        line1 = self._freshness_prefix(slot_number, pane_id, state)
        marker = (text("analyzer.independent.timing.retained_prior_activation") + " · "
                  if self._retained_prior_activation(slot_number, state,
                                                     has_retained_frame=has_retained_frame) else "")
        return line1 + "\n" + marker + details

    def _target_text(self, pane_id: str, timing: PaneHostTiming) -> str:
        target = self._pane_revisits[pane_id].requested_maximum_revisit_s
        if target is None:
            return ""
        observed = timing.last_revisit_s
        key = ("target_unknown" if observed is None or not isfinite(observed) or observed <= 0
               else "target_missed" if observed > target else "target_last_within")
        return " · " + text("analyzer.independent.timing." + key,
                             target=text("analyzer.independent.timing.seconds", value=f"{target:g}"))

    def _timing_text(self, pane_id: str, phase: PanePumpPhase, *, has_retained_frame: bool = False) -> str:
        if phase is not PanePumpPhase.RUNNING:
            key = {
                PanePumpPhase.IDLE: "stopped" if has_retained_frame else "stopped_empty",
                PanePumpPhase.STARTING: "starting",
                PanePumpPhase.STOPPING: "stopping",
                PanePumpPhase.STOP_REQUIRED: "stop_required",
                PanePumpPhase.STOPPED: "stopped" if has_retained_frame else "stopped_empty",
            }[phase]
            return text(f"analyzer.independent.timing.{key}")
        try:
            timing = self.handle.session.pane_host_timing(pane_id)
        except (RuntimeError, ValueError):
            timing = PaneHostTiming(None, None)
            self._error_key = "analyzer.independent.operation_failed"
        estimate = self._pane_revisits[pane_id]
        age = self._age_text(timing.frame_age_s)
        if (estimate.mode is ReceiverBindingMode.TIME_SLICED
                or estimate.physical_stream_resource_id in self._time_sliced_resources):
            modeled = self._interval_text(estimate.maximum_revisit_s)
            target = self._target_text(pane_id, timing)
            if age is None:
                return text("analyzer.independent.timing.sliced_no_frame", modeled=modeled) + target
            observed = timing.last_revisit_s
            if observed is None or not isfinite(observed) or observed <= 0:
                return text("analyzer.independent.timing.sliced_first", modeled=modeled) + target
            return text("analyzer.independent.timing.sliced",
                        observed=self._interval_text(observed), modeled=modeled) + target
        if age is None:
            return text("analyzer.independent.timing.continuous_no_frame")
        return text("analyzer.independent.timing.continuous")

    @staticmethod
    def _rtl_actual_readout(
        bundle: AnalyzerFrameBundle | None, state: PanePumpResourceState,
        binding: PanePresentationBinding, accepted_serial: int | None,
    ) -> str:
        """Only a GUI-accepted frame from this live activation carries actuals."""
        frame = None if bundle is None else bundle.spectrum
        activation = state.activation
        identity = None if bundle is None else bundle.identity
        if (state.phase is not PanePumpPhase.RUNNING or activation is None
                or accepted_serial != activation.host_activation_serial
                or activation.physical_stream_resource_id != binding.physical_stream_resource_id
                or activation.capture_id != binding.capture_id
                or not isinstance(frame, LiveSpectrumFrame) or bundle is None
                or bundle.rtbw is None or identity is None
                or not bundle.session_id or identity.session_id != bundle.session_id
                or type(bundle.acquisition_epoch) is not int
                or bundle.acquisition_epoch != frame.acquisition_epoch
                or identity.acquisition_epoch != frame.acquisition_epoch
                or identity.source_id != binding.source_id
                or frame.source_id != binding.source_id
                or identity.unit != "dBFS/bin" or frame.unit != binding.unit
                or identity.config_generation != frame.config_generation
                or bundle.rtbw.center_frequency_hz != frame.center_frequency_hz
                or bundle.rtbw.sample_rate_hz != frame.sample_rate_hz):
            key = ("analyzer.independent.rtl_retained_unknown" if bundle is not None
                   and state.phase is PanePumpPhase.STOPPED else
                   "analyzer.independent.rtl_actual_unknown")
            return text(key)
        gain = bundle.rtl_tuner_gain
        if (not isinstance(gain, RtlTunerGainReceipt)
                or gain.source_id != binding.source_id
                or gain.config_generation != frame.config_generation
                or gain.acquisition_epoch != frame.acquisition_epoch):
            gain_text = text("analyzer.pane.setup.rtl_gain_frame",
                             requested=text("analyzer.pane.setup.rtl_gain_cache_unknown"),
                             cached=text("analyzer.pane.setup.rtl_gain_cache_unknown"))
        else:
            requested = (text("analyzer.pane.setup.rtl_gain_auto")
                         if gain.requested_manual_tenth_db is None else
                         text("analyzer.pane.setup.rtl_gain_value",
                              value=gain.requested_manual_tenth_db / 10))
            cached = (text("analyzer.pane.setup.rtl_gain_cache_auto")
                      if gain.requested_manual_tenth_db is None else
                      text("analyzer.pane.setup.rtl_gain_cache_unknown")
                      if gain.cached_tenth_db is None else
                      text("analyzer.pane.setup.rtl_gain_value", value=gain.cached_tenth_db / 10))
            gain_text = text("analyzer.pane.setup.rtl_gain_frame",
                             requested=requested, cached=cached)
        return text("analyzer.independent.rtl_actual",
                    center=f"{frame.center_frequency_hz / 1e6:g}",
                    rate=f"{frame.sample_rate_hz / 1e6:g}", gain=gain_text)

    def _refresh(self, _selected_slot: int | None = None) -> None:
        if self._terminal_released:
            return
        self._advance_rf_change()
        for control, key in ((self.start_selected, "analyzer.independent.start_slot"),
                             (self.stop_selected, "analyzer.independent.stop_slot")):
            label = text(key, number=self.board.selected_slot)
            if control.text() != label:
                control.setText(label)
        waiting: list[Future[Any]] = []
        for future in self._futures:
            if future.done():
                if future.exception() is not None:
                    self._error_key = "analyzer.independent.operation_failed"
            else:
                waiting.append(future)
        self._futures = waiting
        snapshots = {item.physical_stream_resource_id: item for item in self.handle.pump.snapshot()}
        states = {resource_id: item.phase for resource_id, item in snapshots.items()}
        selected = self._selected_resource()
        selected_phase = None if selected is None else states[selected[1]]
        startable = (() if self._rf_phase is not None else tuple(
            item for item in self.handle.pump.startable_resource_ids() if item != self._rf_fault_resource))
        self.start_selected.setEnabled(selected is not None and selected[1] in startable)
        self.stop_selected.setEnabled(selected_phase is not None and selected_phase is not PanePumpPhase.STOPPED)
        self.start_all.setEnabled(bool(startable))
        self.stop_all.setEnabled(any(phase is not PanePumpPhase.STOPPED for phase in states.values()))
        self.close_layout.setEnabled(self._close_layout is not None and self.handle.can_close())
        self.rf_shift.setEnabled(self._rf_eligible(self.board.selected_slot))
        running = sum(phase is PanePumpPhase.RUNNING for phase in states.values())
        starting = sum(phase in {PanePumpPhase.STARTING, PanePumpPhase.STOPPING} for phase in states.values())
        failed = sum(phase is PanePumpPhase.STOP_REQUIRED for phase in states.values())
        stopped = sum(phase is PanePumpPhase.STOPPED for phase in states.values())
        summary = text("analyzer.independent.summary", running=running, starting=starting,
                       failed=failed, stopped=stopped)
        if self._rf_phase is not None:
            summary += " · " + text("analyzer.rf.phase." + self._rf_phase)
        if self.status.text() != summary:
            self.status.setText(summary)
        scope = text("analyzer.independent.timing.scope")
        failures: list[str] = []
        for slot in self.handle.layout.slots:
            if slot.request is not None:
                pane_id = slot.request.pane_id
                state = snapshots[self._pane_resources[pane_id]]
                explanation = scope
                if state.first_failure is not None:
                    first = pane_failure_text(state.first_failure)
                    key = ("first" if state.phase is PanePumpPhase.STOP_REQUIRED else "previous")
                    explanation += "\n\n" + text(f"analyzer.independent.failure.{key}", detail=first)
                    if state.cleanup_failure is not None:
                        explanation += "\n" + text("analyzer.independent.failure.cleanup",
                            detail=pane_failure_text(state.cleanup_failure))
                    explanation += "\n" + text("analyzer.independent.failure.scope")
                    if state.phase is PanePumpPhase.STOP_REQUIRED:
                        detail = text("analyzer.independent.failure.pane", number=slot.number, detail=first)
                        if state.cleanup_failure is not None:
                            detail += " " + text("analyzer.independent.failure.cleanup",
                                detail=pane_failure_text(state.cleanup_failure))
                        failures.append(detail)
                pane = self.board.pane(slot.number)
                bundle = None if pane is None else pane.last_bundle
                frame = None if bundle is None else bundle.spectrum
                detail_text = self._timing_text(pane_id, state.phase,
                                                has_retained_frame=frame is not None)
                accepted_serial = self.board.accepted_activation_serial(slot.number)
                if pane_id in self._rtl_pane_ids:
                    binding = self.handle.preparer.bindings[pane_id]
                    actual = self._rtl_actual_readout(
                        bundle, state, binding, accepted_serial)
                    detail_text += " · " + actual
                    explanation += "\n\n" + actual + "\n" + text("analyzer.independent.rtl_scope")
                    context = self.handle.rf_context
                    if context is not None:
                        choice = next((choice for source, choice, _revision in context.selections
                                       if source == binding.source_id), None)
                        if choice is not None and choice.binding.rtl_session_route is not None:
                            explanation += "\n" + text("analyzer.pane.setup.rtl_session_scope")
                activation = state.activation
                if (activation is not None and activation.planned_control_gap is not None
                        and activation.planned_control_gap.reason is PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE):
                    self._rf_boundaries[state.physical_stream_resource_id] = activation
                boundary = self._rf_boundaries.get(state.physical_stream_resource_id)
                if boundary is not None:
                    elapsed = boundary.host_control_elapsed_s
                    explanation += "\n\n" + text("analyzer.rf.boundary", serial=boundary.host_activation_serial,
                        elapsed="—" if elapsed is None else f"{elapsed:.3f}",
                        epoch="—" if bundle is None or bundle.acquisition_epoch is None else bundle.acquisition_epoch)
                if isinstance(frame, SweepLineFrame) and frame.instrument is not None:
                    observation = frame.instrument.settings
                    actual_rbw = None if observation is None else observation.actual_rbw_hz
                    detail_text += " · " + text("analyzer.independent.tinysa.rbw_actual",
                        value="—" if actual_rbw is None else f"{actual_rbw / 1000:g}")
                    explanation += "\n\n" + tinysa_settings_readout(frame)
                    explanation += "\n" + text("analyzer.pane.setup.preview_tinysa_scope")
                self.board.set_pane_timing(slot.number, self._timing_summary_text(
                    slot.number, pane_id, state, has_retained_frame=frame is not None,
                    details=detail_text), explanation)
        detail = ("" if self._error_key is None else text(self._error_key, pane=self._error_pane or ""))
        if failures:
            detail = "\n".join(failures)
        elif (self._error_key == "analyzer.independent.operation_failed"
                and not waiting and any(item.first_failure is not None for item in snapshots.values())
                and all(phase in {PanePumpPhase.RUNNING, PanePumpPhase.STOPPED} for phase in states.values())):
            # Successful explicit cleanup leaves history in the pane tooltip,
            # not an active error banner from the already completed failed Future.
            self._error_key = None
            detail = ""
        if self.error.text() != detail:
            self.error.setText(detail)
        self.error.setVisible(bool(detail))

    def set_theme(self, theme: ThemeId) -> None:
        self.setStyleSheet(stylesheet_for_theme(theme))
        colors = tokens_for_theme(theme).colors
        self.board_scroll.verticalScrollBar().setStyleSheet(f"""
QScrollBar:vertical {{ background: {colors.panel}; width: 12px; border: 0; margin: 0; }}
QScrollBar::handle:vertical {{ background: {colors.border}; min-height: 24px; border-radius: 6px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: {colors.panel}; }}
""")
        self.board.set_theme(theme)

    def set_locale(self) -> None:
        self.title.setText(text("analyzer.independent.title"))
        for control, key in (
            (self.start_selected, "analyzer.independent.start_selected"),
            (self.start_all, "analyzer.independent.start_all"),
            (self.stop_selected, "analyzer.independent.stop_selected"),
            (self.stop_all, "analyzer.independent.stop_all"),
            (self.close_layout, "analyzer.pane.setup.close_layout"),
            (self.rf_shift, "analyzer.rf.shift"),
        ):
            control.setText(text(key))
            control.setAccessibleName(text(key))
            control.setToolTip(text(key))
        self.board.set_locale()
        self._refresh()

    def release_presentation_after_shutdown(self) -> None:
        if self._terminal_released:
            return
        if not self.handle.shutdown_complete:
            raise RuntimeError("independent pane owner has not confirmed terminal shutdown")
        self._state_timer.stop()
        self.delivery.stop()
        for slot in self.handle.layout.slots:
            if slot.request is not None:
                pane = self.board.pane(slot.number)
                if pane is not None:
                    pane.spectrum_scene.stop_delivery_custody()
        self.board.release_presentation_after_shutdown()
        self._terminal_released = True

    def closeEvent(self, event) -> None:
        if not self.handle.can_close():
            event.ignore()
            return
        super().closeEvent(event)


__all__ = ["IndependentPaneSessionV2"]

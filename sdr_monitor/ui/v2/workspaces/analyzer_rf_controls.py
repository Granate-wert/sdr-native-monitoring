"""Default Analyzer RF intent and all-view receipt; no SDK/executor/owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QSignalBlocker
from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QPushButton, QSpinBox

from sdr_monitor.application.analyzer_rf_change import AnalyzerRfApplyReceipt, AnalyzerRfShiftProposal
from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.live import LiveConfiguration
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest

from ..i18n import text
from ..view_models.analyzer_rf_controller import AnalyzerRfBoundary
from .rf_shift_dialog import RfImpactDialog, RfShiftEntryDialog

if TYPE_CHECKING:
    from .analyzer import AnalyzerWorkspaceV2
    from .analyzer_pane import AnalyzerPaneViewV2


@dataclass(frozen=True, slots=True)
class _RfAnchor:
    choice_id: int
    revision: int
    mode: str
    pane_number: int
    measurement: tuple[object, ...]


def default_rf_preview_text(proposal: AnalyzerRfShiftProposal, numbers: tuple[int, ...]) -> str:
    """Bounded scalar profile summary; never stringify correction arrays/SDK data."""
    old, new = proposal.expected.request, proposal.request

    def bounds(request):
        if isinstance(request, LiveConfiguration):
            return request.center_hz - request.sample_rate_hz / 2, request.center_hz + request.sample_rate_hz / 2
        if isinstance(request, HackrfLiveRequest):
            return (request.center_frequency_hz - request.sample_rate_hz / 2,
                    request.center_frequency_hz + request.sample_rate_hz / 2)
        return request.start_hz, request.stop_hz

    old_start, old_stop = bounds(old)
    start, stop = bounds(new)
    lines = [proposal.expected.source.label,
             text("analyzer.rf.mode", value=text("analyzer.mode." + proposal.expected.state.mode.value)),
             text("analyzer.rf.delta", requested=f"{proposal.requested_shift_hz / 1e6:.6f}",
                  effective=f"{proposal.effective_shift_hz / 1e6:.6f}", quantum=f"{proposal.quantum_hz:g}"),
             text("analyzer.rf.impact", panes=", ".join(str(number) for number in numbers)),
             text("analyzer.rf.restart" if proposal.expected.state.phase is AnalyzerPhase.RUNNING
                  else "analyzer.rf.armed")]
    if proposal.expected.route_rf_capabilities is not None:
        lines.extend(("", text("analyzer.rf.route_scope")))
    for number in numbers:
        lines.append(text("analyzer.rf.pane_range", number=number,
                          old_start=f"{old_start / 1e6:.6f}", old_stop=f"{old_stop / 1e6:.6f}",
                          start=f"{start / 1e6:.6f}", stop=f"{stop / 1e6:.6f}"))
    for name in ("sample_rate_hz", "analog_bandwidth_hz", "baseband_filter_hz", "fft_size", "hop_size",
                 "analysis_bins_per_usable_window", "usable_window_hz", "overlap_hz", "gain_db",
                 "lna_gain_db", "vga_gain_db", "lna_gain", "vga_gain", "window", "detector", "averaging_frames",
                 "overlap_ratio", "snapshot_rate_hz", "acquisition_buffer_samples", "segment_frame_timeout_ms",
                 "line_snapshot_rate_hz", "preview_rate_hz", "speed_profile", "points", "timeout_s",
                 "repeat_until_stop", "interval_s", "input_mode", "readback_settings", "backend",
                 "slot_count", "ready_capacity", "dsp_output_capacity", "presentation_capacity",
                 "persistence_enabled", "persistence_mode", "persistence_power_min_db", "persistence_power_max_db",
                 "persistence_power_bins", "persistence_window_frames", "persistence_half_life_s",
                 "persistence_snapshot_rate_hz", "profile_id"):
        for owner in (new, proposal.expected.applied_live):
            value = getattr(owner, name, None)
            if value is not None:
                lines.append(f"{name}: {getattr(value, 'value', value)}")
                break
    if isinstance(new, TinySaSweepRequest):
        for name in ("accuracy", "rbw_mode", "rbw_hz", "sweep_time_ms", "spur_removal", "lna",
                     "attenuation_mode", "attenuation_db", "repeat_count", "nspeedup", "wspeedup"):
            value = getattr(new.settings, name)
            if value is not None:
                lines.append(f"settings.{name}: {getattr(value, 'value', value)}")
    lines.extend(("", text("analyzer.rf.scope")))
    return "\n".join(lines)


class AnalyzerRfControls(QObject):
    """Wire both canvases/numeric entry to the existing default RF controller."""

    def __init__(self, workspace: AnalyzerWorkspaceV2) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.controller = workspace.model.rf_controller
        self.dialog: QDialog | None = None
        self._anchor: _RfAnchor | None = None
        self._installed: list[AnalyzerPaneViewV2] = []
        self._retired = False
        self._boundary: AnalyzerRfBoundary | None = None
        self.button = QPushButton(workspace)
        self.button.setObjectName("defaultRfShiftV2")
        self.button.setProperty("ui2Role", "utility-action")
        self.button.clicked.connect(self.enter)
        self.disarm = QPushButton(workspace)
        self.disarm.setObjectName("defaultRfDisarmV2")
        self.disarm.setProperty("ui2Role", "utility-action")
        self.disarm.clicked.connect(workspace.model.stop)
        index = workspace._commands.indexOf(workspace.settings)
        workspace._commands.insertWidget(index, self.button)
        workspace._commands.insertWidget(index + 1, self.disarm)
        if self.controller is not None:
            self.controller.preview_ready.connect(self._show_preview)
            self.controller.apply_ready.connect(self._install_receipt)
            self.controller.cancelled.connect(self._close_dialog)
            self.controller.boundary_ready.connect(self._on_boundary)
        self.set_locale()

    def install(self, pane: AnalyzerPaneViewV2) -> None:
        if pane in self._installed:
            return
        pane.set_rf_shift_provider(lambda: self.raw_anchor(pane) if self.eligible(pane) else None)
        pane.rf_shift_requested.connect(lambda delta, anchor: self.request(pane, delta, anchor))
        self._installed.append(pane)

    def raw_anchor(self, pane: AnalyzerPaneViewV2) -> _RfAnchor | None:
        state = self.workspace.model.state
        selection, identity = state.source_selection, pane._last_identity
        if (self._retired or self.workspace._independent_session is not None
                or selection is None or selection.selected is None or selection.release_pending
                or state.bundle is None or state.bundle.identity is None or identity is None or identity.acquisition_epoch is None
                or pane._last_mode is not state.mode or pane.pane_number > self.workspace.shared_views.currentData()):
            return None
        fields = ("source_id", "receiver_id", "session_id", "acquisition_epoch", "config_generation",
                  "clock_domain", "unit")
        measurement = tuple(getattr(identity, name) for name in fields)
        if measurement != tuple(getattr(state.bundle.identity, name) for name in fields):
            return None  # This parked/retired canvas cannot authorize current RF.
        return _RfAnchor(id(selection.selected), selection.revision, state.mode.value, pane.pane_number, measurement)

    def eligible(self, pane: AnalyzerPaneViewV2) -> bool:
        state = self.workspace.model.state
        setup = self.workspace._independent_setup
        return bool(self.controller is not None and not self._retired
                    and not (state.live.busy or state.starting or state.stopping or state.configuration_pending
                             or state.stop_required or state.rf_control_pending or state.rf_armed or state.rf_fault)
                    and not (setup is not None and setup.blocks_single_source)
                    and not self.workspace.drawer.dirty and not self.workspace.hackrf_bar.dirty
                    and self.raw_anchor(pane) is not None)

    def enter(self) -> None:
        pane = self.workspace._selected_pane()
        controller, anchor = self.controller, self.raw_anchor(pane)
        if controller is None or anchor is None or not self.eligible(pane) or not controller.begin_entry(anchor):
            return
        self._anchor = anchor
        dialog = RfShiftEntryDialog(self.workspace)
        self.dialog = dialog

        def finished(result: int) -> None:
            if self.dialog is not dialog:
                return
            delta = dialog.offset.value() * 1e6
            self.dialog = None
            dialog.deleteLater()
            if result != QDialog.DialogCode.Accepted or anchor != self.raw_anchor(pane):
                controller.cancel()
            else:
                controller.preview(delta, anchor)

        dialog.finished.connect(finished)
        dialog.open()

    def request(self, pane: AnalyzerPaneViewV2, delta: float, anchor: object) -> None:
        if self.controller is None or not self.eligible(pane) or anchor != self.raw_anchor(pane):
            return
        self._anchor = anchor
        self.controller.preview(delta, anchor)

    def _matches(self, proposal: AnalyzerRfShiftProposal, anchor: object) -> bool:
        if not isinstance(anchor, _RfAnchor) or not 1 <= anchor.pane_number <= len(self.workspace._panes):
            return False
        context = proposal.expected
        pane = self.workspace._panes[anchor.pane_number - 1]
        state = self.workspace.model.state
        return (anchor == self.raw_anchor(pane) and anchor.choice_id == id(context.source)
                and anchor.revision == context.selection_revision and anchor.mode == context.state.mode.value
                and state.running == (context.state.phase is AnalyzerPhase.RUNNING)
                and (context.state.phase is AnalyzerPhase.IDLE or context.acquisition_epoch == anchor.measurement[3]))

    def _show_preview(self, proposal: AnalyzerRfShiftProposal, anchor: object) -> None:
        controller = self.controller
        if controller is None:
            return
        if self._retired or not isinstance(anchor, _RfAnchor) or not self._matches(proposal, anchor):
            controller.cancel()
            return
        self._anchor = anchor
        numbers = tuple(range(1, self.workspace.shared_views.currentData() + 1))
        dialog = RfImpactDialog(default_rf_preview_text(proposal, numbers),
                                restart_required=proposal.expected.state.phase is AnalyzerPhase.RUNNING,
                                parent=self.workspace)
        self.dialog = dialog

        def finished(result: int) -> None:
            if self.dialog is not dialog:
                return
            self.dialog = None
            dialog.deleteLater()
            if result != QDialog.DialogCode.Accepted or not self._matches(proposal, anchor):
                controller.cancel()
            else:
                controller.approve(proposal)

        dialog.finished.connect(finished)
        dialog.open()

    @staticmethod
    def _set_frequency(field: QDoubleSpinBox | QSpinBox, hz: float) -> None:
        value = hz / 1e6
        if not field.minimum() <= value <= field.maximum():
            raise ValueError("RF receipt cannot be silently clamped by a frequency editor")
        with QSignalBlocker(field):
            if isinstance(field, QSpinBox):
                field.setValue(round(value))
            else:
                field.setValue(value)
        tolerance = .5 if isinstance(field, QSpinBox) else .5 * 10 ** -field.decimals()
        if abs(field.value() - value) > tolerance + 1e-9:
            raise ValueError("RF receipt does not match the displayed frequency")

    def _install_receipt(self, receipt: AnalyzerRfApplyReceipt) -> None:
        controller, workspace = self.controller, self.workspace
        if controller is None:
            return
        try:
            state, context = workspace.model.state, receipt.armed_context
            selection = state.source_selection
            if (self._retired or selection is None or selection.selected is not context.source
                    or selection.revision != context.selection_revision or state.mode.value != context.state.mode.value):
                raise ValueError("RF presentation source/mode was retired")
            request = receipt.proposal.request
            if isinstance(request, ContinuousSweepPlanRequest):
                self._set_frequency(workspace.start_frequency, request.start_hz)
                self._set_frequency(workspace.stop_frequency, request.stop_hz)
            elif isinstance(request, HackrfSweepRequest):
                self._set_frequency(workspace.hackrf_sweep_bar.start_mhz, request.start_hz)
                self._set_frequency(workspace.hackrf_sweep_bar.stop_mhz, request.stop_hz)
            elif isinstance(request, TinySaSweepRequest):
                self._set_frequency(workspace.tinysa_bar.start, request.start_hz)
                self._set_frequency(workspace.tinysa_bar.stop, request.stop_hz)
            elif isinstance(context.request, LiveConfiguration):
                self._set_frequency(workspace.frequency_bar.center, context.request.center_hz)
            elif isinstance(context.request, HackrfLiveRequest):
                self._set_frequency(workspace.hackrf_bar.center, context.request.center_frequency_hz)
            else:
                raise TypeError("RF receipt has no supported full profile")
            workspace.model.accept_rf_views(receipt)
            workspace._range_anchors.clear()
            for pane in workspace._panes:  # Includes parked one-source views.
                pane.cancel_rf_drag()
                pane.clear_shared_view()
            workspace._render(workspace.model.state)
        except Exception:
            controller.reject_views()
        else:
            controller.acknowledge_views(receipt)

    def _on_boundary(self, boundary: AnalyzerRfBoundary) -> None:
        self._boundary = boundary
        self.refresh()

    def refresh(self) -> None:
        self.button.setEnabled(self.eligible(self.workspace._selected_pane()))
        armed = self.controller is not None and self.controller.armed
        self.disarm.setVisible(armed)
        self.disarm.setEnabled(armed)
        anchor = self._anchor
        if (self.dialog is not None and anchor is not None
                and anchor != self.raw_anchor(self.workspace._panes[anchor.pane_number - 1])):
            self.cancel()
        detail = text("analyzer.rf.entry_help")
        boundary = self._boundary
        if boundary is not None:
            before, after = boundary.before, boundary.after
            detail += "\n" + text("analyzer.rf.default_boundary",
                old_operation=before.state.operation_id, operation=after.state.operation_id,
                old_epoch=before.acquisition_epoch if before.acquisition_epoch is not None else "—",
                epoch=after.acquisition_epoch if after.acquisition_epoch is not None else "—",
                elapsed="—" if boundary.host_control_elapsed_s is None else f"{boundary.host_control_elapsed_s:.6f}")
        if self.button.toolTip() != detail:
            self.button.setToolTip(detail)
            self.button.setAccessibleDescription(detail)

    def _close_dialog(self) -> None:
        dialog, self.dialog = self.dialog, None
        if dialog is not None:
            dialog.reject()
            dialog.deleteLater()

    def cancel(self) -> None:
        if self._retired:
            return
        self._close_dialog()
        for pane in self._installed:
            pane.cancel_rf_drag()
        if self.controller is not None and self.controller.pending:
            self.controller.cancel()

    def set_locale(self) -> None:
        self.cancel()
        self.button.setText(text("analyzer.rf.shift"))
        self.button.setAccessibleName(self.button.text())
        self.disarm.setText(text("analyzer.rf.disarm"))
        self.disarm.setAccessibleName(self.disarm.text())
        self.disarm.setToolTip(text("analyzer.rf.disarm_help"))
        self.refresh()

    def dispose(self) -> None:
        if self._retired:
            return
        self.cancel()
        self._retired = True
        for pane in self._installed:
            pane.set_rf_shift_provider(None)
        if self.controller is not None:
            for signal, slot in ((self.controller.preview_ready, self._show_preview),
                                 (self.controller.apply_ready, self._install_receipt),
                                 (self.controller.cancelled, self._close_dialog),
                                 (self.controller.boundary_ready, self._on_boundary)):
                signal.disconnect(slot)


__all__ = ["AnalyzerRfControls", "default_rf_preview_text"]

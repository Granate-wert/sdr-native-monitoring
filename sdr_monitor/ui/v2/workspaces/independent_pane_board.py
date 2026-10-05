"""Independent 1–4-slot Analyzer canvas for an already admitted pane plan.

This widget owns no source, capture or scheduler. Its Empty slot has no graph
and cannot accept a delivery. A caller must prepare publications off the GUI
thread and present them here through the exact PaneDeliveryPreparer binding.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
from time import monotonic

from PySide6.QtCore import QSettings, Signal, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from sdr_monitor.domain.analyzer import AnalyzerPublicationKind
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer, PanePresentationBinding, PreparedPaneDelivery
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef, PaneDeliveryStage

from ..design import ThemeId, stylesheet_for_theme
from ..i18n import current_locale, text
from ..spectrum.projection import SpectrumProjector
from .analyzer_display_controls import AnalyzerDisplayControls
from .analyzer_pane import AnalyzerPaneViewV2


@dataclass(frozen=True, slots=True)
class _PairedVisualContext:
    run_serial: int
    activation_serial: int
    session_id: str
    acquisition_epoch: int
    mode: CaptureMeasurementMode
    synchronization_epoch: int | None
    shared_gaps: int | None
    sweep_line: tuple[int, int] | None


class IndependentPaneBoardV2(QWidget):
    """Route only exact resource-scoped deliveries to distinct graph pairs."""

    selected_slot_changed = Signal(int)
    rf_shift_requested = Signal(int, float, object)

    def __init__(self, preparer: PaneDeliveryPreparer, *,
                 projector_factory: Callable[[], SpectrumProjector] | None = None,
                 source_labels: Mapping[str, str] | None = None,
                 monotonic_clock: Callable[[], float] = monotonic,
                 stage_callback: Callable[[PaneDeliveryObligationRef, PaneDeliveryStage], object] | None = None,
                 settings: QSettings | None = None,
                 parent: QWidget | None = None) -> None:
        if not isinstance(preparer, PaneDeliveryPreparer):
            raise TypeError("independent pane board requires a qualified presentation plan")
        if not callable(monotonic_clock):
            raise TypeError("pane presentation clock must be callable")
        super().__init__(parent)
        self._preparer = preparer
        self._monotonic_clock = monotonic_clock
        self._stage_callback = stage_callback
        self._installed_bindings = dict(preparer.bindings)
        self._paired_resources = preparer.paired_resource_ids
        self._retired_bindings: dict[str, PanePresentationBinding] = {}
        self._rf_control_available: Callable[[int], bool] | None = None
        self._source_labels = {} if source_labels is None else dict(source_labels)
        self._panes: dict[int, AnalyzerPaneViewV2] = {}
        self._headers: dict[int, QPushButton] = {}
        self._captions: dict[int, QLabel] = {}
        self._empty_slots: dict[int, QLabel] = {}
        self._timing_labels: dict[int, QLabel] = {}
        self._display_buttons: dict[int, QPushButton] = {}
        self._display_overlays: dict[int, AnalyzerDisplayControls] = {}
        self._range_anchors: dict[int, tuple[object, ...]] = {}
        self._last_order: dict[int, tuple[int, int, int, int, int]] = {}
        self._last_run_serial: dict[int, int] = {}
        self._plot_updated_at: dict[int, float] = {}
        self._paired_visual_context: dict[str, _PairedVisualContext] = {}
        self._paired_awaiting_activation: set[str] = set()
        schedule = preparer.layout.schedule
        resource_job_counts = ({} if schedule is None else
                               {resource.physical_stream_resource_id: len(resource.jobs)
                                for resource in schedule.resources})
        self._time_sliced_pane_ids = (set() if schedule is None else {
            estimate.pane_id for estimate in schedule.pane_revisits
            # A job may fan out to shared subscribers while its physical RX
            # still alternates between multiple jobs. Visit identity follows
            # the resource schedule, not one subscription's binding mode.
            if resource_job_counts[estimate.physical_stream_resource_id] > 1
        })
        self._selected_slot = 1
        self._terminal_released = False
        projector_ids: set[int] = set()
        self.setProperty("ui2Root", True)
        self.setObjectName("independentPaneBoardV2")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._grid.setVerticalSpacing(2)
        self._cells: dict[int, QFrame] = {}
        self._stacked_layout = False
        self._layout_ready = False
        slots = preparer.layout.slots
        self._slot_count = len(slots)
        compact = len(slots) >= 3
        for slot in slots:
            cell = QFrame(self)
            self._cells[slot.number] = cell
            cell.setProperty("ui2Role", "panel")
            cell.setObjectName(f"independentPaneCell{slot.number}")
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(4, 2, 4, 2)
            cell_layout.setSpacing(2)
            header = QHBoxLayout()
            select = QPushButton(str(slot.number), cell)
            select.setProperty("ui2Role", "utility-action")
            select.setProperty("ui2Selected", slot.number == self._selected_slot)
            select.setCheckable(True)
            select.setChecked(slot.number == self._selected_slot)
            select.setFixedWidth(34)
            select.clicked.connect(lambda checked=False, number=slot.number: self.select_slot(number))
            header.addWidget(select)
            self._headers[slot.number] = select
            if slot.request is None:
                header.addStretch(1)
                caption = QLabel(text("analyzer.pane.empty"), cell)
                caption.setObjectName(f"independentPaneEmpty{slot.number}")
                caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
                caption.setProperty("ui2Role", "secondary")
                cell_layout.addLayout(header)
                cell_layout.addWidget(caption, 1)
                self._empty_slots[slot.number] = caption
            else:
                binding = preparer.bindings[slot.request.pane_id]
                caption = QLabel(cell)
                caption.setTextFormat(Qt.TextFormat.PlainText)
                caption.setProperty("ui2Role", "secondary")
                caption.setMinimumWidth(0)
                caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                caption.setText(self._binding_label(binding, self._source_labels.get(binding.source_id)))
                caption.setToolTip(self._binding_label(binding))
                self._captions[slot.number] = caption
                header.addWidget(caption, 1)
                cell_layout.addLayout(header)
                projector = None if projector_factory is None else projector_factory()
                if projector is not None:
                    if not isinstance(projector, SpectrumProjector) or id(projector) in projector_ids:
                        raise ValueError("each occupied resource pane needs its own projection lane")
                    projector_ids.add(id(projector))
                pane = AnalyzerPaneViewV2(
                    binding.mode, pane_number=slot.number, projector=projector,
                    settings=settings,
                    settings_prefix=f"ui_v2/analyzer/resource_pane{slot.number}/v1",
                    parent=cell)
                pane.spectrum_scene.set_delivery_stage_callback(self._stage_callback)
                pane.waterfall_pane.set_delivery_stage_callback(self._stage_callback)
                pane._delivery_stage_callback = self._stage_callback
                pane.set_compact_grid_geometry(compact)
                pane.set_selected(slot.number == self._selected_slot)
                pane.set_rf_shift_provider(self._rf_provider_for(slot.number))
                pane.rf_shift_requested.connect(
                    lambda delta, anchor, number=slot.number: self._request_rf_shift(number, delta, anchor))
                overlay = AnalyzerDisplayControls(
                    pane.spectrum_scene.take_display_controls(),
                    pane.waterfall_pane.take_display_controls(), self)
                overlay.close_requested.connect(overlay.hide)
                overlay.viewport_range_requested.connect(
                    lambda start, stop, number=slot.number: self._set_viewport_range(number, start, stop))
                self._display_overlays[slot.number] = overlay
                display = QPushButton(text("analyzer.display"), cell)
                display.setProperty("ui2Role", "utility-action")
                display.clicked.connect(
                    lambda checked=False, number=slot.number: self._toggle_display(number))
                header.addWidget(display)
                self._display_buttons[slot.number] = display
                timing = QLabel(cell)
                timing.setObjectName(f"independentPaneTiming{slot.number}")
                timing.setProperty("ui2Role", "secondary")
                timing.setTextFormat(Qt.TextFormat.PlainText)
                timing.setMinimumWidth(0)
                timing.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                timing.setWordWrap(False)
                timing.setFixedHeight(timing.fontMetrics().lineSpacing() * 2 + 2)
                timing.hide()
                cell_layout.addWidget(timing)
                self._timing_labels[slot.number] = timing
                cell_layout.addWidget(pane, 1)
                self._panes[slot.number] = pane
            index = slot.number - 1
            if len(slots) == 1:
                self._grid.addWidget(cell, 0, 0, 1, 2)
            elif len(slots) == 2:
                self._grid.addWidget(cell, 0, index)
            elif len(slots) == 3 and index == 2:
                self._grid.addWidget(cell, 1, 0, 1, 2)
            else:
                self._grid.addWidget(cell, index // 2, index % 2)
        self._grid.setRowStretch(0, 1)
        self._grid.setRowStretch(1, 1 if compact else 0)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        self._layout_ready = True
        self._reflow_for_width()
        self.set_theme(ThemeId.DARK)
        self.set_locale()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._layout_ready:
            self._reflow_for_width()
            self._grid.activate()
            for number, overlay in self._display_overlays.items():
                self._position_overlay(number, overlay)

    @property
    def stacked_layout(self) -> bool:
        """True when the same four slots are shown as a scrollable column."""
        return self._stacked_layout

    def _reflow_for_width(self) -> None:
        # Below this width two graph pairs would clip their host timing/readout.
        # Reposition existing cells only: no capture, graph or presentation reset.
        stacked = self._slot_count > 1 and self.width() < 1200
        if stacked == self._stacked_layout:
            return
        for cell in self._cells.values():
            self._grid.removeWidget(cell)
        for row in range(max(4, self._slot_count)):
            self._grid.setRowStretch(row, 0)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 0 if stacked else 1)
        for index, cell in enumerate(self._cells.values()):
            if stacked:
                self._grid.addWidget(cell, index, 0, 1, 2)
                self._grid.setRowStretch(index, 1)
            elif self._slot_count == 2:
                self._grid.addWidget(cell, 0, index)
            elif self._slot_count == 3 and index == 2:
                self._grid.addWidget(cell, 1, 0, 1, 2)
            else:
                self._grid.addWidget(cell, index // 2, index % 2)
        if not stacked:
            self._grid.setRowStretch(0, 1)
            self._grid.setRowStretch(1, 1 if self._slot_count >= 3 else 0)
        self._stacked_layout = stacked
        self.updateGeometry()

    def _position_overlay(self, number: int, overlay: AnalyzerDisplayControls) -> None:
        cell = self._cells[number]
        width = min(800, max(180, min(self.width(), cell.width()) - 16))
        height = min(510, max(180, cell.height() - 40))
        x = max(8, min(cell.x() + 8, self.width() - width - 8))
        y = max(8, min(cell.y() + 32, self.height() - height - 8))
        overlay.setGeometry(x, y, width, height)

    def _toggle_display(self, number: int) -> None:
        overlay = self._display_overlays[number]
        visible = overlay.isVisible()
        for other in self._display_overlays.values():
            other.hide()
        self._range_anchors.clear()
        if not visible:
            self._position_overlay(number, overlay)
            pane = self._panes[number]
            grid = pane.spectrum_scene.measurement_grid
            available = pane.last_bundle is not None and grid is not None and len(grid) >= 2
            overlay.set_range_available(available)
            if available:
                lower, upper = pane.spectrum_scene.view_box.viewRange()[0]
                overlay.set_viewport_range(float(lower), float(upper))
                anchor = self._view_anchor(number)
                if anchor is not None:
                    self._range_anchors[number] = anchor
            overlay.show()
            overlay.raise_()

    def _view_anchor(self, number: int) -> tuple[object, ...] | None:
        pane = self._panes[number]
        grid = pane.spectrum_scene.measurement_grid
        identity = pane._last_identity
        if grid is None or identity is None:
            return None
        return (id(grid), identity.source_id, identity.session_id,
                identity.receiver_id, identity.acquisition_epoch,
                identity.config_generation, identity.unit)

    def _set_viewport_range(self, number: int, start_hz: float, stop_hz: float) -> None:
        binding = next(item for item in self._preparer.bindings.values()
                       if item.slot_number == number)
        overlay = self._display_overlays[number]
        if not overlay.isVisible() or self._range_anchors.get(number) != self._view_anchor(number):
            overlay.set_range_error("analyzer.view_range.stale")
            return
        if (not math.isfinite(start_hz) or not math.isfinite(stop_hz)
                or start_hz >= stop_hz
                or start_hz < binding.crop.start_hz - 0.5
                or stop_hz > binding.crop.stop_hz + 0.5):
            overlay.set_range_error("analyzer.view_range.outside")
            return
        pane = self._panes[number]
        if pane.last_bundle is None:
            overlay.set_range_error("analyzer.view_range.unavailable")
            return
        pane.spectrum_scene.view_box.setXRange(start_hz, stop_hz, padding=0)
        overlay.set_range_error(None)

    @staticmethod
    def _binding_label(binding, source_label: str | None = None) -> str:
        endpoint = ("trace" if binding.measurement_mode is CaptureMeasurementMode.INSTRUMENT_TRACE else
                    binding.receiver_selection.name if binding.receiver_selection in {
                        ReceiverChainSelection.RX1, ReceiverChainSelection.RX2} else
                    binding.receiver_endpoint_id)
        return (f"{source_label or binding.source_id} / {endpoint} · "
                f"{binding.crop.start_hz / 1e6:g}–{binding.crop.stop_hz / 1e6:g} MHz · "
                f"{binding.mode.value.upper()} · {binding.unit}")

    def set_rf_control_available(self, provider: Callable[[int], bool] | None) -> None:
        """An inert eligibility callback; the board never holds an SDR service."""
        self._rf_control_available = provider

    def rf_anchor(self, number: int) -> tuple[object, ...] | None:
        pane = self._panes.get(number)
        binding = next((item for item in self._installed_bindings.values()
                        if item.slot_number == number), None)
        identity = None if pane is None else pane._last_identity
        if (self._terminal_released or binding is None or identity is None
                or identity.source_id is None or identity.acquisition_epoch is None
                or identity.unit is None
                or identity.config_generation is None
                and binding.measurement_mode is not CaptureMeasurementMode.SWEEP):
            return None
        # SDR Sweep has per-segment generations, not one configuration
        # generation. Its exact installed binding and producer epoch anchor
        # the RF intent; absent metadata remains None, never an invented zero.
        # RTBW and instrument traces keep their existing generation guard.
        # Grid allocation, sequence and progressive revision may change every
        # frame. They must not cancel a valid held gesture in the same epoch.
        return (id(binding), binding.mode, identity.source_id, identity.receiver_id,
                identity.session_id, identity.acquisition_epoch,
                identity.config_generation, identity.clock_domain, identity.unit)

    def _gesture_anchor(self, number: int) -> tuple[object, ...] | None:
        if self._rf_control_available is None or not self._rf_control_available(number):
            return None
        return self.rf_anchor(number)

    def _rf_provider_for(self, number: int) -> Callable[[], object | None]:
        def provider() -> object | None:
            return self._gesture_anchor(number)
        return provider

    def _request_rf_shift(self, number: int, delta: float, anchor: object) -> None:
        if anchor != self._gesture_anchor(number):
            return
        self.select_slot(number)
        self.rf_shift_requested.emit(number, delta, anchor)

    def refresh_resource_plan(self, resource_id: str) -> None:
        """Qt receipt before next Start; target captions/history only, peers intact."""
        if self._terminal_released:
            raise RuntimeError("terminal pane board cannot install a new RF plan")
        bindings = self._preparer.bindings
        if (bindings.keys() != self._installed_bindings.keys()
                or any(bindings[key] is not binding for key, binding in self._installed_bindings.items()
                       if binding.physical_stream_resource_id != resource_id)):
            raise ValueError("RF presentation receipt changed an independent peer")
        for key, binding in bindings.items():
            if binding.physical_stream_resource_id != resource_id:
                continue
            previous = self._installed_bindings[key]
            if (previous.slot_number, previous.source_id, previous.receiver_endpoint_id,
                    previous.receiver_selection) != (
                    binding.slot_number, binding.source_id, binding.receiver_endpoint_id,
                    binding.receiver_selection):
                raise ValueError("RF presentation receipt changed pane identity")
        for binding in bindings.values():
            if binding.physical_stream_resource_id != resource_id:
                continue
            number = binding.slot_number
            self._retired_bindings[binding.pane_id] = self._installed_bindings[binding.pane_id]
            self._installed_bindings[binding.pane_id] = binding
            caption = self._captions[number]
            caption.setText(self._binding_label(binding, self._source_labels.get(binding.source_id)))
            caption.setToolTip(self._binding_label(binding))
            self._display_overlays[number].hide()
            self._range_anchors.pop(number, None)
            self._last_order.pop(number, None)
            self._last_run_serial.pop(number, None)
            self._plot_updated_at.pop(number, None)
            self._panes[number].clear_shared_view()
            for view in (self._panes[number].spectrum_scene.view_box,
                         self._panes[number].waterfall_pane.view_box):
                view.cancel_rf_drag()
        if resource_id in self._paired_visual_context:
            # A value-equal old binding can still be queued after this receipt.
            # Keep its accepted context as a floor until a newer activation.
            self._paired_awaiting_activation.add(resource_id)
        schedule = self._preparer.layout.schedule
        assert schedule is not None
        sliced = {item.physical_stream_resource_id for item in schedule.resources if len(item.jobs) > 1}
        self._time_sliced_pane_ids = {
            item.pane_id for item in schedule.pane_revisits if item.physical_stream_resource_id in sliced}

    @property
    def empty_slots(self) -> tuple[int, ...]:
        return tuple(self._empty_slots)

    @property
    def selected_slot(self) -> int:
        return self._selected_slot

    def pane(self, slot_number: int) -> AnalyzerPaneViewV2 | None:
        return self._panes.get(slot_number)

    def accepted_activation_serial(self, slot_number: int) -> int | None:
        """The last GUI-accepted delivery, not a receiver Start receipt."""
        order = self._last_order.get(slot_number)
        return None if order is None else order[0]

    def plot_update_age_s(self, slot_number: int, *, now: float | None = None) -> float | None:
        """Monotonic age since this pane last accepted a GUI presentation packet."""
        if slot_number not in self._timing_labels:
            raise ValueError("plot age requires one active occupied pane")
        updated_at = self._plot_updated_at.get(slot_number)
        if (updated_at is None or not isinstance(updated_at, (int, float))
                or not math.isfinite(updated_at)):
            return None
        current = self._monotonic_clock() if now is None else now
        if not isinstance(current, (int, float)) or not math.isfinite(current):
            return None
        age = float(current) - updated_at
        return age if age >= 0 else None

    def set_pane_timing(self, slot_number: int, summary: str, explanation: str) -> None:
        """Show a compact, truthful host-timing row for one occupied pane."""
        if self._terminal_released or slot_number not in self._timing_labels:
            raise ValueError("timing requires one active occupied pane")
        self._reserve_timing_rows()
        label = self._timing_labels[slot_number]
        if label.text() != summary:
            label.setText(summary)
        if label.toolTip() != explanation:
            label.setToolTip(explanation)
            label.setAccessibleDescription(explanation)
        label.setVisible(bool(summary))

    def select_slot(self, number: int) -> None:
        if self._terminal_released or type(number) is not int or number not in self._headers:
            raise ValueError("selected pane slot is not in the current layout")
        if number == self._selected_slot:
            return
        self._selected_slot = number
        for index, button in self._headers.items():
            button.setProperty("ui2Selected", index == number)
            button.setChecked(index == number)
        for index, pane in self._panes.items():
            pane.set_selected(index == number)
        self.selected_slot_changed.emit(number)

    def set_theme(self, theme: ThemeId) -> None:
        self.setStyleSheet(stylesheet_for_theme(theme))
        for pane in self._panes.values():
            pane.set_theme(theme)
        self._reserve_timing_rows()

    def _reserve_timing_rows(self) -> None:
        for label in self._timing_labels.values():
            label.ensurePolished()
            height = label.fontMetrics().lineSpacing() * 2 + 4
            label.setMinimumHeight(height)
            label.setMaximumHeight(height)

    def set_locale(self) -> None:
        for number, button in self._headers.items():
            button.setAccessibleName(text("analyzer.pane.slot", number=number))
        for label in self._empty_slots.values():
            label.setText(text("analyzer.pane.empty"))
        for pane in self._panes.values():
            pane.set_badge_locale()
            pane.spectrum_scene.set_locale(current_locale())
            pane.waterfall_pane.set_locale(current_locale())
        for button in self._display_buttons.values():
            button.setText(text("analyzer.display"))
        for overlay in self._display_overlays.values():
            overlay.set_locale(current_locale())

    def apply_prepared(self, prepared: PreparedPaneDelivery) -> bool:
        """Return False for a stale/duplicate publication, never repaint it."""
        if self._terminal_released:
            return False
        if not isinstance(prepared, PreparedPaneDelivery):
            raise TypeError("independent pane board accepts only worker-prepared deliveries")
        binding = self._preparer.bindings.get(prepared.binding.pane_id)
        if binding is None:
            raise ValueError("prepared delivery belongs to another pane layout")
        if prepared.binding != binding:
            # A Qt delivery already dequeued before a stopped RF commit may
            # still carry the old binding. Never relabel it as the new plan.
            if prepared.binding in (self._installed_bindings.get(binding.pane_id),
                                     self._retired_bindings.get(binding.pane_id)):
                return False
            raise ValueError("prepared delivery belongs to another pane layout")
        if binding != self._installed_bindings.get(binding.pane_id):
            return False  # GUI receipt has not installed this plan yet.
        pane = self._panes[binding.slot_number]
        frame = prepared.bundle.spectrum
        epoch = prepared.bundle.acquisition_epoch
        if epoch is None:
            raise ValueError("pane publication lacks an acquisition epoch")
        terminal = prepared.bundle.publication_kind in {
            AnalyzerPublicationKind.SWEEP_COMPLETE, AnalyzerPublicationKind.SWEEP_GAP}
        revision = frame.revision if isinstance(frame, SweepProgressFrame) else 0
        order = (prepared.delivery.host_activation_serial, epoch, frame.sequence, int(terminal), revision)
        previous = self._last_order.get(binding.slot_number)
        if previous is not None and order <= previous:
            return False
        paired_capture = prepared.bundle.paired_capture
        paired_sweep = prepared.bundle.paired_sweep
        paired = paired_capture is not None or paired_sweep is not None
        resource_id = binding.physical_stream_resource_id
        if ((resource_id in self._paired_resources) != paired
                or not paired and resource_id in self._paired_visual_context
                or paired_capture is not None and binding.measurement_mode is not CaptureMeasurementMode.RTBW
                or paired_sweep is not None and binding.measurement_mode is not CaptureMeasurementMode.SWEEP):
            # Typed RX1/RX2 binding must agree with the admitted paired
            # payload, including the first delivery and after a plan receipt.
            # A lone logical RX1 never turns another resource into a pair.
            return False
        next_context: _PairedVisualContext | None = None
        clear_paired_resource = False
        if paired:
            session_id = prepared.bundle.session_id
            activation = prepared.delivery.host_activation_serial
            if (prepared.producer_source_id is None or not isinstance(session_id, str)
                    or not session_id or type(activation) is not int or activation < 0
                    or type(epoch) is not int or epoch < 0
                    or binding.receiver_selection not in {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}
                    or prepared.bundle.receiver_id != binding.receiver_selection.name):
                return False
            if paired_sweep is not None:
                request = paired_sweep.run.request
                expected = (request.pair.primary_source_id if binding.receiver_selection is ReceiverChainSelection.RX1
                            else request.pair.secondary_source_id)
                if (not paired_sweep.steps or request.resource_id != resource_id
                        or request.pair.device_id != binding.source_id
                        or request.pair.session_id != session_id
                        or paired_sweep.run.acquisition_epoch != epoch
                        or expected != prepared.producer_source_id):
                    return False
            next_context = _PairedVisualContext(
                prepared.delivery.host_run_serial, activation, session_id, epoch,
                binding.measurement_mode,
                None if paired_capture is None else paired_capture.synchronization_epoch,
                None if paired_sweep is None else sum(
                    step.primary.shared_input_gaps_before for step in paired_sweep.steps),
                None if paired_sweep is None else
                    (paired_sweep.primary.epoch, paired_sweep.primary.sequence))
            current = self._paired_visual_context.get(resource_id)
            if current is not None:
                if (next_context.run_serial < current.run_serial
                        or next_context.activation_serial < current.activation_serial):
                    return False
                same_activation = (next_context.run_serial == current.run_serial
                                   and next_context.activation_serial == current.activation_serial)
                if same_activation:
                    if (resource_id in self._paired_awaiting_activation
                            or next_context.session_id != current.session_id
                            or next_context.acquisition_epoch != current.acquisition_epoch
                            or next_context.mode is not current.mode):
                        return False
                    if paired_sweep is None:
                        next_sync = next_context.synchronization_epoch
                        current_sync = current.synchronization_epoch
                        if next_sync is None or current_sync is None or next_sync < current_sync:
                            return False
                        clear_paired_resource = next_sync > current_sync
                    else:
                        next_line, current_line = next_context.sweep_line, current.sweep_line
                        next_gaps, current_gaps = next_context.shared_gaps, current.shared_gaps
                        if (next_line is None or current_line is None
                                or next_gaps is None or current_gaps is None
                                or next_line < current_line
                                or next_line == current_line and next_gaps < current_gaps):
                            return False
                        # Native's gap prefix is cumulative within one Sweep
                        # line, then resets on the next tune line. A new line
                        # with zero gaps is ordinary progression, not a reset.
                        clear_paired_resource = (
                            next_line == current_line and next_gaps > current_gaps
                            or next_line > current_line and next_gaps > 0)
                else:
                    if (next_context.activation_serial <= current.activation_serial
                            or (next_context.run_serial == current.run_serial
                                and next_context.session_id != current.session_id)
                            or (next_context.session_id == current.session_id
                                and next_context.acquisition_epoch <= current.acquisition_epoch)):
                        return False
                    clear_paired_resource = True
            else:
                clear_paired_resource = True
            if clear_paired_resource:
                # A common input gap invalidates BOTH visible RX histories,
                # even if only the first new RX delivery has reached Qt.
                for sibling in self._installed_bindings.values():
                    if sibling.physical_stream_resource_id == resource_id:
                        self._panes[sibling.slot_number].clear_paired_synchronization_history()
                        self._last_order.pop(sibling.slot_number, None)
                        self._last_run_serial.pop(sibling.slot_number, None)
                        self._plot_updated_at.pop(sibling.slot_number, None)
                previous = None
        scheduled_visit_boundary = (
            previous is not None and binding.pane_id in self._time_sliced_pane_ids
            and prepared.delivery.host_activation_serial > previous[0]
            and prepared.delivery.host_run_serial == self._last_run_serial.get(binding.slot_number)
        )
        pane.apply_prepared_pane_delivery(prepared,
                                          scheduled_visit_boundary=scheduled_visit_boundary)
        self._last_order[binding.slot_number] = order
        self._last_run_serial[binding.slot_number] = prepared.delivery.host_run_serial
        try:
            accepted_at = self._monotonic_clock()
        except Exception:
            accepted_at = None
        if (isinstance(accepted_at, (int, float)) and math.isfinite(accepted_at)):
            self._plot_updated_at[binding.slot_number] = float(accepted_at)
        else:
            self._plot_updated_at.pop(binding.slot_number, None)
        if next_context is not None:
            self._paired_visual_context[binding.physical_stream_resource_id] = next_context
            self._paired_awaiting_activation.discard(binding.physical_stream_resource_id)
        return True

    def release_presentation_after_shutdown(self) -> None:
        """Call only after all resource owners and preparation workers join."""
        if self._terminal_released:
            return
        for pane in self._panes.values():
            pane.release_presentation_after_shutdown()
        self._range_anchors.clear()
        self._last_order.clear()
        self._last_run_serial.clear()
        self._plot_updated_at.clear()
        self._paired_visual_context.clear()
        self._paired_awaiting_activation.clear()
        self._terminal_released = True


__all__ = ["IndependentPaneBoardV2"]

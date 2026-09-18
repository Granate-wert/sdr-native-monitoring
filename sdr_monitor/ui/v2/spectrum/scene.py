"""One-ViewBox spectrum scene with bounded trace presentation and M1/M2."""

from __future__ import annotations

from collections.abc import Mapping
import sys

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..components import ContextPopover, EmptyChartOverlay, HeatLegend
from ..design import ThemeId, stylesheet_for_theme, tokens_for_theme
from ..i18n import UiLocale, text
from .axis import FrequencyAxis
from .sweep_position import SweepPositionOverlay
from .sweep_coverage_overlay import SweepCoverageOverlay
from .contracts import (
    BandMask,
    EnvelopeTrace,
    PreparedSpectrumFrame,
    SpectrumFrameView,
    SpectrumMarker,
    TraceKind,
    VerticalRangeMode,
    adapt_spectrum_frame,
    finite_value_extent,
    format_frequency_hz,
)
from .envelope import peak_preserving_envelope
from .projection import ProjectionRequest, SpectrumProjection, SpectrumProjector
from .persistence_contracts import (
    PersistenceRenderMode,
    adapt_persistence_density,
)
from .persistence_overlay import PersistenceOverlay, PersistenceOverlayMetrics

_TRACE_LABEL_KEYS: Mapping[TraceKind, str] = {
    TraceKind.CURRENT: "spectrum.trace.current",
    TraceKind.AVERAGE: "spectrum.trace.average",
    TraceKind.MAXIMUM: "spectrum.trace.maximum",
    TraceKind.MINIMUM: "spectrum.trace.minimum",
}
_PERSISTENCE_Z_VALUE = -20
_BAND_MASK_Z_VALUE = -10


def _measurement_signature(frame: object, view: SpectrumFrameView) -> tuple[object, ...]:
    """Accumulation identity excluding arrival sequence and partial revision."""
    identity = getattr(frame, "identity", None)
    return (
        getattr(identity, "source_id", None), getattr(identity, "receiver_id", None),
        getattr(identity, "acquisition_epoch", None), getattr(identity, "config_generation", None),
        getattr(identity, "session_id", None), getattr(identity, "accumulation_id", None),
        getattr(identity, "clock_domain", None),
        view.unit_label, int(view.frequencies_hz.size), view.frequencies_hz.dtype.str,
    )


class SpectrumScene(QWidget):
    """Render public frames without receiver, acquisition or DSP ownership."""

    marker_changed = Signal(object)
    vertical_range_changed = Signal(float, float, str)
    measurement_available_changed = Signal(bool)

    def __init__(self, *, theme: ThemeId = ThemeId.DARK, locale: UiLocale = UiLocale.RU,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._presentation_active = True
        self._locale = locale
        self._latest_view: SpectrumFrameView | None = None
        self._prepared_spectrum: PreparedSpectrumFrame | None = None
        self._projector: SpectrumProjector | None = None
        self._projection_owner = object()
        self._projection_generation = 0
        self._projection_key: tuple[object, ...] | None = None
        self._displayed_view: SpectrumFrameView | None = None
        self._displayed_extent: tuple[float, float] | None = None
        self._projection_error: str | None = None
        self._projection_timer = QTimer(self)
        self._projection_timer.setSingleShot(True)
        self._projection_timer.timeout.connect(self._offer_projection)
        self.projection_stale = 0
        self._trace_views: dict[TraceKind, SpectrumFrameView] = {}
        self._measurement_signature: tuple[object, ...] | None = None
        self._measurement_grid: np.ndarray | None = None
        self._envelopes: dict[TraceKind, EnvelopeTrace] = {}
        self._band_masks: tuple[BandMask, ...] = ()
        self._band_mask_items: list[pg.LinearRegionItem] = []
        self._markers: dict[str, SpectrumMarker] = {}
        self._selected_marker_id = "M1"
        self._range_mode = VerticalRangeMode.AUTO
        self._reference_level = 0.0
        self._db_per_division = 10.0
        self._shortcut_popover: ContextPopover | None = None
        self._measurement_available: bool | None = None
        self._build_ui()
        self._sweep_position = SweepPositionOverlay(self._plot_item, self._locale)
        self.sweep_coverage = SweepCoverageOverlay(self._plot_item, self._locale)
        self._set_measurement_available(False)
        self._install_shortcuts()
        self.set_theme(theme)

    @property
    def plot_item(self) -> pg.PlotItem:
        """The sole PlotItem used by all trace and marker layers."""

        return self._plot_item

    @property
    def view_box(self) -> pg.ViewBox:
        """The sole shared ViewBox for traces, future persistence and markers."""

        return self._view_box

    @property
    def latest_frame(self) -> object | None:
        """Return the latest source frame by identity for marker calculations."""

        return None if self._latest_view is None else self._latest_view.source_frame

    @property
    def range_mode(self) -> VerticalRangeMode:
        return self._range_mode

    @property
    def persistence_z_value(self) -> int:
        """Reserved layer directly below traces; density arrives in UI2-05 only."""

        return _PERSISTENCE_Z_VALUE

    @property
    def band_masks(self) -> tuple[BandMask, ...]:
        """Return externally supplied plan regions; the scene never creates a plan."""

        return self._band_masks

    @property
    def persistence_metrics(self) -> PersistenceOverlayMetrics:
        """Return scalar paint delivery metrics, not a native persistence claim."""

        return self._persistence.metrics

    @property
    def markers(self) -> tuple[SpectrumMarker, ...]:
        return tuple(self._markers[key] for key in ("M1", "M2") if key in self._markers)

    def trace_envelope(self, kind: TraceKind) -> EnvelopeTrace | None:
        """Return bounded paint data; never a retained average/max/min source frame."""

        return self._envelopes.get(kind)

    @property
    def displayed_frame(self) -> object | None:
        """Exact source of the current curve and markers, not latest arrival."""
        view = self._marker_view()
        return None if view is None else view.source_frame

    def set_projection_port(self, projector: SpectrumProjector) -> None:
        """Inject application-owned work, once, before the first publication."""
        if self._projector is not None or self._latest_view is not None:
            raise RuntimeError("projection port must be injected before spectrum admission")
        self._projector = projector
        projector.ready.connect(self._accept_projection)
        projector.failed.connect(self._projection_failed)
        self.sweep_coverage.request_projection = self._request_projection
        self._view_box.sigResized.connect(self._request_projection)

    def _viewport(self) -> tuple[float, float, int]:
        left, right = self._view_box.viewRange()[0]
        return float(left), float(right), max(1, int(self._view_box.width()))

    def _invalidate_projection(self) -> None:
        self._projection_generation += 1
        self._projection_key = None
        self._projection_timer.stop()
        if self._projector is not None:
            self._projector.cancel_pending(self._projection_owner)

    def _request_projection(self, *_args) -> None:
        if self._projector is not None and self._presentation_active and self._trace_views:
            # Coalesce all trace/layer updates from one GUI delivery. There is
            # one timer, not one posted callback retaining every source frame.
            if not self._projection_timer.isActive():
                self._projection_timer.start(0)

    def _offer_projection(self) -> None:
        if self._projector is None or not self._presentation_active or not self._trace_views:
            return
        state = self.sweep_coverage.state
        viewport = self._viewport()
        key = (self._projection_generation, viewport,
               tuple((kind, id(view)) for kind, view in self._trace_views.items()),
               id(state.current), id(state.previous))
        if key == self._projection_key:
            return
        self._projection_key = key
        self._projector.offer(ProjectionRequest(
            self._projection_owner, self._projection_generation, viewport,
            tuple(self._trace_views.items()), state.current, state.previous, self._prepared_spectrum))

    def _projection_current(self, request: ProjectionRequest) -> bool:
        return (request.owner is self._projection_owner and self._presentation_active
                and request.generation == self._projection_generation
                and request.viewport == self._viewport())

    def _accept_projection(self, result: SpectrumProjection) -> None:
        request = result.request
        if request.owner is not self._projection_owner:
            return
        if not self._projection_current(request):
            self.projection_stale += 1
            # Qt can finish axis/layout geometry after a resize notification.
            # A rejected result must leave a request for the final viewport,
            # even if no further source publication arrives (stopped view).
            self._request_projection()
            return
        if self._projection_error is not None:
            if self._warning_readout.text() == self._projection_error:
                self.set_warning(None)
            self._projection_error = None
        views = dict(request.traces)
        for kind, envelope in result.traces:
            self._paint_trace(kind, views[kind], envelope)
        self._displayed_view = views.get(TraceKind.CURRENT)
        self._displayed_extent = result.finite_extent
        self._apply_vertical_range()
        self._update_markers_for_new_frame()
        frame = self.displayed_frame
        self._sweep_position.set_position(getattr(getattr(frame, "spectrum", frame), "last_admitted_segment", None))
        if result.coverage is not None:
            self.sweep_coverage.apply_projection(result.coverage, request.viewport, request.previous)
            self.sweep_coverage.refresh()

    def _projection_failed(self, request: ProjectionRequest, reason: str) -> None:
        if self._projection_current(request):
            self._projection_error = text("spectrum.projection.failed", self._locale, reason=reason)
            self.set_warning(self._projection_error)

    def _marker_view(self) -> SpectrumFrameView | None:
        return self._displayed_view if self._projector is not None else self._latest_view

    def set_presentation_active(self, active: bool) -> None:
        """Suspend plot preparation, not frame admission or acquisition.

        Hidden pages keep only the existing latest views and measurement
        identity. Returning projects those views once; no publication backlog.
        This transient gate never changes a user's layer-visibility preference.
        """
        active = bool(active)
        if active == self._presentation_active:
            return
        self._presentation_active = active
        if not active:
            self._invalidate_projection()
        self._persistence.set_presentation_active(active)
        self.sweep_coverage.set_presentation_active(active)
        if active:
            for kind, view in self._trace_views.items():
                self._set_trace_view(kind, view)
            self._apply_vertical_range()
            self._update_markers_for_new_frame()

    def take_display_controls(self) -> QWidget:
        """Detach and return the existing controls without rebuilding canvas state."""
        layout = self.layout()
        if layout is not None and self._toolbar.parent() is self:
            layout.removeWidget(self._toolbar)
            self._toolbar.setParent(None)
        return self._toolbar

    def set_frame(self, frame: object, *, prepared: PreparedSpectrumFrame | None = None) -> None:
        """Set the latest immutable current frame and redraw one bounded envelope."""

        if prepared is not None:
            if not isinstance(prepared, PreparedSpectrumFrame) or prepared.view.source_frame is not frame:
                raise ValueError("prepared spectrum must belong to the exact publication")
            view = prepared.view
        else:
            view = adapt_spectrum_frame(frame)
        signature = _measurement_signature(frame, view)
        same_measurement = (self._measurement_signature == signature
                            and _same_grid(self._measurement_grid, view.frequencies_hz))
        if self._measurement_signature is not None and not same_measurement:
            self.clear_measurement()
        self._measurement_signature = signature
        if not same_measurement:
            # Own one comparison baseline, even for public-shaped mutable arrays.
            # Do not serialize/copy the full grid on every unchanged publication.
            self._measurement_grid = view.frequencies_hz.copy()
            self._measurement_grid.setflags(write=False)
        self._latest_view = view
        self._prepared_spectrum = prepared
        self._trace_views[TraceKind.CURRENT] = view
        self._set_trace_view(TraceKind.CURRENT, view)
        self._plot_item.setLabel("left", view.unit_label)
        if not same_measurement:
            self._plot_item.setXRange(float(view.frequencies_hz[0]), float(view.frequencies_hz[-1]), padding=0.0)
        self._empty_overlay.setVisible(False)
        self._set_measurement_available(True)
        self._apply_vertical_range()
        self._update_markers_for_new_frame()
        if self._projector is None:
            self._sweep_position.set_position(getattr(getattr(frame, "spectrum", frame), "last_admitted_segment", None))

    def set_trace(self, kind: TraceKind, frame: object) -> None:
        """Render a supplied analytical trace without retaining its full frame."""

        view = adapt_spectrum_frame(frame)
        self._trace_views[kind] = view
        self._set_trace_view(kind, view)

    def set_frequency_axis_visible(self, visible: bool) -> None:
        """Show the local x-axis only when no linked lower pane owns it."""

        self._plot_item.showAxis("bottom", show=bool(visible))

    def clear_trace(self, kind: TraceKind) -> None:
        self._invalidate_projection()
        self._envelopes.pop(kind, None)
        self._trace_views.pop(kind, None)
        self._curves[kind].setData([], [])
        if kind is TraceKind.CURRENT:
            self._displayed_view = None
            self._displayed_extent = None
        self._request_projection()

    def clear_measurement(self) -> None:
        """Invalidate all data-derived state, without changing acquisition or UI preferences.

        Hiding a curve alone leaves its frame available to marker/peak commands.
        Mode, identity and geometry transitions must invalidate that frame too.
        Ordinary Stop deliberately retains the last measurement instead.
        """
        self._latest_view = None
        self._displayed_view = None
        self._prepared_spectrum = None
        self._sweep_position.clear()
        self.sweep_coverage.clear()
        self._measurement_signature = None
        self._measurement_grid = None
        for kind in TraceKind:
            self.clear_trace(kind)
        self.clear_persistence_display()
        self._markers.clear()
        for marker_id in ("M1", "M2"):
            self._marker_lines[marker_id].hide()
            self._marker_labels[marker_id].setText("")
            self._marker_labels[marker_id].hide()
        self._cursor_readout.setText(text("spectrum.cursor.empty", self._locale))
        self._plot_item.setLabel("left", "")
        self._empty_overlay.setVisible(True)
        self._set_measurement_available(False)

    def _set_measurement_available(self, available: bool) -> None:
        """Keep empty axes unlabelled without destroying linked plot geometry."""
        if available == self._measurement_available:
            return
        self._measurement_available = available
        for name in ("left", "bottom"):
            self._plot_item.getAxis(name).setStyle(showValues=available)
        self.measurement_available_changed.emit(available)

    def set_band_masks(self, masks: tuple[BandMask, ...]) -> None:
        """Render external band-plan masks behind traces without changing a device."""

        for item in self._band_mask_items:
            self._plot_item.removeItem(item)
        items: list[pg.LinearRegionItem] = []
        for mask in masks:
            color = QColor(mask.color)
            color.setAlpha(38)
            item = pg.LinearRegionItem(
                values=(mask.start_hz, mask.stop_hz),
                movable=False,
                brush=pg.mkBrush(color),
                pen=pg.mkPen(color),
            )
            item.setZValue(_BAND_MASK_Z_VALUE)
            self._plot_item.addItem(item)
            items.append(item)
        self._band_masks = tuple(masks)
        self._band_mask_items = items

    def set_persistence_frame(self, frame: object, *, now_ns: int | None = None) -> None:
        """Upload an externally computed persistence density beneath all traces."""

        view = adapt_persistence_density(frame)
        self._persistence.set_frame(view, now_ns=now_ns)
        minimum, maximum = view.quantitative_labels
        self._persistence_legend.set_labels(minimum, maximum)
        self._refresh_persistence_status()

    def set_persistence_visible(self, visible: bool) -> None:
        """Hide rendering without clearing or reconfiguring native accumulation."""

        self._persistence.set_visible(visible)
        self._persistence_visible.setChecked(visible)

    def set_persistence_logarithmic(self, enabled: bool) -> None:
        self._persistence.set_logarithmic(enabled)
        self._persistence_log.setChecked(enabled)
        self._refresh_persistence_status()

    def set_persistence_render_mode(self, mode: PersistenceRenderMode) -> None:
        self._persistence.set_render_mode(mode)
        self._persistence_mode.setCurrentIndex(
            self._persistence_mode.findData(mode.value)
        )
        self._refresh_persistence_status()

    def clear_persistence_display(self) -> None:
        """Clear only the V2 image; no backend/native reset operation is issued."""

        self._persistence.clear_local_image()
        self._persistence_legend.set_labels(
            text("spectrum.persistence.no_data"),
            text("spectrum.persistence.no_data"),
        )
        self._persistence_status.setText(text("spectrum.persistence.status_cleared"))

    def set_warning(self, message: str | None) -> None:
        """Show an upstream warning without creating a recovery or retry action."""

        message_text = "" if message is None else message.strip()
        self._warning_readout.setText(message_text)
        self._warning_readout.setVisible(bool(message_text))

    def set_theme(self, theme: ThemeId) -> None:
        """Apply actual trace pens as well as chrome, without replacing data."""

        self._theme = theme
        tokens = tokens_for_theme(theme)
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._graphics.setBackground(tokens.colors.panel)
        trace_colors = {
            TraceKind.CURRENT: tokens.scientific.current_spectrum,
            TraceKind.AVERAGE: tokens.scientific.average,
            TraceKind.MAXIMUM: tokens.scientific.max_hold,
            TraceKind.MINIMUM: tokens.scientific.min_hold,
        }
        high_contrast_styles = {
            TraceKind.CURRENT: Qt.PenStyle.SolidLine,
            TraceKind.AVERAGE: Qt.PenStyle.DashLine,
            TraceKind.MAXIMUM: Qt.PenStyle.DotLine,
            TraceKind.MINIMUM: Qt.PenStyle.DashDotLine,
        }
        for kind, curve in self._curves.items():
            style = high_contrast_styles[kind] if theme is ThemeId.HIGH_CONTRAST else Qt.PenStyle.SolidLine
            curve.setPen(pg.mkPen(trace_colors[kind], width=1.4, style=style))
        axis_pen = pg.mkPen(tokens.colors.secondary_text, width=1)
        for axis_name in ("left", "bottom"):
            axis = self._plot_item.getAxis(axis_name)
            axis.setPen(axis_pen)
            axis.setTextPen(axis_pen)
        self._plot_item.showGrid(x=True, y=True, alpha=0.16)
        for line in self._marker_lines.values():
            line.setPen(pg.mkPen(tokens.scientific.marker, width=1.5))
        for label in self._marker_labels.values():
            label.setColor(tokens.scientific.marker)
        self._persistence_legend.set_theme(theme)
        self._sweep_position.set_theme(theme)
        self.sweep_coverage.set_theme(theme)
        if self._shortcut_popover is not None:
            self._shortcut_popover.setStyleSheet(stylesheet_for_theme(theme))

    def set_locale(self, locale: UiLocale) -> None:
        """Retranslate scene chrome without replacing measurement-local state."""
        self._locale = UiLocale(locale)
        self._frequency_axis.set_locale(self._locale)
        self._sweep_position.set_locale(self._locale)
        self.sweep_coverage.set_locale(self._locale)
        self.setAccessibleName(text("spectrum.accessible.name", self._locale))
        self._empty_overlay.set_content(
            title=text("spectrum.empty.title", self._locale),
            detail=text("spectrum.empty.detail", self._locale),
            primary_text=text("spectrum.empty.primary", self._locale),
            secondary_text=text("spectrum.empty.secondary", self._locale),
        )
        self._persistence_visible.setText(text("spectrum.persistence.visible", self._locale))
        self._persistence_log.setText(text("spectrum.persistence.log", self._locale))
        self._persistence_log.setToolTip(text("spectrum.persistence.log.help", self._locale))
        self._persistence_mode.setItemText(0, text("spectrum.persistence.mode.direct", self._locale))
        self._persistence_mode.setItemText(1, text("spectrum.persistence.mode.visual", self._locale))
        self._persistence_clear.setText(text("spectrum.persistence.clear", self._locale))
        self._auto_button.setText(text("spectrum.range.auto", self._locale))
        self._lock_button.setText(text("spectrum.range.lock", self._locale))
        self._shortcut_button.setText(text("spectrum.shortcuts.button", self._locale))
        self._persistence_legend.set_locale(self._locale)
        for kind, curve in self._curves.items():
            curve.opts["name"] = text(_TRACE_LABEL_KEYS[kind], self._locale)
        for marker in self._markers.values():
            self._update_marker_item(marker)
        self._update_range_summary()
        self._refresh_persistence_status()

    def set_reference_level(self, value: float) -> None:
        """Set explicit manual reference level unless the range is locked."""

        if self._range_mode is VerticalRangeMode.LOCKED:
            return
        self._reference_level = float(value)
        self._reference_spin.blockSignals(True)
        self._reference_spin.setValue(self._reference_level)
        self._reference_spin.blockSignals(False)
        self._range_mode = VerticalRangeMode.MANUAL
        self._apply_vertical_range()

    def set_db_per_division(self, value: float) -> None:
        """Set visible dB/div scaling without altering measured values."""

        if self._range_mode is VerticalRangeMode.LOCKED:
            return
        self._db_per_division = max(0.1, float(value))
        self._division_spin.blockSignals(True)
        self._division_spin.setValue(self._db_per_division)
        self._division_spin.blockSignals(False)
        self._range_mode = VerticalRangeMode.MANUAL
        self._apply_vertical_range()

    def set_auto_range(self) -> None:
        """Derive a vertical display range from finite measured values only."""

        self._range_mode = VerticalRangeMode.AUTO
        self._lock_button.blockSignals(True)
        self._lock_button.setChecked(False)
        self._lock_button.blockSignals(False)
        self._reference_spin.setEnabled(True)
        self._division_spin.setEnabled(True)
        self._auto_button.setEnabled(True)
        self._apply_vertical_range()

    def set_vertical_lock(self, locked: bool) -> None:
        """Freeze or re-enable presentation range controls; no device setting changes."""

        self._range_mode = VerticalRangeMode.LOCKED if locked else VerticalRangeMode.MANUAL
        self._reference_spin.setEnabled(not locked)
        self._division_spin.setEnabled(not locked)
        self._auto_button.setEnabled(not locked)
        self._update_range_summary()

    def place_marker(self, marker_id: str, frequency_hz: float) -> SpectrumMarker | None:
        """Place M1/M2 on the nearest finite sample of the displayed source."""

        if marker_id not in {"M1", "M2"}:
            raise ValueError("only M1 and M2 markers are available")
        view = self._marker_view()
        if view is None:
            return None
        index = _nearest_finite_index(view, frequency_hz)
        if index is None:
            return None
        marker = SpectrumMarker(
            marker_id=marker_id,
            frequency_hz=float(view.frequencies_hz[index]),
            value=float(view.values[index]),
            unit_label=view.unit_label,
        )
        self._markers[marker_id] = marker
        self._selected_marker_id = marker_id
        self._update_marker_item(marker)
        self.marker_changed.emit(marker)
        return marker

    def move_selected_marker_to_peak(self, direction: int = 0) -> SpectrumMarker | None:
        """Move selected M1/M2 to the global, next or prior finite local peak."""

        view = self._marker_view()
        if view is None:
            return None
        index = _peak_index(view, self._markers.get(self._selected_marker_id), direction)
        if index is None:
            return None
        return self.place_marker(self._selected_marker_id, float(view.frequencies_hz[index]))

    def place_selected_marker_at_view_center(self) -> SpectrumMarker | None:
        """Place the selected marker at the visible centre without acquisition work."""

        x_range = self._view_box.viewRange()[0]
        return self.place_marker(self._selected_marker_id, (x_range[0] + x_range[1]) / 2.0)

    def reset_view(self) -> None:
        """Restore the published frame span and automatic presentation range."""

        view = self._latest_view
        if view is not None:
            self._plot_item.setXRange(
                float(view.frequencies_hz[0]),
                float(view.frequencies_hz[-1]),
                padding=0.0,
            )
        self.set_auto_range()

    def toggle_shortcut_help(self) -> None:
        """Show or hide a local, non-modal shortcut reference."""

        if self._shortcut_popover is not None and self._shortcut_popover.isVisible():
            self._shortcut_popover.hide()
            return
        if self._shortcut_popover is None:
            popover = ContextPopover(text("spectrum.shortcuts.title"), parent=self)
            body = QLabel(text("spectrum.shortcuts.body"), popover)
            body.setWordWrap(True)
            body.setAccessibleName(text("spectrum.shortcuts.name"))
            popover.add_content(body)
            popover.setAccessibleDescription(text("spectrum.shortcuts.body"))
            popover.setStyleSheet(stylesheet_for_theme(self._theme))
            self._shortcut_popover = popover
        self._shortcut_popover.open_next_to(self._shortcut_button)

    def _install_shortcuts(self) -> None:
        self._help_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F1), self)
        self._help_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._help_shortcut.activated.connect(self.toggle_shortcut_help)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                                | Qt.KeyboardModifier.MetaModifier):
            super().keyPressEvent(event)
            return
        # Qt reports Cyrillic letters under a Russian Windows input layout.
        # Keep the documented physical graph shortcuts without changing the
        # system layout or intercepting text fields elsewhere in the window.
        if sys.platform == "win32":
            key = {
                0x41: Qt.Key.Key_A, 0x4D: Qt.Key.Key_M, 0x50: Qt.Key.Key_P,
                0xDB: Qt.Key.Key_BracketLeft, 0xDD: Qt.Key.Key_BracketRight,
            }.get(event.nativeVirtualKey(), key)
        if key == Qt.Key.Key_1:
            self._selected_marker_id = "M1"
            event.accept()
            return
        if key == Qt.Key.Key_2:
            self._selected_marker_id = "M2"
            event.accept()
            return
        if key == Qt.Key.Key_M:
            self.place_selected_marker_at_view_center()
            event.accept()
            return
        if key == Qt.Key.Key_P:
            self.move_selected_marker_to_peak()
            event.accept()
            return
        if key == Qt.Key.Key_BracketRight:
            self.move_selected_marker_to_peak(1)
            event.accept()
            return
        if key == Qt.Key.Key_BracketLeft:
            self.move_selected_marker_to_peak(-1)
            event.accept()
            return
        if key == Qt.Key.Key_A:
            self.set_auto_range()
            event.accept()
            return
        if key == Qt.Key.Key_0:
            self.reset_view()
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_ui(self) -> None:
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setProperty("ui2Root", True)
        self.setProperty("ui2FocusRing", True)
        self.setAccessibleName(text("spectrum.accessible.name"))
        self.setAccessibleDescription(
            f"{text('spectrum.accessible.description')} {text('spectrum.shortcuts.summary')}"
        )
        self.setToolTip(text("spectrum.shortcuts.summary"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._toolbar = self._build_toolbar()
        layout.addWidget(self._toolbar)
        self._chart_host = QFrame(self)
        self._chart_host.setProperty("ui2Role", "panel")
        host_layout = QVBoxLayout(self._chart_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self._graphics = pg.GraphicsLayoutWidget(self._chart_host)
        # V2 already separates the panels; use its 4 px spacing grid instead
        # of stacking pyqtgraph's default outer padding inside another frame.
        self._graphics.ci.layout.setContentsMargins(4, 4, 4, 4)
        self._frequency_axis = FrequencyAxis(orientation="bottom", locale=self._locale)
        self._plot_item = self._graphics.addPlot(axisItems={"bottom": self._frequency_axis})
        self._plot_item.setMenuEnabled(False)
        self._plot_item.hideButtons()
        self._view_box = self._plot_item.getViewBox()
        self._view_box.setMouseEnabled(x=True, y=False)
        self._view_box.sigXRangeChanged.connect(self._on_x_range_changed)
        self._graphics.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self._graphics.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._persistence = PersistenceOverlay(self._plot_item, z_value=_PERSISTENCE_Z_VALUE)
        host_layout.addWidget(self._graphics)
        self._empty_overlay = EmptyChartOverlay(
            title=text("spectrum.empty.title", self._locale),
            detail=text("spectrum.empty.detail", self._locale),
            primary_text=text("spectrum.empty.primary", self._locale),
            secondary_text=text("spectrum.empty.secondary", self._locale),
            parent=self._chart_host,
        )
        self._empty_overlay.setEnabled(False)
        self._curves = self._make_curves()
        self._marker_lines, self._marker_labels = self._make_marker_items()
        layout.addWidget(self._chart_host, 1)

    def _build_toolbar(self) -> QWidget:
        """Build compact functional rows instead of one non-responsive control strip."""

        toolbar = QFrame(self)
        toolbar.setProperty("ui2Role", "card")
        layout = QVBoxLayout(toolbar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        readouts = QHBoxLayout()
        readouts.setContentsMargins(0, 0, 0, 0)
        readouts.setSpacing(8)
        self._unit_readout = _secondary_label(
            text("spectrum.unit.no_frame"), text("spectrum.unit.name"), toolbar
        )
        readouts.addWidget(self._unit_readout)
        self._range_readout = _secondary_label(
            text("spectrum.range.auto"), text("spectrum.range.name"), toolbar
        )
        readouts.addWidget(self._range_readout)
        self._warning_readout = _secondary_label("", text("spectrum.warning.name"), toolbar)
        self._warning_readout.setProperty("ui2Tone", "warning")
        self._warning_readout.setVisible(False)
        readouts.addWidget(self._warning_readout)
        self._persistence_status = _secondary_label(
            text("spectrum.persistence.no_density"),
            text("spectrum.persistence.visible"),
            toolbar,
        )
        readouts.addWidget(self._persistence_status)
        readouts.addStretch(1)
        self._cursor_readout = _secondary_label(
            text("spectrum.cursor.empty"), text("spectrum.cursor.name"), toolbar
        )
        readouts.addWidget(self._cursor_readout)
        layout.addLayout(readouts)

        persistence_controls = QHBoxLayout()
        persistence_controls.setContentsMargins(0, 0, 0, 0)
        persistence_controls.setSpacing(8)
        self._persistence_visible = QCheckBox(text("spectrum.persistence.visible"), toolbar)
        self._persistence_visible.setProperty("ui2Role", "utility-toggle")
        self._persistence_visible.setAccessibleName(text("spectrum.persistence.visible.name"))
        self._persistence_visible.setChecked(True)
        self._persistence_visible.toggled.connect(self.set_persistence_visible)
        persistence_controls.addWidget(self._persistence_visible)
        self._persistence_log = QCheckBox(text("spectrum.persistence.log"), toolbar)
        self._persistence_log.setProperty("ui2Role", "utility-toggle")
        self._persistence_log.setAccessibleName(text("spectrum.persistence.log.name"))
        self._persistence_log.setToolTip(text("spectrum.persistence.log.help"))
        self._persistence_log.setChecked(True)
        self._persistence_log.toggled.connect(self.set_persistence_logarithmic)
        persistence_controls.addWidget(self._persistence_log)
        self._persistence_mode = QComboBox(toolbar)
        self._persistence_mode.setProperty("ui2Role", "utility-select")
        self._persistence_mode.setAccessibleName(text("spectrum.persistence.mode.name"))
        self._persistence_mode.addItem(text("spectrum.persistence.mode.direct"), PersistenceRenderMode.DIRECT.value)
        self._persistence_mode.addItem(text("spectrum.persistence.mode.visual"), PersistenceRenderMode.VISUAL.value)
        self._persistence_mode.currentIndexChanged.connect(self._on_persistence_mode_changed)
        persistence_controls.addWidget(self._persistence_mode)
        self._persistence_clear = QPushButton(text("spectrum.persistence.clear"), toolbar)
        self._persistence_clear.setProperty("ui2Role", "utility-action")
        self._persistence_clear.setAccessibleName(text("spectrum.persistence.clear.name"))
        self._persistence_clear.clicked.connect(self.clear_persistence_display)
        persistence_controls.addWidget(self._persistence_clear)
        self._persistence_legend = HeatLegend(
            text("spectrum.persistence.no_data"),
            text("spectrum.persistence.no_data"),
            theme=self._theme,
            parent=toolbar,
        )
        self._persistence_legend.setFixedWidth(184)
        persistence_controls.addWidget(self._persistence_legend)
        persistence_controls.addStretch(1)
        layout.addLayout(persistence_controls)

        range_controls = QHBoxLayout()
        range_controls.setContentsMargins(0, 0, 0, 0)
        range_controls.setSpacing(8)
        self._reference_spin = _spin_box(
            text("spectrum.range.reference"), -240.0, 120.0, 0.0, 1.0, toolbar
        )
        self._reference_spin.valueChanged.connect(self.set_reference_level)
        range_controls.addWidget(self._reference_spin)
        self._division_spin = _spin_box(
            text("spectrum.range.db_per_division"), 0.1, 80.0, 10.0, 0.5, toolbar
        )
        self._division_spin.valueChanged.connect(self.set_db_per_division)
        range_controls.addWidget(self._division_spin)
        self._auto_button = QPushButton(text("spectrum.range.auto"), toolbar)
        self._auto_button.setProperty("ui2Role", "utility-action")
        self._auto_button.setAccessibleName(text("spectrum.range.auto.name"))
        self._auto_button.setToolTip(text("spectrum.shortcuts.auto_tooltip"))
        self._auto_button.clicked.connect(self.set_auto_range)
        range_controls.addWidget(self._auto_button)
        self._lock_button = QPushButton(text("spectrum.range.lock"), toolbar)
        self._lock_button.setProperty("ui2Role", "utility-action")
        self._lock_button.setCheckable(True)
        self._lock_button.setAccessibleName(text("spectrum.range.lock.name"))
        self._lock_button.toggled.connect(self.set_vertical_lock)
        range_controls.addWidget(self._lock_button)
        self._shortcut_button = QPushButton(text("spectrum.shortcuts.button"), toolbar)
        self._shortcut_button.setProperty("ui2Role", "utility-action")
        self._shortcut_button.setAccessibleName(text("spectrum.shortcuts.name"))
        self._shortcut_button.setAccessibleDescription(text("spectrum.shortcuts.summary"))
        self._shortcut_button.setToolTip(text("spectrum.shortcuts.summary"))
        self._shortcut_button.clicked.connect(self.toggle_shortcut_help)
        range_controls.addWidget(self._shortcut_button)
        range_controls.addStretch(1)
        layout.addLayout(range_controls)
        return toolbar

    def _make_curves(self) -> dict[TraceKind, pg.PlotDataItem]:
        colors = tokens_for_theme(self._theme).scientific
        color_for = {
            TraceKind.CURRENT: colors.current_spectrum,
            TraceKind.AVERAGE: colors.average,
            TraceKind.MAXIMUM: colors.max_hold,
            TraceKind.MINIMUM: colors.min_hold,
        }
        curves: dict[TraceKind, pg.PlotDataItem] = {}
        for kind in TraceKind:
            curve = pg.PlotDataItem(
                pen=pg.mkPen(color_for[kind], width=1.4), name=text(_TRACE_LABEL_KEYS[kind])
            )
            curve.setSkipFiniteCheck(True)
            self._plot_item.addItem(curve)
            curves[kind] = curve
        return curves

    def _make_marker_items(self) -> tuple[dict[str, pg.InfiniteLine], dict[str, pg.TextItem]]:
        color = tokens_for_theme(self._theme).scientific.marker
        lines: dict[str, pg.InfiniteLine] = {}
        labels: dict[str, pg.TextItem] = {}
        for marker_id in ("M1", "M2"):
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color, width=1.5))
            line.setVisible(False)
            label = pg.TextItem(color=color, anchor=(0.0, 1.0))
            label.setVisible(False)
            self._plot_item.addItem(line)
            self._plot_item.addItem(label)
            lines[marker_id] = line
            labels[marker_id] = label
        return lines, labels

    def _set_trace_view(self, kind: TraceKind, view: SpectrumFrameView) -> None:
        if not self._presentation_active:
            return
        if self._projector is not None:
            self._request_projection()
            return
        visible = self._visible_trace_view(view)
        envelope = peak_preserving_envelope(visible, max(1, self._view_box.width()))
        self._paint_trace(kind, view, envelope)

    def _paint_trace(self, kind: TraceKind, view: SpectrumFrameView, envelope: EnvelopeTrace) -> None:
        self._envelopes[kind] = envelope
        self._curves[kind].setData(envelope.frequencies_hz, envelope.values, connect="finite")
        if kind is TraceKind.CURRENT:
            self._unit_readout.setText(text("spectrum.unit.readout", self._locale, unit=view.unit_label))
            self._unit_readout.setAccessibleDescription(
                text("spectrum.unit.exact_description", self._locale, unit=view.unit_label)
            )

    def _apply_vertical_range(self) -> None:
        if not self._presentation_active:
            return
        view = self._marker_view()
        if view is None:
            self._update_range_summary()
            return
        if self._range_mode is VerticalRangeMode.AUTO:
            prepared = self._prepared_spectrum
            extent = (self._displayed_extent if self._projector is not None else
                      prepared.finite_extent if prepared is not None else finite_value_extent(view.values))
            if extent is not None:
                minimum, maximum = extent
                data_span = maximum - minimum
                margin = max(3.0, data_span * 0.08)
                total_span = max(20.0, data_span + 2.0 * margin)
                self._reference_level = maximum + (total_span - data_span) / 2.0
                self._db_per_division = total_span / 8.0
                self._reference_spin.blockSignals(True)
                self._reference_spin.setValue(self._reference_level)
                self._reference_spin.blockSignals(False)
                self._division_spin.blockSignals(True)
                self._division_spin.setValue(self._db_per_division)
                self._division_spin.blockSignals(False)
        lower = self._reference_level - self._db_per_division * 8.0
        self._plot_item.setYRange(lower, self._reference_level, padding=0.0)
        unit = "" if view is None else view.unit_label
        self.vertical_range_changed.emit(lower, self._reference_level, unit)
        self._update_range_summary()

    def _update_range_summary(self) -> None:
        mode_label = {
            VerticalRangeMode.AUTO: text("spectrum.range.auto", self._locale),
            VerticalRangeMode.MANUAL: text("spectrum.range.manual", self._locale),
            VerticalRangeMode.LOCKED: text("spectrum.range.locked", self._locale),
        }[self._range_mode]
        self._range_readout.setText(
            text(
                "spectrum.range.summary",
                self._locale,
                mode=mode_label,
                reference=self._reference_level,
                division=self._db_per_division,
            )
        )

    def _update_markers_for_new_frame(self) -> None:
        if not self._presentation_active:
            return
        selected = self._selected_marker_id
        for marker_id, marker in tuple(self._markers.items()):
            if self.place_marker(marker_id, marker.frequency_hz) is None:
                # An all-missing current pass must not retain a historical
                # amplitude as a current marker just because history is visible.
                self._markers.pop(marker_id, None)
                self._marker_lines[marker_id].hide()
                self._marker_labels[marker_id].setText("")
                self._marker_labels[marker_id].hide()
        self._selected_marker_id = selected

    def _update_marker_item(self, marker: SpectrumMarker) -> None:
        line = self._marker_lines[marker.marker_id]
        label = self._marker_labels[marker.marker_id]
        line.setValue(marker.frequency_hz)
        line.setVisible(True)
        frequency = format_frequency_hz(marker.frequency_hz, locale=self._locale, resolution_hz=1.0)
        label.setText(f"{marker.marker_id}: {frequency}, {marker.value:.2f} {marker.unit_label}")
        label.setPos(marker.frequency_hz, marker.value)
        label.setVisible(True)

    def _on_mouse_clicked(self, event: object) -> None:
        button = getattr(event, "button", lambda: None)()
        if button != Qt.MouseButton.LeftButton:
            return
        scene_position = getattr(event, "scenePos", lambda: QPointF())()
        point = self._view_box.mapSceneToView(scene_position)
        self.place_marker(self._selected_marker_id, float(point.x()))
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def _on_mouse_moved(self, scene_position: QPointF) -> None:
        view = self._marker_view()
        if view is None:
            return
        point = self._view_box.mapSceneToView(scene_position)
        index = _nearest_finite_index(view, float(point.x()))
        if index is None:
            return
        self._cursor_readout.setText(
            text(
                "spectrum.cursor.value",
                frequency=format_frequency_hz(float(view.frequencies_hz[index]),
                                              locale=self._locale, resolution_hz=1.0),
                value=float(view.values[index]),
                unit=view.unit_label,
            )
        )

    def _on_x_range_changed(self, _view_box: pg.ViewBox, _range: object) -> None:
        for kind, view in tuple(self._trace_views.items()):
            self._set_trace_view(kind, view)

    def _on_persistence_mode_changed(self, index: int) -> None:
        value = self._persistence_mode.itemData(index)
        self.set_persistence_render_mode(PersistenceRenderMode(str(value)))

    def _refresh_persistence_status(self) -> None:
        view = self._persistence.latest_view
        if view is None:
            return
        scale = (
            text("spectrum.persistence.scale.log", self._locale)
            if self._persistence.logarithmic
            else text("spectrum.persistence.scale.linear", self._locale)
        )
        mode = self._persistence.render_mode
        suffix = (
            text("spectrum.persistence.suffix.visual", self._locale)
            if mode is PersistenceRenderMode.VISUAL
            else text("spectrum.persistence.suffix.direct", self._locale)
        )
        self._persistence_status.setText(
            text("spectrum.persistence.status", self._locale,
                 mode=view.value_mode.value, scale=scale, suffix=suffix)
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        host_width = max(260, self._chart_host.width() - 48)
        self._empty_overlay.setGeometry(24, 24, min(420, host_width), 132)
        for kind, view in tuple(self._trace_views.items()):
            self._set_trace_view(kind, view)

    def _visible_trace_view(self, view: SpectrumFrameView) -> SpectrumFrameView:
        """Return a zero-copy analytical slice covering the current viewport."""
        left, right = self._view_box.viewRange()[0]
        start = max(0, int(np.searchsorted(view.frequencies_hz, left, side="left")) - 1)
        stop = min(view.point_count, int(np.searchsorted(view.frequencies_hz, right, side="right")) + 1)
        if stop <= start:
            start, stop = 0, view.point_count
        return SpectrumFrameView(
            source_frame=view.source_frame,
            frequencies_hz=view.frequencies_hz[start:stop],
            values=view.values[start:stop],
            unit_label=view.unit_label,
        )


def _secondary_label(text: str, accessible_name: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("ui2Role", "secondary")
    label.setAccessibleName(accessible_name)
    return label


def _spin_box(
    accessible_name: str,
    minimum: float,
    maximum: float,
    value: float,
    step: float,
    parent: QWidget,
) -> QDoubleSpinBox:
    control = QDoubleSpinBox(parent)
    control.setRange(minimum, maximum)
    control.setProperty("ui2Role", "range-control")
    control.setValue(value)
    control.setSingleStep(step)
    control.setDecimals(1)
    control.setAccessibleName(accessible_name)
    control.setKeyboardTracking(False)
    return control


def _nearest_finite_index(view: SpectrumFrameView, frequency_hz: float) -> int | None:
    if not np.isfinite(frequency_hz):
        return None
    position = int(np.searchsorted(view.frequencies_hz, frequency_hz))
    left = _finite_neighbor(view.values, position - 1, -1)
    right = _finite_neighbor(view.values, position, 1)
    candidates = [index for index in (left, right) if index is not None]
    if not candidates:
        return None
    # Source order preserves the existing lower-frequency tie break.
    return min(candidates, key=lambda index: abs(float(view.frequencies_hz[index]) - frequency_hz))


def _finite_neighbor(values: np.ndarray, index: int, direction: int) -> int | None:
    """Nearest finite value on one side; dense markers inspect only two samples."""
    if not 0 <= index < values.size:
        return None
    if np.isfinite(values[index]):
        return index
    count = 256
    while 0 <= index < values.size:
        first, stop = ((index, min(values.size, index + count)) if direction > 0
                       else (max(0, index - count + 1), index + 1))
        finite = np.isfinite(values[first:stop])
        if np.any(finite):
            return (first + int(np.argmax(finite)) if direction > 0
                    else stop - 1 - int(np.argmax(finite[::-1])))
        index = stop if direction > 0 else first - 1
        count = min(65_536, count * 2)
    return None


def _same_grid(previous: np.ndarray | None, current: np.ndarray) -> bool:
    """Exact full-grid comparison with bounded scratch; never endpoints only."""
    if previous is None or previous.shape != current.shape or previous.dtype != current.dtype:
        return False
    return all(np.array_equal(previous[first:first + 65_536], current[first:first + 65_536])
               for first in range(0, current.size, 65_536))


def _peak_index(view: SpectrumFrameView, selected: SpectrumMarker | None, direction: int) -> int | None:
    finite = np.isfinite(view.values)
    if not np.any(finite):
        return None
    if direction == 0 or selected is None:
        return int(np.nanargmax(view.values))
    local = _local_peak_indices(view.values, finite)
    if local.size == 0:
        return int(np.nanargmax(view.values))
    current_index = _nearest_finite_index(view, selected.frequency_hz)
    if current_index is None:
        return int(np.nanargmax(view.values))
    if direction > 0:
        after = local[local > current_index]
        return int(after[0] if after.size else local[0])
    before = local[local < current_index]
    return int(before[-1] if before.size else local[-1])


def _local_peak_indices(values: np.ndarray, finite: np.ndarray) -> np.ndarray:
    indices: list[int] = []
    for index in np.flatnonzero(finite):
        left = float(values[index - 1]) if index and finite[index - 1] else float("-inf")
        right = (
            float(values[index + 1])
            if index + 1 < values.size and finite[index + 1]
            else float("-inf")
        )
        value = float(values[index])
        if value >= left and value >= right and (value > left or value > right):
            indices.append(int(index))
    return np.asarray(indices, dtype=np.intp)

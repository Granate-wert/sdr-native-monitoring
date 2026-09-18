"""One bounded, presentation-only waterfall pane below a spectrum scene."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import cast

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, QSettings, QSignalBlocker, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..design import ThemeId, stylesheet_for_theme, tokens_for_theme
from ..i18n import UiLocale, enum_text, text
from ..spectrum.axis import FrequencyAxis
from .axis import WaterfallTimeAxis
from .bounded_ring import BoundedWaterfallRenderer, DEFAULT_WATERFALL_PRESENTATION_BUDGET
from .contracts import (
    WaterfallDirection,
    WaterfallDisplayConfig,
    WaterfallGridSignature,
    WaterfallPalette,
    SweepWaterfallLine,
    adapt_waterfall_line,
)

_SETTINGS_PREFIX = "ui_v2/live/waterfall/v1"
_SETTINGS_DEBOUNCE_MS = 250
_IMAGE_TILE_CAPACITY = 2


@dataclass(frozen=True, slots=True)
class WaterfallPaneMetrics:
    """Scalar display delivery observability; no metric claims analytical loss."""

    rows_admitted: int = 0
    sweep_rows_updated: int = 0
    sweep_updates_rejected: int = 0
    rows_cadence_suppressed: int = 0
    rows_frozen_suppressed: int = 0
    rows_out_of_order_rejected: int = 0
    image_uploads: int = 0
    hidden_uploads_suppressed: int = 0
    configuration_rejections: int = 0
    grid_epoch_resets: int = 0
    local_history_clears: int = 0
    level_only_updates: int = 0
    palette_only_updates: int = 0


class WaterfallPane(QWidget):
    """Own a fixed ring/display only; it never discovers or controls a receiver."""

    visibility_requested = Signal(bool)

    def __init__(
        self,
        *,
        settings: QSettings | None = None,
        theme: ThemeId = ThemeId.DARK,
        locale: UiLocale = UiLocale.RU,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings()
        self._theme = theme
        self._locale = locale
        self._config = WaterfallDisplayConfig()
        self._renderer = BoundedWaterfallRenderer()
        self._grid_signature: WaterfallGridSignature | None = None
        self._epoch = 0
        self._last_admitted_timestamp_ns: int | None = None
        self._last_seen_timestamp_ns: int | None = None
        self._last_seen_sequence: int | None = None
        self._render_visible = True
        self._presentation_active = True
        self._frozen = False
        self._sweep_mode = False
        self._metrics = WaterfallPaneMetrics()
        self._x_syncing = False
        self._linked_frequency_source: pg.ViewBox | None = None
        self._linked_frequency_available = False
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.timeout.connect(self._write_settings)
        self._build_ui()
        self._restore_settings()
        self.set_theme(theme)

    @property
    def config(self) -> WaterfallDisplayConfig:
        return self._config

    @property
    def metrics(self) -> WaterfallPaneMetrics:
        return self._metrics

    @property
    def grid_signature(self) -> WaterfallGridSignature | None:
        return self._grid_signature

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def history_rows(self) -> int:
        buffer = self._renderer.buffer
        return 0 if buffer is None else buffer.count

    @property
    def render_visible(self) -> bool:
        """The persisted local paint preference, before a parent applies layout."""
        return self._render_visible

    @property
    def plot_item(self) -> pg.PlotItem:
        return self._plot_item

    @property
    def view_box(self) -> pg.ViewBox:
        return self._view_box

    @property
    def image_items(self) -> tuple[pg.ImageItem, pg.ImageItem]:
        return self._image_items[0], self._image_items[1]

    def set_theme(self, theme: ThemeId) -> None:
        """Apply only V2 visual tokens; stored values and ring rows are unchanged."""

        self._theme = theme
        tokens = tokens_for_theme(theme)
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._graphics.setBackground(tokens.colors.panel)
        axis_pen = pg.mkPen(tokens.colors.secondary_text, width=1)
        for axis_name in ("left", "bottom"):
            axis = self._plot_item.getAxis(axis_name)
            axis.setPen(axis_pen)
            axis.setTextPen(axis_pen)
        self._plot_item.showGrid(x=True, y=False, alpha=0.12)

    def take_display_controls(self) -> QWidget:
        """Detach the same controls; ring, axes and signal connections remain intact."""
        layout = self.layout()
        if layout is not None and self._toolbar.parent() is self:
            layout.removeWidget(self._toolbar)
            self._toolbar.setParent(None)
        return self._toolbar

    @property
    def has_embedded_display_controls(self) -> bool:
        """Whether the standalone pane still owns its Show/Hide control."""
        return self._toolbar.parent() is self

    def restore_display_controls_from(self, host: QWidget) -> bool:
        """Restore controls only from the view that temporarily hosted them."""
        if self._toolbar.parentWidget() is not host:
            return False
        if host.layout() is not None:
            host.layout().removeWidget(self._toolbar)
        layout = self.layout()
        if layout is not None:
            layout.insertWidget(0, self._toolbar)
            return True
        return False

    def set_locale(self, locale: UiLocale) -> None:
        """Retranslate controls and axes without rebuilding presentation history."""
        self._locale = UiLocale(locale)
        self.setAccessibleName(text("waterfall.accessible.name", self._locale))
        self._visible_toggle.setText(text("waterfall.visible", self._locale))
        self._freeze_button.setText(text("waterfall.freeze", self._locale))
        self._clear_button.setText(text("waterfall.clear", self._locale))
        self._history_seconds.setPrefix(text("waterfall.history.prefix", self._locale))
        self._history_seconds.setSuffix(text("waterfall.history.suffix", self._locale))
        for index, value in enumerate((30, 60, 120)):
            self._rows_per_second.setItemText(
                index, text("waterfall.rows_per_second.item", self._locale, value=value),
            )
        self._direction.setItemText(0, text("waterfall.direction.top", self._locale))
        self._direction.setItemText(1, text("waterfall.direction.bottom", self._locale))
        for index, palette in enumerate(WaterfallPalette):
            self._palette.setItemText(index, enum_text("waterfall.palette", palette, self._locale))
        self._follow_spectrum.setText(text("waterfall.follow", self._locale))
        self._time_axis.set_locale(self._locale)
        self._frequency_axis.set_locale(self._locale)
        self._plot_item.setLabel("left", text("waterfall.axis.time", self._locale))
        self._plot_item.setLabel("bottom", text("waterfall.axis.frequency", self._locale))
        self._sync_controls()
        self._update_time_axis()
        self._update_status()

    def link_frequency_view_box(self, source: pg.ViewBox) -> None:
        """Synchronize exact x ranges without letting an empty pane add padding."""

        source.sigXRangeChanged.connect(
            lambda _view_box, interval: self._synchronize_x_range(self._view_box, interval)
        )
        self._view_box.sigXRangeChanged.connect(
            lambda _view_box, interval: self._synchronize_x_range(source, interval)
        )
        self._linked_frequency_source = source
        self._synchronize_x_range(self._view_box, source.viewRange()[0])

    def set_line(self, frame: object) -> None:
        """Admit a declared row into the bounded display ring, or fail closed."""

        line = adapt_waterfall_line(frame)
        if self._frozen:
            self._set_metrics(rows_frozen_suppressed=self._metrics.rows_frozen_suppressed + 1)
            return
        signature = line.grid_signature
        if self._sweep_mode:
            self._sweep_mode = False
            self._sync_controls()
        if signature != self._grid_signature:
            self._begin_epoch(signature)
        if line.timestamp_known and self._last_seen_timestamp_ns is not None and line.timestamp_ns < self._last_seen_timestamp_ns:
            self._set_metrics(rows_out_of_order_rejected=self._metrics.rows_out_of_order_rejected + 1)
            return
        if not line.timestamp_known and line.sequence is not None and self._last_seen_sequence is not None and line.sequence < self._last_seen_sequence:
            self._set_metrics(rows_out_of_order_rejected=self._metrics.rows_out_of_order_rejected + 1)
            return
        if not line.timestamp_known and line.sequence is not None and line.sequence == self._last_seen_sequence:
            # Reconstructed snapshots may carry the same acquisition again.
            # Object identity and unqualified timestamp changes are not new rows.
            return
        if line.timestamp_known:
            self._last_seen_timestamp_ns = line.timestamp_ns
        elif line.sequence is not None:
            self._last_seen_sequence = line.sequence
        if (
            line.timestamp_known and self._last_admitted_timestamp_ns is not None
            and line.timestamp_ns - self._last_admitted_timestamp_ns < self._config.interval_ns
        ):
            self._set_metrics(rows_cadence_suppressed=self._metrics.rows_cadence_suppressed + 1)
            return
        rows, _ = self._config.dimensions(int(line.values.size))
        self._renderer.append(line.values, rows=rows, timestamp_ns=line.timestamp_ns)
        self._last_admitted_timestamp_ns = line.timestamp_ns if line.timestamp_known else None
        self._set_metrics(rows_admitted=self._metrics.rows_admitted + 1)
        self._show_initial_physical_grid_if_needed(line.grid_signature)
        self._update_time_axis()
        self._update_status()
        if not self._render_visible or not self._presentation_active:
            self._set_metrics(hidden_uploads_suppressed=self._metrics.hidden_uploads_suppressed + 1)
            return
        self._upload_tiles()

    def set_sweep_line(self, update: SweepWaterfallLine) -> None:
        """Replace partial passes in the same ring used by RTBW, never append revisions."""
        if not isinstance(update, SweepWaterfallLine):
            raise TypeError("Sweep Waterfall requires an explicit publication adapter")
        if self._frozen:
            self._set_metrics(rows_frozen_suppressed=self._metrics.rows_frozen_suppressed + 1)
            return
        if not self._sweep_mode:
            self._sweep_mode = True
            self._sync_controls()
        line = update.row
        if line.grid_signature != self._grid_signature:
            self._begin_epoch(line.grid_signature)
        rows, _ = self._config.dimensions(int(line.values.size))
        action = self._renderer.upsert_sweep(line.values, rows=rows, stamp=update.stamp)
        if action == "reject":
            self._set_metrics(sweep_updates_rejected=self._metrics.sweep_updates_rejected + 1)
            return
        if action == "append":
            self._set_metrics(rows_admitted=self._metrics.rows_admitted + 1)
        else:
            self._set_metrics(sweep_rows_updated=self._metrics.sweep_rows_updated + 1)
        self._show_initial_physical_grid_if_needed(line.grid_signature)
        self._update_time_axis()
        self._update_status()
        if not self._render_visible or not self._presentation_active:
            self._set_metrics(hidden_uploads_suppressed=self._metrics.hidden_uploads_suppressed + 1)
            return
        self._upload_tiles()

    def set_linked_frequency_available(self, available: bool) -> None:
        """Only a real linked spectrum, not a default ViewBox, supplies an axis."""
        self._linked_frequency_available = bool(available)
        self._refresh_frequency_values()

    def _refresh_frequency_values(self) -> None:
        available = self._linked_frequency_available or self.history_rows > 0
        if self._frequency_axis.style["showValues"] != available:
            self._frequency_axis.setStyle(showValues=available)

    def set_render_visible(self, visible: bool) -> None:
        """Hide/show paint delivery without changing acquisition or ring admission."""

        self._render_visible = bool(visible)
        with QSignalBlocker(self._visible_toggle):
            self._visible_toggle.setChecked(self._render_visible)
        if self._render_visible:
            self._upload_tiles()
        else:
            self._hide_tiles()
        self._schedule_settings_write()

    def set_presentation_active(self, active: bool) -> None:
        """Stop image uploads while retaining bounded row/time/gap history."""
        active = bool(active)
        if active == self._presentation_active:
            return
        self._presentation_active = active
        if active:
            self._update_time_axis()
            self._upload_tiles()

    def set_frozen(self, frozen: bool) -> None:
        """Freeze only local row admission; no backend pause/stop command exists here."""

        self._frozen = bool(frozen)
        with QSignalBlocker(self._freeze_button):
            self._freeze_button.setChecked(self._frozen)
        self._update_status()

    def clear_history(self, *, reset_kind: bool = False) -> None:
        """Clear the local ring and images without modifying spectrum, persistence or RX."""

        self._renderer.clear()
        if reset_kind:
            self._sweep_mode = False
            self._sync_controls()
        self._last_admitted_timestamp_ns = None
        self._last_seen_timestamp_ns = None
        self._last_seen_sequence = None
        self._hide_tiles()
        self._update_time_axis()
        self._set_metrics(local_history_clears=self._metrics.local_history_clears + 1)
        self._status.setText(text("waterfall.status.cleared", self._locale))

    def set_history_seconds(self, seconds: int) -> None:
        try:
            proposal = replace(self._config, history_seconds=int(seconds))
        except (TypeError, ValueError):
            self._reject_configuration_change()
            return
        if proposal == self._config:
            return
        self._config = proposal
        self._sync_controls()
        self._resize_retained_history()
        self._schedule_settings_write()

    def set_rows_per_second(self, rows_per_second: int) -> None:
        try:
            proposal = replace(self._config, rows_per_second=int(rows_per_second))
        except (TypeError, ValueError):
            # A control transition such as 100 s at 30 Hz -> 120 Hz can
            # exceed the fixed ring budget.  Keep the admitted configuration
            # and restore the selector instead of throwing from Qt callback.
            self._reject_configuration_change()
            return
        if proposal == self._config:
            return
        self._config = proposal
        self._sync_controls()
        self._resize_retained_history()
        self._schedule_settings_write()

    def set_palette(self, palette: WaterfallPalette) -> None:
        proposal = replace(self._config, palette=WaterfallPalette(palette))
        if proposal == self._config:
            return
        self._config = proposal
        lookup = waterfall_lookup_table(proposal.palette)
        for item in self._image_items:
            item.setLookupTable(lookup)
        self._set_metrics(palette_only_updates=self._metrics.palette_only_updates + 1)
        self._sync_controls()
        self._schedule_settings_write()

    def set_levels(self, lower: float, upper: float) -> None:
        proposal = replace(self._config, level_min=float(lower), level_max=float(upper))
        if proposal == self._config:
            return
        self._config = proposal
        for item in self._image_items:
            item.setLevels((proposal.level_min, proposal.level_max))
        self._set_metrics(level_only_updates=self._metrics.level_only_updates + 1)
        self._sync_controls()
        self._schedule_settings_write()

    def set_follow_spectrum_levels(self, enabled: bool) -> None:
        proposal = replace(self._config, follow_spectrum_levels=bool(enabled))
        if proposal == self._config:
            return
        self._config = proposal
        self._sync_controls()
        self._schedule_settings_write()

    def follow_spectrum_levels(self, lower: float, upper: float, *, unit_label: str) -> bool:
        """Accept a presentation range only when the active unit matches exactly."""

        signature = self._grid_signature
        if (
            not self._config.follow_spectrum_levels
            or signature is None
            or signature.unit_label != unit_label.strip()
        ):
            return False
        self.set_levels(lower, upper)
        return True

    def set_direction(self, direction: WaterfallDirection) -> None:
        proposal = replace(self._config, direction=WaterfallDirection(direction))
        if proposal == self._config:
            return
        self._config = proposal
        self._sync_controls()
        self._update_time_axis()
        if self._render_visible:
            self._upload_tiles()
        self._schedule_settings_write()

    def flush_settings(self) -> None:
        """Synchronously persist presentation choices for a clean close or test."""

        self._settings_timer.stop()
        self._write_settings()

    def closeEvent(self, event) -> None:
        self.flush_settings()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("waterfall.accessible.name"))
        self.setAccessibleDescription(text("waterfall.accessible.description"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._toolbar = self._build_toolbar()
        layout.addWidget(self._toolbar)
        self._chart_host = QFrame(self)
        self._chart_host.setProperty("ui2Role", "panel")
        host_layout = QVBoxLayout(self._chart_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self._graphics = pg.GraphicsLayoutWidget(self._chart_host)
        self._graphics.ci.layout.setContentsMargins(4, 4, 4, 4)
        self._time_axis = WaterfallTimeAxis(locale=self._locale)
        self._frequency_axis = FrequencyAxis(orientation="bottom", locale=self._locale)
        self._frequency_axis.setStyle(showValues=False)
        self._plot_item = self._graphics.addPlot(
            axisItems={"left": self._time_axis, "bottom": self._frequency_axis}
        )
        self._plot_item.setMenuEnabled(False)
        self._plot_item.hideButtons()
        self._plot_item.setLabel("left", text("waterfall.axis.time"))
        self._plot_item.setLabel("bottom", text("waterfall.axis.frequency"))
        self._view_box = self._plot_item.getViewBox()
        self._view_box.setMouseEnabled(x=True, y=False)
        self._view_box.invertY(True)
        self._image_items: list[pg.ImageItem] = []
        for _ in range(_IMAGE_TILE_CAPACITY):
            image = pg.ImageItem(axisOrder="row-major")
            image.setZValue(-20)
            image.setVisible(False)
            self._plot_item.addItem(image)
            self._image_items.append(image)
        host_layout.addWidget(self._graphics)
        layout.addWidget(self._chart_host, 1)

    def _build_toolbar(self) -> QWidget:
        toolbar = QFrame(self)
        toolbar.setProperty("ui2Role", "card")
        root = QVBoxLayout(toolbar)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(4)
        first = QHBoxLayout()
        first.setSpacing(8)
        self._status = _secondary_label(
            text("waterfall.status.waiting"), text("waterfall.status.name"), toolbar
        )
        first.addWidget(self._status)
        self._visible_toggle = QCheckBox(text("waterfall.visible"), toolbar)
        self._visible_toggle.setProperty("ui2Role", "utility-toggle")
        self._visible_toggle.setAccessibleName(text("waterfall.visible.name"))
        self._visible_toggle.setChecked(True)
        self._visible_toggle.toggled.connect(self.visibility_requested.emit)
        first.addWidget(self._visible_toggle)
        self._freeze_button = QPushButton(text("waterfall.freeze"), toolbar)
        self._freeze_button.setProperty("ui2Role", "utility-action")
        self._freeze_button.setCheckable(True)
        self._freeze_button.setAccessibleName(text("waterfall.freeze.name"))
        self._freeze_button.toggled.connect(self.set_frozen)
        first.addWidget(self._freeze_button)
        self._clear_button = QPushButton(text("waterfall.clear"), toolbar)
        self._clear_button.setProperty("ui2Role", "utility-action")
        self._clear_button.setAccessibleName(text("waterfall.clear.name"))
        self._clear_button.clicked.connect(self.clear_history)
        first.addWidget(self._clear_button)
        first.addStretch(1)
        root.addLayout(first)
        second = QHBoxLayout()
        second.setSpacing(8)
        self._history_seconds = QSpinBox(toolbar)
        self._history_seconds.setProperty("ui2Role", "range-control")
        self._history_seconds.setPrefix(text("waterfall.history.prefix"))
        self._history_seconds.setSuffix(text("waterfall.history.suffix"))
        self._history_seconds.setAccessibleName(text("waterfall.history.name"))
        self._history_seconds.valueChanged.connect(self.set_history_seconds)
        second.addWidget(self._history_seconds)
        self._rows_per_second = QComboBox(toolbar)
        self._rows_per_second.setProperty("ui2Role", "utility-select")
        self._rows_per_second.setAccessibleName(text("waterfall.rows_per_second.name"))
        for value in (30, 60, 120):
            self._rows_per_second.addItem(text("waterfall.rows_per_second.item", value=value), value)
        self._rows_per_second.currentIndexChanged.connect(self._on_rows_per_second_changed)
        second.addWidget(self._rows_per_second)
        self._direction = QComboBox(toolbar)
        self._direction.setProperty("ui2Role", "utility-select")
        self._direction.setAccessibleName(text("waterfall.direction.name"))
        self._direction.addItem(text("waterfall.direction.top"), WaterfallDirection.NEWEST_AT_TOP.value)
        self._direction.addItem(text("waterfall.direction.bottom"), WaterfallDirection.NEWEST_AT_BOTTOM.value)
        self._direction.currentIndexChanged.connect(self._on_direction_changed)
        second.addWidget(self._direction)
        self._palette = QComboBox(toolbar)
        self._palette.setProperty("ui2Role", "utility-select")
        self._palette.setAccessibleName(text("waterfall.palette.name"))
        for palette in WaterfallPalette:
            self._palette.addItem(enum_text("waterfall.palette", palette), palette.value)
        self._palette.currentIndexChanged.connect(self._on_palette_changed)
        second.addWidget(self._palette)
        self._level_min = _level_spin(toolbar, text("waterfall.level.minimum.name"))
        self._level_min.valueChanged.connect(self._on_levels_changed)
        second.addWidget(self._level_min)
        self._level_max = _level_spin(toolbar, text("waterfall.level.maximum.name"))
        self._level_max.valueChanged.connect(self._on_levels_changed)
        second.addWidget(self._level_max)
        self._follow_spectrum = QCheckBox(text("waterfall.follow"), toolbar)
        self._follow_spectrum.setProperty("ui2Role", "utility-toggle")
        self._follow_spectrum.setAccessibleName(text("waterfall.follow.name"))
        self._follow_spectrum.toggled.connect(self.set_follow_spectrum_levels)
        second.addWidget(self._follow_spectrum)
        second.addStretch(1)
        root.addLayout(second)
        return toolbar

    def _begin_epoch(self, signature: WaterfallGridSignature) -> None:
        if self._grid_signature is not None:
            self._set_metrics(grid_epoch_resets=self._metrics.grid_epoch_resets + 1)
        self._epoch += 1
        self._renderer.reset()
        self._grid_signature = signature
        self._last_admitted_timestamp_ns = None
        self._last_seen_timestamp_ns = None
        self._last_seen_sequence = None
        self._hide_tiles()
        self._status.setText(text("waterfall.new_grid", epoch=self._epoch))

    def _resize_retained_history(self) -> None:
        """Apply a compatible local capacity change without issuing an RX command."""

        signature = self._grid_signature
        if signature is None:
            return
        rows, _ = self._config.dimensions(signature.columns)
        self._renderer.resize_rows(rows)
        self._update_time_axis()
        self._update_status()
        if self._render_visible:
            self._upload_tiles()

    def _synchronize_x_range(self, target: pg.ViewBox, interval: list[float]) -> None:
        if self._x_syncing or len(interval) != 2:
            return
        self._x_syncing = True
        try:
            target.setXRange(float(interval[0]), float(interval[1]), padding=0.0)
        finally:
            self._x_syncing = False

    def _show_initial_physical_grid_if_needed(self, signature: WaterfallGridSignature) -> None:
        """Make a synthetic/pre-Live row visible without overriding a real spectrum span."""

        source = self._linked_frequency_source
        if source is None:
            return
        current = source.viewRange()[0]
        if len(current) != 2 or (float(current[0]), float(current[1])) != (0.0, 1.0):
            return
        self._synchronize_x_range(source, [signature.first_edge_hz, signature.last_edge_hz])
        self._synchronize_x_range(self._view_box, [signature.first_edge_hz, signature.last_edge_hz])

    def _upload_tiles(self) -> None:
        if not self._render_visible or not self._presentation_active:
            return
        signature = self._grid_signature
        if signature is None:
            self._hide_tiles()
            return
        tiles = self._renderer.tiles()
        if len(tiles) > _IMAGE_TILE_CAPACITY:
            raise RuntimeError("waterfall renderer exceeded fixed two-tile presentation capacity")
        row_count = self.history_rows
        if not tiles or row_count == 0:
            self._hide_tiles()
            return
        width = signature.last_edge_hz - signature.first_edge_hz
        row_offset = 0
        capacity_rows, _ = self._config.dimensions(signature.columns)
        uploads = 0
        for index, tile in enumerate(tiles):
            height = int(tile.shape[0])
            image = self._image_items[index]
            image.setLookupTable(waterfall_lookup_table(self._config.palette))
            image.setImage(
                tile,
                autoLevels=False,
                levels=(self._config.level_min, self._config.level_max),
            )
            if self._config.direction is WaterfallDirection.NEWEST_AT_TOP:
                image.setRect(
                    QRectF(
                        signature.first_edge_hz,
                        float(row_count - row_offset),
                        width,
                        float(-height),
                    )
                )
            else:
                image.setRect(
                    QRectF(
                        signature.first_edge_hz,
                        float(capacity_rows - row_count + row_offset),
                        width,
                        float(height),
                    )
                )
            image.setVisible(True)
            row_offset += height
            uploads += 1
        for image in self._image_items[uploads:]:
            image.clear()
            image.setVisible(False)
        self._plot_item.setYRange(0.0, float(capacity_rows), padding=0.0)
        self._update_time_axis()
        self._set_metrics(image_uploads=self._metrics.image_uploads + uploads)

    def _hide_tiles(self) -> None:
        for image in self._image_items:
            image.clear()
            image.setVisible(False)

    def _update_time_axis(self) -> None:
        if not self._presentation_active:
            return
        self._refresh_frequency_values()
        signature = self._grid_signature
        capacity_rows = (
            self._config.dimensions(signature.columns)[0] if signature is not None else 0
        )
        self._time_axis.set_presentation_timebase(
            direction=self._config.direction,
            rows_per_second=self._config.rows_per_second,
            display_rows=self.history_rows,
            capacity_rows=capacity_rows,
            timestamps_ns=self._renderer.timestamps_ns(),
            timestamps_known=signature is not None and signature.timestamp_known,
            sweep_stamps=self._renderer.sweep_stamps() if self._sweep_mode else (),
        )
        self._plot_item.setLabel(
            "left",
            text("waterfall.axis.sweep", self._locale) if self._sweep_mode else
            text("waterfall.axis.time", self._locale)
            if signature is None or signature.timestamp_known
            else text("waterfall.time_axis.unknown", self._locale),
        )
        self._graphics.setToolTip(text("waterfall.sweep.help", self._locale) if self._sweep_mode else "")
        self._graphics.setAccessibleDescription(self._graphics.toolTip())

    def _reject_configuration_change(self) -> None:
        self._sync_controls()
        self._set_metrics(configuration_rejections=self._metrics.configuration_rejections + 1)
        self._update_status()

    def _update_status(self) -> None:
        if self._frozen:
            self._status.setText(text("waterfall.status.frozen", self._locale, rows=self.history_rows))
        elif self._grid_signature is None:
            self._status.setText(text("waterfall.status.waiting", self._locale))
        else:
            self._status.setText(
                text(
                    "waterfall.status.ready",
                    self._locale,
                    epoch=self._epoch,
                    rows=self.history_rows,
                    unit=self._grid_signature.unit_label,
                )
            )

    def _on_rows_per_second_changed(self, index: int) -> None:
        value = self._rows_per_second.itemData(index)
        if value is not None:
            self.set_rows_per_second(int(value))

    def _on_direction_changed(self, index: int) -> None:
        value = self._direction.itemData(index)
        if value is not None:
            self.set_direction(WaterfallDirection(str(value)))

    def _on_palette_changed(self, index: int) -> None:
        value = self._palette.itemData(index)
        if value is not None:
            self.set_palette(WaterfallPalette(str(value)))

    def _on_levels_changed(self) -> None:
        self.set_levels(self._level_min.value(), self._level_max.value())

    def _sync_controls(self) -> None:
        self._history_seconds.setPrefix(text(
            "waterfall.history.blocks" if self._sweep_mode else "waterfall.history.prefix", self._locale))
        self._history_seconds.setSuffix(text(
            "waterfall.history.block_size", self._locale, rows=self._config.rows_per_second)
            if self._sweep_mode else text("waterfall.history.suffix", self._locale))
        for index, value in enumerate((30, 60, 120)):
            self._rows_per_second.setItemText(index, text(
                "waterfall.rows_per_block" if self._sweep_mode else "waterfall.rows_per_second.item",
                self._locale, value=value))
        maximum = DEFAULT_WATERFALL_PRESENTATION_BUDGET.max_history_seconds(self._config.rows_per_second)
        with QSignalBlocker(self._history_seconds):
            self._history_seconds.setRange(1, maximum)
            self._history_seconds.setValue(self._config.history_seconds)
        with QSignalBlocker(self._rows_per_second):
            self._rows_per_second.setCurrentIndex(
                self._rows_per_second.findData(self._config.rows_per_second)
            )
        with QSignalBlocker(self._direction):
            self._direction.setCurrentIndex(self._direction.findData(self._config.direction.value))
        with QSignalBlocker(self._palette):
            self._palette.setCurrentIndex(self._palette.findData(self._config.palette.value))
        with QSignalBlocker(self._level_min):
            self._level_min.setValue(self._config.level_min)
        with QSignalBlocker(self._level_max):
            self._level_max.setValue(self._config.level_max)
        with QSignalBlocker(self._follow_spectrum):
            self._follow_spectrum.setChecked(self._config.follow_spectrum_levels)

    def _restore_settings(self) -> None:
        if str(self._settings.value(f"{_SETTINGS_PREFIX}/version", "")) != "1":
            self._sync_controls()
            return
        try:
            self._config = WaterfallDisplayConfig(
                history_seconds=_read_int(self._settings, "history_seconds", self._config.history_seconds),
                rows_per_second=_read_int(self._settings, "rows_per_second", self._config.rows_per_second),
                palette=WaterfallPalette(
                    str(self._settings.value(f"{_SETTINGS_PREFIX}/palette", self._config.palette.value))
                ),
                level_min=_read_float(self._settings, "level_min", self._config.level_min),
                level_max=_read_float(self._settings, "level_max", self._config.level_max),
                direction=WaterfallDirection(
                    str(self._settings.value(f"{_SETTINGS_PREFIX}/direction", self._config.direction.value))
                ),
                follow_spectrum_levels=_read_bool(
                    self._settings,
                    "follow_spectrum_levels",
                    self._config.follow_spectrum_levels,
                ),
            )
            self._render_visible = _read_bool(self._settings, "visible", True)
        except (TypeError, ValueError):
            self._config = WaterfallDisplayConfig()
            self._render_visible = True
        self._sync_controls()
        with QSignalBlocker(self._visible_toggle):
            self._visible_toggle.setChecked(self._render_visible)
        for item in self._image_items:
            item.setLookupTable(waterfall_lookup_table(self._config.palette))
            item.setLevels((self._config.level_min, self._config.level_max))

    def _schedule_settings_write(self) -> None:
        if not self._settings_timer.isActive():
            self._settings_timer.start(_SETTINGS_DEBOUNCE_MS)

    def _write_settings(self) -> None:
        self._settings.setValue(f"{_SETTINGS_PREFIX}/version", "1")
        self._settings.setValue(f"{_SETTINGS_PREFIX}/history_seconds", self._config.history_seconds)
        self._settings.setValue(f"{_SETTINGS_PREFIX}/rows_per_second", self._config.rows_per_second)
        self._settings.setValue(f"{_SETTINGS_PREFIX}/palette", self._config.palette.value)
        self._settings.setValue(f"{_SETTINGS_PREFIX}/level_min", self._config.level_min)
        self._settings.setValue(f"{_SETTINGS_PREFIX}/level_max", self._config.level_max)
        self._settings.setValue(f"{_SETTINGS_PREFIX}/direction", self._config.direction.value)
        self._settings.setValue(
            f"{_SETTINGS_PREFIX}/follow_spectrum_levels", self._config.follow_spectrum_levels
        )
        self._settings.setValue(f"{_SETTINGS_PREFIX}/visible", self._render_visible)
        self._settings.sync()

    def _set_metrics(self, **updates: int) -> None:
        self._metrics = replace(self._metrics, **updates)


@lru_cache(maxsize=len(WaterfallPalette))
def waterfall_lookup_table(palette: WaterfallPalette) -> np.ndarray:
    """Return a deterministic UI palette without changing a measurement value."""

    stops = {
        WaterfallPalette.VIRIDIS: ((68, 1, 84), (59, 82, 139), (33, 145, 140), (253, 231, 37)),
        WaterfallPalette.CIVIDIS: ((0, 32, 76), (52, 95, 141), (125, 151, 120), (253, 231, 55)),
        WaterfallPalette.TURBO: ((48, 18, 59), (38, 188, 225), (249, 251, 14), (180, 4, 38)),
        WaterfallPalette.GRAYSCALE: ((0, 0, 0), (92, 92, 92), (180, 180, 180), (255, 255, 255)),
    }[WaterfallPalette(palette)]
    positions = np.linspace(0.0, 1.0, len(stops))
    target = np.linspace(0.0, 1.0, 256)
    lookup: np.ndarray = np.empty((256, 4), dtype=np.ubyte)
    for channel in range(3):
        lookup[:, channel] = np.interp(target, positions, [stop[channel] for stop in stops]).astype(np.ubyte)
    lookup[:, 3] = 255
    lookup.setflags(write=False)
    return lookup


def _level_spin(parent: QWidget, accessible_name: str) -> QDoubleSpinBox:
    spin = QDoubleSpinBox(parent)
    spin.setProperty("ui2Role", "range-control")
    spin.setRange(-300.0, 200.0)
    spin.setDecimals(1)
    spin.setSingleStep(1.0)
    spin.setAccessibleName(accessible_name)
    return spin


def _secondary_label(text: str, accessible_name: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("ui2Role", "secondary")
    label.setAccessibleName(accessible_name)
    return label


def _read_int(settings: QSettings, key: str, default: int) -> int:
    return int(cast(str | int, settings.value(f"{_SETTINGS_PREFIX}/{key}", default)))


def _read_float(settings: QSettings, key: str, default: float) -> float:
    return float(cast(str | int | float, settings.value(f"{_SETTINGS_PREFIX}/{key}", default)))


def _read_bool(settings: QSettings, key: str, default: bool) -> bool:
    value = settings.value(f"{_SETTINGS_PREFIX}/{key}", default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}

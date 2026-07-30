from __future__ import annotations

from typing import Protocol, Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Signal, Qt
from PySide6.QtGui import QPainter

from .domain import FrequencyRegion, Marker, SpectrumTrace, Viewport, WaterfallData
from .smoothing import (
    SpectrumSmoothSettings,
    SpectrumSmoothMethod,
    WaterfallSmoothSettings,
    WaterfallSmoothMethod,
    upsampled_spectrum_points,
)


class SpectrumRenderer(Protocol):
    widget: Any
    def set_trace(self, trace: SpectrumTrace) -> None: ...
    def update_trace(self, trace_id: str, x: np.ndarray, y: np.ndarray) -> None: ...
    def trace_data(self, trace_id: str) -> tuple[np.ndarray, np.ndarray] | None: ...
    def raw_trace_data(self, trace_id: str) -> tuple[np.ndarray, np.ndarray] | None: ...
    def remove_trace(self, trace_id: str) -> None: ...
    def set_marker(self, marker: Marker) -> None: ...
    def remove_marker(self, marker_id: str) -> None: ...
    def set_regions(self, regions: list[FrequencyRegion]) -> None: ...
    def set_x_limits(self, minimum: float, maximum: float) -> None: ...
    def set_smoothing(self, settings: SpectrumSmoothSettings) -> None: ...
    def set_viewport(self, viewport: Viewport) -> None: ...
    def clear(self) -> None: ...


class WaterfallRenderer(Protocol):
    widget: Any
    def set_data(self, waterfall: WaterfallData) -> None: ...
    def update_rows(self, values: np.ndarray) -> None: ...
    def set_levels(self, minimum: float, maximum: float) -> None: ...
    def set_colormap(self, name: str) -> None: ...
    def set_viewport(self, viewport: Viewport) -> None: ...
    def set_cursor(self, frequency_hz: float, row: float, label: str = "") -> None: ...
    def set_current_frame_row(self, values: np.ndarray, row: float) -> None: ...
    def set_smoothing(self, settings: WaterfallSmoothSettings) -> None: ...
    def set_frequency_region(self, start_hz: float, stop_hz: float) -> None: ...
    def clear_frequency_region(self) -> None: ...
    def clear_time_region(self) -> None: ...
    def clear_noise_region(self) -> None: ...
    def clear(self) -> None: ...


class SpectrumViewBox(pg.ViewBox):
    """Plain wheel zooms frequency only; Ctrl+wheel zooms both axes."""

    def wheelEvent(self, event: Any, axis: int | None = None) -> None:
        modifiers = event.modifiers()
        target_axis = None if modifiers & Qt.KeyboardModifier.ControlModifier else 0
        super().wheelEvent(event, axis=target_axis)


class WaterfallViewBox(pg.ViewBox):
    """Wheel navigation is delegated to the frame controller, never to zoom.

    The signal carries the raw angle and pixel deltas so that the controller
    can distinguish a mouse wheel (±120 angle clicks) from a touchpad (smooth
    pixel deltas).
    """

    frameWheel = Signal(int, object, object)

    def wheelEvent(self, event: Any, axis: int | None = None) -> None:
        pixel = event.pixelDelta()
        if pixel is not None and (pixel.x() or pixel.y()):
            self.frameWheel.emit(0, pixel, event.modifiers())
        else:
            self.frameWheel.emit(-int(event.delta()), None, event.modifiers())
        event.accept()


class SmoothableImageItem(pg.ImageItem):
    """ImageItem with a controllable nearest/bilinear interpolation flag."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._smooth = False

    def set_smooth(self, smooth: bool) -> None:
        if self._smooth != smooth:
            self._smooth = smooth
            self.update()

    def paint(self, painter: Any, *args: Any) -> None:
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform, self._smooth
        )
        super().paint(painter, *args)


class PyQtGraphSpectrumRenderer:
    def __init__(self) -> None:
        self.view_box = SpectrumViewBox(enableMenu=False)
        self.view_box.setMouseEnabled(x=True, y=True)
        self.widget = pg.PlotWidget(background="#10151c", viewBox=self.view_box)
        self.plot = self.widget.getPlotItem()
        self.plot.showGrid(x=True, y=True, alpha=0.22)
        self.plot.setLabel("bottom", "Частота", units="Hz")
        self.plot.setLabel("left", "Уровень", units="dBm")
        self.plot.addLegend(offset=(10, 10))
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.items: dict[str, pg.PlotDataItem] = {}
        self.markers: dict[str, tuple[pg.InfiniteLine, pg.TextItem]] = {}
        self.regions: dict[str, pg.LinearRegionItem] = {}
        self._raw: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._full_x_range: dict[str, tuple[float, float]] = {}
        self.smoothing_settings = SpectrumSmoothSettings()
        self.plot.sigXRangeChanged.connect(self._on_x_range_changed)
        # Heatmap Spectrum density layer: physical coordinates (X = Hz, Y = dBm),
        # always below traces, markers and the grid (their zValues stay >= 0).
        self.heatmap_image = pg.ImageItem(axisOrder="row-major")
        self.heatmap_image.setZValue(-10.0)
        self.heatmap_image.setOpacity(0.65)
        self.heatmap_image.hide()
        self.plot.addItem(self.heatmap_image)
        self.heatmap_visible = False
        self._heatmap_stale = False
        self._heatmap_colormap = "Viridis"
        self._heatmap_lut = self._build_heatmap_lut()
        self._heatmap_last_levels: tuple[float, float] | None = None
        self._heatmap_last_rect: tuple[float, float, float, float] | None = None
        self.heatmap_image.setLookupTable(self._heatmap_lut)

    def _on_x_range_changed(self, _plot: Any) -> None:
        # Interpolation methods depend on the visible frequency window; "none"
        # does not, so skip the refresh to avoid overwriting a manually
        # substituted exact frame or transient display data.
        if self.smoothing_settings.method == SpectrumSmoothMethod.NONE:
            return
        for trace_id in list(self._raw):
            self._refresh_trace(trace_id)

    def set_smoothing(self, settings: SpectrumSmoothSettings) -> None:
        self.smoothing_settings = settings
        for trace_id in list(self._raw):
            self._refresh_trace(trace_id)

    def set_axis_units(self, x_unit: str, y_unit: str) -> None:
        x_name = "Частота" if x_unit == "Hz" else ("Время" if x_unit == "s" else "Ось X")
        self.plot.setLabel("bottom", x_name, units=x_unit or None)
        self.plot.setLabel("left", "Уровень", units=y_unit or None)

    def _should_smooth(self, trace_id: str) -> bool:
        if self.smoothing_settings.method == SpectrumSmoothMethod.NONE:
            return False
        if not self.smoothing_settings.auto_zoom:
            return True
        full_range = self._full_x_range.get(trace_id)
        if full_range is None:
            return False
        x_min, x_max = self.plot.viewRange()[0]
        visible_span = float(x_max) - float(x_min)
        full_span = full_range[1] - full_range[0]
        if full_span <= 0:
            return False
        return visible_span < full_span * self.smoothing_settings.zoom_threshold

    def _refresh_trace(self, trace_id: str) -> None:
        item = self.items.get(trace_id)
        raw = self._raw.get(trace_id)
        if item is None or raw is None:
            return
        x, y = raw
        if self._should_smooth(trace_id):
            displayed_x, displayed_y = self._smoothed(x, y)
        else:
            displayed_x, displayed_y = x, y
        item.setData(x=displayed_x, y=displayed_y, connect="finite", skipFiniteCheck=False)

    def _smoothed(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        method = self.smoothing_settings.method
        x_min, x_max = self.plot.viewRange()[0]
        width = max(1, self.widget.width())
        visible = (x >= x_min) & (x <= x_max)
        visible_count = int(np.count_nonzero(visible))
        target = max(visible_count, int(width * self.smoothing_settings.points_per_pixel))
        return upsampled_spectrum_points(
            x, y, float(x_min), float(x_max), target, method
        )

    def set_trace(self, trace: SpectrumTrace) -> None:
        x = trace.x_values
        y = trace.power_values
        self._raw[trace.trace_id] = (x, y)
        self._full_x_range[trace.trace_id] = (
            float(np.min(x)) if x.size else 0.0,
            float(np.max(x)) if x.size else 0.0,
        )
        displayed_x, displayed_y = self._smoothed(x, y) if self._should_smooth(trace.trace_id) else (x, y)
        if trace.trace_id in self.items:
            item = self.items[trace.trace_id]
            item.setData(x=displayed_x, y=displayed_y, skipFiniteCheck=False)
            item.setPen(pg.mkPen(trace.color, width=1.4))
            item.setVisible(trace.enabled)
            return
        item = self.plot.plot(
            displayed_x, displayed_y, name=trace.name, pen=pg.mkPen(trace.color, width=1.4),
            connect="finite", skipFiniteCheck=False,
        )
        item.setClipToView(True)
        item.setDownsampling(auto=True, method="peak")
        item.setVisible(trace.enabled)
        self.items[trace.trace_id] = item

    def update_trace(self, trace_id: str, x: np.ndarray, y: np.ndarray) -> None:
        self._raw[trace_id] = (x, y)
        self._full_x_range[trace_id] = (
            float(np.min(x)) if x.size else 0.0,
            float(np.max(x)) if x.size else 0.0,
        )
        self._refresh_trace(trace_id)

    def trace_data(self, trace_id: str) -> tuple[np.ndarray, np.ndarray] | None:
        item = self.items.get(trace_id)
        if item is None:
            return None
        x, y = item.getData()
        if x is None or y is None:
            return None
        return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)

    def raw_trace_data(self, trace_id: str) -> tuple[np.ndarray, np.ndarray] | None:
        return self._raw.get(trace_id)

    def remove_trace(self, trace_id: str) -> None:
        self._raw.pop(trace_id, None)
        self._full_x_range.pop(trace_id, None)
        item = self.items.pop(trace_id, None)
        if item is not None:
            self.plot.removeItem(item)

    def set_marker(self, marker: Marker) -> None:
        if marker.marker_id not in self.markers:
            line = pg.InfiniteLine(
                pos=marker.frequency_hz, angle=90, movable=not marker.locked,
                pen=pg.mkPen(marker.color, width=1.2), label=marker.name,
                labelOpts={"position": 0.95, "color": marker.color},
            )
            text = pg.TextItem(color=marker.color, anchor=(0, 1))
            self.plot.addItem(line)
            self.plot.addItem(text)
            self.markers[marker.marker_id] = (line, text)
        line, text = self.markers[marker.marker_id]
        line.blockSignals(True)
        line.setValue(marker.frequency_hz)
        line.blockSignals(False)
        line.setMovable(not marker.locked)
        line.setVisible(marker.enabled)
        text.setText(f"{marker.name}  {marker.power:.2f} dBm")
        text.setPos(marker.frequency_hz, marker.power)
        text.setVisible(bool(marker.enabled and np.isfinite(marker.power)))

    def remove_marker(self, marker_id: str) -> None:
        pair = self.markers.pop(marker_id, None)
        if pair:
            for item in pair:
                self.plot.removeItem(item)

    def set_regions(self, regions: list[FrequencyRegion]) -> None:
        for item in self.regions.values():
            self.plot.removeItem(item)
        self.regions.clear()
        for region in regions:
            if not region.enabled:
                continue
            item = pg.LinearRegionItem(
                (region.start_frequency_hz, region.stop_frequency_hz),
                movable=True, brush=pg.mkBrush(region.color + "30"),
                pen=pg.mkPen(region.color, width=1.0),
            )
            self.plot.addItem(item)
            self.regions[region.region_id] = item

    def set_x_limits(self, minimum: float, maximum: float) -> None:
        minimum, maximum = sorted((float(minimum), float(maximum)))
        span = maximum - minimum
        if span <= 0:
            return
        self.view_box.setLimits(xMin=minimum, xMax=maximum, maxXRange=span)

    def set_viewport(self, viewport: Viewport) -> None:
        if viewport.x_min is not None and viewport.x_max is not None:
            self.plot.setXRange(viewport.x_min, viewport.x_max, padding=0)
        if viewport.y_min is not None and viewport.y_max is not None:
            self.plot.setYRange(viewport.y_min, viewport.y_max, padding=0)

    # --- Heatmap Spectrum layer -------------------------------------------
    def _build_heatmap_lut(self) -> np.ndarray:
        try:
            color_map = pg.colormap.get(COLORMAPS.get(self._heatmap_colormap, self._heatmap_colormap))
        except Exception:
            color_map = pg.colormap.get("viridis")
        lut = np.array(color_map.getLookupTable(0.0, 1.0, 256, alpha=True), copy=True)
        lut[0] = (0, 0, 0, 0)  # zero-density cells stay fully transparent
        return lut

    def set_heatmap(
        self,
        normalized_image: np.ndarray | None,
        freq_start_hz: float,
        freq_end_hz: float,
        power_min: float,
        power_max: float,
        *,
        levels: tuple[float, float] | None = None,
    ) -> None:
        """Apply a normalized density image (rows = power bins, cols = freq bins).

        The layer is positioned in physical coordinates: ``freq_start_hz`` and
        ``freq_end_hz`` are the physical EDGES of the grid (see
        ``frequency_bin_edges`` in heatmap.py), Y in dBm. Values must be >= 0;
        exact zeros render fully transparent via the LUT. ``levels`` carries
        the caller's display policy (main window owns it); when omitted the
        legacy per-snapshot max is used.
        """
        if normalized_image is None:
            self.clear_heatmap()
            return
        image = np.asarray(normalized_image, dtype=np.float32)
        if image.ndim != 2 or image.size == 0:
            self.clear_heatmap()
            return
        if levels is None:
            finite = image[np.isfinite(image)]
            vmax = float(finite.max()) if finite.size else 0.0
            levels = (0.0, vmax if vmax > 0.0 else 1.0)
        self.heatmap_image.setImage(image, autoLevels=False)
        normalized_levels = (float(levels[0]), float(levels[1]))
        if normalized_levels != self._heatmap_last_levels:
            self.heatmap_image.setLevels(normalized_levels, update=True)
            self._heatmap_last_levels = normalized_levels
        rect = (freq_start_hz, power_min, freq_end_hz - freq_start_hz, power_max - power_min)
        if rect != self._heatmap_last_rect:
            self.heatmap_image.setRect(QRectF(*rect))
            self._heatmap_last_rect = rect
        self._heatmap_stale = False
        self.heatmap_image.show()
        self.heatmap_visible = True

    @property
    def heatmap_lut(self) -> np.ndarray:
        """Current ``(256, 4)`` uint8 RGBA lookup table (entry 0 is transparent)."""
        return self._heatmap_lut

    def set_heatmap_palette(self, name: str) -> None:
        """Restyle the current heatmap image; never touches the density data."""
        self._heatmap_colormap = name
        self._heatmap_lut = self._build_heatmap_lut()
        self.heatmap_image.setLookupTable(self._heatmap_lut)

    def set_heatmap_opacity(self, value: float) -> None:
        """Change only the layer opacity; never touches the density data."""
        self.heatmap_image.setOpacity(float(np.clip(value, 0.0, 1.0)))

    def clear_heatmap(self) -> None:
        self.heatmap_image.clear()
        self.heatmap_image.hide()
        self.heatmap_visible = False
        self._heatmap_stale = False
        self._heatmap_last_levels = None
        self._heatmap_last_rect = None

    def set_heatmap_stale(self, stale: bool, *, hide: bool) -> None:
        """Mark the current layer stale; optionally hide it without dropping data.

        Hiding is used while a seek/config/context rebuild runs so an obsolete
        snapshot is not mistaken for the current one. Sequential rolling
        updates keep the old layer visible (hide=False) with an Updating
        status instead. The next set_heatmap() clears the stale flag and shows
        the layer again.
        """
        self._heatmap_stale = stale
        if hide:
            self.heatmap_image.hide()
            self.heatmap_visible = False
        elif not stale and self.heatmap_image.image is not None:
            self.heatmap_image.show()
            self.heatmap_visible = True

    def clear(self) -> None:
        self._raw.clear()
        self._full_x_range.clear()
        for key in list(self.items):
            self.remove_trace(key)
        for key in list(self.markers):
            self.remove_marker(key)
        self.set_regions([])


COLORMAPS = {
    "Turbo": "turbo",
    "Viridis": "viridis",
    "Plasma": "plasma",
    "Inferno": "inferno",
    "Magma": "magma",
    "Grayscale": "grey",
    "Jet": "CET-R4",
}


class PyQtGraphWaterfallRenderer:
    def __init__(self) -> None:
        self.view_box = WaterfallViewBox(enableMenu=False)
        self.widget = pg.PlotWidget(background="#10151c", viewBox=self.view_box)
        self.plot = self.widget.getPlotItem()
        self.plot.showGrid(x=True, y=True, alpha=0.16)
        self.plot.setLabel("bottom", "Частота", units="Hz")
        self.plot.setLabel("left", "Кадр")
        self.image = SmoothableImageItem(axisOrder="row-major")
        self.image.setAutoDownsample(True)
        self.plot.addItem(self.image)
        self.current_frame_image = SmoothableImageItem(axisOrder="row-major")
        self.current_frame_image.setAutoDownsample(True)
        self.current_frame_image.setZValue(95)
        self.current_frame_image.hide()
        self.plot.addItem(self.current_frame_image)
        self.frequency_cursor = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ffffffa0", width=1.2))
        self.time_cursor = pg.InfiniteLine(
            angle=0,
            pen=pg.mkPen("#fff176", width=2.2),
            label="",
            labelOpts={"position": 0.03, "color": "#fff176"},
        )
        self.frequency_cursor.setZValue(90)
        self.time_cursor.setZValue(100)
        self.plot.addItem(self.frequency_cursor)
        self.plot.addItem(self.time_cursor)
        self.time_region = pg.LinearRegionItem(
            (0.0, 0.0), orientation="horizontal", movable=True,
            brush=pg.mkBrush("#ff8c4230"), pen=pg.mkPen("#ff8c42", width=1.0),
        )
        self.time_region.hide()
        self.plot.addItem(self.time_region)
        self.frequency_region = pg.LinearRegionItem(
            (0.0, 0.0), movable=False, brush=pg.mkBrush("#3ddc9718"),
            pen=pg.mkPen("#3ddc97", width=1.0),
        )
        self.frequency_region.hide()
        self.plot.addItem(self.frequency_region)
        self.noise_region = pg.LinearRegionItem(
            (0.0, 0.0), orientation="horizontal", movable=False,
            brush=pg.mkBrush("#d2a8ff20"), pen=pg.mkPen("#d2a8ff", width=1.0),
        )
        self.noise_region.hide()
        self.plot.addItem(self.noise_region)
        self.event_regions: list[pg.LinearRegionItem] = []
        self.data: WaterfallData | None = None
        self._raw_values: np.ndarray | None = None
        self._raw_current_frame: np.ndarray | None = None
        self.smoothing_settings = WaterfallSmoothSettings()
        self._y_min: float = 1.0
        self._y_max: float = 1.0
        self.view_box.sigRangeChanged.connect(self._on_range_changed)
        self.set_colormap("Turbo")

    def set_smoothing(self, settings: WaterfallSmoothSettings) -> None:
        self.smoothing_settings = settings
        self._update_interpolation()
        if self._raw_current_frame is not None:
            # Row value is unknown here; caller will redraw overlay on next frame.
            self.current_frame_image.hide()

    def _on_range_changed(self, _view_box: Any) -> None:
        self._update_interpolation()

    def _effective_smooth(self) -> bool:
        method = self.smoothing_settings.method
        if method == WaterfallSmoothMethod.BILINEAR and not self.smoothing_settings.auto_zoom:
            return True
        if method == WaterfallSmoothMethod.NEAREST:
            return False
        if not self.smoothing_settings.auto_zoom:
            return method == WaterfallSmoothMethod.BILINEAR
        # Auto: use bilinear only when zoomed in enough that individual frames
        # occupy several pixels on screen.
        if self.data is None or self._raw_values is None:
            return False
        y_min, y_max = self.plot.viewRange()[1]
        visible_span = abs(float(y_max) - float(y_min))
        full_span = max(1.0, self._y_max - self._y_min)
        if visible_span >= full_span * 0.5:
            return False
        pixels = max(1, self.widget.height())
        frames_per_pixel = visible_span / pixels
        return frames_per_pixel < 2.0

    def _update_interpolation(self) -> None:
        smooth = self._effective_smooth()
        self.image.set_smooth(smooth)
        self.current_frame_image.set_smooth(smooth)

    def _smoothed(self, values: np.ndarray) -> np.ndarray:
        # No data pre-filtering: waterfall smoothing is purely the image
        # interpolation mode (Nearest / Bilinear) controlled by the painter hint.
        return values

    def set_data(self, waterfall: WaterfallData) -> None:
        self.data = waterfall
        self.current_frame_image.hide()
        if waterfall.values is None or waterfall.values.size == 0:
            self._raw_values = None
            self.image.clear()
            return
        self._raw_values = waterfall.values
        self.image.setImage(
            np.asarray(self._smoothed(self._raw_values), dtype=np.float32),
            autoLevels=False,
        )
        height, width = self._raw_values.shape
        # The public Y axis is the one-based source frame number.  Preview rows
        # are merely samples of that axis and must never leak into UI labels.
        first_frame = 1.0
        last_frame = float(max(1, waterfall.line_count))
        frame_span = last_frame - first_frame
        self._y_min = first_frame
        self._y_max = last_frame
        self.image.setRect(
            QRectF(
                waterfall.start_frequency_hz, first_frame,
                waterfall.stop_frequency_hz - waterfall.start_frequency_hz,
                frame_span if frame_span != 0.0 else 1.0,
            )
        )
        minimum = waterfall.min_level
        maximum = waterfall.max_level
        if minimum is None or maximum is None or minimum >= maximum:
            finite = self._raw_values[np.isfinite(self._raw_values)]
            minimum = float(np.percentile(finite, 2.0)) if finite.size else -120.0
            maximum = float(np.percentile(finite, 99.5)) if finite.size else -20.0
        self.set_levels(minimum, maximum)
        self.set_colormap(waterfall.colormap)
        self.set_x_limits(waterfall.start_frequency_hz, waterfall.stop_frequency_hz)
        self.set_y_limits(first_frame, last_frame)
        self._update_interpolation()

    def update_rows(self, values: np.ndarray) -> None:
        self._raw_values = np.asarray(values, dtype=np.float32)
        self.image.setImage(
            np.asarray(self._smoothed(self._raw_values), dtype=np.float32),
            autoLevels=False,
        )

    def set_levels(self, minimum: float, maximum: float) -> None:
        self.image.setLevels((float(minimum), float(maximum)), update=True)
        self.current_frame_image.setLevels((float(minimum), float(maximum)), update=True)
        if self.data is not None:
            self.data.min_level, self.data.max_level = float(minimum), float(maximum)

    def set_colormap(self, name: str) -> None:
        try:
            color_map = pg.colormap.get(COLORMAPS.get(name, name))
        except Exception:
            color_map = pg.colormap.get("viridis")
        self.image.setLookupTable(color_map.getLookupTable(0.0, 1.0, 256))
        self.current_frame_image.setLookupTable(color_map.getLookupTable(0.0, 1.0, 256))
        if self.data is not None:
            self.data.colormap = name

    def set_viewport(self, viewport: Viewport) -> None:
        if viewport.x_min is not None and viewport.x_max is not None:
            self.plot.setXRange(viewport.x_min, viewport.x_max, padding=0)
        if viewport.y_min is not None and viewport.y_max is not None:
            self.plot.setYRange(viewport.y_min, viewport.y_max, padding=0)

    def set_cursor(self, frequency_hz: float, row: float, label: str = "") -> None:
        self.frequency_cursor.setValue(frequency_hz)
        self.time_cursor.setValue(row)
        if self.time_cursor.label is not None:
            self.time_cursor.label.setFormat(label)

    def set_current_frame_row(self, values: np.ndarray, row: float) -> None:
        if self.data is None:
            return
        self._raw_current_frame = np.asarray(values, dtype=np.float32)
        exact = self._smoothed(self._raw_current_frame.reshape(1, -1))
        self.current_frame_image.setImage(exact, autoLevels=False)
        visible_y = self.plot.viewRange()[1]
        visible_span = abs(float(visible_y[1]) - float(visible_y[0]))
        pixels = max(1, self.widget.height())
        # One source frame is sub-pixel when all 100k rows are visible. Keep
        # the exact row visibly identifiable while never claiming a different
        # Y coordinate; zooming in naturally returns the height to one frame.
        visible_height = max(1.0, visible_span / pixels * 2.0)
        self.current_frame_image.setRect(
            QRectF(
                self.data.start_frequency_hz,
                float(row) - visible_height / 2.0,
                self.data.stop_frequency_hz - self.data.start_frequency_hz,
                visible_height,
            )
        )
        self.current_frame_image.show()

    def set_time_region(self, start_row: float, stop_row: float, visible: bool = True) -> None:
        self.time_region.setRegion((start_row, stop_row))
        self.time_region.setVisible(visible)

    def set_frequency_region(self, start_hz: float, stop_hz: float) -> None:
        self.frequency_region.setRegion(tuple(sorted((start_hz, stop_hz))))
        self.frequency_region.show()

    def clear_frequency_region(self) -> None:
        self.frequency_region.hide()

    def clear_time_region(self) -> None:
        self.time_region.hide()

    def clear_noise_region(self) -> None:
        self.noise_region.hide()

    def set_x_limits(self, minimum: float, maximum: float) -> None:
        minimum, maximum = sorted((float(minimum), float(maximum)))
        span = maximum - minimum
        if span <= 0:
            return
        self.view_box.setLimits(xMin=minimum, xMax=maximum, maxXRange=span)

    def set_y_limits(self, minimum: float, maximum: float) -> None:
        minimum, maximum = sorted((float(minimum), float(maximum)))
        span = maximum - minimum
        if span <= 0:
            return
        self.view_box.setLimits(
            yMin=minimum, yMax=maximum, maxYRange=span, minYRange=1.0
        )

    def set_noise_region(self, start_row: float, stop_row: float, visible: bool = True) -> None:
        self.noise_region.setRegion((start_row, stop_row))
        self.noise_region.setVisible(visible)

    def set_event_regions(
        self, regions: list[tuple[float, float] | tuple[float, float, bool]]
    ) -> None:
        for item in self.event_regions:
            self.plot.removeItem(item)
        self.event_regions.clear()
        for region in regions:
            start, stop = region[:2]
            manual = bool(region[2]) if len(region) > 2 else False
            color = "#ff8c42" if manual else "#3ddc97"
            item = pg.LinearRegionItem(
                (start, stop), orientation="horizontal", movable=False,
                brush=pg.mkBrush(color + "25"), pen=pg.mkPen(color + "80"),
            )
            item.setZValue(-5)
            self.plot.addItem(item)
            self.event_regions.append(item)

    def clear(self) -> None:
        self.data = None
        self._raw_values = None
        self._raw_current_frame = None
        self.image.clear()
        self.current_frame_image.clear()
        self.current_frame_image.hide()
        self.time_region.hide()
        self.frequency_region.hide()
        self.noise_region.hide()
        self.set_event_regions([])


class VisPyWaterfallRenderer:
    """Optional renderer boundary reserved for the GPU implementation.

    It deliberately shares the domain-facing protocol with the PyQtGraph renderer;
    importing this module never requires VisPy.
    """

    def __init__(self) -> None:
        try:
            from vispy import scene
        except ImportError as exc:
            raise RuntimeError("VisPy не установлен; выберите PyQtGraph") from exc
        self._scene = scene
        self.canvas = scene.SceneCanvas(keys="interactive", bgcolor="#10151c", show=False)
        self.widget = self.canvas.native
        self.view = self.canvas.central_widget.add_view()
        self.view.camera = "panzoom"
        self.image = scene.visuals.Image(parent=self.view.scene, method="subdivide")

    def set_data(self, waterfall: WaterfallData) -> None:
        if waterfall.values is not None:
            self.image.set_data(waterfall.values)

    def update_rows(self, values: np.ndarray) -> None:
        self.image.set_data(values)

    def set_levels(self, minimum: float, maximum: float) -> None:
        self.image.clim = (minimum, maximum)

    def set_colormap(self, name: str) -> None:
        self.image.cmap = name.casefold()

    def set_viewport(self, viewport: Viewport) -> None:
        if None not in (viewport.x_min, viewport.x_max, viewport.y_min, viewport.y_max):
            self.view.camera.set_range(
                x=(viewport.x_min, viewport.x_max), y=(viewport.y_min, viewport.y_max)
            )

    def set_cursor(self, frequency_hz: float, row: float, label: str = "") -> None:
        pass

    def set_current_frame_row(self, values: np.ndarray, row: float) -> None:
        pass

    def set_smoothing(self, settings: WaterfallSmoothSettings) -> None:
        pass

    def set_frequency_region(self, start_hz: float, stop_hz: float) -> None:
        pass

    def clear_frequency_region(self) -> None:
        pass

    def clear_time_region(self) -> None:
        pass

    def clear_noise_region(self) -> None:
        pass

    def clear(self) -> None:
        self.image.set_data(np.empty((0, 0), dtype=np.float32))

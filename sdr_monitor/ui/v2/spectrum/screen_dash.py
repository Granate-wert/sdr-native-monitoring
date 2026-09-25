"""Bounded screen-space dashes for the high-contrast average trace.

Qt's dashed-path stroker has a severe paint-time cost on a noisy, dense
envelope.  This item keeps PlotDataItem's source/measurement contract, but
draws only the visible dash fragments with a solid cosmetic pen.  Dash phase
is horizontal screen position, not the noisy trace's arc length: otherwise
thousands of vertical noise excursions produce imperceptibly short dashes.
Geometry comes from the current device transform on every paint, so zoom,
resize and DPR changes cannot reuse stale screen-space coordinates.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLineF, Qt
from PySide6.QtGui import QPainter, QPen, QTransform

try:
    from pyqtgraph.graphicsItems.PlotCurveItem import OpenGLHelpers
    from pyqtgraph.Qt.internals import PrimitiveArray
except ImportError:  # Keep the declared older pyqtgraph range usable, if slower.
    OpenGLHelpers = None
    PrimitiveArray = None

_MAX_INPUT_POINTS = 65_536
_MAX_DASH_SEGMENTS = 65_536
_MAX_CUTS = 131_072


def screen_dash_segments(
    x: np.ndarray,
    y: np.ndarray,
    transform: QTransform,
    *,
    on_pixels: float,
    off_pixels: float,
) -> np.ndarray | None:
    """Return finite, bounded local-coordinate lines or ``None`` on admission denial.

    Dashes are anchored to physical screen X; each finite source run is split
    separately. Every vertex is a cut, so no returned line can bridge a
    vertex, let alone a missing sample. Unsupported non-horizontal transforms
    fall back to Qt's native dashed path instead of drawing misleading data.
    """

    if (
        x.ndim != 1 or y.ndim != 1 or len(x) != len(y)
        or len(x) > _MAX_INPUT_POINTS or not transform.isAffine()
        or not math.isfinite(on_pixels) or not math.isfinite(off_pixels)
        or on_pixels <= 0.0 or off_pixels <= 0.0
    ):
        return None
    if len(x) < 2:
        return np.empty((0, 4), dtype=np.float64)
    scale_x = transform.m11()
    offset_x = transform.dx()
    if (not all(math.isfinite(value) for value in (
            scale_x, transform.m12(), transform.m21(), transform.m22(), offset_x, transform.dy()))
            or scale_x <= 0.0 or transform.m12() != 0.0 or transform.m21() != 0.0):
        return None

    finite = np.isfinite(x) & np.isfinite(y)
    starts = np.flatnonzero(finite & ~np.r_[False, finite[:-1]])
    stops = np.flatnonzero(finite & ~np.r_[finite[1:], False]) + 1
    period = on_pixels + off_pixels
    parts: list[np.ndarray] = []
    total_segments = 0
    for start, stop in zip(starts, stops, strict=True):
        if stop - start < 2:
            continue
        rx = np.asarray(x[start:stop], dtype=np.float64)
        ry = np.asarray(y[start:stop], dtype=np.float64)
        screen_x = scale_x * rx + offset_x
        if not np.all(np.isfinite(screen_x)) or np.any(np.diff(screen_x) <= 0.0):
            return None
        first_cycle = math.floor(screen_x[0] / period)
        last_cycle = math.ceil(screen_x[-1] / period)
        cycles = last_cycle - first_cycle + 1
        if cycles * 2 + len(screen_x) > _MAX_CUTS:
            return None
        dash_starts = np.arange(first_cycle, last_cycle + 1, dtype=np.float64) * period
        cuts = np.concatenate((screen_x, dash_starts, dash_starts + on_pixels))
        cuts = cuts[(cuts >= screen_x[0]) & (cuts <= screen_x[-1])]
        cuts.sort()
        cuts = np.unique(cuts)
        mids = (cuts[:-1] + cuts[1:]) * 0.5
        edge = np.searchsorted(screen_x, mids, side="right") - 1
        selected = (
            (edge < len(rx) - 1)
            & (np.mod(mids, period) < on_pixels)
            & (cuts[1:] > cuts[:-1] + 1e-9)
        )
        first = cuts[:-1][selected]
        last = cuts[1:][selected]
        edge = edge[selected]
        total_segments += len(edge)
        if total_segments > _MAX_DASH_SEGMENTS:
            return None
        if not len(edge):
            continue
        dx = np.diff(rx)
        dy = np.diff(ry)
        t0 = (first - screen_x[edge]) / (screen_x[edge + 1] - screen_x[edge])
        t1 = (last - screen_x[edge]) / (screen_x[edge + 1] - screen_x[edge])
        parts.append(np.stack((
            rx[edge] + dx[edge] * t0,
            ry[edge] + dy[edge] * t0,
            rx[edge] + dx[edge] * t1,
            ry[edge] + dy[edge] * t1,
        ), axis=1))
    if not parts:
        return np.empty((0, 4), dtype=np.float64)
    result = np.concatenate(parts)
    return result if np.all(np.isfinite(result)) else None


class ScreenDashCurveItem(pg.PlotCurveItem):
    """Use fast solid fragments only for the average trace's dashed pen."""

    def paint(self, painter: QPainter, option: object, widget: object) -> None:
        pen = self.opts["pen"]
        if (PrimitiveArray is None or OpenGLHelpers is None or pen is None
                or pen.style() != Qt.PenStyle.DashLine or not pen.isCosmetic()
                or self.opts["connect"] != "finite" or self.opts["fillLevel"] is not None
                or self.opts["shadowPen"] is not None or self.opts["stepMode"]
                or self.opts["compositionMode"] is not None or self._exportOpts is not False
                or isinstance(widget, OpenGLHelpers.GraphicsViewGLWidget)):
            super().paint(painter, option, widget)
            return
        x, y = self.getData()
        if x is None or y is None or len(x) < 2:
            return
        device = painter.device()
        dpr = device.devicePixelRatioF() if device is not None else 1.0
        dash_width = max(1.0, float(pen.widthF())) * dpr
        segments = screen_dash_segments(
            x, y, painter.deviceTransform(),
            on_pixels=8.0 * dash_width,
            off_pixels=4.0 * dash_width,
        )
        if segments is None:
            # Unsupported geometry must remain correct, even if slower.
            super().paint(painter, option, widget)
            return
        if not len(segments):
            return
        lines = PrimitiveArray(QLineF, 4)
        lines.resize(len(segments))
        lines.ndarray()[:] = segments
        solid = QPen(pen)
        solid.setStyle(Qt.PenStyle.SolidLine)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, bool(self.opts["antialias"]))
            painter.setPen(solid)
            painter.drawLines(*lines.drawargs())
        finally:
            painter.restore()


class ScreenDashPlotDataItem(pg.PlotDataItem):
    """PlotDataItem with a bounded average-only curve painter."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        original: pg.PlotCurveItem = self.curve  # type: ignore[has-type]  # pyqtgraph is untyped
        original.sigClicked.disconnect(self.sigClicked)
        original.setParentItem(None)
        original.deleteLater()
        self.curve = ScreenDashCurveItem()
        self.curve.setParentItem(self)
        self.curve.sigClicked.connect(self.sigClicked)
        self.updateItems(styleUpdate=True)

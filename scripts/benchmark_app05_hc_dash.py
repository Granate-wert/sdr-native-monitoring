"""Matched visible-Qt high-contrast trace paint comparison; no SDR is opened.

Run with QT_QPA_PLATFORM=windows for a visible Qt surface. QWidget.grab() is
not desktop/DWM scanout evidence. Baseline temporarily uses pyqtgraph's own
PlotCurveItem.paint; the candidate uses the product painter, when present.
Neither mode changes source data, FFT, SDR settings or acquisition cadence.
"""

from __future__ import annotations

import argparse
import faulthandler
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    faulthandler.enable()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=850)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--trace", choices=("average", "maximum", "minimum"),
                        default="average")
    parser.add_argument("--baseline-only", action="store_true",
                        help="Diagnostic native-paint profile before a product candidate exists")
    parser.add_argument("--smooth-history", action="store_true",
                        help="Use a dense, low-slope trace to inspect pattern continuity")
    parser.add_argument("--mixed-history", action="store_true",
                        help="Use a long flat plateau followed by a noisy region")
    parser.add_argument("--diagnostic-component", choices=("full", "lines", "path"),
                        default="full", help="Isolate one painter component; not a quality capture")
    parser.add_argument("--diagnostic-cache", action="store_true",
                        help="Probe Qt device-coordinate cache on static data; not product evidence")
    parser.add_argument("--cache-limit-kb", type=int, default=65_536,
                        help="Process-global Qt cache ceiling for diagnostic cache mode")
    parser.add_argument("--fixed-y-range", type=float, nargs=2, metavar=("LOW", "HIGH"),
                        help="Explicit visible dB range for representative paint/cache geometry")
    args = parser.parse_args()
    output = args.output.resolve()
    capture_dir = args.capture_dir.resolve()
    if (not output.parent.is_dir() or output.exists() or not capture_dir.is_dir()
            or not 800 <= args.width <= 3840 or not 500 <= args.height <= 2160
            or not 2 <= args.samples <= 20
            or (args.smooth_history and args.mixed_history)
            or (args.diagnostic_cache and args.diagnostic_component != "full")
            or not 10_240 <= args.cache_limit_kb <= 262_144
            or (args.fixed_y_range is not None and not
                (-200.0 <= args.fixed_y_range[0] < args.fixed_y_range[1] <= 100.0))):
        raise SystemExit("invalid or existing bounded output/geometry/samples")

    import pyqtgraph as pg
    import PySide6
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainterPath, QPixmapCache, QTransform
    from PySide6.QtWidgets import QApplication, QGraphicsItem

    from sdr_monitor.ui.v2.design.tokens import ThemeId
    from sdr_monitor.ui.v2.spectrum import TraceKind
    from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
    from sdr_monitor.ui.v2.spectrum import screen_dash
    from sdr_monitor.ui.v2.spectrum.screen_dash import ScreenDashCurveItem

    trace_kind = {
        "average": TraceKind.AVERAGE,
        "maximum": TraceKind.MAXIMUM,
        "minimum": TraceKind.MINIMUM,
    }[args.trace]

    app = QApplication.instance() or QApplication([])
    if args.diagnostic_cache:
        QPixmapCache.setCacheLimit(args.cache_limit_kb)
    frequencies = np.linspace(2.3e9, 2.6e9, 4600)
    rng = np.random.default_rng(17)
    if args.mixed_history:
        history = np.full(frequencies.size, -73.0, dtype=np.float32)
        boundary = round(frequencies.size * 0.60)
        history[boundary:] = (-75.0 + 5.0 * rng.normal(
            size=frequencies.size - boundary)).astype(np.float32)
    elif args.smooth_history:
        history = (-73.0 + 2.0 * np.sin(np.linspace(0.0, 18.0,
                                                  frequencies.size))).astype(np.float32)
    else:
        history = (-85.0 + 5.0 * rng.normal(size=frequencies.size)).astype(np.float32)
    current = (-70.0 + 2.0 * np.sin(np.linspace(0.0, 15.0, frequencies.size))).astype(np.float32)
    for values in (frequencies, history, current):
        values.setflags(write=False)

    class Frame:
        def __init__(self, values: np.ndarray) -> None:
            self.frequencies_hz = frequencies
            self.values = values
            self.unit = "dBFS/Hz"

    current_frame = Frame(current)
    history_frame = Frame(history)
    product_paint = ScreenDashCurveItem.paint
    native_paint = pg.PlotCurveItem.paint
    product_partition = screen_dash._partition_history_edges

    def diagnostic_partition(
        x: np.ndarray, y: np.ndarray, transform: QTransform,
        style: Qt.PenStyle, width_pixels: float,
    ) -> tuple[np.ndarray, QPainterPath] | None:
        result = product_partition(x, y, transform, style, width_pixels)
        if result is None or args.diagnostic_component == "full":
            return result
        lines, path = result
        if args.diagnostic_component == "lines":
            return lines, QPainterPath()
        return np.empty((0, 4), dtype=np.float64), path

    if args.diagnostic_component != "full":
        screen_dash._partition_history_edges = diagnostic_partition
    blocks: list[dict[str, object]] = []
    pictures: list[tuple[str, str]] = []
    try:
        modes = (("baseline",) if args.baseline_only else
                 ("baseline", "candidate", "candidate", "baseline"))
        for index, mode in enumerate(modes):
            print(f"APP05_HC_DASH block={index} mode={mode} begin", flush=True)
            paints_ms: list[float] = []
            paint_state = SimpleNamespace(target=None, in_custom=False,
                                          paints=paints_ms, mode=mode)

            def timed_native(self: pg.PlotCurveItem, painter: object, option: object,
                             widget: object, _state=paint_state) -> None:
                start = perf_counter_ns()
                native_paint(self, painter, option, widget)
                if self is _state.target and not _state.in_custom:
                    _state.paints.append((perf_counter_ns() - start) / 1e6)

            def timed_custom(self: ScreenDashCurveItem, painter: object, option: object,
                             widget: object, _state=paint_state) -> None:
                start = perf_counter_ns()
                _state.in_custom = True
                try:
                    (native_paint if _state.mode == "baseline" else product_paint)(
                        self, painter, option, widget)
                finally:
                    _state.in_custom = False
                if self is _state.target:
                    _state.paints.append((perf_counter_ns() - start) / 1e6)

            # PySide's QGraphicsItem virtual dispatch binds the class override
            # when a new item is constructed; patch before constructing scene.
            pg.PlotCurveItem.paint = timed_native
            ScreenDashCurveItem.paint = timed_custom
            scene = SpectrumScene()
            try:
                scene.resize(args.width, args.height)
                scene.show()
                app.processEvents()
                scene.set_theme(ThemeId.HIGH_CONTRAST)
                scene.set_frame(current_frame)
                if args.fixed_y_range is not None:
                    lower, upper = args.fixed_y_range
                    scene.set_reference_level(upper)
                    scene.set_db_per_division((upper - lower) / 8.0)
                scene.set_trace(trace_kind, history_frame)
                app.processEvents()
                if scene.latest_frame is not current_frame:
                    raise RuntimeError("current measurement identity changed")
                envelope = scene.trace_envelope(trace_kind)
                if envelope is None or envelope.display_point_count != history.size:
                    raise RuntimeError("history display fixture changed")
                paint_state.target = scene._curves[trace_kind].curve
                if args.diagnostic_cache:
                    paint_state.target.setCacheMode(
                        QGraphicsItem.CacheMode.DeviceCoordinateCache)
                # Exclude construction, theme and first-paint warm-up.
                scene._graphics.grab()
                paints_ms.clear()
                grabs_ms: list[float] = []
                for sample in range(args.samples):
                    start = perf_counter_ns()
                    pixmap = scene._graphics.grab()
                    grabs_ms.append((perf_counter_ns() - start) / 1e6)
                    if sample == 0:
                        path = capture_dir / f"hc_{args.trace}_{index:02d}_{mode}.png"
                        if path.exists() or not pixmap.save(str(path)):
                            raise RuntimeError(f"capture cannot be written: {path}")
                        # Keep the QImage wrapper alive while reading its
                        # native pixel pointer; a temporary can be destroyed
                        # before bytes() copies it on PySide/Windows.
                        image = pixmap.toImage()
                        pictures.append((str(path), hashlib.sha256(image.bits()).hexdigest()))
                if ((not args.diagnostic_cache and len(paints_ms) != args.samples)
                        or len(paints_ms) > args.samples):
                    raise RuntimeError(
                        f"unexpected history trace paint count {len(paints_ms)}")
                scene_bounds = paint_state.target.sceneBoundingRect()
                device_bounds = scene._graphics.viewportTransform().mapRect(scene_bounds)
                blocks.append({
                    "mode": mode, "index": index, "trace_paint_ms": paints_ms,
                    "graphics_grab_ms": grabs_ms,
                    "actual_window_logical": [scene.width(), scene.height()],
                    "graphics_logical": [scene._graphics.width(), scene._graphics.height()],
                    "dpr": scene.devicePixelRatioF(),
                    "source_identity_unchanged": scene.latest_frame is current_frame,
                    "envelope_points": envelope.display_point_count,
                    "history_cache_mode": int(paint_state.target.cacheMode().value),
                    "curve_scene_bounds": [
                        scene_bounds.x(), scene_bounds.y(),
                        scene_bounds.width(), scene_bounds.height(),
                    ],
                    "curve_device_bounds": [
                        device_bounds.x(), device_bounds.y(),
                        device_bounds.width(), device_bounds.height(),
                    ],
                })
            finally:
                scene.close()
                app.processEvents()
            print(f"APP05_HC_DASH block={index} paints={len(paints_ms)} done", flush=True)
    finally:
        ScreenDashCurveItem.paint = product_paint
        pg.PlotCurveItem.paint = native_paint
        screen_dash._partition_history_edges = product_partition

    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                         text=True, timeout=2).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                        text=True, timeout=2).splitlines()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        commit, dirty = None, None
    result = {
        "schema": "app05-hc-trace-paint-v2",
        "trace_kind": args.trace,
        "history_fixture": ("mixed" if args.mixed_history else
                            "smooth" if args.smooth_history else "noisy"),
        "diagnostic_component": args.diagnostic_component,
        "diagnostic_cache": args.diagnostic_cache,
        "fixed_y_range": args.fixed_y_range,
        "qt_pixmap_cache_limit_kb": QPixmapCache.cacheLimit(),
        "scope": "Visible Qt surface paint/grab only; no desktop/DWM, LPS or physical RX claim",
        "source_commit": commit,
        "dirty_paths": dirty,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pyqtgraph": pg.__version__, "pyside": PySide6.__version__,
        "qpa": app.platformName(), "requested_window_logical": [args.width, args.height],
        "source_sha256": {
            "frequency": hashlib.sha256(frequencies.tobytes()).hexdigest(),
            "history": hashlib.sha256(history.tobytes()).hexdigest(),
            "current": hashlib.sha256(current.tobytes()).hexdigest(),
        },
        "blocks": blocks,
        "captures": [{"path": path, "pixel_sha256": digest}
                     for path, digest in pictures],
    }
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "blocks": [
        {"mode": b["mode"], "trace_paint_ms": b["trace_paint_ms"],
         "graphics_grab_ms": b["graphics_grab_ms"]} for b in blocks
    ]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

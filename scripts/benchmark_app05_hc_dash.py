"""Matched visible-Qt average-trace paint comparison; no SDR is opened.

Run with QT_QPA_PLATFORM=windows for a visible Qt surface. QWidget.grab() is
not desktop/DWM scanout evidence. Baseline temporarily uses pyqtgraph's own
PlotCurveItem.paint; the candidate uses the product ScreenDashCurveItem.paint.
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
    args = parser.parse_args()
    output = args.output.resolve()
    capture_dir = args.capture_dir.resolve()
    if (not output.parent.is_dir() or output.exists() or not capture_dir.is_dir()
            or not 800 <= args.width <= 3840 or not 500 <= args.height <= 2160
            or not 2 <= args.samples <= 20):
        raise SystemExit("invalid or existing bounded output/geometry/samples")

    import pyqtgraph as pg
    import PySide6
    from PySide6.QtWidgets import QApplication

    from sdr_monitor.ui.v2.design.tokens import ThemeId
    from sdr_monitor.ui.v2.spectrum import TraceKind
    from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
    from sdr_monitor.ui.v2.spectrum.screen_dash import ScreenDashCurveItem

    app = QApplication.instance() or QApplication([])
    frequencies = np.linspace(2.3e9, 2.6e9, 4600)
    rng = np.random.default_rng(17)
    average = (-85.0 + 5.0 * rng.normal(size=frequencies.size)).astype(np.float32)
    current = (-70.0 + 2.0 * np.sin(np.linspace(0.0, 15.0, frequencies.size))).astype(np.float32)
    for values in (frequencies, average, current):
        values.setflags(write=False)

    class Frame:
        def __init__(self, values: np.ndarray) -> None:
            self.frequencies_hz = frequencies
            self.values = values
            self.unit = "dBFS/Hz"

    current_frame = Frame(current)
    average_frame = Frame(average)
    product_paint = ScreenDashCurveItem.paint
    blocks: list[dict[str, object]] = []
    pictures: list[tuple[str, str]] = []
    try:
        for index, mode in enumerate(("baseline", "candidate", "candidate", "baseline")):
            print(f"APP05_HC_DASH block={index} mode={mode} begin", flush=True)
            method = pg.PlotCurveItem.paint if mode == "baseline" else product_paint
            paints_ms: list[float] = []

            def timed_paint(self: ScreenDashCurveItem, painter: object, option: object,
                            widget: object, _method=method, _paints=paints_ms) -> None:
                start = perf_counter_ns()
                _method(self, painter, option, widget)
                _paints.append((perf_counter_ns() - start) / 1e6)

            # PySide's QGraphicsItem virtual dispatch binds the class override
            # when a new item is constructed; patch before constructing scene.
            ScreenDashCurveItem.paint = timed_paint
            scene = SpectrumScene()
            try:
                scene.resize(args.width, args.height)
                scene.show()
                app.processEvents()
                scene.set_theme(ThemeId.HIGH_CONTRAST)
                scene.set_frame(current_frame)
                scene.set_trace(TraceKind.AVERAGE, average_frame)
                app.processEvents()
                if scene.latest_frame is not current_frame:
                    raise RuntimeError("current measurement identity changed")
                envelope = scene.trace_envelope(TraceKind.AVERAGE)
                if envelope is None or envelope.display_point_count != average.size:
                    raise RuntimeError("average display fixture changed")
                # Exclude construction, theme and first-paint warm-up.
                scene._graphics.grab()
                paints_ms.clear()
                grabs_ms: list[float] = []
                for sample in range(args.samples):
                    start = perf_counter_ns()
                    pixmap = scene._graphics.grab()
                    grabs_ms.append((perf_counter_ns() - start) / 1e6)
                    if sample == 0:
                        path = capture_dir / f"hc_dash_{index:02d}_{mode}.png"
                        if path.exists() or not pixmap.save(str(path)):
                            raise RuntimeError(f"capture cannot be written: {path}")
                        # Keep the QImage wrapper alive while reading its
                        # native pixel pointer; a temporary can be destroyed
                        # before bytes() copies it on PySide/Windows.
                        image = pixmap.toImage()
                        pictures.append((str(path), hashlib.sha256(image.bits()).hexdigest()))
                if len(paints_ms) != args.samples:
                    raise RuntimeError("average trace paint attribution was not one per grab")
                blocks.append({
                    "mode": mode, "index": index, "average_paint_ms": paints_ms,
                    "graphics_grab_ms": grabs_ms,
                    "actual_window_logical": [scene.width(), scene.height()],
                    "graphics_logical": [scene._graphics.width(), scene._graphics.height()],
                    "dpr": scene.devicePixelRatioF(),
                    "source_identity_unchanged": scene.latest_frame is current_frame,
                    "envelope_points": envelope.display_point_count,
                })
            finally:
                scene.close()
                app.processEvents()
            print(f"APP05_HC_DASH block={index} paints={len(paints_ms)} done", flush=True)
    finally:
        ScreenDashCurveItem.paint = product_paint

    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                         text=True, timeout=2).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                        text=True, timeout=2).splitlines()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        commit, dirty = None, None
    result = {
        "schema": "app05-hc-average-paint-v1",
        "scope": "Visible Qt surface paint/grab only; no desktop/DWM, LPS or physical RX claim",
        "source_commit": commit,
        "dirty_paths": dirty,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pyqtgraph": pg.__version__, "pyside": PySide6.__version__,
        "qpa": app.platformName(), "requested_window_logical": [args.width, args.height],
        "source_sha256": {
            "frequency": hashlib.sha256(frequencies.tobytes()).hexdigest(),
            "average": hashlib.sha256(average.tobytes()).hexdigest(),
            "current": hashlib.sha256(current.tobytes()).hexdigest(),
        },
        "blocks": blocks,
        "captures": [{"path": path, "pixel_sha256": digest}
                     for path, digest in pictures],
    }
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "blocks": [
        {"mode": b["mode"], "average_paint_ms": b["average_paint_ms"],
         "graphics_grab_ms": b["graphics_grab_ms"]} for b in blocks
    ]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Diagnostic only: compare direct history strokes with a transparent raster layer.

This intentionally does not change UI V2 or enable a product cache. It asks
whether an unchanged MAX/MIN curve can be composited from a retained QImage
without changing the exact Qt surface pixels. Run under Windows QPA too:
offscreen-only equality is not sufficient for the APP-05 quality gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument("--scene", action="store_true",
                        help="Also compare exact UI V2 QWidget surfaces on this QPA")
    args = parser.parse_args()
    if args.samples < 2 or args.output.exists() or not args.output.parent.is_dir():
        raise SystemExit("invalid output or samples")

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen
    from PySide6.QtWidgets import QApplication, QWidget

    from sdr_monitor.ui.v2.spectrum.screen_dash import ScreenDashCurveItem

    app = QApplication.instance() or QApplication([])
    results: list[dict[str, object]] = []
    for style in (Qt.PenStyle.DotLine, Qt.PenStyle.DashDotLine):
        for dpr in (1.0, 1.75):
            for fixture in ("noisy", "mixed"):
                width, height = 900, 400
                image_width, image_height = round(width * dpr), round(height * dpr)
                x = np.linspace(30.0, 870.0, 4600, dtype=np.float64)
                y = np.full(x.size, 160.0, dtype=np.float64)
                if fixture == "noisy":
                    y[:] = 150.0 + 35.0 * (np.arange(x.size) % 2)
                else:
                    y[round(x.size * 0.6):] = 150.0 + 35.0 * (
                        np.arange(x.size - round(x.size * 0.6)) % 2)
                y[2300:2304] = np.nan
                curve = ScreenDashCurveItem()
                pen = QPen(QColor(180, 225, 35), 1.4, style)
                pen.setCosmetic(True)
                curve.setPen(pen)
                curve.setData(x, y, connect="finite")

                def make_image(transparent: bool, *, image_width: int = image_width,
                               image_height: int = image_height, dpr: float = dpr) -> QImage:
                    result = QImage(image_width, image_height,
                                    QImage.Format.Format_ARGB32_Premultiplied)
                    result.setDevicePixelRatio(dpr)
                    result.fill(Qt.GlobalColor.transparent if transparent
                                else QColor(27, 29, 34))
                    return result

                direct = make_image(False)
                start = perf_counter_ns()
                painter = QPainter(direct)
                curve.paint(painter, None, None)
                painter.end()
                direct_ms = (perf_counter_ns() - start) / 1e6

                layer = make_image(True)
                start = perf_counter_ns()
                painter = QPainter(layer)
                curve.paint(painter, None, None)
                painter.end()
                build_ms = (perf_counter_ns() - start) / 1e6

                composited = make_image(False)
                blit_ms: list[float] = []
                for _ in range(args.samples):
                    composited.fill(QColor(27, 29, 34))
                    start = perf_counter_ns()
                    painter = QPainter(composited)
                    painter.drawImage(QPointF(0.0, 0.0), layer)
                    painter.end()
                    blit_ms.append((perf_counter_ns() - start) / 1e6)

                a = np.frombuffer(direct.bits(), dtype=np.uint8)
                b = np.frombuffer(composited.bits(), dtype=np.uint8)
                channels_changed = int(np.count_nonzero(a != b))
                pixels_changed = int(np.count_nonzero(np.any(
                    (a != b).reshape(image_height, image_width, 4), axis=2)))
                results.append({
                    "style": style.name, "dpr": dpr, "fixture": fixture,
                    "pixels_changed": pixels_changed,
                    "channels_changed": channels_changed,
                    "max_channel_delta": int(np.max(np.abs(a.astype(np.int16) - b))),
                    "direct_ms": direct_ms, "layer_build_ms": build_ms,
                    "blit_p50_ms": statistics.median(blit_ms),
                    "layer_bytes": image_width * image_height * 4,
                })
                del curve
    scene_results: list[dict[str, object]] = []
    if args.scene:
        from sdr_monitor.ui.v2.design.tokens import ThemeId
        from sdr_monitor.ui.v2.spectrum import TraceKind
        from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene

        original_paint = ScreenDashCurveItem.paint
        state: dict[str, object] = {"target": None, "mode": "direct", "image": None,
                                    "key": None,
                                    "builds": 0, "hits": 0, "paints_ms": [],
                                    "device": None}

        def observed_paint(self: ScreenDashCurveItem, painter: QPainter,
                           option: object, widget: object) -> None:
            if self is not state["target"] or state["mode"] == "direct":
                original_paint(self, painter, option, widget)
                return
            start = perf_counter_ns()
            layer = state["image"]
            device = painter.device()
            dpr = device.devicePixelRatioF()
            physical_width = (round(device.width() * dpr) if isinstance(device, QWidget)
                              else device.width())
            physical_height = (round(device.height() * dpr) if isinstance(device, QWidget)
                               else device.height())
            transform = painter.worldTransform()
            pen = self.opts["pen"]
            x, y = self.getData()
            assert x is not None and y is not None
            key = (physical_width, physical_height, dpr,
                   tuple(float(value) for value in (
                       transform.m11(), transform.m12(), transform.m13(),
                       transform.m21(), transform.m22(), transform.m23(),
                       transform.m31(), transform.m32(), transform.m33())),
                   pen.color().rgba(), int(pen.style().value), float(pen.widthF()),
                   tuple((painter.window().x(), painter.window().y(),
                          painter.window().width(), painter.window().height(),
                          painter.viewport().x(), painter.viewport().y(),
                          painter.viewport().width(), painter.viewport().height())),
                   hashlib.sha256(x.tobytes()).digest(),
                   hashlib.sha256(y.tobytes()).digest())
            if layer is None or state["key"] != key:
                layer = QImage(physical_width, physical_height,
                               QImage.Format.Format_ARGB32_Premultiplied)
                layer.setDevicePixelRatio(dpr)
                layer.fill(Qt.GlobalColor.transparent)
                offscreen = QPainter(layer)
                offscreen.setWindow(painter.window())
                offscreen.setViewport(painter.viewport())
                offscreen.setWorldTransform(transform)
                original_paint(self, offscreen, option, widget)
                offscreen.end()
                state["image"] = layer
                state["key"] = key
                state["builds"] += 1
                state["device"] = {
                    "kind": type(device).__name__, "width": device.width(),
                    "height": device.height(), "dpr": dpr,
                    "window": [painter.window().x(), painter.window().y(),
                               painter.window().width(), painter.window().height()],
                    "viewport": [painter.viewport().x(), painter.viewport().y(),
                                 painter.viewport().width(), painter.viewport().height()],
                }
            else:
                state["hits"] += 1
            painter.save()
            try:
                painter.resetTransform()
                painter.drawImage(QPointF(0.0, 0.0), layer)
            finally:
                painter.restore()
            state["paints_ms"].append((perf_counter_ns() - start) / 1e6)

        ScreenDashCurveItem.paint = observed_paint
        try:
            for kind in (TraceKind.MAXIMUM, TraceKind.MINIMUM):
                frequencies = np.linspace(2.3e9, 2.6e9, 4600)
                current = np.full(frequencies.size, -90.0, dtype=np.float32)
                history = np.full(frequencies.size, -80.0, dtype=np.float32)
                boundary = round(history.size * 0.6)
                history[boundary:] = -85.0 + 15.0 * (
                    np.arange(history.size - boundary) % 2)
                history[2300:2304] = np.nan
                frame = type("Frame", (), {"frequencies_hz": frequencies,
                                            "values": current, "unit": "dBFS/Hz"})()
                trace = type("Frame", (), {"frequencies_hz": frequencies,
                                            "values": history, "unit": "dBFS/Hz"})()
                scene = SpectrumScene(theme=ThemeId.HIGH_CONTRAST)
                try:
                    scene.resize(1400, 850)
                    scene.show()
                    app.processEvents()
                    scene.set_frame(frame)
                    scene.set_reference_level(-20.0)
                    scene.set_db_per_division(12.5)
                    scene.set_trace(kind, trace)
                    app.processEvents()
                    state.update(target=scene._curves[kind].curve, mode="direct",
                                 image=None, key=None, builds=0, hits=0,
                                 paints_ms=[], device=None)

                    def compare_state(label: str, *, scene: SpectrumScene = scene,
                                      kind: TraceKind = kind) -> None:
                        state["mode"] = "direct"
                        prior_builds = state["builds"]
                        prior_hits = state["hits"]
                        state["paints_ms"] = []
                        scene._graphics.grab()
                        baseline = scene._graphics.grab().toImage()
                        state["mode"] = "cached"
                        candidate_first = scene._graphics.grab().toImage()
                        candidate_hit = scene._graphics.grab().toImage()
                        state["mode"] = "direct"
                        baseline_again = scene._graphics.grab().toImage()
                        arrays = [np.frombuffer(image.bits(), dtype=np.uint8).copy()
                                  for image in (baseline, candidate_first,
                                                candidate_hit, baseline_again)]
                        if any(array.size != arrays[0].size for array in arrays):
                            raise RuntimeError("UI V2 capture dimensions changed")

                        def changed(index: int) -> int:
                            return int(np.count_nonzero(np.any(
                                (arrays[0] != arrays[index]).reshape(baseline.height(),
                                                                    baseline.width(), 4), axis=2)))

                        scene_results.append({
                            "kind": kind.name, "state": label,
                            "physical_size": [baseline.width(), baseline.height()],
                            "dpr": baseline.devicePixelRatioF(),
                            "first_pixels_changed": changed(1),
                            "hit_pixels_changed": changed(2),
                            "baseline_repeat_pixels_changed": changed(3),
                            "builds": state["builds"] - prior_builds,
                            "hits": state["hits"] - prior_hits,
                            "paint_ms": state["paints_ms"], "device": state["device"],
                        })

                    compare_state("initial")
                    scene.plot_item.setXRange(2.39e9, 2.48e9, padding=0.0)
                    app.processEvents()
                    compare_state("zoom")
                    changed_history = history.copy()
                    changed_history[boundary:] += 3.0
                    changed_trace = type("Frame", (), {"frequencies_hz": frequencies,
                        "values": changed_history, "unit": "dBFS/Hz"})()
                    scene.set_trace(kind, changed_trace)
                    app.processEvents()
                    compare_state("new-data")
                    scene.resize(1200, 700)
                    app.processEvents()
                    compare_state("resize")
                    scene.hide()
                    app.processEvents()
                    state.update(image=None, key=None)  # Product Hide clears plot data/cache.
                    scene.show()
                    app.processEvents()
                    compare_state("hide-show")
                    scene.set_theme(ThemeId.DARK)
                    app.processEvents()
                    compare_state("dark-theme")
                finally:
                    state.update(target=None, image=None, mode="direct")
                    scene.close()
                    app.processEvents()
        finally:
            ScreenDashCurveItem.paint = original_paint

    payload = {
        "qpa": os.environ.get("QT_QPA_PLATFORM", ""),
        "checkout_head": subprocess.check_output(
            ("git", "rev-parse", "HEAD"), cwd=Path(__file__).resolve().parents[1],
            text=True).strip(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "tracked_dirty_paths": subprocess.check_output(
            ("git", "diff", "--name-only"), cwd=Path(__file__).resolve().parents[1],
            text=True).splitlines(),
        "scope": "QImage and optional UI V2 QWidget surface probe, not DWM/product acceptance",
        "results": results,
        "scene_results": scene_results,
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload))
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

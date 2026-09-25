"""Visible Qt paint diagnostic for the composed UI V2 spectrum/waterfall area.

CURRENT, AVERAGE, MAXIMUM, MINIMUM, persistence and Waterfall use one fixed,
immutable synthetic grid. History-on/off ABBA changes only the three auxiliary
traces. This times QWidget backing-store grabs and individual trace paint calls;
it does not measure acquisition, presenter delivery, DWM scanout, or Live FPS.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from types import SimpleNamespace
from typing import cast

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_RUNTIME_MODULES = (
    "sdr_monitor.ui.v2.spectrum.scene",
    "sdr_monitor.ui.v2.spectrum.screen_dash",
    "sdr_monitor.ui.v2.spectrum.envelope",
    "sdr_monitor.ui.v2.spectrum.envelope_batch",
    "sdr_monitor.ui.v2.spectrum.contracts",
    "sdr_monitor.ui.v2.spectrum.persistence_overlay",
    "sdr_monitor.ui.v2.spectrum.persistence_projection",
    "sdr_monitor.ui.v2.spectrum.persistence_contracts",
    "sdr_monitor.ui.v2.waterfall.spectrum_view",
    "sdr_monitor.ui.v2.waterfall.pane",
    "sdr_monitor.ui.v2.waterfall.bounded_ring",
    "sdr_monitor.ui.v2.waterfall.contracts",
    "sdr_monitor.ui.v2.state.analyzer_layers",
    "sdr_monitor.ui.v2.design.tokens",
    "pyqtgraph.graphicsItems.PlotCurveItem",
    "pyqtgraph.Qt.internals",
    "PySide6.QtWidgets",
)
_MAX_BOUNDED_PROBE_SEGMENTS = 65_536
_MAX_BOUNDED_PROBE_INPUT = _MAX_BOUNDED_PROBE_SEGMENTS // 5


def _runtime_module_hashes() -> dict[str, dict[str, str]]:
    result = {}
    for name in _RUNTIME_MODULES:
        module = importlib.import_module(name)
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            raise RuntimeError(f"runtime module has no file: {name}")
        path = Path(module_file).resolve()
        if not path.is_file() or name.startswith("sdr_monitor.") and not path.is_relative_to(ROOT):
            raise RuntimeError(f"runtime module was not loaded from the checkout: {name}: {path}")
        result[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def _source_arrays(bins: int, fixture: str) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    frequency = np.linspace(2.3e9, 2.6e9, bins, dtype=np.float64)
    rng = np.random.default_rng(17)
    current: np.ndarray
    average: np.ndarray
    maximum: np.ndarray
    minimum: np.ndarray
    if fixture == "mixed":
        plateau = round(bins * 0.60)
        noisy = rng.normal(size=bins - plateau)
        current = np.full(bins, -78.0, dtype=np.float32)
        current[plateau:] = (-78.0 + 5.0 * noisy).astype(np.float32)
        average = np.full(bins, -82.0, dtype=np.float32)
        average[plateau:] = (-82.0 + 2.0 * noisy).astype(np.float32)
        maximum = np.full(bins, -70.0, dtype=np.float32)
        maximum[plateau:] = (-70.0 + 5.0 * noisy).astype(np.float32)
        minimum = np.full(bins, -92.0, dtype=np.float32)
        minimum[plateau:] = (-92.0 + 5.0 * noisy).astype(np.float32)
    else:
        noisy = rng.normal(size=bins)
        current = (-78.0 + 5.0 * noisy).astype(np.float32)
        average = (-82.0 + 2.0 * noisy).astype(np.float32)
        maximum = (-70.0 + 5.0 * noisy).astype(np.float32)
        minimum = (-92.0 + 5.0 * noisy).astype(np.float32)
    values: dict[str, np.ndarray] = {"current": current, "average": average,
                                     "maximum": maximum, "minimum": minimum}
    frequency.setflags(write=False)
    for array in values.values():
        array.setflags(write=False)
    return frequency, values


def _bounded_noisy_fragments(edges: np.ndarray, *, dotted: bool) -> np.ndarray:
    """Diagnostic source-edge fragments; preserve both endpoints, not native dash phase."""
    if edges.ndim != 2 or edges.shape[1] != 4 or len(edges) > _MAX_BOUNDED_PROBE_INPUT:
        raise ValueError("bounded noisy-edge probe exceeds its input contract")
    if not np.all(np.isfinite(edges)):
        raise ValueError("bounded noisy-edge probe requires finite source edges")
    fractions = (np.array(((0.0, 0.06), (0.23, 0.29), (0.47, 0.53),
                           (0.71, 0.77), (0.94, 1.0)), dtype=np.float64)
                 if dotted else
                 np.array(((0.0, 0.17), (0.32, 0.37), (0.53, 0.70),
                           (0.88, 1.0)), dtype=np.float64))
    start = edges[:, None, :2]
    delta = edges[:, None, 2:] - start
    first = start + delta * fractions[None, :, 0, None]
    last = start + delta * fractions[None, :, 1, None]
    return np.concatenate((first, last), axis=2).reshape(-1, 4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--qt-platform", choices=("windows", "offscreen"), default="windows")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=850)
    parser.add_argument("--bins", type=int, default=4600)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--fixture", choices=("noisy", "mixed"), default="mixed")
    parser.add_argument("--attribute-history-geometry", action="store_true",
                        help="Time MAX/MIN partition separately from the full trace paint")
    parser.add_argument("--solid-noisy-edge-probe", action="store_true",
                        help="Observer only: draw source-exact noisy MAX/MIN edges with a solid pen")
    parser.add_argument("--bounded-noisy-edge-probe", action="store_true",
                        help="Observer only: draw bounded solid fragments on each noisy MAX/MIN edge")
    parser.add_argument("--expected-dpr", type=float,
                        help="Require this actual Qt widget/screen DPR for every ABBA sample")
    args = parser.parse_args()
    if args.solid_noisy_edge_probe and args.bounded_noisy_edge_probe:
        parser.error("select only one noisy-edge probe")
    if args.bounded_noisy_edge_probe and not args.attribute_history_geometry:
        parser.error("bounded noisy-edge probe requires history-geometry attribution")
    if args.bounded_noisy_edge_probe and args.bins > _MAX_BOUNDED_PROBE_INPUT:
        parser.error(f"bounded noisy-edge diagnostic is limited to {_MAX_BOUNDED_PROBE_INPUT} input bins")
    output = args.output.resolve()
    captures = args.capture_dir.resolve()
    if (not output.parent.is_dir() or output.exists() or not captures.is_dir()
            or not 800 <= args.width <= 3840 or not 500 <= args.height <= 2160
            or not 512 <= args.bins <= 65_536 or not 2 <= args.samples <= 10
            or args.expected_dpr is not None and (not math.isfinite(args.expected_dpr)
                                                  or args.expected_dpr <= 0)):
        parser.error("output/capture directory, fresh output or bounded geometry is invalid")
    os.environ["QT_QPA_PLATFORM"] = args.qt_platform

    import pyqtgraph as pg
    import PySide6
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings
    from PySide6.QtGui import QPen
    from PySide6.QtWidgets import QApplication

    from sdr_monitor.ui.v2.design.tokens import ThemeId
    from sdr_monitor.ui.v2.spectrum import TraceKind
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
        DensityValueMode, PersistenceDensityFrame,
    )
    from sdr_monitor.ui.v2.spectrum import screen_dash
    from sdr_monitor.ui.v2.spectrum.screen_dash import ScreenDashCurveItem
    from sdr_monitor.ui.v2.state.analyzer_layers import _waterfall_projection
    from sdr_monitor.ui.v2.waterfall import SpectrumWaterfallView, WaterfallLineFrame

    app = cast(QApplication, QApplication.instance() or QApplication([]))
    if app.platformName() != args.qt_platform:
        raise RuntimeError(f"requested {args.qt_platform} QPA, got {app.platformName()}")

    frequency, values = _source_arrays(args.bins, args.fixture)
    step = float(frequency[1] - frequency[0])
    edges = np.linspace(frequency[0] - step / 2, frequency[-1] + step / 2,
                        args.bins + 1, dtype=np.float64)
    levels = np.linspace(-120.0, -20.0, 65, dtype=np.float64)
    rows = np.arange(64, dtype=np.float32)[:, None]
    level_cell = ((values["current"] + 120.0) * 64.0 / 100.0)[None, :]
    density = (0.85 * np.exp(-((rows - level_cell) / 1.6) ** 2)).astype(np.float32)
    density[density < 0.03] = 0.0
    for array in (edges, levels, density):
        array.setflags(write=False)
    frames = {name: SimpleNamespace(frequencies_hz=frequency, values=sample,
                                    unit="dBFS/Hz") for name, sample in values.items()}
    density_frame = PersistenceDensityFrame(
        density, edges, levels, DensityValueMode.PROBABILITY, "dBFS/Hz")
    waterfall_values, waterfall_edges = _waterfall_projection(frequency, values["current"])
    loaded_code = _runtime_module_hashes()

    original_native = pg.PlotCurveItem.paint
    original_custom = ScreenDashCurveItem.paint
    original_partition = screen_dash._partition_history_edges
    target_roles: dict[int, str] = {}
    recorded: dict[str, list[float]] = {}
    geometry_recorded: dict[str, list[float]] = {}
    in_custom = False
    active_role: str | None = None

    class SolidNoisyEdgePainter:
        """Forward every operation, changing only the batched long-edge stroke."""

        def __init__(self, painter):
            self._painter = painter

        def __getattr__(self, name):
            return getattr(self._painter, name)

        def drawLines(self, *lines):
            original = self._painter.pen()
            solid = QPen(original)
            solid.setStyle(screen_dash.Qt.PenStyle.SolidLine)
            self._painter.setPen(solid)
            try:
                self._painter.drawLines(*lines)
            finally:
                self._painter.setPen(original)

    def timed_partition(*params):
        start = perf_counter_ns()
        try:
            result = original_partition(*params)
            if (args.bounded_noisy_edge_probe and active_role in ("maximum", "minimum")
                    and result is not None and len(result[0])):
                return _bounded_noisy_fragments(
                    result[0], dotted=params[3] == screen_dash.Qt.PenStyle.DotLine), result[1]
            return result
        finally:
            if active_role is not None:
                geometry_recorded.setdefault(active_role, []).append((perf_counter_ns() - start) / 1e6)

    def timed_native(self, painter, option, widget):
        start = perf_counter_ns()
        original_native(self, painter, option, widget)
        role = target_roles.get(id(self))
        if role is not None and not in_custom:
            recorded.setdefault(role, []).append((perf_counter_ns() - start) / 1e6)

    def timed_custom(self, painter, option, widget):
        nonlocal in_custom, active_role
        start = perf_counter_ns()
        in_custom = True
        active_role = target_roles.get(id(self))
        try:
            target = (SolidNoisyEdgePainter(painter)
                      if (args.solid_noisy_edge_probe or args.bounded_noisy_edge_probe)
                      and active_role in ("maximum", "minimum")
                      else painter)
            original_custom(self, target, option, widget)
        finally:
            in_custom = False
            active_role = None
        role = target_roles.get(id(self))
        if role is not None:
            recorded.setdefault(role, []).append((perf_counter_ns() - start) / 1e6)

    pg.PlotCurveItem.paint = timed_native
    ScreenDashCurveItem.paint = timed_custom  # type: ignore[method-assign]
    if args.attribute_history_geometry:
        screen_dash._partition_history_edges = timed_partition
    blocks: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="app05-all-layers-") as temporary:
        settings = QSettings(str(Path(temporary) / "ui.ini"), QSettings.Format.IniFormat)
        view = None
        try:
            view = SpectrumWaterfallView(settings=settings, theme=ThemeId.HIGH_CONTRAST)
            view.resize(args.width, args.height)
            view.show()
            app.processEvents()
            scene, waterfall = view.spectrum_scene, view.waterfall_pane
            scene.set_frame(frames["current"])
            scene.set_persistence_frame(density_frame)
            waterfall.set_history_seconds(1)
            for index in range(30):
                waterfall.set_line(WaterfallLineFrame(
                    waterfall_values, waterfall_edges, (index + 1) * 1_000_000_000,
                    1, "dBFS/Hz", sequence=index + 1))
            app.processEvents()
            if (scene._persistence.image_item.image is None or waterfall.history_rows != 30
                    or waterfall.metrics.image_uploads == 0 or not view.isVisible()
                    or not waterfall.isVisible()):
                raise RuntimeError("the composed plot layers are not visibly populated")

            def geometry() -> tuple[object, ...]:
                screen = view.screen()
                viewport = scene.view_box.sceneBoundingRect()
                return (
                    view.width(), view.height(), scene.width(), scene.height(),
                    scene._graphics.width(), scene._graphics.height(),
                    waterfall.width(), waterfall.height(),
                    waterfall._graphics.width(), waterfall._graphics.height(),
                    round(viewport.width(), 4), round(viewport.height(), 4),
                    view.devicePixelRatioF(), scene.devicePixelRatioF(),
                    scene._graphics.devicePixelRatioF(), waterfall.devicePixelRatioF(),
                    waterfall._graphics.devicePixelRatioF(),
                    screen.name() if screen else None,
                    screen.devicePixelRatio() if screen else None,
                )

            target_geometry = geometry()
            if target_geometry[:2] != (args.width, args.height):
                raise RuntimeError(f"visible Qt target was clamped: {target_geometry[:2]}")
            if (args.expected_dpr is not None
                    and any(not math.isclose(cast(float, actual), args.expected_dpr, abs_tol=1e-6)
                            for actual in target_geometry[12:17] + target_geometry[18:19])):
                raise RuntimeError(f"visible Qt target DPR did not match: {target_geometry}")
            target_roles.update({id(scene._curves[kind].curve): kind.value for kind in TraceKind})
            for index, enabled in enumerate((False, True, True, False)):
                recorded.clear()
                geometry_recorded.clear()
                for kind in (TraceKind.AVERAGE, TraceKind.MAXIMUM, TraceKind.MINIMUM):
                    if enabled:
                        scene.set_trace(kind, frames[kind.value])
                    else:
                        scene.clear_trace(kind)
                app.processEvents()
                for _ in range(2):
                    view.grab()  # Exclude geometry and curve-cache warm-up.
                if geometry() != target_geometry:
                    raise RuntimeError("ABBA Qt target geometry changed during warm-up")
                recorded.clear()
                geometry_recorded.clear()
                grabs_ms: list[float] = []
                image_hash = None
                path = captures / f"all_layers_{index:02d}_{'on' if enabled else 'off'}.png"
                if path.exists():
                    raise RuntimeError(f"capture already exists: {path}")
                for sample in range(args.samples):
                    if geometry() != target_geometry:
                        raise RuntimeError("ABBA Qt target geometry changed before a sample")
                    started = perf_counter_ns()
                    pixmap = view.grab()
                    grabs_ms.append((perf_counter_ns() - started) / 1e6)
                    if geometry() != target_geometry:
                        raise RuntimeError("ABBA Qt target geometry changed during a sample")
                    if sample == 0:
                        if not pixmap.save(str(path)):
                            raise RuntimeError(f"capture failed: {path}")
                        image = pixmap.toImage()
                        image_hash = hashlib.sha256(image.bits()).hexdigest()
                # Initially absent curves do not paint. After clear_trace,
                # Qt may still invoke an empty item; count its real cost.
                required = ({"current", "average", "maximum", "minimum"}
                            if enabled else {"current"})
                if (not required <= set(recorded)
                        or not set(recorded) <= {kind.value for kind in TraceKind}
                        or any(len(paints) != args.samples for paints in recorded.values())):
                    raise RuntimeError(f"unexpected trace paints: { {k: len(v) for k, v in recorded.items()} }")
                if args.attribute_history_geometry and enabled and any(
                    len(geometry_recorded.get(role, ())) != args.samples
                    for role in ("maximum", "minimum")
                ):
                    raise RuntimeError(f"missing history partition samples: {geometry_recorded}")
                if scene.latest_frame is not frames["current"]:
                    raise RuntimeError("history overlay replaced the current measurement")
                if any((scene.trace_envelope(kind) is None) != (not enabled)
                       for kind in (TraceKind.AVERAGE, TraceKind.MAXIMUM, TraceKind.MINIMUM)):
                    raise RuntimeError("history overlay state differs from requested block")
                envelope_points = {}
                if enabled:
                    for kind in (TraceKind.AVERAGE, TraceKind.MAXIMUM, TraceKind.MINIMUM):
                        envelope = scene.trace_envelope(kind)
                        if envelope is None:
                            raise RuntimeError(f"missing enabled history envelope: {kind.value}")
                        envelope_points[kind.value] = envelope.display_point_count
                blocks.append({
                    "index": index, "histories_enabled": enabled,
                    "grab_ms": grabs_ms, "trace_paint_ms": {k: list(v) for k, v in recorded.items()},
                    "history_partition_ms": {k: list(v) for k, v in geometry_recorded.items()},
                    "image_path": str(path), "image_sha256": image_hash,
                    "actual_window_logical": [view.width(), view.height()],
                    "spectrum_logical": [scene.width(), scene.height()],
                    "waterfall_logical": [waterfall.width(), waterfall.height()],
                    "spectrum_graphics_logical": [scene._graphics.width(), scene._graphics.height()],
                    "waterfall_graphics_logical": [waterfall._graphics.width(), waterfall._graphics.height()],
                    "spectrum_viewport_logical": list(target_geometry[10:12]),
                    "dpr": view.devicePixelRatioF(),
                    "history_envelope_points": envelope_points,
                })
                print(f"APP05_ALL_LAYERS block={index} enabled={enabled} grabs={len(grabs_ms)}", flush=True)
            screen = view.screen()
            environment = {
                "screen_name": screen.name() if screen else None,
                "screen_logical": ([screen.geometry().width(), screen.geometry().height()]
                                   if screen else None),
                "screen_dpr": screen.devicePixelRatio() if screen else None,
                "persistence_uploads": scene.persistence_metrics.image_uploads,
                "waterfall_metrics": asdict(waterfall.metrics),
                "waterfall_rows": waterfall.history_rows,
                "verified_geometry": list(target_geometry),
            }
        finally:
            if view is not None:
                view.close()
                view.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
            ScreenDashCurveItem.paint = original_custom  # type: ignore[method-assign]
            pg.PlotCurveItem.paint = original_native
            screen_dash._partition_history_edges = original_partition
    if _runtime_module_hashes() != loaded_code:
        raise RuntimeError("loaded renderer module bytes changed during the ABBA run")

    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                         text=True, timeout=2).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                        text=True, timeout=2).splitlines()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        commit, dirty = None, None
    result = {
        "schema": "app05-visible-all-layer-static-v2" if (args.attribute_history_geometry
                                                               or args.solid_noisy_edge_probe
                                                               or args.bounded_noisy_edge_probe)
                  else "app05-visible-all-layer-static-v1",
        "scope": __doc__, "source_commit": commit, "dirty_paths": dirty,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "loaded_code": loaded_code,
        "qpa": app.platformName(), "pyside": PySide6.__version__,
        "pyqtgraph": pg.__version__, "fixture": args.fixture, "bins": args.bins,
        "requested_window_logical": [args.width, args.height],
        "source_sha256": {"frequency": hashlib.sha256(frequency.tobytes()).hexdigest(),
                          **{name: hashlib.sha256(sample.tobytes()).hexdigest()
                             for name, sample in values.items()},
                          "density": hashlib.sha256(density.tobytes()).hexdigest(),
                          "waterfall": hashlib.sha256(waterfall_values.tobytes()).hexdigest()},
        "waterfall_presentation_columns": waterfall_values.size,
        "history_geometry_attributed": args.attribute_history_geometry,
        "solid_noisy_edge_probe": args.solid_noisy_edge_probe,
        "bounded_noisy_edge_probe": args.bounded_noisy_edge_probe,
        "environment": environment, "blocks": blocks,
    }
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "blocks": [
        {"enabled": b["histories_enabled"], "grab_ms": b["grab_ms"],
         "trace_paint_ms": b["trace_paint_ms"]} for b in blocks
    ]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

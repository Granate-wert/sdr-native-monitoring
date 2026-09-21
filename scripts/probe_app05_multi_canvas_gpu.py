"""Two actual V2 compositions sharing nominal graphics admission; fake SDR only.

Separate RTBW and progressive Sweep presenters/canvases, not multi-RX product
integration. Native Windows GL, no visible window. Not a throughput benchmark.
"""
import argparse
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not sys.flags.isolated or args.output.exists():
        parser.error("requires Python -I and a new output")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = "windows"
    import numpy as np
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QWidget
    from scripts.app05_graphics_budget import GraphicsBudget
    from scripts.app05_plot_lifecycle import PlotGpuLifecycle
    from scripts.app05_scene_gpu_support import SceneExtent, image_target, paint_scenes, compare_images
    from scripts.app05_scientific_layers import detach_layers
    from scripts.app05_full_composition import build_plot_plan, draw_plot_composition, paint_plot_backgrounds, paint_qt_commands
    from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
    from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
    from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
    from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
    from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
    from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
    from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests
    from tests.ui_v2.test_app05_prepared_live import measurement
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_Use96Dpi)
    app = QApplication.instance() or QApplication([])
    pool = GraphicsBudget(80*1024*1024)
    fixtures: list[AnalyzerWorkspaceProductTests] = []
    adapters: list[PlotGpuLifecycle] = []
    closed: set[int] = set()
    results = []
    show = QWidget.show
    def hidden_show(widget):
        widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        show(widget)

    def source_hashes(f):
        view = f.page.visualization
        scene, waterfall = view.spectrum_scene, view.waterfall_pane
        arrays = {"spectrum": scene.latest_frame.spectrum.values if hasattr(scene.latest_frame.spectrum, "values")
                  else scene.latest_frame.spectrum.values_db,
                  "frequencies": scene.latest_frame.spectrum.frequencies_hz,
                  "analytical_density": scene._persistence.latest_view.density,
                  "display_density": scene._persistence.image_item.image}
        arrays.update({f"waterfall{i}": item.image for i, item in enumerate(waterfall.image_items)
                       if item.isVisible() and item.image is not None})
        return {key: hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest() for key, value in arrays.items()}

    def capture(index, label, envelope=64*1024*1024):
        f, adapter = fixtures[index], adapters[index]
        scene = f.page.visualization.spectrum_scene
        waterfall = f.page.visualization.waterfall_pane
        f.wait(lambda: scene.displayed_frame is scene.latest_frame
               and scene.trace_envelope(TraceKind.CURRENT) is not None)
        for _ in range(6):
            app.processEvents()
        graphics = (scene._graphics, waterfall._graphics)
        heights = [v.viewport().height() for v in graphics]
        width = max(v.viewport().width() for v in graphics)
        extent = SceneExtent(width, sum(heights), target_budget_bytes=envelope)
        panels = [(v, QRectF(0, sum(heights[:i]), width, heights[i])) for i, v in enumerate(graphics)]
        before = source_hashes(f)
        cpu = image_target(extent, QColor("#151c24"))
        paint_scenes(cpu, panels)
        bundle = detach_layers(scene, waterfall, panels)
        plan = build_plot_plan(scene, waterfall, panels, bundle)
        ordered = image_target(extent, QColor("#151c24"))
        paint_plot_backgrounds(ordered, panels)
        paint_qt_commands(ordered, plan)
        if not compare_images(cpu, ordered)["equal"]:
            raise AssertionError("actual Qt traversal mismatch")
        del ordered
        def draw(device, functions, size):
            draw_plot_composition(device, functions, size, plan, panels, bundle,
                                  resources=adapter.target.scientific_resources())
        image, status = adapter.render(extent, panels, QColor("#151c24"), draw=draw,
                                       expected_revision=adapter.revision)
        if status["backend"] != "gpu" or source_hashes(f) != before:
            raise AssertionError("unexpected fallback or mutated scientific data")
        row = dict(label=label, canvas=index, source=before, gpu_sha256=hashlib.sha256(image.constBits()).hexdigest(),
                   cpu_gpu=compare_images(cpu, image), budget=pool.snapshot(),
                   target=adapter.target.snapshot(), source_unchanged=True, product_accepted=False,
                   speed_accepted=False, qt_traversal_exact=True)
        results.append(row)
        return row

    def publish_live(sequence):
        f = fixtures[0]
        scene = f.page.visualization.spectrum_scene
        snapshot = measurement(f, sequence)
        values = (-90 + 7*np.sin(np.arange(snapshot.spectrum.fft_size)/19+sequence)).astype(np.float32)
        values[700] = -20-sequence
        values.setflags(write=False)
        frame = replace(snapshot.spectrum, values=values)
        snapshot = replace(snapshot, spectrum=frame, persistence=synthetic_persistence(frame, 32, sequence, min(sequence, 4)))
        f.live._snapshot = snapshot
        previous = scene.persistence_metrics.image_uploads
        f.presenter.offer_snapshot_for_render(snapshot)
        f.wait(lambda: scene.displayed_frame is not None and scene.displayed_frame.spectrum is frame
               and scene.persistence_metrics.image_uploads > previous)

    report = dict(scope=__doc__, graphics_budget_scope=pool.snapshot()["scope"],
                  checkout_head=subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
                  tracked_dirty=bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True).strip()),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    try:
        with patch.object(QWidget, "show", hidden_show):
            for _ in range(2):
                f = AnalyzerWorkspaceProductTests("runTest")
                setattr(f, "app", app)
                f.setUp()
                fixtures.append(f)
                f.shell.resize(1920, 1080)
                f.shell.select_workspace("analyzer")
                f.select_and_apply()
                f.page.visualization.waterfall_pane.set_history_seconds(1)
                adapter = PlotGpuLifecycle(f.page.visualization, graphics_budget=pool)
                adapters.append(adapter)
                if not adapter.target.available:
                    raise RuntimeError("native GL required")
            a, b = fixtures
            a.page.primary.click()
            a.wait(lambda: a.live.is_running() and not a.composition.view_model.state.busy)
            for sequence in range(1, 5):
                publish_live(sequence)
            first_a = capture(0, "rtbw-initial")
            b.page.mode.setCurrentIndex(b.page.mode.findData(AnalyzerMode.SWEEP))
            partial, final = importlib.import_module("sdr_monitor._sdr_native")._make_test_sweep_statistics_frames()
            partial, final = _to_domain_progress(partial), _to_domain_line(final)
            assert partial.statistics is not None and final.statistics is not None
            partial_density, final_density = partial.statistics.probability, final.statistics.probability
            with patch.object(_FakeAnalyzerDisplay, "poll_latest",
                              return_value=ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), partial)) as poll:
                b.page.primary.click()
                b.wait(lambda: b.page.visualization.spectrum_scene._persistence._uploaded_density is partial_density)
                first_b = capture(1, "sweep-partial")
                if first_a["gpu_sha256"] == first_b["gpu_sha256"]:
                    raise AssertionError("independent canvases unexpectedly identical")
                for sequence in range(5, 8):
                    publish_live(sequence)
                    updated = capture(0, f"rtbw-update-{sequence}")
                    unchanged = capture(1, f"sweep-unaffected-{sequence}")
                    if updated["gpu_sha256"] == first_a["gpu_sha256"] or unchanged["gpu_sha256"] != first_b["gpu_sha256"]:
                        raise AssertionError("cross-canvas content contamination or stale RTBW")
                latest_a = updated
                poll.return_value = ContinuousSweepDisplaySnapshot(final, ContinuousSweepDisplayMetrics())
                b.composition.analyzer_presenter._poll()
                b.wait(lambda: b.page.visualization.spectrum_scene._persistence._uploaded_density is final_density)
                final_b = capture(1, "sweep-final")
                if final_b["gpu_sha256"] == first_b["gpu_sha256"]:
                    raise AssertionError("Sweep failed to advance")
                a.page.visualization.hide()
                if adapters[0].target.snapshot()["live_targets"]:
                    raise AssertionError("hidden RTBW retained storage")
                if capture(1, "sweep-with-rtbw-hidden")["gpu_sha256"] != final_b["gpu_sha256"]:
                    raise AssertionError("hiding RTBW altered Sweep")
                a.page.visualization.show()
                if capture(0, "rtbw-reshown")["gpu_sha256"] != latest_a["gpu_sha256"]:
                    raise AssertionError("RTBW changed on Show")
                before = adapters[0].target.snapshot()
                try:
                    capture(0, "must-be-denied", envelope=80*1024*1024)
                except MemoryError as error:
                    report["denied"] = str(error)
                else:
                    raise AssertionError("aggregate admission failed to reject oversubscribed frame")
                if adapters[0].target.allocations != before["allocations"]:
                    raise AssertionError("denied frame allocated an FBO")
                if capture(1, "sweep-after-denial")["gpu_sha256"] != final_b["gpu_sha256"]:
                    raise AssertionError("denial damaged another canvas")
                b.tearDown()
                b.doCleanups()
                closed.add(1)
                if not adapters[1].closed:
                    raise AssertionError("closed Sweep retained graphics owner")
                if capture(0, "rtbw-after-sweep-close", envelope=80*1024*1024)["gpu_sha256"] != latest_a["gpu_sha256"]:
                    raise AssertionError("remaining canvas changed after other close")
                report["events"] = [f.events.copy() for f in fixtures]
    finally:
        try:
            for index in reversed(range(len(fixtures))):
                if index not in closed:
                    try:
                        fixtures[index].tearDown()
                    finally:
                        fixtures[index].doCleanups()
        finally:
            for adapter in adapters:
                adapter.close()
    report.update(cases=results, graphics_budget=pool.snapshot(), targets=[a.target.snapshot() for a in adapters],
                  product_reserved=[f.composition.allocation_budget.snapshot().reserved_bytes for f in fixtures],
                  workers=[t.name for t in threading.enumerate() if any(word in t.name.lower() for word in ("sdr", "synthetic"))],
                  outside_imports=[name for name, module in tuple(sys.modules.items())
                    if name.startswith(("scripts", "tests", "sdr_monitor")) and getattr(module, "__file__", None)
                    and not Path(str(module.__file__)).resolve().is_relative_to(root)])
    if pool.charged_bytes or pool.snapshot()["owners"] or report["workers"] or report["outside_imports"] or any(report["product_reserved"]):
        raise AssertionError("multi-canvas lifecycle leaked or used outside checkout")
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    print(json.dumps(dict(cases=len(results), graphics_budget=report["graphics_budget"],
                         product_reserved=report["product_reserved"], workers=report["workers"]), indent=2))


if __name__ == "__main__":
    main()

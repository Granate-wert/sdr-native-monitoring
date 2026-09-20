"""Actual V2 RTBW/progressive Sweep composition -> shared CPU/GPU scene target.

EXPERIMENT only. Uses fake services and existing native synthetic statistics,
never discovers/opens physical SDR. No visible window. No product GL toggle.
Paint-only samples do not measure source latency, Qt delivery, swap or DWM FPS.
Source hashes and layer witnesses prevent accepting a fast empty/stale picture.
"""
import argparse
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from time import perf_counter
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366, 1920, 2560), default=1920)
    parser.add_argument("--height", type=int, choices=(768, 1080, 1440), default=1080)
    parser.add_argument("--dpr", type=float, choices=(1., 1.25, 1.5, 2.), default=1.)
    parser.add_argument("--theme", choices=("dark", "light", "high_contrast"), default="dark")
    parser.add_argument("--persistence", choices=("direct", "visual"), default="direct")
    parser.add_argument("--platform", choices=("windows", "offscreen"), default="windows")
    args = parser.parse_args()
    if not sys.flags.isolated or args.output.exists():
        parser.error("Python -I and new output required")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = args.platform
    import numpy as np
    from PySide6.QtCore import QCoreApplication, QEvent, QRectF, Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QWidget
    from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, compare_images, image_target, paint_scenes
    from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
    from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
    from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
    from sdr_monitor.ui.v2.design import ThemeId, tokens_for_theme
    from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
    from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
    from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
    from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests
    from tests.ui_v2.test_app05_prepared_live import measurement
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_Use96Dpi)
    app = QApplication([])
    f = AnalyzerWorkspaceProductTests("runTest")
    setattr(f, "app", app)
    original_show = QWidget.show

    def hidden_show(widget):
        widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        original_show(widget)

    output_images = args.output.with_suffix("")
    output_images.mkdir(exist_ok=False)
    target = SceneGpuTarget()
    rows = []
    ready = False
    info = dict(scope=__doc__, gpu=target.info, platform=args.platform, logical_size=[args.width, args.height],
        dpr=args.dpr, theme=args.theme, persistence=args.persistence,
        checkout_head=subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    try:
        with patch.object(QWidget, "show", hidden_show):
            f.setUp()
            ready = True
            f.shell.resize(args.width, args.height)
            f.shell.select_workspace("analyzer")
            f.select_and_apply()
            visualization = f.page.visualization
            scene, waterfall = visualization.spectrum_scene, visualization.waterfall_pane
            visualization.set_theme(ThemeId(args.theme))
            scene.set_persistence_render_mode(PersistenceRenderMode(args.persistence))
            waterfall.set_history_seconds(1)  # Explicit quality-fixture view, NOT a speed optimization.
            background = QColor(tokens_for_theme(ThemeId(args.theme)).colors.background)

            def witness():
                density = scene._persistence.image_item
                publication = f.page._last_bundle.spectrum
                arrays = dict(spectrum=scene.trace_envelope(TraceKind.CURRENT).values,
                    density=density.image)
                for name in ("values", "values_db", "frequencies_hz", "quality_flags", "source_segment_indices"):
                    if hasattr(publication, name):
                        arrays["source_" + name] = getattr(publication, name)
                visible_tiles = [item for item in waterfall.image_items if item.isVisible() and item.image is not None]
                for number, item in enumerate(visible_tiles):
                    arrays[f"waterfall_{number}"] = item.image
                hashes = {name: hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()
                          for name, value in arrays.items() if value is not None}
                return dict(hashes=hashes, current_finite=int(np.count_nonzero(np.isfinite(arrays["spectrum"]))),
                    density_visible=density.isVisible() and density.image is not None,
                    persistence_mode=scene._persistence.render_mode.value,
                    persistence_z=density.zValue(), current_z=scene._curves[TraceKind.CURRENT].zValue(),
                    waterfall_visible_tiles=len(visible_tiles), waterfall_rows=waterfall.history_rows,
                    markers=len(scene.markers), sweep_coverage_runs=len(scene.sweep_coverage.strip.runs),
                    publication_kind=f.page._last_bundle.publication_kind.value,
                    publication_identity=dict(source=str(getattr(publication, "source_id", "")),
                        sequence=int(getattr(publication, "sequence", 0)),
                        epoch=str(getattr(publication, "epoch", getattr(publication, "acquisition_epoch", ""))),
                        revision=int(getattr(publication, "revision", 0))))

            def capture(label):
                # Let legitimate axis relayout settle; no changes to frame cadence.
                for _ in range(6):
                    app.processEvents()
                graphics = [scene._graphics, waterfall._graphics]
                heights = [view.viewport().height() for view in graphics]
                width = max(view.viewport().width() for view in graphics)
                extent = SceneExtent(width, sum(heights), args.dpr)
                panels = [(view, QRectF(0, sum(heights[:i]), width, heights[i])) for i, view in enumerate(graphics)]
                before = witness()
                if not before["current_finite"] or not before["density_visible"] or not before["waterfall_visible_tiles"]:
                    raise AssertionError(("missing required scientific layers", label, before))
                cpu = image_target(extent, background)
                paint_scenes(cpu, panels)  # warm existing caches, never a speed sample
                cpu.fill(background)
                begin = perf_counter()
                paint_scenes(cpu, panels)
                cpu_ms = (perf_counter() - begin) * 1000
                row = dict(label=label, source=before, physical_size=[extent.pixel_width, extent.pixel_height],
                    nominal_target_bytes=extent.nominal_target_bytes, cpu_paint_ms=cpu_ms,
                    gpu_available=target.available, speed_acceptance=False,
                    panels=[dict(source=view.mapToScene(view.viewport().rect()).boundingRect().getRect(),
                                 target=rect.getRect()) for view, rect in panels],
                    projection_pending_at_capture=f.composition.spectrum_projector._future is not None)
                if target.available:
                    candidate, gpu_ms = target.render(extent, panels, background)
                    row.update(comparison=compare_images(cpu, candidate), gpu_completed_paint_ms=gpu_ms)
                    row["candidate_accepted"] = row["comparison"]["equal"]
                    row["reference_selected"] = not row["candidate_accepted"]
                    if not candidate.save(str(output_images / f"{label}-gpu.png")):
                        raise RuntimeError("could not save GPU scene evidence")
                    del candidate
                else:
                    row.update(fallback="cpu-reference", comparison=None, candidate_accepted=False, reference_selected=True)
                if not cpu.save(str(output_images / f"{label}-cpu.png")):
                    raise RuntimeError("could not save CPU scene evidence")
                after = witness()
                if before != after:
                    raise AssertionError("render mutated scientific source/layer state")
                row["source_unchanged"] = True
                rows.append(row)

            f.page.primary.click()
            f.wait(lambda: f.live.is_running() and not f.composition.view_model.state.busy)
            for sequence in range(1, 5):
                snapshot = measurement(f, sequence)
                values = (-95 + 9 * np.sin(np.arange(snapshot.spectrum.fft_size) / 17 + sequence)).astype(np.float32)
                values[700] = -20
                values[1700:1703] = -35
                values.setflags(write=False)
                frame = replace(snapshot.spectrum, values=values)
                snapshot = replace(snapshot, spectrum=frame,
                    persistence=synthetic_persistence(frame, 32, sequence, min(sequence, 4)))
                f.live._snapshot = snapshot
                uploads = scene.persistence_metrics.image_uploads
                f.presenter.offer_snapshot_for_render(snapshot)
                try:
                    f.wait(lambda: scene.displayed_frame is not None and scene.displayed_frame.spectrum is frame
                        and scene.persistence_metrics.image_uploads > uploads)
                except Exception:
                    print(dict(stage="rtbw admission", sequence=sequence,
                        displayed=scene.displayed_frame is not None,
                        exact=scene.displayed_frame is not None and scene.displayed_frame.spectrum is frame,
                        uploads=scene.persistence_metrics.image_uploads, previous_uploads=uploads,
                        active=scene._presentation_active, density_active=scene._persistence._presentation_active,
                        pending=str(f.composition.spectrum_projector._future),
                        preparation=str(f.presenter._preparation_future), status=f.page.status.text(),
                        qt_errors=f._qt_errors))
                    raise
            scene.place_marker("M1", float(frame.frequencies_hz[700]))
            capture("rtbw-live")
            f.page.primary.click()
            f.wait(lambda: not f.live.is_running() and not f.composition.view_model.state.busy)
            capture("rtbw-stopped")
            original_scene = scene
            f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
            if scene.latest_frame is not None or scene.markers:
                raise AssertionError("mode switch retained stale RTBW presentation")
            partial, final = importlib.import_module("sdr_monitor._sdr_native")._make_test_sweep_statistics_frames()
            partial, final = _to_domain_progress(partial), _to_domain_line(final)
            assert partial.statistics is not None and final.statistics is not None
            partial_density, final_density = partial.statistics.probability, final.statistics.probability
            snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), partial)
            with patch.object(_FakeAnalyzerDisplay, "poll_latest", return_value=snapshot) as poll:
                f.page.primary.click()
                f.wait(lambda: scene._persistence._uploaded_density is partial_density)
                if f.page.visualization.spectrum_scene is not original_scene:
                    raise AssertionError("Sweep did not reuse the RTBW canvas")
                capture("sweep-partial")
                poll.return_value = ContinuousSweepDisplaySnapshot(final, ContinuousSweepDisplayMetrics())
                f.composition.analyzer_presenter._poll()
                f.wait(lambda: scene._persistence._uploaded_density is final_density)
                capture("sweep-complete")
                f.page.primary.click()
                f.wait(lambda: f.composition.analyzer_presenter.can_close())
            info["events"] = f.events.copy()
            info["same_rtbw_sweep_canvas"] = True
    finally:
        try:
            if ready:
                try:
                    f.tearDown()
                finally:
                    f.doCleanups()
        finally:
            target.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    info.update(cases=rows, target_lifetime=target.snapshot(),
        remaining_workers=[t.name for t in threading.enumerate() if any(s in t.name.lower() for s in ("sdr", "synthetic"))],
        post_close_reserved_bytes=f.composition.allocation_budget.snapshot().reserved_bytes,
        all_pixels_equal=target.available and all(row["comparison"]["equal"] for row in rows))
    info["outside_imports"] = [name for name, module in tuple(sys.modules.items())
        if name.startswith(("scripts", "tests", "sdr_monitor")) and getattr(module, "__file__", None)
        and not Path(str(module.__file__)).resolve().is_relative_to(root)]
    if info["remaining_workers"] or info["outside_imports"] or info["post_close_reserved_bytes"]:
        raise AssertionError("scene diagnostic did not release/import within its verified checkout")
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(info, stream, indent=2)
    print(json.dumps(dict(gpu=info["gpu"], cases=[dict(label=row["label"], comparison=row["comparison"])
        for row in rows], lifetime=info["target_lifetime"], all_pixels_equal=info["all_pixels_equal"]), indent=2))


if __name__ == "__main__":
    main()

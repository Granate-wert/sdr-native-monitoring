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
    parser.add_argument("--scientific", action="store_true", help="Detached image shader/explicit paths ONLY, not all-layer acceptance")
    parser.add_argument("--qt-raster-compat", action="store_true", help="Explicit Qt 6.11.1 Indexed8/RGBA8888 nearest raster sampling experiment")
    parser.add_argument("--images-only", action="store_true", help="With --scientific, isolate exact texture sampling/blending")
    parser.add_argument("--composition", action="store_true", help="Actual Qt stacking with scientific replacements; two complete plot viewports")
    parser.add_argument("--component-review", action="store_true", help="With composition: isolate each actual command on the same opaque background")
    parser.add_argument("--persistent-resources", action="store_true", help="Experimental composition with context-owned reusable GPU storage")
    parser.add_argument("--recreate-context", action="store_true", help="With persistent resources: explicitly destroy native context and verify CPU/recreated GPU per capture")
    parser.add_argument("--fail-upload", action="store_true", help="Inject texture upload failure, verify current CPU scene and explicit GPU recovery")
    parser.add_argument("--widget-lifecycle", action="store_true", help="Bind prototype to actual V2 widget hide/show and close")
    parser.add_argument("--lifetime-seconds", type=int, choices=(0, 10, 60, 120), default=0,
                        help="Bounded stopped-scene GPU lifetime run; not a live speed benchmark")
    args = parser.parse_args()
    if not sys.flags.isolated or args.output.exists():
        parser.error("Python -I and new output required")
    if args.images_only and not args.scientific:
        parser.error("--images-only requires --scientific")
    if args.composition and (not args.scientific or args.images_only):
        parser.error("--composition requires --scientific without --images-only")
    if args.qt_raster_compat and not args.composition:
        parser.error("--qt-raster-compat requires --composition; not the pixel-centre oracle")
    if args.component_review and not args.composition:
        parser.error("--component-review requires --composition")
    if args.persistent_resources and (not args.composition or args.component_review):
        parser.error("--persistent-resources requires composition without component-review")
    if args.recreate_context and not args.persistent_resources:
        parser.error("--recreate-context requires --persistent-resources")
    if args.fail_upload and (not args.persistent_resources or args.recreate_context):
        parser.error("--fail-upload requires persistent resources without recreate-context")
    if args.widget_lifecycle and (not args.persistent_resources or args.fail_upload or args.recreate_context):
        parser.error("--widget-lifecycle requires persistent resources without fault injection")
    if args.lifetime_seconds and not args.widget_lifecycle:
        parser.error("--lifetime-seconds requires --widget-lifecycle")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = args.platform
    import numpy as np
    from PySide6.QtCore import QCoreApplication, QEvent, QRectF, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QWidget
    from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, compare_images, image_target, paint_scenes
    from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
    from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
    from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
    from sdr_monitor.ui.v2.design import ThemeId, tokens_for_theme
    from sdr_monitor.ui.v2.spectrum.contracts import BandMask, TraceKind
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
    lifecycle = None
    rows = []
    ready = False
    info = dict(scope=__doc__, gpu=target.info, platform=args.platform, logical_size=[args.width, args.height],
        dpr=args.dpr, theme=args.theme, persistence=args.persistence, scientific_only=args.scientific,
        images_only=args.images_only, qt_raster_compat=args.qt_raster_compat,
        full_plot_composition=args.composition,
        persistent_resources=args.persistent_resources,
        recreate_context=args.recreate_context,
        fail_upload=args.fail_upload,
        widget_lifecycle=args.widget_lifecycle,
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
            if args.widget_lifecycle:
                from scripts.app05_plot_lifecycle import PlotGpuLifecycle, PresentationInactive
                target.close()
                lifecycle = PlotGpuLifecycle(visualization)
                target = lifecycle.target
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
                if scene._persistence.latest_view is not None:
                    arrays["analytical_density"] = scene._persistence.latest_view.density
                for name in ("values", "values_db", "frequencies_hz", "quality_flags", "source_segment_indices"):
                    if hasattr(publication, name):
                        arrays["source_" + name] = getattr(publication, name)
                visible_tiles = [item for item in waterfall.image_items if item.isVisible() and item.image is not None]
                for number, item in enumerate(visible_tiles):
                    arrays[f"waterfall_{number}"] = item.image
                for kind, item in list(scene._curves.items()) + [("previous-sweep", scene.sweep_coverage.history)]:
                    if item.isVisible() and item.curve.isVisible() and item.curve.xData is not None:
                        key = getattr(kind, "value", kind)
                        arrays[f"curve_{key}_x"] = item.curve.xData
                        arrays[f"curve_{key}_y"] = item.curve.yData
                hashes = {name: hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()
                          for name, value in arrays.items() if value is not None}
                return dict(hashes=hashes, current_finite=int(np.count_nonzero(np.isfinite(arrays["spectrum"]))),
                    density_visible=density.isVisible() and density.image is not None,
                    persistence_mode=scene._persistence.render_mode.value,
                    persistence_z=density.zValue(), current_z=scene._curves[TraceKind.CURRENT].zValue(),
                    waterfall_visible_tiles=len(visible_tiles), waterfall_rows=waterfall.history_rows,
                    markers=len(scene.markers), sweep_coverage_runs=len(scene.sweep_coverage.strip.runs),
                    coverage_runs_sha256=hashlib.sha256(repr(scene.sweep_coverage.strip.runs).encode()).hexdigest(),
                    band_masks_sha256=hashlib.sha256(repr(scene._band_masks).encode()).hexdigest(),
                    publication_kind=f.page._last_bundle.publication_kind.value,
                    publication_identity=dict(source=str(getattr(publication, "source_id", "")),
                        sequence=int(getattr(publication, "sequence", 0)),
                        epoch=str(getattr(publication, "epoch", getattr(publication, "acquisition_epoch", ""))),
                        revision=int(getattr(publication, "revision", 0))))

            def capture(label):
                # Let legitimate axis relayout settle; no changes to frame cadence.
                for _ in range(6):
                    app.processEvents()
                f.wait(lambda: scene.trace_envelope(TraceKind.CURRENT) is not None
                       and scene.displayed_frame is scene.latest_frame)
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
                bundle = None
                gpu_resources = {}
                plan = None
                if args.scientific:
                    from scripts.app05_scientific_layers import detach_layers, layer_metadata, paint_layers
                    from scripts.app05_scientific_gpu import draw_scientific_gpu
                    bundle = detach_layers(scene, waterfall, panels, qt_raster_compat=args.qt_raster_compat)
                    if args.composition:
                        from scripts.app05_full_composition import build_plot_plan, draw_plot_composition, paint_plot_backgrounds, paint_qt_commands, plan_metadata
                        plan = build_plot_plan(scene, waterfall, panels, bundle)
                begin = perf_counter()
                if bundle is None or plan is not None:
                    paint_scenes(cpu, panels)
                else:
                    paint_layers(cpu, bundle, curves=not args.images_only)
                cpu_ms = (perf_counter() - begin) * 1000
                row = dict(label=label, source=before, physical_size=[extent.pixel_width, extent.pixel_height],
                    nominal_target_bytes=extent.nominal_target_bytes, cpu_paint_ms=cpu_ms,
                    gpu_available=target.available, speed_acceptance=False,
                    panels=[dict(source=view.mapToScene(view.viewport().rect()).boundingRect().getRect(),
                                 target=rect.getRect()) for view, rect in panels],
                    projection_pending_at_capture=f.composition.spectrum_projector._future is not None)
                if bundle is not None:
                    row["scientific_layers"] = layer_metadata(bundle)
                if plan is not None:
                    assert bundle is not None
                    if extent.nominal_target_bytes + extent.pixel_width*extent.pixel_height*4 + bundle.retained_bytes > extent.target_budget_bytes:
                        raise MemoryError("full-composition CPU reference budget exceeded")
                    explicit_cpu = image_target(extent, background)
                    paint_plot_backgrounds(explicit_cpu, panels)
                    paint_qt_commands(explicit_cpu, plan)
                    row["qt_traversal_comparison"] = compare_images(cpu, explicit_cpu)
                    row["plot_plan"] = plan_metadata(plan)
                    explicit_cpu.save(str(output_images / f"{label}-ordered-cpu.png"))
                    del explicit_cpu
                traversal_valid = plan is None or row["qt_traversal_comparison"]["equal"]
                if target.available and traversal_valid:
                    def draw(device, functions, size):
                        if plan is not None:
                            reusable = target.scientific_resources() if args.persistent_resources else None
                            gpu_resources.update(draw_plot_composition(device, functions, size, plan, panels, bundle, resources=reusable))
                            if reusable is not None:
                                gpu_resources["persistent"] = reusable.snapshot()
                        else:
                            gpu_resources.update(draw_scientific_gpu(device, functions, size, bundle, curves=not args.images_only))
                    if lifecycle is None:
                        candidate, gpu_ms = target.render(extent, panels, background,
                            draw=draw if bundle is not None else None)
                    else:
                        candidate, status = lifecycle.render(extent, panels, background, draw=draw,
                                                             expected_revision=lifecycle.revision)
                        if status["backend"] != "gpu":
                            raise AssertionError("unexpected lifecycle CPU fallback")
                        gpu_ms = status["completed_paint_ms"]
                    row.update(comparison=compare_images(cpu, candidate), gpu_completed_paint_ms=gpu_ms)
                    if args.persistent_resources:
                        retained_gpu = gpu_resources["persistent"]["live_bytes"]
                        # Existing candidate readback and retained GL storage
                        # remain live while drawing the ephemeral same-source oracle.
                        oracle_extent = replace(extent, target_budget_bytes=extent.target_budget_bytes
                            -retained_gpu-extent.pixel_width*extent.pixel_height*4)
                        def fresh_draw(device, functions, size):
                            draw_plot_composition(device, functions, oracle_extent, plan, panels, bundle)
                        fresh, _ = target.render(extent, panels, background, draw=fresh_draw)
                        row["persistent_vs_fresh_gpu"] = compare_images(fresh, candidate)
                        del fresh
                        if not row["persistent_vs_fresh_gpu"]["equal"]:
                            raise AssertionError("persistent resources changed exact same-scene GPU pixels")
                    if args.fail_upload:
                        from PySide6.QtOpenGL import QOpenGLTexture
                        reduced = replace(extent, target_budget_bytes=extent.target_budget_bytes
                            -extent.pixel_width*extent.pixel_height*4)
                        def failed_draw(device, functions, size):
                            draw_plot_composition(device, functions, reduced, plan, panels, bundle,
                                                  resources=target.scientific_resources())
                        with patch.object(QOpenGLTexture, "setData", side_effect=RuntimeError("diagnostic upload failure")):
                            fallback, status = target.render_or_cpu(extent, panels, background, draw=failed_draw)
                        cpu_parity = compare_images(cpu, fallback)
                        del fallback
                        failed = target.snapshot()
                        if status["backend"] != "cpu" or not cpu_parity["equal"]:
                            raise AssertionError("upload failure fallback is not current actual CPU scene")
                        if failed["live_targets"] or failed["scientific_resources"]["live_bytes"]:
                            raise AssertionError("failed upload retained partial GPU resources")
                        target.recover_context()
                        renewed, _ = target.render(extent, panels, background, draw=failed_draw)
                        gpu_parity = compare_images(candidate, renewed)
                        del renewed
                        row["upload_failure"] = dict(failed=failed, cpu_fallback=cpu_parity,
                                                     recovered_gpu=gpu_parity)
                        if not gpu_parity["equal"]:
                            raise AssertionError("upload recovery changed same-scene GPU pixels")
                    if args.recreate_context:
                        from shiboken6 import delete
                        old_generation = target.context_generation
                        assert target._context is not None
                        delete(target._context)  # explicit diagnostic destruction, not an SDR/Live restart
                        destroyed = target.snapshot()
                        fallback, status = target.render_or_cpu(extent, panels, background)
                        cpu_parity = compare_images(cpu, fallback)
                        del fallback
                        if status["backend"] != "cpu" or not cpu_parity["equal"]:
                            raise AssertionError("destroyed-context fallback is not the current CPU scene")
                        target.recreate_context()
                        reduced = replace(extent, target_budget_bytes=extent.target_budget_bytes
                            -extent.pixel_width*extent.pixel_height*4)
                        def renewed_draw(device, functions, size):
                            draw_plot_composition(device, functions, reduced, plan, panels, bundle,
                                                  resources=target.scientific_resources())
                        renewed, _ = target.render(extent, panels, background, draw=renewed_draw,
                                                   expected_generation=old_generation+1)
                        gpu_parity = compare_images(candidate, renewed)
                        del renewed
                        row["context_recreation"] = dict(old_generation=old_generation,
                            new_generation=target.context_generation, destroyed=destroyed,
                            cpu_fallback=cpu_parity, renewed_gpu=gpu_parity)
                        if not gpu_parity["equal"]:
                            raise AssertionError("context recreation changed exact same-scene GPU pixels")
                    if args.images_only:
                        from scripts.app05_scientific_layers import paint_texel_oracle
                        assert bundle is not None
                        oracle_peak = extent.nominal_target_bytes + extent.pixel_width * extent.pixel_height * 8 + bundle.retained_bytes
                        if oracle_peak > extent.target_budget_bytes:
                            raise MemoryError("scientific diagnostic oracle target budget exceeded")
                        oracle = cpu.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
                        oracle.fill(background)
                        paint_texel_oracle(oracle, bundle)
                        oracle = oracle.convertToFormat(cpu.format())
                        row["analytic_image_comparison"] = compare_images(oracle, candidate)
                        row["qt_vs_analytic_image_comparison"] = compare_images(oracle, cpu)
                        row["nominal_oracle_peak_bytes"] = oracle_peak
                        del oracle
                    row["gpu_resources"] = gpu_resources
                    row["candidate_accepted"] = row["comparison"]["equal"] and not args.scientific
                    row["reference_selected"] = not row["candidate_accepted"]
                    if not candidate.save(str(output_images / f"{label}-gpu.png")):
                        raise RuntimeError("could not save GPU scene evidence")
                    del candidate
                else:
                    row.update(fallback="cpu-reference", comparison=None, candidate_accepted=False, reference_selected=True)
                    if not traversal_valid:
                        row["gpu_declined"] = "Qt traversal does not match original scene; substitutions not admitted"
                if not cpu.save(str(output_images / f"{label}-cpu.png")):
                    raise RuntimeError("could not save CPU scene evidence")
                after = witness()
                if before != after:
                    raise AssertionError("render mutated scientific source/layer state")
                row["source_unchanged"] = True
                if args.component_review and target.available and traversal_valid:
                    from scripts.app05_full_composition import review_plot_commands
                    # Release full-scene images before isolated bounded review.
                    del cpu
                    row["component_review"] = review_plot_commands(target, extent, plan, background, bundle)
                    if before != witness():
                        raise AssertionError("command review mutated scientific source/layer state")
                rows.append(row)

            def visibility_cycle(label):
                if lifecycle is None:
                    return
                before = witness()
                events = f.events.copy()
                revision = lifecycle.revision
                visualization.hide()  # actual Qt event, not a manual active flag
                hidden = target.snapshot()
                if hidden["live_targets"] or hidden["scientific_resources"]["live_bytes"]:
                    raise AssertionError("hidden actual plot retained GPU storage")
                try:
                    lifecycle.render(SceneExtent(80, 64), [], background, expected_revision=revision)
                except PresentationInactive:
                    pass
                else:
                    raise AssertionError("hidden actual plot rendered")
                visualization.show()
                try:
                    lifecycle.render(SceneExtent(80, 64), [], background, expected_revision=revision)
                except ValueError:
                    pass
                else:
                    raise AssertionError("pre-hide render request was accepted after Show")
                capture(label)
                after = witness()
                changed = [key for key in before["hashes"] if before["hashes"].get(key) != after["hashes"].get(key)]
                if any(key != "density" for key in changed) or f.events != events:
                    raise AssertionError("visibility change altered source bytes or acquisition events")
                # Record exact rehydration independently of CPU/GPU raster quality.
                # The same-scene CPU/GPU oracle still runs independently above.
                rows[-1]["visibility_cycle"] = dict(hidden=hidden, revision=lifecycle.revision,
                    events_unchanged=True, measurement_hashes_unchanged=True, changed_rendered_layers=changed,
                    rendered_layers_unchanged=not changed, quality_accepted=not changed)

            def stopped_lifetime():
                if not args.lifetime_seconds:
                    return
                assert lifecycle is not None
                from scripts.app05_scientific_layers import detach_layers
                from scripts.app05_full_composition import build_plot_plan, draw_plot_composition
                for _ in range(6):
                    app.processEvents()  # settle queued Stop/layout work before lifetime baseline
                before = witness()
                events = f.events.copy()
                baseline = None
                frames = peak_bytes = hide_cycles = 0
                started = perf_counter()
                while perf_counter() - started < args.lifetime_seconds:
                    app.processEvents()  # no detached plan/payload survives this boundary
                    if frames and frames % 128 == 0:
                        visualization.hide()
                        if target.snapshot()["live_targets"]:
                            raise AssertionError("lifetime hidden plot retained target")
                        visualization.show()
                        f.wait(lambda: scene.trace_envelope(TraceKind.CURRENT) is not None
                               and scene.displayed_frame is scene.latest_frame)
                        hide_cycles += 1
                    graphics = [scene._graphics, waterfall._graphics]
                    heights = [view.viewport().height() for view in graphics]
                    width = max(view.viewport().width() for view in graphics)
                    size = SceneExtent(width, sum(heights), args.dpr)
                    panels = [(view, QRectF(0, sum(heights[:i]), width, heights[i])) for i, view in enumerate(graphics)]
                    warm = image_target(size, background)
                    paint_scenes(warm, panels)  # materialize accepted deferred Qt images after Stop/Show
                    del warm
                    bundle = detach_layers(scene, waterfall, panels, qt_raster_compat=args.qt_raster_compat)
                    plan = build_plot_plan(scene, waterfall, panels, bundle)
                    def draw(device, functions, extent):
                        draw_plot_composition(device, functions, extent, plan, panels, bundle,
                                              resources=target.scientific_resources())
                    image, status = lifecycle.render(size, panels, background, draw=draw,
                                                     expected_revision=lifecycle.revision)
                    digest = hashlib.sha256(image.constBits()).hexdigest()
                    if baseline is None:
                        baseline = digest
                        image.save(str(output_images / "lifetime-first.png"))
                    if status["backend"] != "gpu" or digest != baseline:
                        image.save(str(output_images / "lifetime-changed.png"))
                        print(dict(lifetime_frame=frames, elapsed=perf_counter()-started, size=[size.width, size.height],
                                   before=before, after=witness(), events=f.events), flush=True)
                        raise AssertionError("stopped-scene pixels changed during lifetime run")
                    stats = target.snapshot()["scientific_resources"]
                    if stats["program_builds"] != 2 or stats["live_textures"] > 3:
                        raise AssertionError("GPU object count grew beyond fixed slots")
                    peak_bytes = max(peak_bytes, stats["live_bytes"])
                    frames += 1
                    del draw, image, plan, bundle, panels, graphics
                if witness() != before or f.events != events:
                    raise AssertionError("stopped lifetime mutated measurement or restarted acquisition")
                info["bounded_lifetime"] = dict(seconds=perf_counter()-started, frames=frames,
                    hide_cycles=hide_cycles, peak_scientific_bytes=peak_bytes, stable_gpu_sha256=baseline,
                    source_unchanged=True, events_unchanged=True, speed_acceptance=False,
                    scope="stopped actual V2 scene, sequential native GL; not live/multi-pane throughput or driver memory proof")

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
            if args.composition:
                scene.set_band_masks((BandMask(float(frame.frequencies_hz[600]), float(frame.frequencies_hz[800]), "composition fixture"),))
            capture("rtbw-live")
            visibility_cycle("rtbw-live-reshown")
            f.page.primary.click()
            f.wait(lambda: not f.live.is_running() and not f.composition.view_model.state.busy)
            capture("rtbw-stopped")
            visibility_cycle("rtbw-stopped-reshown")
            original_scene = scene
            f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
            if scene.latest_frame is not None or scene.markers:
                raise AssertionError("mode switch retained stale RTBW presentation")
            if lifecycle is not None:
                lifecycle.invalidate()  # explicit presentation boundary, NOT an SDR epoch mutation
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
                if args.composition:
                    scene.set_band_masks((BandMask(float(partial.frequencies_hz[64]), float(partial.frequencies_hz[256]), "composition fixture"),))
                    scene.place_marker("M1", float(partial.frequencies_hz[128]))
                capture("sweep-partial")
                visibility_cycle("sweep-partial-reshown")
                poll.return_value = ContinuousSweepDisplaySnapshot(final, ContinuousSweepDisplayMetrics())
                f.composition.analyzer_presenter._poll()
                f.wait(lambda: scene._persistence._uploaded_density is final_density)
                capture("sweep-complete")
                visibility_cycle("sweep-complete-reshown")
                if lifecycle is not None:
                    new_epoch = partial.epoch + 1
                    next_partial = replace(partial, epoch=new_epoch,
                                           statistics=replace(partial.statistics, epoch=new_epoch))
                    poll.return_value = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), next_partial)
                    f.composition.analyzer_presenter._poll()
                    f.wait(lambda: scene.latest_frame is not None and scene.latest_frame.spectrum is next_partial
                           and scene._persistence._uploaded_density is next_partial.statistics.probability)
                    if scene.markers or waterfall.history_rows > 1:
                        raise AssertionError("new Sweep epoch retained markers or prior waterfall history")
                    lifecycle.invalidate()
                    scene.set_band_masks((BandMask(float(partial.frequencies_hz[64]), float(partial.frequencies_hz[256]), "composition fixture"),))
                    scene.place_marker("M1", float(partial.frequencies_hz[128]))
                    capture("sweep-new-epoch")
                    rows[-1]["epoch_boundary"] = dict(old=partial.epoch, new=new_epoch, old_history_cleared=True)
                    visibility_cycle("sweep-new-epoch-reshown")
                f.page.primary.click()
                f.wait(lambda: f.composition.analyzer_presenter.can_close())
                stopped_lifetime()
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
            if lifecycle is not None:
                lifecycle.close()
                info["widget_lifetime"] = dict(closed=lifecycle.closed, revision=lifecycle.revision,
                                               event_error=lifecycle.event_error)
            else:
                target.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    info.update(cases=rows, target_lifetime=target.snapshot(),
        remaining_workers=[t.name for t in threading.enumerate() if any(s in t.name.lower() for s in ("sdr", "synthetic"))],
        post_close_reserved_bytes=f.composition.allocation_budget.snapshot().reserved_bytes,
        all_pixels_equal=target.available and all(row["comparison"] is not None and row["comparison"]["equal"] for row in rows))
    info["visibility_quality_accepted"] = all(row.get("visibility_cycle", {}).get("quality_accepted", True) for row in rows)
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

"""Actual V2 shell size/DPR/axis capture with deterministic SYNTHETIC data.

No physical I/O, no OS display or firewall changes. Uses existing product
composition test ports and normal Qt layout; direct local Waterfall history
feeding is ONLY a layout/axis test, not acquisition/coherence/performance proof.
"""

import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
from time import time_ns

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", nargs=2, type=int, required=True)
    parser.add_argument("--expected-pixels", nargs=2, type=int, required=True)
    parser.add_argument("--minimum-plot-fraction", type=float,
                        help="optional predeclared measured-ViewBox area gate; no default design acceptance")
    parser.add_argument("--theme", choices=("dark", "light", "high_contrast"), default="dark")
    parser.add_argument("--locale", choices=("ru", "en"), default="ru")
    parser.add_argument("--direction", choices=("newest_at_top", "newest_at_bottom"), default="newest_at_top")
    args = parser.parse_args()
    if min(*args.size, *args.expected_pixels) <= 0:
        parser.error("size/pixels must be positive")
    if (args.minimum_plot_fraction is not None
            and (not math.isfinite(args.minimum_plot_fraction) or not 0 < args.minimum_plot_fraction <= 1)):
        parser.error("minimum plot fraction must be finite in (0, 1]")
    output = args.output.resolve()
    capture = output.with_suffix(".png")
    if output.exists() or capture.exists():
        parser.error("output/capture must be new")
    output.parent.mkdir(parents=True, exist_ok=True)

    import numpy as np
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from sdr_monitor.domain.identity import TimestampQuality
    from sdr_monitor.domain.live import LivePerformance, LiveSpectrumFrame
    from sdr_monitor.ui.v2.design import ThemeId
    from sdr_monitor.ui.v2.i18n import UiLocale
    from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_spectrum
    from sdr_monitor.ui.v2.waterfall import WaterfallDirection
    from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests

    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("SDR Native Monitoring Bench")
    app.setApplicationName("APP05 Time Axis Layout")
    harness = AnalyzerWorkspaceProductTests("runTest")
    harness.app = app
    report = {}
    ready = False
    try:
        harness.setUp()
        ready = True
        shell, page = harness.shell, harness.page
        shell.select_appearance_theme(ThemeId(args.theme))
        shell.select_appearance_locale(UiLocale(args.locale))
        shell.resize(*args.size)
        app.processEvents()
        harness.select_and_apply()
        page.primary.click()
        harness.wait(lambda: harness.live.is_running() and not harness.composition.view_model.state.busy)
        snapshot = harness.live.latest_snapshot()
        configuration = snapshot.applied.applied
        frequencies = configuration.center_hz + (np.arange(4096) - 2048) * (61.44e6 / 4096)
        values = (-90 + 30 * np.exp(-((np.arange(4096) - 2100) / 35) ** 2)).astype(np.float32)
        frame = LiveSpectrumFrame(sequence=1, timestamp_ns=time_ns(), source_id="synthetic-layout",
            config_generation=snapshot.generation, center_frequency_hz=configuration.center_hz,
            sample_rate_hz=61.44e6, fft_size=4096, hop_size=4096,
            frequencies_hz=frequencies, values=values, unit="dBFS/bin",
            clock_domain="unix_ns", timestamp_quality=TimestampQuality.SYNTHETIC)
        delivered = replace(snapshot, spectrum=frame,
            performance=LivePerformance(iq_block_rate_hz=25, rate_observation_interval_s=1))
        harness.live._snapshot = delivered
        harness.presenter.offer_snapshot_for_render(delivered)
        scene = page.visualization.spectrum_scene
        harness.wait(lambda: scene.displayed_frame is not None)
        pane = page.visualization.waterfall_pane
        pane.set_history_seconds(10)
        pane.set_direction(WaterfallDirection(args.direction))
        pane.clear_history()
        base = waterfall_line_from_spectrum(frame)
        intervals = (35, 51, 39, 55, 45, 59, 42)
        stamps = [0]
        for index in range(369):
            stamps.append(stamps[-1] + intervals[index % len(intervals)] * 1_000_000)
        for index, stamp in enumerate(stamps):
            pane.set_line(replace(base, timestamp_ns=frame.timestamp_ns - stamps[-1] + stamp,
                                  sequence=index + 1))
        pane.set_frozen(True)
        app.processEvents()
        shell.setWindowTitle("SYNTHETIC UI V2 layout/axis probe - no RF")
        image = QImage(*args.expected_pixels, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        try:
            specs = pane._time_axis.generateDrawSpecs(painter)
        finally:
            painter.end()
        texts = [] if specs is None else specs[2]
        overlaps = sum(rect.intersects(other) for index, (rect, _, _) in enumerate(texts)
                       for other, _, _ in texts[index + 1:])
        before = [(label, rect.x(), rect.y()) for rect, _, label in texts]
        gutter = pane._time_axis.textWidth
        pane.set_render_visible(False)
        app.processEvents()
        pane.set_render_visible(True)
        app.processEvents()
        pixmap = shell.grab()
        if pixmap.isNull() or not pixmap.save(str(capture), "PNG"):
            raise RuntimeError("Qt capture failed")
        plots = (scene.view_box, pane.view_box)
        plot_area = sum(plot.width() * plot.height() for plot in plots)
        controls = {}
        for name in ("primary", "source", "mode", "status", "periods"):
            widget = getattr(page, name)
            rect = QRect(widget.mapTo(shell, QPoint()), widget.size())
            controls[name] = dict(visible=widget.isVisible(),
                rect=[rect.x(), rect.y(), rect.width(), rect.height()],
                in_client=shell.rect().contains(rect),
                content_height=widget.contentsRect().height(),
                font_line_height=widget.fontMetrics().lineSpacing(),
                single_line_fits=(widget.fontMetrics().horizontalAdvance(widget.text()) <= widget.contentsRect().width())
                    if name == "periods" else None)
        report = dict(schema="app05-time-axis-layout-v1", source="synthetic-no-RF",
            source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            requested_logical_size=args.size, actual_logical_size=[shell.width(), shell.height()],
            expected_pixels=args.expected_pixels, actual_pixels=[pixmap.width(), pixmap.height()],
            dpr=shell.devicePixelRatioF(), platform=app.platformName(), theme=args.theme, locale=args.locale,
            direction=args.direction, history_rows=pane.history_rows,
            axis_texts=before, overlaps=overlaps, gutter_before_show=gutter,
            gutter_after_show=pane._time_axis.textWidth,
            plot_area_fraction=plot_area / (shell.width() * shell.height()),
            minimum_plot_fraction=args.minimum_plot_fraction,
            plot_area_gate_passed=(plot_area / (shell.width() * shell.height()) >= args.minimum_plot_fraction)
                if args.minimum_plot_fraction is not None else None,
            periods_text=page.periods.text(), capture=str(capture),
            capture_sha256=hashlib.sha256(capture.read_bytes()).hexdigest(),
            controls=controls,
            controls_visible=all(value["visible"] and value["in_client"] for value in controls.values()),
            scope="Qt surface/layout/relative producer-time axis only; not physical SDR, DWM or monitor acceptance")
    finally:
        if ready:
            harness.tearDown()
        harness.doCleanups()
    report["workers_after_close"] = [thread.name for thread in threading.enumerate()
                                     if thread is not threading.main_thread()]
    report["reserved_after_close"] = harness.composition.allocation_budget.snapshot().reserved_bytes
    report["passed"] = bool(report["actual_pixels"] == args.expected_pixels
        and not report["overlaps"] and report["controls_visible"] and report["axis_texts"]
        and report["controls"]["periods"]["content_height"] >= report["controls"]["periods"]["font_line_height"]
        and report["controls"]["periods"]["single_line_fits"]
        and report["plot_area_gate_passed"] is not False
        and not report["workers_after_close"] and not report["reserved_after_close"])
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "actual_pixels", "dpr", "plot_area_fraction")}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

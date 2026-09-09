"""Reproducible synthetic Qt evidence, never Windows DPI/RX/performance proof."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import time

import numpy as np

from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import UiLocale
from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--physical-size", nargs=2, type=int)
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    if not math.isfinite(args.scale) or args.scale <= 0:
        parser.error("--scale must be finite and positive")
    if args.physical_size and min(args.physical_size) <= 0:
        parser.error("--physical-size must contain positive dimensions")
    args.output.mkdir(parents=True, exist_ok=True)
    owner = AnalyzerWorkspaceProductTests
    owner.setUpClass()
    case = owner()
    case.setUp()
    records = []
    try:
        if args.physical_size and not math.isclose(case.shell.devicePixelRatioF(), args.scale, abs_tol=0.01):
            raise RuntimeError("Set QT_SCALE_FACTOR before starting this fresh process; actual DPR does not match --scale")
        case.select_and_apply()
        case.page.primary.click()
        case.wait(lambda: case.live.is_running() and not case.composition.view_model.state.busy)
        snapshot = case.live.latest_snapshot()
        config = snapshot.applied.applied
        indices = np.arange(config.fft_size)
        values = -95 + 2 * np.sin(indices * 0.57)
        values += 55 * np.exp(-((indices - 1100) / 18) ** 2)
        values += 35 * np.exp(-((indices - 2700) / 180) ** 2)
        frame = LiveSpectrumFrame(
            sequence=1, timestamp_ns=time.time_ns(), source_id="fake-pluto-usb",
            config_generation=snapshot.generation, center_frequency_hz=config.center_hz,
            sample_rate_hz=config.sample_rate_hz, fft_size=config.fft_size, hop_size=config.fft_size,
            frequencies_hz=config.center_hz + (indices - config.fft_size / 2) * config.sample_rate_hz / config.fft_size,
            values=values.astype(np.float32), unit="dBFS/bin",
        )
        delivered = replace(snapshot, spectrum=frame)
        case.live._snapshot = delivered
        case.presenter.offer_snapshot_for_render(delivered)
        case.wait(lambda: case.page._last_bundle is not None)
        for locale in (UiLocale.RU, UiLocale.EN):
            case.shell.select_appearance_locale(locale)
            for theme in (ThemeId.DARK, ThemeId.LIGHT, ThemeId.HIGH_CONTRAST):
                case.shell.set_theme(theme)
                sizes = ((round(args.physical_size[0] / args.scale),
                          round(args.physical_size[1] / args.scale)),) if args.physical_size else ((1280, 720), (1366, 768))
                for width, height in sizes:
                    case.shell.resize(width, height)
                    case.app.processEvents()
                    name = f"analyzer-{locale.value}-{theme.value}-{width}x{height}.png"
                    if not case.shell.grab().save(str(args.output / name)):
                        raise RuntimeError(f"failed to save {name}")
                    plots = (case.page.visualization.spectrum_scene.view_box.sceneBoundingRect(),
                             case.page.visualization.waterfall_pane.view_box.sceneBoundingRect())
                    fraction = sum(rect.width() * rect.height() for rect in plots) / (case.shell.width() * case.shell.height())
                    records.append({"file": name, "locale": locale.value, "theme": theme.value,
                                    "requested_size": [width, height],
                                    "physical_client_target": args.physical_size,
                                    "requested_scale": args.scale,
                                    "device_pixel_ratio": case.shell.devicePixelRatioF(),
                                    "fits_requested_client": case.shell.width() <= width and case.shell.height() <= height,
                                    "plot_area_fraction": fraction,
                                    "actual_size": [case.shell.width(), case.shell.height()]})
                    if not args.physical_size and width == 1366 and fraction < 0.65:
                        raise AssertionError(f"{name}: useful plot area {fraction:.3%} < 65%")
        manifest = {"scope": "synthetic Qt offscreen, actual composition, in-memory SDR",
                    "not_evidence_for": ["hardware", "Windows DPI", "performance", "release EXE"],
                    "screenshots": records}
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    finally:
        try:
            case.tearDown()
        finally:
            case.doCleanups()


if __name__ == "__main__":
    main()

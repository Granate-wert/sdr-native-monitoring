"""Reproducible synthetic/offscreen actual-composition evidence, NEVER RX/EXE proof."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import os
import sys
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product_fixture
from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal


def spectrum(sequence: int, *, revision: int | None = None, gap: bool = False):
    frequency = 100e6 + np.arange(4096) * (128e6 / 4096)
    rng = np.random.default_rng(20260917 + sequence)
    values = -92. + rng.normal(0., 1.2, frequency.size)
    for center, amplitude, width in ((113.5e6, 40, .18e6), (149.8e6, 50, .6e6),
                                     (175e6, 30, 2e6), (209e6, 55, .15e6)):
        values += amplitude * np.exp(-.5 * ((frequency-center)/width)**2)
    segments = np.arange(4096, dtype=np.int32) // 1024
    missing = segments >= (revision if revision is not None else 2 if gap else 4)
    values[missing] = np.nan
    quality = np.where(missing, 4096, 0).astype(np.uint32)
    sources = np.where(missing, -1, segments).astype(np.int32)
    values = values.astype(np.float32)
    for array in (frequency, values, quality, sources):
        array.setflags(write=False)
    frame = progress(sequence, revision) if revision is not None else terminal(sequence, gap=gap)
    return replace(frame, frequencies_hz=frequency, values_db=values,
                   quality_flags=quality, source_segment_indices=sources)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    output = parser.parse_args().output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    product_fixture.AnalyzerWorkspaceProductTests.setUpClass()
    harness = product_fixture.AnalyzerWorkspaceProductTests("runTest")
    harness.setUp()
    try:
        harness.select_and_apply()
        page = harness.page
        page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
        page.start_frequency.setValue(100)
        page.stop_frequency.setValue(228)
        pane = page.visualization.waterfall_pane
        pane.set_history_seconds(1)
        snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), spectrum(1, revision=1))
        with patch.object(_FakeAnalyzerDisplay, "poll_latest", side_effect=lambda: snapshot):
            page.primary.click()
            harness.wait(lambda: harness.composition.analyzer_presenter._timer.isActive())
            for sequence in range(1, 28):
                snapshot = ContinuousSweepDisplaySnapshot(spectrum(sequence), ContinuousSweepDisplayMetrics())
                harness.composition.analyzer_presenter._poll()
            snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), spectrum(28, revision=3))
            harness.composition.analyzer_presenter._poll()
            for width, height in ((1920, 1080), (2560, 1440)):
                harness.shell.resize(width, height)
                harness.app.processEvents()
                target = output / f"synthetic-sweep-partial-{width}x{height}.png"
                if not harness.shell.grab().save(str(target)):
                    raise RuntimeError(f"cannot save {target}")
                print(target)
            harness.shell.select_appearance_locale(UiLocale.EN)
            snapshot = ContinuousSweepDisplaySnapshot(spectrum(28, gap=True), ContinuousSweepDisplayMetrics())
            page.primary.click()
            harness.wait(lambda: harness.composition.analyzer_presenter.can_close())
            harness.shell.resize(1920, 1080)
            harness.app.processEvents()
            target = output / "synthetic-sweep-stopped-gap-en-1920x1080.png"
            if not harness.shell.grab().save(str(target)):
                raise RuntimeError(f"cannot save {target}")
            print(target)
            print("EVIDENCE_SCOPE: synthetic actual-composition / offscreen only; no RX, EXE or Windows DPI claim")
    finally:
        harness.tearDown()
        harness.doCleanups()


if __name__ == "__main__":
    main()

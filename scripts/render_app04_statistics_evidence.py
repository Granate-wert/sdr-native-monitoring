"""Compiled-native synthetic statistics in actual V2 composition. NOT physical RX/EXE/DPI."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import sys
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product_fixture
from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    output = parser.parse_args().output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    native = importlib.import_module("sdr_monitor._sdr_native")
    partial, terminal = native._make_test_sweep_statistics_frames()
    partial, terminal = _to_domain_progress(partial), _to_domain_line(terminal)
    product_fixture.AnalyzerWorkspaceProductTests.setUpClass()
    harness = product_fixture.AnalyzerWorkspaceProductTests("runTest")
    harness.setUp()
    try:
        harness.select_and_apply()
        page = harness.page
        page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
        page.start_frequency.setValue(100)
        page.stop_frequency.setValue(104.095)
        snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), partial)
        with patch.object(_FakeAnalyzerDisplay, "poll_latest", side_effect=lambda: snapshot):
            page.primary.click()
            harness.wait(lambda: page._last_statistics_key is not None)
            for width, height in ((1920, 1080), (2560, 1440)):
                harness.shell.resize(width, height)
                harness.app.processEvents()
                target = output / f"native-synthetic-partial-average-density-{width}x{height}.png"
                if not harness.shell.grab().save(str(target)):
                    raise RuntimeError(f"cannot save {target}")
                print(target)
            snapshot = ContinuousSweepDisplaySnapshot(terminal, ContinuousSweepDisplayMetrics())
            page.primary.click()
            harness.wait(lambda: harness.composition.analyzer_presenter.can_close())
            harness.shell.select_appearance_locale(UiLocale.EN)
            harness.shell.resize(1920, 1080)
            harness.app.processEvents()
            target = output / "native-synthetic-stopped-statistics-en-1920x1080.png"
            if not harness.shell.grab().save(str(target)):
                raise RuntimeError(f"cannot save {target}")
            print(target)
            print("Compiled native kernel -> immutable bridge -> shared V2 spectrum; synthetic/offscreen only")
    finally:
        try:
            harness.tearDown()
        finally:
            harness.doCleanups()


if __name__ == "__main__":
    main()

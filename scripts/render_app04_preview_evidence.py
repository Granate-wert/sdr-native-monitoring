"""Inert actual-composition plan UI; synthetic/offscreen, NOT EXE/RX/DPI proof."""
import argparse
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    output = parser.parse_args().output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture.AnalyzerWorkspaceProductTests.setUpClass()
    harness = fixture.AnalyzerWorkspaceProductTests("runTest")
    harness.setUp()
    try:
        harness.select_and_apply()  # In-memory fake device only.
        page = harness.page
        page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
        assert page.sweep_preview.resolve()
        page.sweep_preview.details_button.click()
        for locale in UiLocale:
            harness.shell.select_appearance_locale(locale)
            for width, height in ((1920, 1080), (2560, 1440)):
                harness.shell.resize(width, height)
                harness.app.processEvents()
                target = output / f"plan-{locale.value}-{width}x{height}.png"
                if not harness.shell.grab().save(str(target)):
                    raise RuntimeError(f"Cannot save {target}")
                print(target)
        page.stop_frequency.setValue(99)
        harness.app.processEvents()
        target = output / "invalid-range-en.png"
        if not harness.shell.grab().save(str(target)):
            raise RuntimeError(f"Cannot save {target}")
        assert harness.events == [], "Preview must not dispatch an acquisition command"
        print(target)
        print("No native/device open, Start, retune or acquisition; no performance claim")
    finally:
        try:
            harness.tearDown()
        finally:
            harness.doCleanups()


if __name__ == "__main__":
    main()

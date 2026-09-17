"""Native synthetic Sweep publications in V2; not RF/EXE/Windows DPI evidence."""
import argparse
from dataclasses import replace
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import UiLocale
from tests.ui_v2.test_app04_sweep_coverage_overlay import SweepCoverageOverlayTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    output = parser.parse_args().output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    harness = SweepCoverageOverlayTests("runTest")
    harness.setUp()
    try:
        shell = harness.harness.shell
        for locale, theme, width, height in (
            (UiLocale.RU, ThemeId.DARK, 1920, 1080),
            (UiLocale.EN, ThemeId.LIGHT, 2560, 1440),
            (UiLocale.RU, ThemeId.HIGH_CONTRAST, 1366, 768),
        ):
            shell.select_appearance_locale(locale)
            shell.set_theme(theme)
            shell.resize(width, height)
            harness.scene.sweep_coverage.clear()
            for phase, line, progress in (
                ("first-partial", None, harness.early),
                ("previous-partial", harness.previous, harness.early),
                ("cancel-gap", replace(harness.gap, sequence=5), None),
                ("empty-current", replace(harness.empty, sequence=6), None),
            ):
                harness.publish(line, progress)
                # Explicit display scale so history's -20 dBFS peak is visible;
                # history is deliberately excluded from current autorange.
                harness.scene.set_reference_level(-10)
                harness.scene.set_db_per_division(12.5)
                harness.harness.app.processEvents()
                target = output / f"{phase}-{locale.value}-{width}x{height}.png"
                if not shell.grab().save(str(target)):
                    raise RuntimeError(f"Cannot save {target}")
                print(target)
        assert harness.harness.events == [], "Evidence must not issue acquisition commands"
        print("Native synthetic publications; history peak modified for visibility; no RF/EXE/DPI claim")
    finally:
        try:
            harness.tearDown()
        finally:
            harness.doCleanups()


if __name__ == "__main__":
    main()

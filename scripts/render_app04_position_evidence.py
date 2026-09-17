"""Native synthetic assembler -> shared V2 canvas. No physical RX/EXE/DPI claim."""
import argparse
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import UiLocale
from tests.ui_v2.test_app04_sweep_position_overlay import SweepPositionOverlayTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    output = parser.parse_args().output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    harness = SweepPositionOverlayTests("runTest")
    harness.setUp()
    try:
        shell = harness.harness.shell
        for locale, theme, width, height in (
            (UiLocale.RU, ThemeId.DARK, 1920, 1080),
            (UiLocale.EN, ThemeId.LIGHT, 2560, 1440),
        ):
            shell.select_appearance_locale(locale)
            shell.set_theme(theme)
            shell.resize(width, height)
            for phase, line, progress in (
                ("partial-right", None, harness.early),
                ("terminal-left", harness.final, harness.early),
            ):
                harness.publish(line, progress)
                harness.harness.app.processEvents()
                target = output / f"{phase}-{locale.value}-{width}x{height}.png"
                if not shell.grab().save(str(target)):
                    raise RuntimeError(f"Cannot save {target}")
                print(target)
        assert harness.harness.events == [], "No acquisition command is permitted in this renderer"
        print("Native synthetic spectra; not device/EXE/Windows DPI/performance evidence")
    finally:
        try:
            harness.tearDown()
        finally:
            harness.doCleanups()


if __name__ == "__main__":
    main()

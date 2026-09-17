"""Synthetic/offscreen actual V2 composition; not EXE, RX, DPI or speed proof."""
import argparse
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import UiLocale
from tests.ui_v2.test_app04_segment_inspector import SegmentInspectorTests, snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    output = parser.parse_args().output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    harness = SegmentInspectorTests("runTest")
    harness.setUp()
    try:
        shell = harness.harness.shell
        for locale, theme, width, height in (
            (UiLocale.RU, ThemeId.DARK, 1920, 1080),
            (UiLocale.EN, ThemeId.LIGHT, 2560, 1440),
            (UiLocale.RU, ThemeId.HIGH_CONTRAST, 960, 540),
        ):
            shell.select_appearance_locale(locale)
            shell.set_theme(theme)
            shell.resize(width, height)
            harness.harness.app.processEvents()
            harness.publish(snapshot(revision=2))
            panel = harness.open()
            target = output / f"segments-{locale.value}-{theme.value}-{width}x{height}.png"
            if not shell.grab().save(str(target)):
                raise RuntimeError(f"Cannot save {target}")
            print(target)
            panel.segment.setCurrentIndex(3)
            harness.harness.app.processEvents()
            target = output / f"pending-{locale.value}-{theme.value}-{width}x{height}.png"
            if not shell.grab().save(str(target)):
                raise RuntimeError(f"Cannot save {target}")
            print(target)
            shell._hide_narrow_inspector_drawer()
        assert harness.harness.events == [], "Evidence must not dispatch acquisition"
        print("Synthetic scalar/trace data; no device/receiver commands, no performance claim")
    finally:
        try:
            harness.tearDown()
        finally:
            harness.doCleanups()


if __name__ == "__main__":
    main()

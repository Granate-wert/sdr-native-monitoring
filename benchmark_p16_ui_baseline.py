"""P16UI-00 offscreen legacy GUI inventory and timing harness.

The harness never opens a DFL, recording, or SDR device.  It redirects
QSettings into a temporary file and can write only anonymised UI structure and
timing evidence to an explicitly requested output directory.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import cast
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings, QThreadPool
from PySide6.QtWidgets import QApplication, QMainWindow

from esw_dfl import gui
from esw_dfl.ui_inventory import (
    capture_main_window_inventory,
    measure_ui_timing,
    settings_keys_from_source,
    write_inventory_atomic,
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _dispose_window(window: QMainWindow, app: QApplication) -> None:
    """Mirror the tested MainWindow shutdown sequence without test imports."""

    window._frame_loader.cancel_all()
    window._frame_scheduler.stop()
    QThreadPool.globalInstance().waitForDone(5_000)
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchmark_p16_ui_baseline.py")
    parser.add_argument("--output-dir", type=Path, help="optional private evidence directory")
    parser.add_argument("--creation-runs", type=_positive_int, default=3)
    parser.add_argument("--status-update-runs", type=_positive_int, default=60)
    parser.add_argument("--screenshot", action="store_true", help="write an optional offscreen PNG alongside JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    existing_application = QApplication.instance()
    app = existing_application if isinstance(existing_application, QApplication) else QApplication([])
    with tempfile.TemporaryDirectory(prefix="sdr-p16-ui-baseline-") as directory:
        settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)

        def create_window() -> QMainWindow:
            with patch.object(gui, "QSettings", lambda *args, **kwargs: settings):
                return cast(QMainWindow, gui.MainWindow())

        window = create_window()
        try:
            inventory = capture_main_window_inventory(window, gui)
            payload: dict[str, object] = {
                "inventory": inventory.to_dict(),
                "legacy_settings_keys": sorted(settings_keys_from_source(Path(gui.__file__))),
                "timing": measure_ui_timing(
                    create_window,
                    lambda item: _dispose_window(item, app),
                    creation_runs=args.creation_runs,
                    status_update_runs=args.status_update_runs,
                ).to_dict(),
            }
            if args.output_dir is not None:
                evidence = args.output_dir / "p16ui_00_inventory.json"
                write_inventory_atomic(evidence, payload)
                payload["evidence_path"] = str(evidence)
                if args.screenshot:
                    args.output_dir.mkdir(parents=True, exist_ok=True)
                    window.show()
                    app.processEvents()
                    screenshot = args.output_dir / "p16ui_00_baseline.png"
                    temporary = Path(f"{screenshot}.part")
                    try:
                        if not window.grab().save(str(temporary), "PNG"):
                            raise RuntimeError("could not save offscreen baseline screenshot")
                        os.replace(temporary, screenshot)
                    except BaseException:
                        temporary.unlink(missing_ok=True)
                        raise
                    payload["screenshot_path"] = str(screenshot)
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        finally:
            _dispose_window(window, app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Small isolated renderer used to prove a UI V2 gallery at a requested DPI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.gallery import DesignSystemGallery


def _register_windows_font(filename: str) -> None:
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / filename
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--theme", choices=[theme.value for theme in ThemeId], default=ThemeId.DARK.value)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    _register_windows_font("segoeui.ttf")
    _register_windows_font("consola.ttf")
    gallery = DesignSystemGallery(theme=ThemeId(args.theme))
    gallery.resize(1280, 720)
    gallery.show()
    app.processEvents()
    pixmap = gallery.grab()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(args.output)):
        raise RuntimeError(f"could not save {args.output}")
    print(f"dpr={pixmap.devicePixelRatio():.1f} width={pixmap.width()} height={pixmap.height()}")
    gallery.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

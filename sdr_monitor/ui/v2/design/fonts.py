"""Product-local V2 typography setup for Windows Qt processes."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QFontDatabase

_WINDOWS_UI_FONTS = ("segoeui.ttf", "consola.ttf")
_registered_font_paths: set[Path] = set()


def register_windows_ui_fonts() -> tuple[Path, ...]:
    """Register installed Windows text fallbacks once for a headless Qt process.

    Normal desktop Windows resolves these fonts itself.  Registering their known
    local paths makes the same Cyrillic-capable typography available to the
    offscreen renderer used by V2 evidence without bundling, replacing or
    modifying a system font.
    """

    if os.name != "nt":
        return ()
    fonts_directory = Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Fonts"
    registered: list[Path] = []
    for filename in _WINDOWS_UI_FONTS:
        font_path = fonts_directory / filename
        if not font_path.is_file() or font_path in _registered_font_paths:
            continue
        if QFontDatabase.addApplicationFont(str(font_path)) >= 0:
            _registered_font_paths.add(font_path)
            registered.append(font_path)
    return tuple(registered)

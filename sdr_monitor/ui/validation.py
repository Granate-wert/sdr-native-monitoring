"""Standalone accessibility and subprocess DPI validation helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget


DPI_MATRIX = (96, 192, 288)
WINDOW_MATRIX = ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160))


@dataclass(frozen=True, slots=True)
class AccessibilityIssue:
    widget_class: str
    object_name: str
    description: str
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class AccessibilityReport:
    issues: tuple[AccessibilityIssue, ...]
    focusable_widgets: int
    named_widgets: int

    @property
    def passed(self) -> bool:
        return not any(item.severity == "error" for item in self.issues)


def audit_accessibility(root: QWidget) -> AccessibilityReport:
    issues: list[AccessibilityIssue] = []
    focusable = 0
    named = 0
    for widget in (root, *root.findChildren(QWidget)):
        focusable_now = widget.focusPolicy() != Qt.FocusPolicy.NoFocus
        if not focusable_now:
            continue
        focusable += 1
        name = widget.accessibleName().strip() or getattr(widget, "text", lambda: "")().strip()
        if name:
            named += 1
        else:
            issues.append(AccessibilityIssue(type(widget).__name__, widget.objectName(), "Focusable control has no accessible name"))
    return AccessibilityReport(tuple(issues), focusable, named)


def run_dpi_probe(scale: int, width: int = 1280, height: int = 720) -> subprocess.CompletedProcess[str]:
    if scale not in DPI_MATRIX:
        raise ValueError(f"unsupported DPI scale: {scale}")
    code = "from PySide6.QtWidgets import QApplication; from sdr_monitor.ui.app_shell import SDRAppShell; app=QApplication([]); w=SDRAppShell(); w.resize(%d,%d); w.show(); app.processEvents(); assert w.width() > 0; w.close(); print('dpi-ok')" % (width, height)
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_SCALE_FACTOR"] = str(scale / 96.0)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=environment, check=False, timeout=30)


def shortcut_collisions(shortcuts: dict[str, str]) -> tuple[str, ...]:
    reverse: dict[str, list[str]] = {}
    for action, shortcut in shortcuts.items():
        reverse.setdefault(shortcut.lower(), []).append(action)
    return tuple(shortcut for shortcut, actions in reverse.items() if len(actions) > 1)


__all__ = ["AccessibilityIssue", "AccessibilityReport", "DPI_MATRIX", "WINDOW_MATRIX", "audit_accessibility", "run_dpi_probe", "shortcut_collisions"]

"""P16UI-00 helpers for a deterministic, read-only legacy GUI inventory.

The functions in this module inspect an already created ``MainWindow``.  They
do not change application behaviour, settings, or source data; the inventory
is a compatibility contract for the later strangler migration.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
import ast
import json
import os
from pathlib import Path
import time
from types import ModuleType

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QDockWidget, QMainWindow, QMenu, QToolBar


REQUIRED_DOCK_OBJECT_NAMES: tuple[str, ...] = (
    "filesTracesDock",
    "markersDock",
    "measurementsDock",
    "propertiesDock",
    "displayDock",
    "waterfallSettingsDock",
    "playbackDock",
    "eventsDock",
    "logDock",
    "metadataDock",
    "channelPowerDock",
    "channelPowerTimeDock",
    "heatmapDock",
    "liveSdrDock",
)

REQUIRED_LEGACY_SETTINGS_KEYS: frozenset[str] = frozenset(
    {
        "geometry",
        "windowState",
        "windowStateVersion",
        "splitter",
        "theme",
        "frame_navigation/sequential_mode",
        "frame_navigation/wheel_step",
        "frame_navigation/touchpad_threshold",
        "frame_navigation/fps",
        "frame_navigation/settle_delay_ms",
        "heatmap/enabled",
        "heatmap/persistence_mode",
        "heatmap/window_unit",
        "heatmap/window_frames",
        "heatmap/window_seconds",
        "heatmap/follow_playhead",
        "heatmap/sampling_policy",
        "heatmap/normalization",
        "heatmap/power_min_dbm",
        "heatmap/power_max_dbm",
        "heatmap/power_bins",
        "heatmap/opacity",
        "heatmap/palette",
        "heatmap/color_scale_mode",
        "heatmap/color_min",
        "heatmap/color_max",
        "heatmap/half_life_seconds",
        "heatmap/half_life_unit",
    }
)


# Keys written unconditionally by MainWindow.closeEvent. The Heatmap
# entries above are persisted only after their controls are changed, so they
# remain part of the migration inventory but are not a close-lifecycle claim.
PERSISTED_ON_CLOSE_SETTINGS_KEYS: frozenset[str] = frozenset(
    {
        "geometry",
        "windowState",
        "windowStateVersion",
        "splitter",
        "theme",
        "frame_navigation/sequential_mode",
        "frame_navigation/wheel_step",
        "frame_navigation/touchpad_threshold",
        "frame_navigation/fps",
        "frame_navigation/settle_delay_ms",
    }
)


@dataclass(frozen=True, slots=True)
class ActionInventory:
    text: str
    shortcut: str
    object_name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class DockInventory:
    object_name: str
    title: str


@dataclass(frozen=True, slots=True)
class MenuInventory:
    title: str
    actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolbarInventory:
    object_name: str
    title: str
    actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UiInventory:
    docks: tuple[DockInventory, ...]
    actions: tuple[ActionInventory, ...]
    menus: tuple[MenuInventory, ...]
    toolbars: tuple[ToolbarInventory, ...]
    dialog_classes: tuple[str, ...]
    duplicate_shortcuts: Mapping[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "sdr-native-p16-ui-inventory",
            "schema_version": 1,
            "docks": [asdict(item) for item in self.docks],
            "actions": [asdict(item) for item in self.actions],
            "menus": [asdict(item) for item in self.menus],
            "toolbars": [asdict(item) for item in self.toolbars],
            "dialog_classes": list(self.dialog_classes),
            "duplicate_shortcuts": {key: list(value) for key, value in self.duplicate_shortcuts.items()},
        }


@dataclass(frozen=True, slots=True)
class UiTimingBaseline:
    creation_ms: tuple[float, ...]
    status_update_ms: tuple[float, ...]

    def to_dict(self) -> dict[str, float | int]:
        return {
            "creation_runs": len(self.creation_ms),
            "status_update_runs": len(self.status_update_ms),
            "creation_p50_ms": _percentile(self.creation_ms, 50.0),
            "creation_p95_ms": _percentile(self.creation_ms, 95.0),
            "status_update_p50_ms": _percentile(self.status_update_ms, 50.0),
            "status_update_p95_ms": _percentile(self.status_update_ms, 95.0),
        }


def capture_main_window_inventory(window: QMainWindow, gui_module: ModuleType) -> UiInventory:
    """Capture stable structure from a constructed legacy ``MainWindow``."""

    actions = tuple(
        sorted(
            (
                ActionInventory(
                    text=action.text(),
                    shortcut=action.shortcut().toString(),
                    object_name=action.objectName(),
                    enabled=action.isEnabled(),
                )
                for action in window.findChildren(QAction)
                if action.text()
            ),
            key=lambda item: (item.text, item.shortcut, item.object_name),
        )
    )
    docks = tuple(
        sorted(
            (DockInventory(dock.objectName(), dock.windowTitle()) for dock in window.findChildren(QDockWidget)),
            key=lambda item: item.object_name,
        )
    )
    menus = tuple(
        MenuInventory(
            title=menu.title(),
            actions=tuple(action.text() for action in menu.actions() if action.text()),
        )
        for menu in window.findChildren(QMenu)
        if menu.parent() is window.menuBar() and menu.title()
    )
    toolbars = tuple(
        ToolbarInventory(
            object_name=toolbar.objectName(),
            title=toolbar.windowTitle(),
            actions=tuple(action.text() for action in toolbar.actions() if action.text()),
        )
        for toolbar in window.findChildren(QToolBar)
    )
    dialog_classes = tuple(
        sorted(
            name
            for name, value in vars(gui_module).items()
            if isinstance(value, type) and issubclass(value, QDialog) and value is not QDialog
        )
    )
    return UiInventory(
        docks=docks,
        actions=actions,
        menus=menus,
        toolbars=toolbars,
        dialog_classes=dialog_classes,
        duplicate_shortcuts=duplicate_shortcuts(actions),
    )


def duplicate_shortcuts(actions: Iterable[ActionInventory]) -> dict[str, tuple[str, ...]]:
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for action in actions:
        if action.shortcut:
            grouped[action.shortcut].append(action.text)
    return {shortcut: tuple(sorted(texts)) for shortcut, texts in sorted(grouped.items()) if len(texts) > 1}


def settings_keys_from_source(source_path: str | Path) -> frozenset[str]:
    """Extract literal QSettings keys used by the legacy GUI source."""

    root = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(root):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"value", "setValue"} or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            keys.add(first.value)
    return frozenset(keys)


def measure_ui_timing(
    create_window: Callable[[], QMainWindow],
    dispose_window: Callable[[QMainWindow], None],
    *,
    creation_runs: int = 3,
    status_update_runs: int = 60,
) -> UiTimingBaseline:
    """Measure bounded construction and status-refresh work without a device."""

    if creation_runs <= 0 or status_update_runs <= 0:
        raise ValueError("timing run counts must be positive")
    creation_ms: list[float] = []
    status_update_ms: list[float] = []
    for _ in range(creation_runs):
        started = time.perf_counter()
        window = create_window()
        creation_ms.append((time.perf_counter() - started) * 1000.0)
        try:
            updater = getattr(window, "_update_status", None)
            if not callable(updater):
                raise TypeError("legacy window does not expose _update_status")
            for _ in range(status_update_runs):
                started = time.perf_counter()
                updater()
                status_update_ms.append((time.perf_counter() - started) * 1000.0)
        finally:
            dispose_window(window)
    return UiTimingBaseline(tuple(creation_ms), tuple(status_update_ms))


def write_inventory_atomic(path: str | Path, payload: Mapping[str, object]) -> Path:
    """Write a manifest through a temporary file, keeping partial evidence out."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{target}.part")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate percentile of an empty sequence")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower))


__all__ = [
    "ActionInventory",
    "DockInventory",
    "MenuInventory",
    "PERSISTED_ON_CLOSE_SETTINGS_KEYS",
    "REQUIRED_DOCK_OBJECT_NAMES",
    "REQUIRED_LEGACY_SETTINGS_KEYS",
    "ToolbarInventory",
    "UiInventory",
    "UiTimingBaseline",
    "capture_main_window_inventory",
    "duplicate_shortcuts",
    "measure_ui_timing",
    "settings_keys_from_source",
    "write_inventory_atomic",
]

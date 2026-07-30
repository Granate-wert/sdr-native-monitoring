from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Protocol


BASE_DPI = 96.0


class TkWindow(Protocol):
    def winfo_id(self) -> int: ...

    def winfo_fpixels(self, value: str) -> float: ...

    def winfo_screenwidth(self) -> int: ...

    def winfo_screenheight(self) -> int: ...


@dataclass(frozen=True, slots=True)
class WorkArea:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class WindowGeometry:
    width: int
    height: int
    x: int
    y: int
    min_width: int
    min_height: int

    @property
    def tk_geometry(self) -> str:
        return f"{self.width}x{self.height}+{self.x}+{self.y}"


def enable_windows_dpi_awareness() -> None:
    """Enable crisp per-monitor rendering before the first Tk window is created."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, Windows 10 1703+.
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE, Windows 8.1+.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def get_window_dpi(window: TkWindow) -> float:
    override = os.environ.get("ESW_DFL_DPI_OVERRIDE")
    if override:
        try:
            return max(BASE_DPI, min(480.0, float(override)))
        except ValueError:
            pass
    if sys.platform == "win32":
        try:
            dpi = int(ctypes.windll.user32.GetDpiForWindow(window.winfo_id()))
            if dpi > 0:
                return float(dpi)
        except (AttributeError, OSError, TypeError):
            pass
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            if dpi > 0:
                return float(dpi)
        except (AttributeError, OSError):
            pass
    try:
        tk_dpi = float(window.winfo_fpixels("1i"))
        return tk_dpi if tk_dpi > 0 else BASE_DPI
    except (TypeError, ValueError):
        return BASE_DPI


def get_work_area(window: TkWindow) -> WorkArea:
    if sys.platform == "win32":
        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = Rect()
        try:
            # SPI_GETWORKAREA excludes the taskbar.
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width > 0 and height > 0:
                    return WorkArea(int(rect.left), int(rect.top), width, height)
        except (AttributeError, OSError):
            pass
    return WorkArea(0, 0, int(window.winfo_screenwidth()), int(window.winfo_screenheight()))


def calculate_window_geometry(
    work_area: WorkArea,
    dpi: float,
    desired_logical: tuple[int, int] = (1380, 850),
    minimum_logical: tuple[int, int] = (820, 560),
) -> WindowGeometry:
    """Fit a logical-size window into any physical work area and center it."""
    scale = max(1.0, dpi / BASE_DPI)
    max_width = max(1, int(work_area.width * 0.94))
    max_height = max(1, int(work_area.height * 0.92))
    width = min(max_width, max(320, int(round(desired_logical[0] * scale))))
    height = min(max_height, max(240, int(round(desired_logical[1] * scale))))
    min_width = min(width, max(300, int(round(minimum_logical[0] * scale))), max_width)
    min_height = min(height, max(220, int(round(minimum_logical[1] * scale))), max_height)
    x = work_area.left + max(0, (work_area.width - width) // 2)
    y = work_area.top + max(0, (work_area.height - height) // 2)
    return WindowGeometry(width, height, x, y, min_width, min_height)

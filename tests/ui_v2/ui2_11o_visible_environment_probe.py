"""Capture real Qt/Windows display metadata for a visible UI2 walkthrough.

This helper deliberately does not construct the product shell or touch a
presenter.  It records only the current interactive display environment and
refuses to label an offscreen/minimal Qt platform as visible evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import re
import sys
from typing import Protocol

from PySide6.QtGui import QGuiApplication


_SOURCE_REVISION = re.compile(r"^[0-9a-f]{7,40}$")
_NON_VISIBLE_PLATFORMS = frozenset({"offscreen", "minimal", "minimalegl", "vnc"})


class _RectLike(Protocol):
    def x(self) -> int: ...

    def y(self) -> int: ...

    def width(self) -> int: ...

    def height(self) -> int: ...


class _ScreenLike(Protocol):
    def geometry(self) -> _RectLike: ...

    def availableGeometry(self) -> _RectLike: ...

    def logicalDotsPerInch(self) -> float: ...

    def physicalDotsPerInch(self) -> float: ...

    def devicePixelRatio(self) -> float: ...


@dataclass(frozen=True, slots=True)
class RectEvidence:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ScreenEvidence:
    index: int
    geometry: RectEvidence
    available_geometry: RectEvidence
    logical_dpi: float
    physical_dpi: float
    device_pixel_ratio: float
    qt_coordinate_scale_percent: float
    effective_device_scale_percent: float
    estimated_native_pixel_width: int
    estimated_native_pixel_height: int


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-revision",
        required=True,
        help="Git revision of the visible product build being examined.",
    )
    return parser.parse_args()


def _rect_payload(rect: _RectLike) -> RectEvidence:
    return RectEvidence(x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height())


def _screen_payload(screen: _ScreenLike, index: int) -> ScreenEvidence:
    logical_dpi = float(screen.logicalDotsPerInch())
    device_pixel_ratio = float(screen.devicePixelRatio())
    geometry = _rect_payload(screen.geometry())
    return ScreenEvidence(
        index=index,
        geometry=geometry,
        available_geometry=_rect_payload(screen.availableGeometry()),
        logical_dpi=round(logical_dpi, 3),
        physical_dpi=round(float(screen.physicalDotsPerInch()), 3),
        device_pixel_ratio=round(device_pixel_ratio, 3),
        qt_coordinate_scale_percent=round(logical_dpi / 96.0 * 100.0, 3),
        effective_device_scale_percent=round(device_pixel_ratio * 100.0, 3),
        estimated_native_pixel_width=round(geometry.width * device_pixel_ratio),
        estimated_native_pixel_height=round(geometry.height * device_pixel_ratio),
    )


def main() -> int:
    arguments = _arguments()
    revision = arguments.source_revision.strip().casefold()
    if _SOURCE_REVISION.fullmatch(revision) is None:
        raise SystemExit("--source-revision must be a 7-40 character lowercase hexadecimal Git revision")

    app = QGuiApplication.instance() or QGuiApplication([])
    platform_name = QGuiApplication.platformName().strip().casefold()
    if platform_name in _NON_VISIBLE_PLATFORMS:
        raise SystemExit(f"Qt platform {platform_name!r} is not visible Windows evidence")
    if sys.platform != "win32" or platform_name != "windows":
        raise SystemExit(f"expected an interactive Windows Qt platform, got {sys.platform!r}/{platform_name!r}")

    screens = tuple(app.screens())
    if not screens:
        raise SystemExit("Qt reported no screens")
    primary = app.primaryScreen()
    payload = {
        "schema": "ui2.visible-environment.v1",
        "evidence_scope": "current-interactive-windows-session-only",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": revision,
        "qt_platform": platform_name,
        "screen_count": len(screens),
        "primary_screen_index": screens.index(primary) if primary in screens else None,
        "screens": [asdict(_screen_payload(screen, index)) for index, screen in enumerate(screens)],
        "limitations": [
            "This records only the current display configuration.",
            "It is not proof for another DPI, resolution, theme, focus path, screen reader, or monitor transition.",
            "It creates no product shell, presenter, service, device, receiver, or recording session.",
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

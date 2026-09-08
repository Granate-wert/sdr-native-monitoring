"""UI-owned state that must never become a second Live-session truth."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UiWorkspace(StrEnum):
    """Product workspaces known to UI V2, independent of legacy navigation."""

    HOME = "home"
    LIVE = "live"
    SWEEP = "sweep"
    CALIBRATION = "calibration"
    RECORDING = "recording"
    REPLAY = "replay"
    DIAGNOSTICS = "diagnostics"


@dataclass(frozen=True, slots=True)
class UiPresentationPreferences:
    """Purely visual preferences; none duplicates device or stream state."""

    navigation_expanded: bool = False
    inspector_visible: bool = True
    display_fps: int = 60
    theme_id: str = "dark"
    locale_id: str = "ru"

    def __post_init__(self) -> None:
        if self.display_fps not in (15, 30, 60, 120, 144, 240):
            raise ValueError("display_fps must be one supported presentation cadence")
        if not self.theme_id.strip():
            raise ValueError("theme_id must not be blank")
        if self.locale_id not in {"ru", "en"}:
            raise ValueError("locale_id must be one supported UI V2 locale")


@dataclass(frozen=True, slots=True)
class AppViewState:
    """Top-level UI state; lifecycle truth continues to come from snapshots."""

    active_workspace: UiWorkspace = UiWorkspace.HOME
    preferences: UiPresentationPreferences = UiPresentationPreferences()

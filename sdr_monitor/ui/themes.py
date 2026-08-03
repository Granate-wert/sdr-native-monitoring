"""The only stylesheet source for standalone SDR widgets."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from .design_tokens import DARK_COLORS, HIGH_CONTRAST_COLORS, LIGHT_COLORS, SemanticColors, StatusTone, ThemeId


def _stylesheet(colors: SemanticColors, *, high_contrast: bool = False) -> str:
    weight = "600" if high_contrast else "400"
    return f"""
QWidget {{ background: {colors.surface}; color: {colors.text}; font-family: 'Segoe UI Variable'; font-size: 13px; }}
QMainWindow, QStatusBar, #topBar, #navigationRail, #inspector {{ background: {colors.panel}; }}
QFrame[card='true'] {{ background: {colors.elevated}; border: 1px solid {colors.border}; border-radius: 6px; }}
QLabel[role='secondary'] {{ color: {colors.secondary_text}; }}
QLabel[role='muted'] {{ color: {colors.muted}; }}
QLabel[numeric='true'] {{ font-family: 'Cascadia Mono'; font-weight: 600; }}
QToolButton {{ border: 0; padding: 8px; text-align: left; min-height: 28px; }}
QToolButton:checked, QToolButton:hover {{ background: {colors.elevated}; border-left: 3px solid {colors.accent}; }}
QPushButton {{ background: {colors.elevated}; border: 1px solid {colors.border}; border-radius: 3px; min-height: 32px; padding: 0 12px; }}
QPushButton:focus, QToolButton:focus, QLineEdit:focus {{ border: 2px solid {colors.accent}; }}
QLineEdit, QComboBox {{ background: {colors.panel}; border: 1px solid {colors.border}; min-height: 28px; padding: 0 8px; }}
QProgressBar {{ border: 1px solid {colors.border}; text-align: center; }}
QProgressBar::chunk {{ background: {colors.accent}; }}
QStatusBar {{ border-top: 1px solid {colors.border}; font-size: 11px; font-weight: {weight}; }}
"""


class ThemeProvider:
    @staticmethod
    def resolve(value: ThemeId | str) -> ThemeId:
        try:
            return ThemeId(value)
        except ValueError:
            return ThemeId.DARK

    @classmethod
    def colors(cls, value: ThemeId | str) -> SemanticColors:
        theme = cls.resolve(value)
        if theme is ThemeId.LIGHT:
            return LIGHT_COLORS
        if theme is ThemeId.HIGH_CONTRAST:
            return HIGH_CONTRAST_COLORS
        return DARK_COLORS

    @classmethod
    def stylesheet(cls, value: ThemeId | str) -> str:
        theme = cls.resolve(value)
        if theme is ThemeId.SYSTEM:
            return ""
        return _stylesheet(cls.colors(theme), high_contrast=theme is ThemeId.HIGH_CONTRAST)

    @classmethod
    def apply(cls, app: QApplication, value: ThemeId | str) -> ThemeId:
        theme = cls.resolve(value)
        app.setStyleSheet(cls.stylesheet(theme))
        return theme

    @classmethod
    def status_color(cls, tone: StatusTone, theme: ThemeId | str = ThemeId.DARK) -> str:
        colors = cls.colors(theme)
        return {
            StatusTone.NEUTRAL: colors.secondary_text, StatusTone.INFO: colors.info,
            StatusTone.SUCCESS: colors.success, StatusTone.WARNING: colors.warning, StatusTone.ERROR: colors.error,
        }[tone]

"""Central Qt theme provider based on semantic design tokens."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from .design_tokens import DARK_COLORS, HIGH_CONTRAST_COLORS, LIGHT_COLORS, SemanticColors, StatusTone, ThemeId
from .i18n import Translator


def _stylesheet(colors: SemanticColors, *, high_contrast: bool = False) -> str:
    focus = colors.primary
    return f"""
QWidget {{ background: {colors.surface}; color: {colors.text}; font-size: 10pt; }}
QMainWindow::separator {{ background: {colors.border}; width: 5px; height: 5px; }}
QMenuBar, QMenu, QToolBar, QStatusBar {{ background: {colors.panel}; }}
QDockWidget::title {{ background: {colors.panel}; padding: 6px; font-weight: 600; }}
QTreeWidget, QTableWidget, QTextEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background: {colors.panel}; border: 1px solid {colors.border}; selection-background-color: {colors.primary};
}}
QHeaderView::section {{ background: {colors.panel}; color: {colors.text}; padding: 5px; border: 0; }}
QPushButton {{ background: {colors.panel}; border: 1px solid {colors.border}; border-radius: 3px; padding: 5px 9px; }}
QPushButton:hover {{ background: {colors.primary}; }}
QPushButton:pressed {{ background: {colors.primary}; }}
QProgressBar {{ border: 1px solid {colors.border}; text-align: center; }}
QProgressBar::chunk {{ background: {colors.primary}; }}
QWidget:focus {{ outline: 2px solid {focus}; }}
""" + ("QWidget { font-weight: 600; }" if high_contrast else "")


DARK_STYLESHEET = _stylesheet(DARK_COLORS)
LIGHT_STYLESHEET = _stylesheet(LIGHT_COLORS)
HIGH_CONTRAST_STYLESHEET = _stylesheet(HIGH_CONTRAST_COLORS, high_contrast=True)


@dataclass(frozen=True, slots=True)
class ThemeOption:
    theme_id: ThemeId
    label: str


class ThemeProvider:
    """Applies the only application stylesheet source used by new UI code."""

    _LEGACY_LABELS = {
        "Системная": ThemeId.SYSTEM,
        "Тёмная": ThemeId.DARK,
        "Высокая контрастность": ThemeId.HIGH_CONTRAST,
        "Светлая": ThemeId.LIGHT,
        "System": ThemeId.SYSTEM,
        "Dark": ThemeId.DARK,
        "Light": ThemeId.LIGHT,
        "High contrast": ThemeId.HIGH_CONTRAST,
    }

    @classmethod
    def resolve(cls, value: ThemeId | str) -> ThemeId:
        if isinstance(value, ThemeId):
            return value
        if value in cls._LEGACY_LABELS:
            return cls._LEGACY_LABELS[value]
        try:
            return ThemeId(value)
        except ValueError:
            return ThemeId.DARK

    @classmethod
    def options(cls, translator: Translator) -> tuple[ThemeOption, ...]:
        return tuple(ThemeOption(theme, translator.text(f"theme.{theme.value}")) for theme in ThemeId)

    @classmethod
    def stylesheet(cls, value: ThemeId | str) -> str:
        theme = cls.resolve(value)
        if theme is ThemeId.DARK:
            return DARK_STYLESHEET
        if theme is ThemeId.LIGHT:
            return LIGHT_STYLESHEET
        if theme is ThemeId.HIGH_CONTRAST:
            return HIGH_CONTRAST_STYLESHEET
        return ""

    @classmethod
    def colors(cls, value: ThemeId | str) -> SemanticColors:
        theme = cls.resolve(value)
        if theme is ThemeId.LIGHT:
            return LIGHT_COLORS
        if theme is ThemeId.HIGH_CONTRAST:
            return HIGH_CONTRAST_COLORS
        return DARK_COLORS

    @classmethod
    def apply(cls, app: QApplication, value: ThemeId | str) -> ThemeId:
        theme = cls.resolve(value)
        stylesheet = cls.stylesheet(theme)
        if app.styleSheet() != stylesheet:
            app.setStyleSheet(stylesheet)
        return theme

    @classmethod
    def status_stylesheet(cls, tone: StatusTone, theme: ThemeId | str = ThemeId.DARK) -> str:
        colors = cls.colors(theme)
        color = {
            StatusTone.NEUTRAL: colors.text,
            StatusTone.SUCCESS: colors.success,
            StatusTone.WARNING: colors.warning,
            StatusTone.ERROR: colors.error,
            StatusTone.INFO: colors.info,
        }[tone]
        return f"color: {color};"

"""QSS generation and application for UI V2, isolated from Legacy themes."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from .tokens import DesignTokens, ThemeId, tokens_for_theme


@dataclass(frozen=True, slots=True)
class V2Theme:
    """One resolved theme with its semantic tokens and stylesheet."""

    tokens: DesignTokens

    @property
    def stylesheet(self) -> str:
        return stylesheet_for_theme(self.tokens.theme)


def stylesheet_for_theme(theme: ThemeId | str) -> str:
    """Return QSS with real role/tone selectors and a visible focus ring."""

    tokens = tokens_for_theme(theme)
    colors = tokens.colors
    radius = tokens.radius
    typefaces = tokens.typography
    return f"""
QWidget[ui2Root='true'] {{
    background: {colors.background}; color: {colors.primary_text};
    font-family: {typefaces.body_family}; font-size: {typefaces.body_px}px;
}}
QWidget[ui2Root='true'] QWidget {{
    font-family: {typefaces.body_family}; font-size: {typefaces.body_px}px;
}}
QFrame[ui2Role='panel'], QFrame[ui2Role='card'] {{
    background: {colors.panel}; border: 1px solid {colors.border}; border-radius: {radius.card}px;
}}
QWidget[ui2Root='true'] QLabel[ui2Role='workspace-heading'] {{
    color: {colors.primary_text}; font-size: {typefaces.workspace_title_px}px; font-weight: 600;
}}
QWidget[ui2Root='true'] QLabel[ui2Role='section-heading'] {{
    color: {colors.primary_text}; font-size: {typefaces.section_title_px}px; font-weight: 600;
}}
QWidget[ui2Root='true'] QLabel[ui2Role='secondary'] {{ color: {colors.secondary_text}; font-size: {typefaces.secondary_px}px; }}
QListWidget[ui2Role='data-list'], QTableWidget[ui2Role='data-table'] {{
    background: {colors.control}; color: {colors.primary_text}; border: 1px solid {colors.border};
    selection-background-color: {colors.accent}; selection-color: {colors.background};
}}
QTableWidget[ui2Role='data-table'] QHeaderView::section {{
    background: {colors.elevated}; color: {colors.primary_text}; border: 0; border-right: 1px solid {colors.border};
    padding: 4px;
}}
QLabel[ui2Tone='warning'] {{ color: {colors.warning}; }}
QWidget[ui2Root='true'] QLabel[ui2Role='numeric'] {{
    color: {colors.primary_text}; font-family: {typefaces.numeric_family};
    font-size: {typefaces.measurement_px}px; font-weight: 600;
}}
QWidget[ui2Root='true'] QLabel[ui2Role='strip-numeric'] {{
    color: {colors.primary_text}; font-family: {typefaces.numeric_family};
    font-size: {typefaces.body_px}px; font-weight: 600;
}}
QFrame[ui2Role='status-chip'] {{
    background: {colors.elevated}; border: 1px solid {colors.border}; border-radius: {radius.control}px;
}}
QFrame[ui2Role='status-chip'] QLabel {{ color: {colors.primary_text}; }}
QFrame[ui2Role='status-chip'][tone='success'] {{ border-color: {colors.success}; }}
QFrame[ui2Role='status-chip'][tone='warning'] {{ border-color: {colors.warning}; }}
QFrame[ui2Role='status-chip'][tone='error'] {{ border-color: {colors.error}; }}
QFrame[ui2Role='status-chip'][tone='info'] {{ border-color: {colors.info}; }}
QPushButton[ui2Role='primary-action'] {{
    background: {colors.accent}; color: {colors.background}; border: 1px solid {colors.accent};
    border-radius: {radius.control}px; min-height: 32px; padding: 0 12px; font-weight: 600;
}}
QPushButton[ui2Role='primary-action']:disabled {{
    background: {colors.control}; color: {colors.muted_text}; border-color: {colors.border};
}}
QPushButton[ui2Role='primary-action']:hover,
QPushButton[ui2Role='primary-action'][ui2PreviewState='hover'] {{
    background: {colors.accent_hover}; border-color: {colors.accent_hover};
}}
QPushButton[ui2Role='primary-action']:pressed,
QPushButton[ui2Role='primary-action'][ui2PreviewState='pressed'] {{
    background: {colors.accent_pressed}; border-color: {colors.accent_pressed};
}}
QLineEdit[ui2Role='command-field'] {{
    background: {colors.control}; border: 1px solid {colors.border}; border-radius: {radius.control}px;
    min-height: 28px; padding: 0 8px;
}}
QToolButton[ui2Role='navigation-item'] {{
    color: {colors.secondary_text}; border: 0; border-radius: {radius.control}px;
    min-height: 32px; padding: 0 8px; text-align: left;
}}
QToolButton[ui2Role='navigation-item'][ui2Active='true'] {{
    background: {colors.elevated}; color: {colors.primary_text}; border-left: 3px solid {colors.accent};
}}
QPushButton[ui2Role='primary-action'][ui2PreviewState='focus'] {{
    border: 2px solid {colors.focus_ring};
}}
QPushButton[ui2Role='utility-action'] {{
    background: {colors.control}; color: {colors.primary_text}; border: 1px solid {colors.border};
    border-radius: {radius.control}px; min-height: 28px; padding: 0 10px;
}}
QPushButton[ui2Role='utility-action']:hover {{
    border-color: {colors.accent}; color: {colors.primary_text};
}}
QPushButton[ui2Role='utility-action']:checked {{
    background: {colors.elevated}; border-color: {colors.accent}; color: {colors.primary_text};
}}
QDoubleSpinBox[ui2Role='range-control'], QSpinBox[ui2Role='range-control'] {{
    background: {colors.control}; color: {colors.primary_text}; border: 1px solid {colors.border};
    border-radius: {radius.control}px; min-height: 28px; padding: 0 4px;
}}
QComboBox[ui2Role='utility-select'], QCheckBox[ui2Role='utility-toggle'] {{
    color: {colors.primary_text};
}}
QComboBox[ui2Role='utility-select'] {{
    background: {colors.control}; border: 1px solid {colors.border}; border-radius: {radius.control}px;
    min-height: 28px; padding: 0 6px;
}}
QComboBox[ui2Role='utility-select']::drop-down {{ border: 0; width: 20px; }}
QComboBox[ui2Role='utility-select'] QAbstractItemView {{
    background: {colors.panel}; color: {colors.primary_text}; border: 1px solid {colors.border};
}}
QPushButton:focus,
QToolButton:focus,
QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QListWidget:focus,
QTableWidget:focus,
QWidget[ui2FocusRing='true']:focus {{
    border: 2px solid {colors.focus_ring}; outline: 0;
}}
QCheckBox[ui2Role='utility-toggle']:focus {{
    color: {colors.focus_ring}; border: 2px solid {colors.focus_ring};
    border-radius: {radius.control}px;
}}
"""


def apply_v2_theme(app: QApplication, theme: ThemeId | str) -> V2Theme:
    """Apply UI V2 QSS only when the future V2 root is explicitly constructed."""

    resolved = V2Theme(tokens_for_theme(theme))
    app.setStyleSheet(resolved.stylesheet)
    return resolved

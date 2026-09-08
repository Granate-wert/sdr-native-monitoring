"""Offscreen evidence for the isolated UI V2 design-system package."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.components import ContextPopover, NavigationItem, SectionHeader, SplitterHandle
from sdr_monitor.ui.v2.design import (
    ThemeId,
    contrast_ratio,
    stylesheet_for_theme,
    themed_icon,
    tokens_for_theme,
)
from sdr_monitor.ui.v2.design.icons import V2IconId
from sdr_monitor.ui.v2.gallery import DesignSystemGallery


class DesignSystemTests(unittest.TestCase):
    """Verify semantics, accessibility and offscreen paint for every V2 theme."""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._register_windows_font("segoeui.ttf")
        cls._register_windows_font("consola.ttf")

    @staticmethod
    def _register_windows_font(filename: str) -> None:
        """Make headless Qt render the same system fallback as the Windows EXE."""

        font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / filename
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))

    def test_semantic_text_contrast_and_stable_scientific_colours(self) -> None:
        scientific = None
        for theme in ThemeId:
            tokens = tokens_for_theme(theme)
            self.assertGreaterEqual(
                contrast_ratio(tokens.colors.primary_text, tokens.colors.background), 4.5
            )
            self.assertGreaterEqual(
                contrast_ratio(tokens.colors.secondary_text, tokens.colors.background), 4.5
            )
            if scientific is None:
                scientific = tokens.scientific
            else:
                self.assertEqual(tokens.scientific, scientific)

    def test_qss_has_real_semantic_selectors_and_focus_ring(self) -> None:
        stylesheet = stylesheet_for_theme(ThemeId.DARK)
        for selector in (
            "QLabel[ui2Role='workspace-heading']",
            "QLabel[ui2Tone='warning']",
            "QFrame[ui2Role='status-chip'][tone='warning']",
            "QFrame[ui2Role='status-chip'] QLabel",
            "QPushButton[ui2Role='primary-action']:disabled",
            "QPushButton[ui2Role='primary-action']:hover,",
            "QPushButton[ui2Role='primary-action'][ui2PreviewState='hover']",
            "QPushButton[ui2Role='primary-action']:pressed,",
            "QPushButton[ui2Role='primary-action'][ui2PreviewState='focus']",
            "QPushButton[ui2Role='utility-action']:checked",
            "QDoubleSpinBox[ui2Role='range-control']",
            "QComboBox[ui2Role='utility-select']",
            "QToolButton[ui2Role='navigation-item'][ui2Active='true']",
            "QPushButton:focus,",
            "QComboBox:focus,",
            "QDoubleSpinBox:focus,",
            "QWidget[ui2FocusRing='true']:focus",
            "QCheckBox[ui2Role='utility-toggle']:focus {",
        ):
            self.assertIn(selector, stylesheet)
        self.assertGreater(
            stylesheet.index("QPushButton:focus,"),
            stylesheet.index("QPushButton[ui2Role='utility-action']"),
        )
        self.assertGreater(
            stylesheet.index("QComboBox:focus,"),
            stylesheet.index("QComboBox[ui2Role='utility-select']"),
        )

    def test_windows_fallback_font_can_render_russian_labels(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows product typography contract")
        font = QFontDatabase.font("Segoe UI", "", 13)
        self.assertTrue(QFontMetrics(font).inFontUcs4(ord("П")))

    def test_explicit_icons_have_visible_pixels(self) -> None:
        for icon_id in V2IconId:
            icon = themed_icon(icon_id, "#53D18B", size=24)
            image = icon.pixmap(24, 24).toImage()
            self.assertFalse(image.isNull())
            self.assertTrue(
                any(image.pixelColor(x, y).alpha() > 0 for x in range(24) for y in range(24)),
                icon_id,
            )

    def test_gallery_exposes_all_required_primitives_with_accessible_labels(self) -> None:
        gallery = DesignSystemGallery()
        self.addCleanup(gallery.deleteLater)
        required = {
            "status-neutral",
            "status-success",
            "status-warning",
            "status-error",
            "action-normal",
            "action-preview-hover",
            "action-preview-focus",
            "action-preview-pressed",
            "command-field",
            "numeric-readout",
            "measurement-strip",
            "heat-legend",
            "splitter",
            "empty-overlay",
            "error-banner",
            "context-popover",
            "navigation-active",
        }
        self.assertTrue(required.issubset(gallery.components))
        for name in required - {"context-popover"}:
            self.assertTrue(gallery.components[name].accessibleName(), name)
        self.assertEqual(gallery.components["action-busy"].isEnabled(), False)
        self.assertEqual(gallery.components["navigation-active"].is_active, True)
        self.assertEqual(gallery.components["action-preview-hover"].text(), "Наведение")
        self.assertEqual(gallery.components["action-preview-focus"].text(), "Фокус")
        self.assertEqual(gallery.components["action-preview-pressed"].text(), "Нажато")
        splitter = gallery.components["splitter"]
        self.assertIsInstance(splitter.handle(1), SplitterHandle)

    def test_navigation_item_is_a_button_with_explicit_current_page_semantics(self) -> None:
        item = NavigationItem(
            "Приём",
            description="Открыть приёмник",
            active_name="Приём, текущая страница",
            active_description="Текущая страница. Открыть приёмник",
        )
        self.addCleanup(item.deleteLater)
        interface = QAccessible.queryAccessibleInterface(item)
        assert interface is not None
        self.assertEqual(interface.role(), QAccessible.Role.Button)
        self.assertFalse(item.isCheckable())
        self.assertFalse(item.is_active)
        item.set_active(True)
        self.assertTrue(item.is_active)
        self.assertEqual(item.accessibleName(), "Приём, текущая страница")
        self.assertEqual(item.accessibleDescription(), "Текущая страница. Открыть приёмник")

    def test_popover_is_non_modal(self) -> None:
        popover = ContextPopover("Параметры")
        self.addCleanup(popover.deleteLater)
        self.assertEqual(popover.windowModality(), Qt.WindowModality.NonModal)
        self.assertTrue(popover.focusPolicy() & Qt.FocusPolicy.StrongFocus)
        self.assertEqual(popover.property("ui2FocusRing"), True)

    def test_section_header_separates_long_context_and_exposes_it_accessibly(self) -> None:
        header = SectionHeader(
            "Spectrum replay",
            "Opening a file and advancing to the next frame are explicit actions.",
        )
        self.addCleanup(header.deleteLater)
        header.resize(360, 72)
        header.show()
        self.app.processEvents()
        self.assertGreaterEqual(header._subtitle.geometry().left(), header._heading.geometry().right() + 16)
        self.assertEqual(header.accessibleDescription(), header._subtitle.text())
        header.set_subtitle("Updated context")
        self.assertEqual(header.accessibleDescription(), "Updated context")

    def test_offscreen_gallery_renders_at_each_supported_theme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            evidence_dir = Path(temporary_directory)
            for theme in ThemeId:
                gallery = DesignSystemGallery(theme=theme)
                self.addCleanup(gallery.deleteLater)
                gallery.resize(1280, 720)
                gallery.show()
                self.app.processEvents()
                image = gallery.grab().toImage()
                target = evidence_dir / f"gallery-{theme.value}.png"
                self.assertFalse(image.isNull(), theme)
                self.assertTrue(image.save(str(target)), theme)
                self.assertGreater(target.stat().st_size, 1024, theme)
                colors = {
                    image.pixelColor(x, y).rgba()
                    for x in range(0, image.width(), 32)
                    for y in range(0, image.height(), 32)
                }
                self.assertGreater(len(colors), 2, theme)
                gallery.hide()

    def test_gallery_renders_in_a_fresh_200_percent_dpi_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "gallery-dark-200pct.png"
            environment = os.environ.copy()
            environment["QT_QPA_PLATFORM"] = "offscreen"
            environment["QT_SCALE_FACTOR"] = "2"
            project_root = Path(__file__).parents[2]
            environment["PYTHONPATH"] = os.pathsep.join(
                (str(project_root), environment.get("PYTHONPATH", ""))
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "tests/ui_v2/render_design_system_gallery.py",
                    str(target),
                    "--theme",
                    ThemeId.DARK.value,
                ],
                cwd=project_root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dpr=2.0", result.stdout)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()

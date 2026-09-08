"""Vertical presentation composition of an existing SpectrumScene and WaterfallPane."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..components import V2Splitter
from ..design import ThemeId, stylesheet_for_theme
from ..i18n import text
from ..spectrum import SpectrumScene
from .pane import WaterfallPane

_SETTINGS_PREFIX = "ui_v2/live/waterfall/v1"
_SETTINGS_DEBOUNCE_MS = 250


class SpectrumWaterfallView(QWidget):
    """Own the UI-only splitter; neither child gains a service or receiver owner."""

    def __init__(
        self,
        *,
        settings: QSettings | None = None,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings()
        self._theme = theme
        self._restored_sizes: list[int] | None = self._read_splitter_sizes()
        self._initial_sizes_applied = False
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.timeout.connect(self._write_splitter_settings)
        self._build_ui()
        self.set_theme(theme)

    @property
    def spectrum_scene(self) -> SpectrumScene:
        return self._spectrum

    @property
    def waterfall_pane(self) -> WaterfallPane:
        return self._waterfall

    @property
    def splitter(self) -> V2Splitter:
        return self._splitter

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._splitter.set_theme(theme)
        self._spectrum.set_theme(theme)
        self._waterfall.set_theme(theme)

    def set_waterfall_visible(self, visible: bool) -> None:
        """Change only layout/render visibility; retained rows stay local to the pane."""

        self._waterfall.set_render_visible(visible)
        self._waterfall.setVisible(visible)
        self._schedule_splitter_write()

    def flush_settings(self) -> None:
        self._settings_timer.stop()
        self._write_splitter_settings()
        self._waterfall.flush_settings()

    def closeEvent(self, event) -> None:
        self.flush_settings()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._initial_sizes_applied or self.height() <= 0:
            return
        self._initial_sizes_applied = True
        sizes = self._restored_sizes or _default_splitter_sizes(self.height())
        self._splitter.setSizes(sizes)

    def _build_ui(self) -> None:
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("waterfall.view.accessible.name"))
        self.setAccessibleDescription(text("waterfall.view.accessible.description"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._splitter = V2Splitter(Qt.Orientation.Vertical, theme=self._theme, parent=self)
        self._spectrum = SpectrumScene(theme=self._theme, parent=self._splitter)
        self._spectrum.set_frequency_axis_visible(False)
        self._waterfall = WaterfallPane(settings=self._settings, theme=self._theme, parent=self._splitter)
        self._waterfall.setMinimumHeight(120)
        self._spectrum.setMinimumHeight(180)
        self._splitter.addWidget(self._spectrum)
        self._splitter.addWidget(self._waterfall)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.splitterMoved.connect(lambda _position, _index: self._schedule_splitter_write())
        self._waterfall.link_frequency_view_box(self._spectrum.view_box)
        layout.addWidget(self._splitter)

    def _read_splitter_sizes(self) -> list[int] | None:
        if str(self._settings.value(f"{_SETTINGS_PREFIX}/version", "")) != "1":
            return None
        raw = self._settings.value(f"{_SETTINGS_PREFIX}/splitter_sizes")
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        try:
            sizes = [int(value) for value in raw]
        except (TypeError, ValueError):
            return None
        return sizes if all(value > 0 for value in sizes) else None

    def _schedule_splitter_write(self) -> None:
        if not self._settings_timer.isActive():
            self._settings_timer.start(_SETTINGS_DEBOUNCE_MS)

    def _write_splitter_settings(self) -> None:
        self._settings.setValue(f"{_SETTINGS_PREFIX}/version", "1")
        self._settings.setValue(f"{_SETTINGS_PREFIX}/splitter_sizes", self._splitter.sizes())
        self._settings.sync()


def _default_splitter_sizes(total_height: int) -> list[int]:
    """Return the required 60/40 default without allocating display history."""

    total = max(300, int(total_height))
    return [round(total * 0.60), max(120, total - round(total * 0.60))]

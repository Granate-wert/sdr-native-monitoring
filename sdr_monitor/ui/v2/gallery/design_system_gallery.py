"""Offscreen-testable presentation gallery for the UI V2 visual language."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..components import (
    CommandField,
    ContextPopover,
    EmptyChartOverlay,
    ErrorBanner,
    HeatLegend,
    MeasurementStripItem,
    NavigationItem,
    NumericReadout,
    PrimaryActionButton,
    SectionHeader,
    StatusChipV2,
    V2Splitter,
)
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import text


class DesignSystemGallery(QScrollArea):
    """One isolated page showing UI V2 primitives and meaningful states."""

    def __init__(self, *, theme: ThemeId = ThemeId.DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setWidgetResizable(True)
        self.setAccessibleName(text("gallery.accessible.name"))
        root = QWidget(self)
        root.setProperty("ui2Root", True)
        root.setMinimumWidth(920)
        self._root = root
        self._layout = QVBoxLayout(root)
        self._layout.setContentsMargins(24, 24, 24, 24)
        self._layout.setSpacing(16)
        self.setWidget(root)
        self._components: dict[str, QWidget] = {}
        self._build()
        self.set_theme(theme)

    @property
    def components(self) -> dict[str, QWidget]:
        """Named primitives allow deterministic gallery and accessibility tests."""

        return dict(self._components)

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self._root.setStyleSheet(stylesheet_for_theme(theme))
        for component in self._components.values():
            set_theme = getattr(component, "set_theme", None)
            if callable(set_theme):
                set_theme(theme)

    def _build(self) -> None:
        title = QLabel(text("gallery.title"), self.widget())
        title.setProperty("ui2Role", "workspace-heading")
        self._layout.addWidget(title)
        self._add_status_section()
        self._add_action_section()
        self._add_interaction_section()
        self._add_measurement_section()
        self._add_feedback_section()
        self._add_navigation_section()

    def _add_status_section(self) -> None:
        section = self._section(text("gallery.status.title"), text("gallery.status.detail"))
        row = QHBoxLayout()
        for name, label, tone, detail in (
            ("status-neutral", "CPU", StatusTone.NEUTRAL, text("gallery.status.cpu")),
            ("status-success", text("gallery.status.calibration_label"), StatusTone.SUCCESS, text("gallery.status.calibration")),
            ("status-warning", text("gallery.status.loss_label"), StatusTone.WARNING, text("gallery.status.loss")),
            ("status-error", text("gallery.status.error_label"), StatusTone.ERROR, text("gallery.status.rx_unavailable")),
        ):
            chip = StatusChipV2(label, tone=tone, detail=detail, parent=section)
            self._components[name] = chip
            row.addWidget(chip)
        row.addStretch(1)
        section.layout().addLayout(row)

    def _add_action_section(self) -> None:
        section = self._section(text("gallery.actions.title"), text("gallery.actions.detail"))
        row = QHBoxLayout()
        start = PrimaryActionButton(text("gallery.actions.start"), accessible_description=text("gallery.actions.start.name"), parent=section)
        busy = PrimaryActionButton(text("gallery.actions.apply"), accessible_description=text("gallery.actions.apply.name"), parent=section)
        busy.set_busy(True, activity=text("gallery.actions.applying"))
        disabled = PrimaryActionButton(text("gallery.actions.unavailable"), accessible_description=text("gallery.actions.unavailable.name"), parent=section)
        disabled.setDisabled(True)
        for name, widget in (("action-normal", start), ("action-busy", busy), ("action-disabled", disabled)):
            self._components[name] = widget
            row.addWidget(widget)
        row.addStretch(1)
        section.layout().addLayout(row)
        field = CommandField(text("gallery.field.center"), value="433.920", placeholder=text("gallery.field.frequency"), parent=section)
        self._components["command-field"] = field
        section.layout().addWidget(field)

    def _add_measurement_section(self) -> None:
        section = self._section(text("gallery.measurements.title"), text("gallery.measurements.detail"))
        row = QHBoxLayout()
        readout = NumericReadout(text("gallery.measurements.peak"), "−52.4", "dBFS/bin", detail=text("gallery.measurements.unit"), parent=section)
        strip = MeasurementStripItem(text("gallery.measurements.frame"), text("gallery.measurements.age_value"), detail=text("gallery.measurements.age"), parent=section)
        heat = HeatLegend(theme=self._theme, parent=section)
        for name, widget in (("numeric-readout", readout), ("measurement-strip", strip), ("heat-legend", heat)):
            self._components[name] = widget
            row.addWidget(widget, 1 if name == "heat-legend" else 0)
        section.layout().addLayout(row)
        splitter = V2Splitter(Qt.Orientation.Vertical, theme=self._theme, parent=section)
        splitter.addWidget(self._splitter_label(text("gallery.measurements.spectrum_pane")))
        splitter.addWidget(self._splitter_label(text("gallery.measurements.waterfall_pane")))
        splitter.setSizes([84, 48])
        splitter.setFixedHeight(140)
        self._components["splitter"] = splitter
        section.layout().addWidget(splitter)

    def _add_interaction_section(self) -> None:
        section = self._section(
            text("gallery.interaction.title"), text("gallery.interaction.detail"),
        )
        row = QHBoxLayout()
        for name, label, preview_state in (
            ("action-preview-normal", text("gallery.interaction.normal"), None),
            ("action-preview-hover", text("gallery.interaction.hover"), "hover"),
            ("action-preview-focus", text("gallery.interaction.focus"), "focus"),
            ("action-preview-pressed", text("gallery.interaction.pressed"), "pressed"),
        ):
            button = PrimaryActionButton(
                label,
                accessible_description=text("gallery.interaction.preview", label=label),
                parent=section,
            )
            button.set_preview_state(preview_state)
            self._components[name] = button
            row.addWidget(button)
        row.addStretch(1)
        section.layout().addLayout(row)

    def _add_feedback_section(self) -> None:
        section = self._section(text("gallery.feedback.title"), text("gallery.feedback.detail"))
        overlay = EmptyChartOverlay(parent=section)
        error = ErrorBanner(
            text("gallery.feedback.error.title"), text("gallery.feedback.error.detail"), action_text=text("gallery.feedback.error.action"),
            parent=section,
        )
        self._components["empty-overlay"] = overlay
        self._components["error-banner"] = error
        section.layout().addWidget(overlay)
        section.layout().addWidget(error)
        trigger = QPushButton(text("gallery.feedback.popover.open"), section)
        trigger.setAccessibleName(text("gallery.feedback.popover.open_name"))
        popover = ContextPopover(text("gallery.feedback.popover.title"), parent=self.widget())
        popover.add_content(QLabel(text("gallery.feedback.popover.detail"), popover))
        trigger.clicked.connect(lambda: popover.open_next_to(trigger))
        self._components["context-popover"] = popover
        self._components["context-popover-trigger"] = trigger
        section.layout().addWidget(trigger, alignment=Qt.AlignmentFlag.AlignLeft)

    def _add_navigation_section(self) -> None:
        section = self._section(text("gallery.navigation.title"), text("gallery.navigation.detail"))
        row = QHBoxLayout()
        live = NavigationItem(
            text("gallery.navigation.live"),
            icon=V2IconId.NAVIGATION,
            description=text("gallery.navigation.live.description"),
            active_name=text(
                "navigation.current_name",
                label=text("gallery.navigation.live"),
            ),
            active_description=text(
                "navigation.current_description",
                detail=text("gallery.navigation.live.description"),
            ),
            theme=self._theme,
            parent=section,
        )
        live.set_active(True)
        sweep = NavigationItem(
            text("gallery.navigation.sweep"),
            icon=V2IconId.CHEVRON,
            description=text("gallery.navigation.sweep.description"),
            theme=self._theme,
            parent=section,
        )
        self._components["navigation-active"] = live
        self._components["navigation-normal"] = sweep
        row.addWidget(live)
        row.addWidget(sweep)
        row.addStretch(1)
        section.layout().addLayout(row)

    def _section(self, title: str, subtitle: str) -> QFrame:
        section = QFrame(self.widget())
        section.setProperty("ui2Role", "card")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(SectionHeader(title, subtitle, parent=section))
        self._layout.addWidget(section)
        return section

    @staticmethod
    def _splitter_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setProperty("ui2Role", "secondary")
        return label

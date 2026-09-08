"""Truthful inert V2 placeholders used before production workspace packages."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from ..components import EmptyChartOverlay, SectionHeader
from ..design.icons import V2IconId
from ..i18n import text
from .contracts import V2ShellContext, WorkspaceDefinition


def placeholder_workspace(definition: WorkspaceDefinition) -> QWidget:
    """Return a visible placeholder that owns neither presenter nor device work."""

    page = QFrame()
    page.setProperty("ui2Role", "panel")
    label = definition.resolved_label()
    description = definition.resolved_description()
    page.setAccessibleName(label)
    page.setAccessibleDescription(description)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(16)
    layout.addWidget(SectionHeader(label, description, parent=page))
    overlay = EmptyChartOverlay(
        title=text("placeholder.workspace.title"),
        detail=text("placeholder.workspace.detail"),
        primary_text=text("placeholder.workspace.primary"),
        secondary_text=text("placeholder.workspace.secondary"),
        parent=page,
    )
    overlay.setEnabled(False)
    layout.addWidget(overlay)
    layout.addStretch(1)
    return page


def placeholder_inspector(definition: WorkspaceDefinition) -> QWidget:
    """Return replacement inspector content for one inactive V2 workspace."""

    inspector = QFrame()
    inspector.setProperty("ui2Role", "panel")
    inspector.setAccessibleName(
        text("placeholder.inspector", label=definition.resolved_label())
    )
    layout = QVBoxLayout(inspector)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)
    layout.addWidget(
        SectionHeader(text("placeholder.context"), definition.resolved_label(), parent=inspector)
    )
    overlay = EmptyChartOverlay(
        title=text("placeholder.inspector.title"),
        detail=text("placeholder.inspector.detail"),
        primary_text=text("placeholder.inspector.primary"),
        secondary_text=text("placeholder.help"),
        parent=inspector,
    )
    overlay.setEnabled(False)
    layout.addWidget(overlay)
    layout.addStretch(1)
    return inspector


def make_placeholder_definition(
    workspace_id: str,
    label: str,
    description: str,
    icon: V2IconId,
    *,
    label_key: str | None = None,
    description_key: str | None = None,
) -> WorkspaceDefinition:
    """Bind placeholder factories without exposing a mutable workspace model."""

    definition: WorkspaceDefinition

    def workspace_factory() -> QWidget:
        return placeholder_workspace(definition)

    def inspector_factory() -> QWidget:
        return placeholder_inspector(definition)

    definition = WorkspaceDefinition(
        workspace_id=workspace_id,
        label=label,
        description=description,
        icon=icon,
        workspace_factory=workspace_factory,
        inspector_factory=inspector_factory,
        label_key=label_key,
        description_key=description_key,
    )
    return definition


def default_workspace_definitions() -> tuple[WorkspaceDefinition, ...]:
    """Return the initial non-operational V2 navigation catalogue."""

    return (
        make_placeholder_definition(
            "home", text("workspace.home.label"), text("workspace.home.description"), V2IconId.INFO,
            label_key="workspace.home.label", description_key="workspace.home.description",
        ),
        make_placeholder_definition(
            "live", text("workspace.live.label"), text("workspace.live.description"), V2IconId.NAVIGATION,
            label_key="workspace.live.label", description_key="workspace.live.description",
        ),
        make_placeholder_definition(
            "sweep", text("workspace.sweep.label"), text("workspace.sweep.description"), V2IconId.CHEVRON,
            label_key="workspace.sweep.label", description_key="workspace.sweep.description",
        ),
        make_placeholder_definition(
            "calibration", text("workspace.calibration.label"), text("workspace.calibration.description"), V2IconId.INFO,
            label_key="workspace.calibration.label", description_key="workspace.calibration.description",
        ),
        make_placeholder_definition(
            "recording", text("workspace.recording.label"), text("workspace.recording.description"), V2IconId.NAVIGATION,
            label_key="workspace.recording.label", description_key="workspace.recording.description",
        ),
        make_placeholder_definition(
            "replay", text("workspace.replay.label"), text("workspace.replay.description"), V2IconId.CHEVRON,
            label_key="workspace.replay.label", description_key="workspace.replay.description",
        ),
        make_placeholder_definition(
            "diagnostics", text("workspace.diagnostics.label"), text("workspace.diagnostics.description"), V2IconId.INFO,
            label_key="workspace.diagnostics.label", description_key="workspace.diagnostics.description",
        ),
    )


def default_shell_context(*, automatic_discovery_enabled: bool = False) -> V2ShellContext:
    """Build the no-device V2 context used before a production workspace exists."""

    return V2ShellContext(
        workspaces=default_workspace_definitions(),
        automatic_discovery_enabled=automatic_discovery_enabled,
    )

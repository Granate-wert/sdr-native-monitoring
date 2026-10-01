"""Bounded, default-Cancel RF intent dialogs; no owner, query or RF operation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QLabel, QPlainTextEdit,
    QVBoxLayout, QWidget,
)

from sdr_monitor.ui.v2_pane_rf_plan import PaneRfChangePreview

from ..i18n import text


def pane_rf_preview_text(preview: PaneRfChangePreview) -> str:
    """At most four pane rows; original and effective plans remain distinct."""
    proposal = preview.proposal
    old, new = proposal.expected_context, proposal.proposed_context
    lines = [text("analyzer.rf.delta", requested=f"{proposal.requested_shift_hz / 1e6:.6f}",
                  effective=f"{proposal.effective_shift_hz / 1e6:.6f}",
                  quantum=f"{proposal.quantum_hz:g}"),
             text("analyzer.rf.impact", panes=", ".join(preview.resource.affected_pane_ids)),
             text("analyzer.rf.restart" if preview.resource.restart_required else "analyzer.rf.armed")]
    schedule = new.plan.layout.schedule
    assert schedule is not None
    resource = next(item for item in schedule.resources
                    if item.physical_stream_resource_id == proposal.physical_stream_resource_id)
    estimates = {item.pane_id: item for item in schedule.pane_revisits}
    for previous, changed in zip(old.drafts, new.drafts, strict=True):
        pane_id = f"pane-{changed.number}"
        if pane_id not in preview.resource.affected_pane_ids:
            continue
        assert previous.start_hz is not None and previous.stop_hz is not None
        assert changed.start_hz is not None and changed.stop_hz is not None
        job = next(item for item in resource.jobs if any(crop.pane_id == pane_id for crop in item.crops))
        estimate = estimates[pane_id]
        lines.extend(("", text("analyzer.rf.pane_range", number=changed.number,
            old_start=f"{previous.start_hz / 1e6:g}", old_stop=f"{previous.stop_hz / 1e6:g}",
            start=f"{changed.start_hz / 1e6:g}", stop=f"{changed.stop_hz / 1e6:g}"),
            text("analyzer.rf.schedule", requested=changed.priority,
                 effective=job.scheduler_policy.weight, binding=estimate.mode.value,
                 revisit=f"{estimate.maximum_revisit_s:.3f}",
                 target="—" if changed.maximum_revisit_s is None else f"{changed.maximum_revisit_s:g}")))
        lines.append(text("analyzer.rf.mode", value=job.profile.measurement_mode.value))
        # The real typed profile is authoritative, not reconstructed UI defaults.
        profile = job.profile
        configuration = getattr(profile, "configuration", None)
        template = getattr(profile, "request_template", None)
        for name in ("sample_rate_hz", "analog_bandwidth_hz", "baseband_filter_hz",
                     "usable_capture_span_hz", "fft_size", "hop_size", "window", "detector",
                     "averaging_frames", "gain_mode", "manual_gain_db", "gain_db",
                     "lna_gain_db", "vga_gain_db", "lna_gain", "vga_gain", "points",
                     "usable_window_hz", "analysis_bins_per_usable_window", "overlap_hz"):
            for owner in (profile, configuration, template):
                value = getattr(owner, name, None)
                if value is not None:
                    lines.append(f"{name}: {getattr(value, 'value', value)}")
                    break
    lines.extend(("", text("analyzer.rf.scope")))
    return "\n".join(lines)


class RfImpactDialog(QDialog):
    """Nonblocking Qt modal; Cancel/Escape do not reach a receiver."""

    def __init__(self, detail: str, *, restart_required: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rfImpactDialogV2")
        self.setWindowTitle(text("analyzer.rf.preview_title"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        label = QLabel(text("analyzer.rf.preview_help"), self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setObjectName("rfImpactDetailsV2")
        self.details.setAccessibleName(text("analyzer.rf.preview_title"))
        self.details.setPlainText(detail)
        layout.addWidget(self.details, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText(text("analyzer.rf.cancel"))
        self.cancel_button.setDefault(True)
        self.confirm_button = self.buttons.addButton(
            text("analyzer.rf.confirm_restart" if restart_required else "analyzer.rf.confirm_apply"),
            QDialogButtonBox.ButtonRole.ActionRole)
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.setAccessibleName(self.confirm_button.text())
        self.buttons.rejected.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        layout.addWidget(self.buttons)
        screen = self.screen().availableGeometry()
        self.resize(min(720, max(320, screen.width() - 80)),
                    min(620, max(300, screen.height() - 100)))
        self.cancel_button.setFocus()


class RfShiftEntryDialog(QDialog):
    """Accessible numeric alternative to a held-middle gesture, with same preview."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rfShiftEntryDialogV2")
        self.setWindowTitle(text("analyzer.rf.shift"))
        layout = QVBoxLayout(self)
        label = QLabel(text("analyzer.rf.entry_help"), self)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label)
        self.offset = QDoubleSpinBox(self)
        self.offset.setObjectName("rfShiftOffsetV2")
        self.offset.setRange(-6000.0, 6000.0)
        self.offset.setDecimals(6)
        self.offset.setSuffix(text("analyzer.rf.unit.mhz"))
        self.offset.setAccessibleName(text("analyzer.rf.shift"))
        layout.addWidget(self.offset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText(text("analyzer.rf.cancel"))
        cancel.setDefault(True)
        preview = buttons.addButton(text("analyzer.rf.preview_title"), QDialogButtonBox.ButtonRole.ActionRole)
        preview.setAutoDefault(False)
        preview.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.offset.setFocus()


__all__ = ["RfImpactDialog", "RfShiftEntryDialog", "pane_rf_preview_text"]

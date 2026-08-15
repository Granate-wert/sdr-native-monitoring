"""Accessible, inert-by-default tinySA analyzer workspace."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...application.tinysa_analyzer import (
    TinySaAnalyzerPhase,
    TinySaAnalyzerSnapshot,
    TinySaSettingsReview,
    TinySaTraceApplicationResult,
)
from ...services.tinysa_capability_adapter import TinySaModel
from ...services.tinysa_serial_trace_collector import TinySaScanRawRequest
from ...services.tinysa_source_composition import TinySaVerifiedSource
from ...services.tinysa_sweep_policy import (
    TINYSA_PRODUCT_SCANRAW_POINTS_MAX,
    TinySaSweepAccuracy,
)
from ...services.tinysa_sweep_settings_controller import (
    TinySaAttenuationMode,
    TinySaRbwMode,
    TinySaSpurPolicy,
    TinySaSweepSettingsPlan,
    TinySaSwitchPolicy,
)
from ..components import EmptyState, SectionCard, StatusChip
from ..design_tokens import StatusTone
from ..presenters.tinysa_analyzer_presenter import TinySaAnalyzerPresenter
from ..tinysa_trace_canvas import TinySaTraceCanvas


class TinySaAnalyzerWorkspace(QWidget):
    """Never opens or configures tinySA until a visible explicit action occurs."""

    trace_observed = Signal(object)
    trace_busy_changed = Signal(bool)
    trace_painted = Signal(object)

    def __init__(self, presenter: TinySaAnalyzerPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not isinstance(presenter, TinySaAnalyzerPresenter):
            raise TypeError("tinySA workspace requires its bounded presenter")
        self._presenter = presenter
        self._verified_source: TinySaVerifiedSource | None = None
        self._visible_trace_lock: tuple[TinySaScanRawRequest, int] | None = None
        self._last_trace_result: TinySaTraceApplicationResult | None = None
        self.setAccessibleName("tinySA spectrum analyzer workspace")
        self._build_ui()
        presenter.snapshot_changed.connect(self._apply_snapshot)
        presenter.trace_ready.connect(self._show_trace)
        presenter.settings_review_ready.connect(self._show_settings_review)
        presenter.busy_changed.connect(self._set_busy)
        presenter.task_failed.connect(self._show_error)
        self._apply_snapshot(presenter.current_snapshot)

    def shutdown(self) -> None:
        self._presenter.shutdown()

    @property
    def verified_source(self) -> TinySaVerifiedSource | None:
        """Route-free source identity for an explicit evidence observer."""

        return self._verified_source

    @property
    def last_trace_result(self) -> TinySaTraceApplicationResult | None:
        """Last scalar/presentation result; it never exposes the full trace."""

        return self._last_trace_result

    @property
    def visible_trace_locked(self) -> bool:
        return self._visible_trace_lock is not None

    @property
    def presenter_metrics(self):
        """Scalar presenter metrics for an explicit visible-evidence observer."""

        return self._presenter.metrics

    @property
    def canvas_metrics(self):
        """Scalar paint metrics; full analytical trace stays outside Qt."""

        return self._canvas.metrics

    def lock_unchanged_trace_for_visible_evidence(
        self,
        request: TinySaScanRawRequest,
        presentation_width: int,
    ) -> None:
        """Lock one UI witness to an unchanged one-shot trace before its click.

        This is deliberately an opt-in test seam.  It prevents the visible
        witness from staging or applying a setting and keeps its profile out of
        persistent UI state.  Normal product use never calls this method.
        """

        if not isinstance(request, TinySaScanRawRequest):
            raise TypeError("tinySA visible evidence requires an immutable request")
        if request.model is not TinySaModel.ULTRA or request.points != 10_001:
            raise ValueError("tinySA visible evidence requires the Ultra 10001-point product bound")
        if not 1 <= presentation_width <= 4_096:
            raise ValueError("tinySA visible evidence presentation width is invalid")
        if self._visible_trace_lock is not None:
            raise RuntimeError("tinySA visible evidence trace is already locked")
        self._visible_trace_lock = (request, presentation_width)
        self._start_mhz.setValue(request.start_frequency_hz / 1_000_000.0)
        self._stop_mhz.setValue(request.stop_frequency_hz / 1_000_000.0)
        self._points.setValue(request.points)
        for control in (self._start_mhz, self._stop_mhz, self._points):
            control.setEnabled(False)
        self._review_settings.setEnabled(False)
        self._apply_settings.setEnabled(False)
        self._settings_detail.setText(
            "Visible evidence profile: one unchanged trace only; runtime settings are disabled."
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("tinySA analyzer")
        title.setProperty("role", "heading")
        self._status = StatusChip("Ready", StatusTone.INFO)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._status)
        layout.addLayout(header)
        self._source_identity = QLabel("No verified tinySA source is bound.")
        self._source_identity.setProperty("role", "secondary")
        self._source_identity.setAccessibleName("tinySA source identity status")
        layout.addWidget(self._source_identity)

        self._empty = EmptyState(
            "No trace acquired",
            "Choose a range and point count, then request one bounded trace. Connecting never starts a scan or changes settings.",
        )
        layout.addWidget(self._empty)

        acquisition = SectionCard("One-shot acquisition")
        form = QFormLayout()
        self._start_mhz = self._frequency_control("Start frequency in MHz", 87.5)
        self._stop_mhz = self._frequency_control("Stop frequency in MHz", 108.0)
        self._points = QSpinBox()
        self._points.setRange(2, TINYSA_PRODUCT_SCANRAW_POINTS_MAX)
        self._points.setValue(TINYSA_PRODUCT_SCANRAW_POINTS_MAX)
        self._points.setAccessibleName("tinySA trace point count")
        form.addRow("Start, MHz", self._start_mhz)
        form.addRow("Stop, MHz", self._stop_mhz)
        form.addRow("Points", self._points)
        acquisition.content.addLayout(form)
        self._acquire = QPushButton("Acquire one trace")
        self._acquire.setAccessibleDescription(
            "Reads zero offset and requests one option-zero trace without changing settings"
        )
        self._acquire.clicked.connect(self._request_trace)
        acquisition.content.addWidget(self._acquire)
        layout.addWidget(acquisition)

        trace_card = SectionCard("Device-reported dBm trace")
        self._canvas = TinySaTraceCanvas()
        self._canvas.trace_painted.connect(self._emit_trace_painted)
        self._trace_summary = QLabel("No analytical trace retained.")
        self._trace_summary.setProperty("role", "secondary")
        self._trace_summary.setWordWrap(True)
        self._trace_summary.setAccessibleName("tinySA trace acquisition summary")
        self._trace_summary.setAccessibleDescription(
            "No analytical trace has been retained; points per second is not LPS or FFT per second"
        )
        trace_card.content.addWidget(self._canvas)
        trace_card.content.addWidget(self._trace_summary)
        layout.addWidget(trace_card, 1)

        settings = SectionCard("Runtime sweep settings")
        settings_form = QFormLayout()
        self._accuracy = self._enum_combo(
            "Sweep accuracy policy",
            TinySaSweepAccuracy,
        )
        self._rbw_mode = self._enum_combo("RBW mode", TinySaRbwMode)
        self._rbw_hz = QSpinBox()
        self._rbw_hz.setRange(200, 850_000)
        self._rbw_hz.setSingleStep(100)
        self._rbw_hz.setValue(10_000)
        self._rbw_hz.setAccessibleName("Manual RBW in hertz")
        self._lna = self._enum_combo("LNA policy", TinySaSwitchPolicy)
        self._attenuation_mode = self._enum_combo(
            "Attenuation mode", TinySaAttenuationMode
        )
        self._attenuation_db = QSpinBox()
        self._attenuation_db.setRange(0, 31)
        self._attenuation_db.setAccessibleName("Manual attenuation in dB")
        self._spur = self._enum_combo("Spur removal policy", TinySaSpurPolicy)
        self._sweep_time_enabled = QCheckBox("Set explicit sweep time")
        self._sweep_time_enabled.setAccessibleName("Enable explicit sweep time")
        self._sweep_time_ms = QSpinBox()
        self._sweep_time_ms.setRange(3, 60_000)
        self._sweep_time_ms.setValue(1_000)
        self._sweep_time_ms.setSuffix(" ms")
        self._sweep_time_ms.setAccessibleName("Sweep time in milliseconds")
        self._repeat_enabled = QCheckBox("Set repeat count")
        self._repeat_enabled.setAccessibleName("Enable explicit repeat count")
        self._repeat = QSpinBox()
        self._repeat.setRange(1, 1_000)
        self._repeat.setValue(1)
        self._repeat.setAccessibleName("Repeat count")
        settings_form.addRow("Accuracy", self._accuracy)
        settings_form.addRow("RBW", self._rbw_mode)
        settings_form.addRow("Manual RBW", self._rbw_hz)
        settings_form.addRow("LNA", self._lna)
        settings_form.addRow("Attenuation", self._attenuation_mode)
        settings_form.addRow("Manual attenuation", self._attenuation_db)
        settings_form.addRow("Spur removal", self._spur)
        settings_form.addRow(self._sweep_time_enabled, self._sweep_time_ms)
        settings_form.addRow(self._repeat_enabled, self._repeat)
        settings.content.addLayout(settings_form)
        self._settings_detail = QLabel(
            "No settings are staged. Shell acknowledgement will not be shown as verified state."
        )
        self._settings_detail.setWordWrap(True)
        self._settings_detail.setProperty("role", "secondary")
        settings.content.addWidget(self._settings_detail)
        actions = QHBoxLayout()
        self._review_settings = QPushButton("Review settings")
        self._review_settings.setAccessibleDescription(
            "Builds a review only; it does not open a port or change the instrument"
        )
        self._review_settings.clicked.connect(self._stage_settings)
        self._apply_settings = QPushButton("Apply reviewed settings…")
        self._apply_settings.setAccessibleDescription(
            "Shows a second confirmation before invoking the injected settings executor"
        )
        self._apply_settings.setEnabled(False)
        self._apply_settings.clicked.connect(self._confirm_settings)
        actions.addWidget(self._review_settings)
        actions.addWidget(self._apply_settings)
        actions.addStretch(1)
        settings.content.addLayout(actions)
        layout.addWidget(settings)

        self._busy = QLabel("Operation in progress…")
        self._busy.setAccessibleName("tinySA operation in progress")
        self._busy.setVisible(False)
        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setProperty("statusTone", "error")
        self._error.setAccessibleName("tinySA operation error")
        self._error.setVisible(False)
        layout.addWidget(self._busy)
        layout.addWidget(self._error)

    @staticmethod
    def _frequency_control(accessible_name: str, value: float) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setRange(0.1, 5_300.0)
        control.setDecimals(6)
        control.setValue(value)
        control.setSuffix(" MHz")
        control.setAccessibleName(accessible_name)
        return control

    @staticmethod
    def _enum_combo(accessible_name: str, enum_type: type[StrEnum]) -> QComboBox:
        combo = QComboBox()
        combo.setAccessibleName(accessible_name)
        for item in enum_type.__members__.values():
            combo.addItem(item.value.replace("_", " ").title(), item.value)
        return combo

    def _request_trace(self) -> None:
        locked = self._visible_trace_lock
        if locked is not None:
            request, width = locked
        else:
            try:
                request = TinySaScanRawRequest(
                    model=TinySaModel.ULTRA,
                    start_frequency_hz=round(self._start_mhz.value() * 1_000_000),
                    stop_frequency_hz=round(self._stop_mhz.value() * 1_000_000),
                    points=self._points.value(),
                    deadline_seconds=120.0,
                )
            except ValueError:
                self._show_error("Invalid tinySA range or point count")
                return
            width = max(1, min(4_096, self._canvas.width()))
        self._presenter.collect_trace(request, width)

    def _build_settings_plan(self) -> TinySaSweepSettingsPlan:
        rbw_mode = TinySaRbwMode(self._rbw_mode.currentData())
        attenuation_mode = TinySaAttenuationMode(self._attenuation_mode.currentData())
        return TinySaSweepSettingsPlan(
            accuracy=TinySaSweepAccuracy(self._accuracy.currentData()),
            rbw_mode=rbw_mode,
            rbw_hz=self._rbw_hz.value() if rbw_mode is TinySaRbwMode.MANUAL else None,
            sweep_time_ms=(
                self._sweep_time_ms.value() if self._sweep_time_enabled.isChecked() else None
            ),
            spur_removal=TinySaSpurPolicy(self._spur.currentData()),
            lna=TinySaSwitchPolicy(self._lna.currentData()),
            attenuation_mode=attenuation_mode,
            attenuation_db=(
                self._attenuation_db.value()
                if attenuation_mode is TinySaAttenuationMode.MANUAL
                else None
            ),
            repeat_count=self._repeat.value() if self._repeat_enabled.isChecked() else None,
        )

    def set_source_identity(self, source: TinySaVerifiedSource) -> None:
        """Show route-free verified identity provenance; never open the source."""

        if not isinstance(source, TinySaVerifiedSource):
            raise TypeError("tinySA workspace source identity is invalid")
        self._verified_source = source
        assurance = source.identity_assurance.value.replace("_", " ")
        text = (
            f"{source.label}; identity assurance: {assurance}; "
            "device continuity remains unverified."
        )
        self._source_identity.setText(text)
        self._source_identity.setAccessibleDescription(text)

    def _stage_settings(self) -> None:
        if self._visible_trace_lock is not None:
            self._show_error("Runtime settings are disabled for the visible evidence trace")
            return
        try:
            plan = self._build_settings_plan()
        except ValueError:
            self._show_error("Invalid tinySA settings")
            return
        self._presenter.stage_settings(plan)

    def _confirm_settings(self) -> None:
        if self._visible_trace_lock is not None:
            self._show_error("Runtime settings are disabled for the visible evidence trace")
            return
        answer = QMessageBox.warning(
            self,
            "Apply tinySA runtime settings",
            "Apply the reviewed runtime settings now? Some fields cannot be read back or restored. No persistent save will be performed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        self._presenter.confirm_settings(
            user_confirmed=answer == QMessageBox.StandardButton.Yes
        )

    def _show_trace(self, result: TinySaTraceApplicationResult) -> None:
        if not isinstance(result, TinySaTraceApplicationResult):
            self._show_error("Invalid tinySA trace result")
            return
        self._last_trace_result = result
        self._canvas.set_presentation(result.presentation)
        summary = (
            f"{result.presentation.source_point_count:,} analytical points; "
            f"{result.presentation.display_point_count:,} peak-preserving display extrema; "
            f"{result.elapsed_seconds:.3f} s; {result.observed_points_per_second:.2f} "
            "host-total points/s (not LPS or FFT/s)."
        )
        self._trace_summary.setText(summary)
        self._trace_summary.setAccessibleDescription(summary)
        self._empty.setVisible(False)
        self._error.setVisible(False)
        self.trace_observed.emit(result)

    def _emit_trace_painted(self, metrics: object) -> None:
        """Forward only scalar paint completion facts to an explicit observer."""

        self.trace_painted.emit(metrics)

    def _show_settings_review(self, review: TinySaSettingsReview) -> None:
        if not isinstance(review, TinySaSettingsReview):
            self._show_error("Invalid tinySA settings review")
            return
        warning_text = ", ".join(review.warnings)
        self._settings_detail.setText(
            f"Review: {', '.join(review.commands)}. State is not fully readable or restorable. "
            f"Warnings: {warning_text or 'none'}."
        )
        self._apply_settings.setEnabled(True)

    def _apply_snapshot(self, snapshot: TinySaAnalyzerSnapshot) -> None:
        tone = {
            TinySaAnalyzerPhase.READY: StatusTone.INFO,
            TinySaAnalyzerPhase.BUSY: StatusTone.WARNING,
            TinySaAnalyzerPhase.TRACE_READY: StatusTone.SUCCESS,
            TinySaAnalyzerPhase.SETTINGS_REVIEW: StatusTone.WARNING,
            TinySaAnalyzerPhase.FAULTED: StatusTone.ERROR,
        }[snapshot.phase]
        self._status.set_status(snapshot.phase.value.replace("_", " ").title(), tone)
        self._acquire.setEnabled(snapshot.can_collect)
        settings_allowed = self._visible_trace_lock is None
        self._review_settings.setEnabled(settings_allowed and snapshot.can_review_settings)
        self._apply_settings.setEnabled(settings_allowed and snapshot.can_confirm_settings)
        if snapshot.reason is not None:
            self._settings_detail.setText(snapshot.reason.value.replace("_", " "))

    def _set_busy(self, busy: bool) -> None:
        self.trace_busy_changed.emit(busy)
        self._busy.setVisible(busy)
        if busy:
            self._acquire.setEnabled(False)
            self._review_settings.setEnabled(False)
            self._apply_settings.setEnabled(False)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setAccessibleDescription(message)
        self._error.setVisible(True)


__all__ = ["TinySaAnalyzerWorkspace"]

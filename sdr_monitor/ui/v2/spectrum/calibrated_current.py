"""Separate CURRENT-only axis using the established spectrum renderer/worker."""
from weakref import ref

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..i18n import current_locale, text
from ..state.prepared_calibration import CalibratedCurrentFrame, PreparedCalibratedCurrent
from ..view_models.live_calibration_view_model import LiveCalibrationState, LiveCalibrationViewModel
from .contracts import TraceKind
from .projection import ProjectionRequest, SpectrumProjection, SpectrumProjector
from .scene import SpectrumScene


class _CurrentScene(SpectrumScene):
    """The existing renderer, plus authoritative projection AND repaint guards."""

    def __init__(self, model: LiveCalibrationViewModel, parent: QWidget) -> None:
        self._calibration_model = ref(model)
        self._current: PreparedCalibratedCurrent | None = None
        super().__init__(locale=current_locale(), parent=parent)
        self._graphics.viewport().installEventFilter(self)

    def valid(self, frame: object) -> bool:
        model = self._calibration_model()
        current = self._current
        return (model is not None and current is not None and frame is current.frame
                and model.is_valid(current))

    def _projection_current(self, request: ProjectionRequest) -> bool:
        view = dict(request.traces).get(TraceKind.CURRENT)
        return (view is not None and self.valid(view.source_frame)
                and super()._projection_current(request))

    def _accept_projection(self, result: SpectrumProjection, *, required_only: bool = False) -> None:
        view = dict(result.request.traces).get(TraceKind.CURRENT)
        if view is None or not self.valid(view.source_frame):
            return  # No invalid-result retry loop or historical rollback.
        # Authority and geometry have different lifetimes. Still-valid CURRENT
        # must retain the base renderer's bounded stale-viewport retry.
        admitted_geometry = self._projection_current(result.request)
        super()._accept_projection(result, required_only=required_only)
        model = self._calibration_model()
        if (admitted_geometry and model is not None and self._current is not None
                and self.displayed_frame is self._current.frame):
            model.displayed(self._current)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override.
        if event.type() == QEvent.Type.Paint and isinstance(self.displayed_frame, CalibratedCurrentFrame):
            if not self.valid(self.displayed_frame):
                self._current = None
                self.clear_measurement()
                model = self._calibration_model()
                if model is not None:
                    model.display_invalidated()
        return super().eventFilter(watched, event)


class CalibratedCurrentPlot(QWidget):
    """No raw overlay, hold, Waterfall or persistence mutation/activation."""

    def __init__(self, model: LiveCalibrationViewModel, projector: SpectrumProjector,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._projector = projector
        self._released = False
        self.setAccessibleName(text("live_calibration.current"))
        self.setMinimumHeight(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel(self)
        self.status.setProperty("ui2Role", "secondary")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.scene = _CurrentScene(model, self)
        self.scene.set_projection_port(projector)
        self.scene.set_presentation_active(False)
        # CURRENT is the only source; no analytical hold controls are exposed.
        self.scene.take_display_controls().hide()
        layout.addWidget(self.scene, 1)
        self._unsubscribe = model.subscribe(self._render)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override.
        if not self._released:
            self.scene.set_presentation_active(True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override.
        if not self._released:
            self.scene.set_presentation_active(False)
            self._model.display_invalidated()
        super().hideEvent(event)

    def _render(self, state: LiveCalibrationState) -> None:
        if self._released:
            return
        # Only explicit binding opens this extra analytical view. The default
        # raw Analyzer layout stays intact; no producer-rate visibility jitter.
        self.setVisible(state.binding is not None)
        current = state.current
        if current is None or not self._model.is_valid(current):
            self.scene._current = None
            self.scene.clear_measurement()
        elif self.scene.latest_frame is not current.frame:
            # admit_delivery occurred once at queued GUI acknowledgement;
            # repeated viewport/paint checks use is_valid ONLY.
            if self.scene.displayed_frame is not None and not self.scene.valid(self.scene.displayed_frame):
                self.scene.clear_measurement()
            self.scene._current = current
            self.scene.set_frame(current.frame, prepared=current.spectrum)
            self.scene.commit_projection()
        displayed = state.displayed
        if displayed is None or not self._model.is_valid(displayed):
            caption = text("live_calibration.current_pending")
        else:
            value = displayed.frame.publication.analytical
            caption = text("live_calibration.current_detail", profile=value.result.profile_id or "—",
                           version=value.profile_version or "—", fingerprint=value.profile_fingerprint or "—",
                           status=text("live_calibration.status." + value.result.status.value), unit=value.result.unit)
        if self.status.text() != caption:
            self.status.setText(caption)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._unsubscribe()
        self._model.close_binding()
        self.scene._current = None
        self.scene.clear_measurement()
        self._projector.dispose()
        self.scene.release_graphics_after_shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        self.release()
        super().closeEvent(event)

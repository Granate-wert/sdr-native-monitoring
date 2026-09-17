"""Debounced scalar plan preview; no frame, device or acquisition ownership."""
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from sdr_monitor.domain import LiveConfiguration
from sdr_monitor.domain.analyzer_resources import AnalyzerGeometryPreflight
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..i18n import text


class AnalyzerSweepPreview(QWidget):
    changed = Signal()

    def __init__(self, calculate: Callable[
        [LiveConfiguration, ContinuousSweepPlanRequest], AnalyzerGeometryPreflight,
    ], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._calculate = calculate
        self._inputs: tuple[LiveConfiguration, ContinuousSweepPlanRequest] | None = None
        self.result: AnalyzerGeometryPreflight | None = None
        self._error: str | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self.resolve)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.summary = QLabel(self)
        self.summary.setProperty("ui2Role", "secondary")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard
                                             | Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        self.set_locale()

    def set_inputs(self, configuration: LiveConfiguration,
                   request: ContinuousSweepPlanRequest) -> None:
        inputs = (configuration, request)
        if inputs == self._inputs:
            return
        self._inputs = inputs
        self.result, self._error = None, None
        self._timer.start()
        self.set_locale()

    def invalidate(self, reason: str) -> None:
        self._timer.stop()
        self._inputs = None
        self.result, self._error = None, reason
        self.set_locale()

    def cancel(self) -> None:
        self._timer.stop()
        self._inputs = None
        self.result, self._error = None, None

    def resolve(self) -> bool:
        """Also used immediately before explicit Start, bypassing debounce."""
        self._timer.stop()
        if self._inputs is None:
            return False
        if self.result is None:
            try:
                result = self._calculate(*self._inputs)
                if not isinstance(result, AnalyzerGeometryPreflight) or result.mode != "sweep":
                    raise ValueError("Invalid Sweep preview result")
                self.result, self._error = result, None
            except Exception as error:
                self.result, self._error = None, str(error)
        self.set_locale()
        self.changed.emit()
        return self.result is not None

    def set_locale(self) -> None:
        result = self.result
        detail = text("analyzer.preview.scope")
        if self._error is not None:
            summary = text("analyzer.preview.invalid", reason=self._error)
        elif result is None:
            summary = text("analyzer.preview.pending")
        else:
            summary = text(
                "analyzer.preview.summary", segments=result.segment_count,
                bins=result.reduced.output_bins, spacing=f"{result.output_spacing_hz:g}",
                average=result.fft_averaging_frames,
                memory=f"{(result.reduced.total_bytes + result.statistics_payload_bytes) / 2**20:.2f}",
            )
            detail = text(
                "analyzer.preview.details", window=f"{result.usable_window_hz / 1e6:g}",
                stride=f"{result.segment_stride_hz / 1e6:g}", fft=result.physical_fft_size,
                physical=f"{result.physical_bin_spacing_hz:g}",
                samples=result.minimum_samples_per_spectrum,
                reduced=result.reduced.total_bytes, statistics=result.statistics_payload_bytes,
            ) + "\n" + detail
        if self.summary.text() != summary:
            self.summary.setText(summary)
        self.summary.setToolTip(detail)
        self.summary.setAccessibleName(summary)
        self.summary.setAccessibleDescription(detail)

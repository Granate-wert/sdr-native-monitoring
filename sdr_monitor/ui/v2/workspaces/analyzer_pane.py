"""One independently resettable UI V2 Analyzer spectrum/waterfall pane.

The pane receives already admitted Analyzer publications. It owns only
presentation state: no receiver, source selection, worker, or RF command.
Multiple panes may therefore observe one capture without reopening an SDR,
while their history, persistence, viewport and paint cadence remain separate.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QLabel, QWidget

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot

from ..i18n import text
from ..spectrum import PersistenceDensityFrame
from ..spectrum.allocation_budget import PresentationBudgetExceeded
from ..spectrum.contracts import PreparedSpectrumFrame, TraceKind
from ..spectrum.projection import SpectrumProjector
from ..state.analyzer_layers import persistence_density_from_sweep, waterfall_line_from_sweep
from ..view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewState
from ..waterfall import SpectrumWaterfallView, WaterfallLineFrame


class AnalyzerPaneViewV2(SpectrumWaterfallView):
    """Apply one coherent Analyzer state to one local pair of graph canvases."""

    def __init__(self, initial_mode: AnalyzerMode, *, projector: SpectrumProjector | None = None,
                 on_frame_applied: Callable[[], None] | None = None,
                 settings: QSettings | None = None,
                 settings_prefix: str | None = None,
                 pane_number: int = 1,
                 parent: QWidget | None = None) -> None:
        if type(pane_number) is not int or not 1 <= pane_number <= 4:
            raise ValueError("Analyzer pane number must be in [1, 4]")
        if settings_prefix is None:
            super().__init__(settings=settings, parent=parent)
        else:
            super().__init__(settings=settings, settings_prefix=settings_prefix, parent=parent)
        self._last_bundle: AnalyzerFrameBundle | None = None
        self._last_mode = initial_mode
        self._last_waterfall: WaterfallLineFrame | None = None
        self._last_persistence: PersistenceDensityFrame | None = None
        self._last_identity = None
        self._last_sweep_snapshot: ContinuousSweepDisplaySnapshot | None = None
        self._last_statistics_key: tuple[str, int, int] | None = None
        self._sweep_waterfall_error = False
        self._terminal_released = False
        self._on_frame_applied = on_frame_applied
        self.pane_number = pane_number
        self._pane_badge = QLabel(str(pane_number), self)
        self._pane_badge.setProperty("ui2Role", "pane-badge")
        self._pane_badge.setProperty("ui2Selected", False)
        self._pane_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pane_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._pane_badge.setFixedSize(28, 24)
        self._position_badge()
        self.set_badge_locale()
        if projector is not None:
            self.spectrum_scene.set_projection_port(projector)
            self.waterfall_pane._renderer.allocation_budget = projector.allocation_budget

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_badge()
        self._pane_badge.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_pane_badge"):
            self._position_badge()

    def _position_badge(self) -> None:
        self._pane_badge.move(max(8, self.width() - self._pane_badge.width() - 8), 8)

    def set_badge_locale(self) -> None:
        label = f"{text('analyzer.shared_views.short')} {self.pane_number}"
        self._pane_badge.setAccessibleName(label)
        self._pane_badge.setToolTip(label)

    def set_selected(self, selected: bool) -> None:
        if self._pane_badge.property("ui2Selected") == bool(selected):
            return
        self._pane_badge.setProperty("ui2Selected", bool(selected))
        self._pane_badge.style().unpolish(self._pane_badge)
        self._pane_badge.style().polish(self._pane_badge)
        self._pane_badge.update()

    @property
    def last_bundle(self) -> AnalyzerFrameBundle | None:
        return self._last_bundle

    @property
    def last_statistics_key(self) -> tuple[str, int, int] | None:
        return self._last_statistics_key

    @property
    def sweep_waterfall_error(self) -> bool:
        return self._sweep_waterfall_error

    def apply_analyzer_state(self, state: AnalyzerViewState) -> None:
        if self._terminal_released:
            return
        identity = getattr(state.bundle, "identity", None)
        previous = self._last_identity
        scene = self.spectrum_scene
        prepared_spectrum = (state.prepared_sweep.spectrum if state.prepared_sweep is not None else
                             state.live.prepared_spectrum if state.mode is AnalyzerMode.RTBW else None)
        if state.bundle is not None and state.bundle is not self._last_bundle and prepared_spectrum is not None:
            # Reject foreign preparation before clearing the accepted history.
            if (not isinstance(prepared_spectrum, PreparedSpectrumFrame)
                    or prepared_spectrum.view.source_frame is not state.bundle):
                raise ValueError("prepared spectrum must belong to the exact publication")
        prepared_grid = None if prepared_spectrum is None else prepared_spectrum.measurement_grid
        previous_grid = scene.measurement_grid
        fields = ("source_id", "session_id", "receiver_id", "acquisition_epoch", "config_generation",
                  "clock_domain", "accumulation_id", "unit")
        # Lifecycle/error/locale publications can carry the exact bundle already
        # applied below. They are not new measurements: do not re-scan its entire
        # frequency grid on the GUI thread. New prepared bundles can share an
        # owned baseline only after exact worker-side content comparison.
        changed_identity = (state.bundle is not self._last_bundle
                            and identity is not None and previous is not None and (
            any(getattr(identity, name) != getattr(previous, name) for name in fields)
            or not (prepared_grid is not None and prepared_grid is previous_grid
                    or np.array_equal(
                        prepared_grid if prepared_grid is not None else identity.frequencies_hz,
                        previous_grid if previous_grid is not None else previous.frequencies_hz))
        ))
        if (state.mode is not self._last_mode or changed_identity
                or state.bundle is None and self._last_bundle is not None):
            scene.clear_measurement()
            self.waterfall_pane.clear_history(reset_kind=True)
            if self._sweep_waterfall_error:
                scene.set_warning(None)
                self._sweep_waterfall_error = False
            self._last_bundle = self._last_waterfall = self._last_persistence = None
            self._last_sweep_snapshot = None
            self._last_statistics_key = None
            self._last_mode = state.mode
        self._last_identity = identity
        bundle = state.bundle
        if bundle is not None and bundle is not self._last_bundle:
            scene.set_frame(bundle, prepared=prepared_spectrum)
            self._last_bundle = bundle
            if self._on_frame_applied is not None:
                self._on_frame_applied()
        if state.mode is AnalyzerMode.SWEEP:
            statistics = bundle.sweep_statistics if bundle is not None else None
            statistics_key = ((statistics.source_id, statistics.epoch, statistics.update_sequence)
                   if statistics is not None else None)
            if statistics is not None and statistics_key != self._last_statistics_key:
                scene.set_trace(TraceKind.AVERAGE, statistics)
                scene.set_persistence_frame(persistence_density_from_sweep(statistics))
                self._last_statistics_key = statistics_key
            elif statistics is None and self._last_statistics_key is not None:
                scene.clear_trace(TraceKind.AVERAGE)
                scene.clear_persistence_display()
                self._last_statistics_key = None
        if state.mode is AnalyzerMode.RTBW:
            density = state.live.persistence_frame
            if isinstance(density, PersistenceDensityFrame) and density is not self._last_persistence:
                scene.set_persistence_frame(density)
                self._last_persistence = density
            elif (density is None and self._last_persistence is not None
                  and "persistence_pending" not in getattr(bundle, "coherence_issues", ())):
                scene.clear_persistence_display()
                self._last_persistence = None
            row = state.live.waterfall_line
            if isinstance(row, WaterfallLineFrame) and row is not self._last_waterfall:
                self.waterfall_pane.set_line(row)
                self._last_waterfall = row
        elif state.sweep_snapshot is not None and state.sweep_snapshot is not self._last_sweep_snapshot:
            snapshot = state.sweep_snapshot
            scene.sweep_coverage.accept(snapshot)
            # Preserve terminal N and progressive N+1 from the same backend
            # poll, even though only N+1 is current on the upper spectrum.
            try:
                prepared = state.prepared_sweep
                if prepared is not None:
                    if prepared.snapshot is not snapshot:
                        raise ValueError("Prepared Sweep snapshot identity mismatch")
                    if prepared.memory_limited:
                        raise PresentationBudgetExceeded(prepared.waterfall_error)
                    if prepared.waterfall_error is not None:
                        raise ValueError(prepared.waterfall_error)
                    rows = prepared.waterfall_rows
                else:
                    # Public injected/fake ports retain the legacy adapter seam.
                    # Normal product composition prepares in its worker.
                    rows = tuple(waterfall_line_from_sweep(frame)
                                 for frame in (snapshot.line, snapshot.progress) if frame is not None)
            except PresentationBudgetExceeded:
                scene.set_warning(text("analyzer.memory_limited"))
                self._sweep_waterfall_error = True
            except (ValueError, TypeError) as error:
                self.waterfall_pane.clear_history()
                scene.set_warning(text("waterfall.sweep.invalid", reason=str(error)))
                self._sweep_waterfall_error = True
            else:
                for update in rows:
                    self.waterfall_pane.set_sweep_line(update)
                if self._sweep_waterfall_error:
                    scene.set_warning(None)
                    self._sweep_waterfall_error = False
            self._last_sweep_snapshot = snapshot

    def release_presentation_after_shutdown(self) -> None:
        """Retire only this pane after the application confirms terminal close."""
        if self._terminal_released:
            return
        self.hide()
        self.spectrum_scene.set_presentation_active(False)
        self.spectrum_scene.clear_measurement()
        self.waterfall_pane.release_presentation_after_shutdown()
        self.spectrum_scene.release_graphics_after_shutdown()
        self._last_bundle = self._last_waterfall = self._last_persistence = None
        self._last_identity = self._last_sweep_snapshot = self._last_statistics_key = None
        self._terminal_released = True

    def clear_shared_view(self) -> None:
        """Drop a parked pane's measurement roots without touching the RX owner."""
        if self._terminal_released:
            return
        self.spectrum_scene.clear_measurement()
        self.waterfall_pane.drop_parked_history_storage()
        self._last_bundle = self._last_waterfall = self._last_persistence = None
        self._last_identity = self._last_sweep_snapshot = self._last_statistics_key = None
        self._sweep_waterfall_error = False


__all__ = ["AnalyzerPaneViewV2"]

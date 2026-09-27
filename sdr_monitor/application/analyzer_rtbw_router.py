"""Family dispatch beneath the EXISTING Analyzer lifecycle, not another session."""

from __future__ import annotations

from typing import Protocol, cast

from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.device_capabilities import DeviceFamily
from ..domain.hackrf_live import HackrfConfigurationPatch
from ..domain.live import LiveAdmissionRejected, LiveSnapshot
from .analyzer_sources import AnalyzerSourceSelectionApplicationService


class NativeRtbwPort(Protocol):
    def start_admitted(self) -> LiveSnapshot: ...
    def stop(self) -> LiveSnapshot: ...
    def latest_snapshot(self) -> LiveSnapshot: ...
    def is_running(self) -> bool: ...
    def poll_frames(self) -> list[LiveSnapshot]: ...


class HackrfRtbwPort(Protocol):
    def bind_selection(self, selection: AnalyzerSourceSelection) -> None: ...
    def stage(self, patch: HackrfConfigurationPatch) -> LiveSnapshot: ...
    def start(self) -> LiveSnapshot: ...
    def stop(self) -> LiveSnapshot: ...
    def current_snapshot(self) -> LiveSnapshot: ...
    def is_running(self) -> bool: ...
    def poll_frames(self) -> list[LiveSnapshot]: ...


class AnalyzerRtbwRouter:
    """Retain the dispatched port until confirmed Stop; never switch underneath RX."""

    def __init__(self, native: NativeRtbwPort,
                 sources: AnalyzerSourceSelectionApplicationService,
                 hackrf: HackrfRtbwPort | None) -> None:
        self._native, self._sources, self._hackrf = native, sources, hackrf
        self._dispatched: NativeRtbwPort | HackrfRtbwPort | None = None

    def refresh_selection(self) -> None:
        if self._hackrf is not None:
            self._hackrf.bind_selection(self._sources.current())

    @property
    def hackrf_selected(self) -> bool:
        selection = self._sources.current()
        return bool(self._hackrf is not None and not selection.release_pending
                    and selection.selected is not None and selection.selected.family is DeviceFamily.HACKRF)

    def _selected_port(self) -> NativeRtbwPort | HackrfRtbwPort:
        if self.hackrf_selected:
            assert self._hackrf is not None
            return self._hackrf
        self._sources.require_ad936x_controls()
        return self._native

    def stage_hackrf(self, patch: HackrfConfigurationPatch) -> LiveSnapshot:
        if not self.hackrf_selected or self._hackrf is None:
            raise LiveAdmissionRejected("HackRF RTBW runtime is not composed for this source")
        return self._hackrf.stage(patch)

    def current_snapshot(self) -> LiveSnapshot:
        port = self._dispatched or self._selected_port()
        return self._native.latest_snapshot() if port is self._native else cast(HackrfRtbwPort, port).current_snapshot()

    def start(self) -> LiveSnapshot:
        if self._dispatched is not None:
            raise LiveAdmissionRejected("Release the existing RTBW port before another Start")
        port = self._selected_port()
        self._dispatched = port  # Capture BEFORE SDK effects; Stop uses this exact owner.
        try:
            return self._native.start_admitted() if port is self._native else cast(HackrfRtbwPort, port).start()
        except LiveAdmissionRejected:
            self._dispatched = None  # Contract means refusal before ANY resource acquisition.
            raise

    def stop(self) -> LiveSnapshot:
        port = self._dispatched or self._selected_port()
        snapshot = port.stop()
        if snapshot.error is None and not snapshot.stop_required and not port.is_running():
            self._dispatched = None
        return snapshot

    def is_running(self) -> bool:
        # Analyzer checks BEFORE dispatch even when no source is selected.
        return self._native.is_running() or bool(self._hackrf and self._hackrf.is_running())

    def poll_frames(self) -> list[LiveSnapshot]:
        if self._dispatched is not None:
            return self._dispatched.poll_frames()
        if self.hackrf_selected and self._hackrf is not None:
            return self._hackrf.poll_frames()
        return self._native.poll_frames() if self._sources.current().ad936x_controls_available else []


__all__ = ["AnalyzerRtbwRouter"]

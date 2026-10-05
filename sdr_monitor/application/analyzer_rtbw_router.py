"""Family dispatch beneath the EXISTING Analyzer lifecycle, not another session."""

from __future__ import annotations

from typing import Protocol, cast

from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.device_capabilities import DeviceFamily
from ..domain.device_capabilities import AdapterRuntimeAvailability
from ..services.rtl_capability_provider import RTL_ADAPTER_ID
from ..domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from ..domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
from ..domain.live import LiveAdmissionRejected, LiveSnapshot
from ..domain.analytical_journal import OwnerJournalSnapshot
from ..services.owner_journal_scope import cached_owner_journals
from ..domain.layer_journal import LayerJournalSnapshot
from ..services.pane_layer_admission import cached_layer_journals
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
    def preflight(self, request: HackrfLiveRequest) -> None: ...


class RtlRtbwPort(Protocol):
    def native_control_available(self) -> bool: ...
    def bind_selection(self, selection: AnalyzerSourceSelection) -> None: ...
    def stage(self, patch: RtlConfigurationPatch) -> LiveSnapshot: ...
    def start(self) -> LiveSnapshot: ...
    def stop(self) -> LiveSnapshot: ...
    def current_snapshot(self) -> LiveSnapshot: ...
    def is_running(self) -> bool: ...
    def poll_frames(self) -> list[LiveSnapshot]: ...
    def preflight(self, request: RtlLiveRequest) -> None: ...


class AnalyzerRtbwRouter:
    """Retain the dispatched port until confirmed Stop; never switch underneath RX."""

    def __init__(self, native: NativeRtbwPort,
                 sources: AnalyzerSourceSelectionApplicationService,
                 hackrf: HackrfRtbwPort | None, rtl: RtlRtbwPort | None = None) -> None:
        self._native, self._sources, self._hackrf, self._rtl = native, sources, hackrf, rtl
        self._dispatched: NativeRtbwPort | HackrfRtbwPort | RtlRtbwPort | None = None
        self._terminal_layers: tuple[LayerJournalSnapshot, ...] = ()

    def refresh_selection(self) -> None:
        if self._hackrf is not None:
            self._hackrf.bind_selection(self._sources.current())
        if self._rtl is not None:
            self._rtl.bind_selection(self._sources.current())

    def analytical_journal_snapshots(self) -> tuple[OwnerJournalSnapshot, ...]:
        """Cached SAME dispatched owner only; no selection fallback or SDK."""
        return cached_owner_journals(self._dispatched) if self._dispatched is not None else ()

    def density_layer_journal_snapshots(self) -> tuple[LayerJournalSnapshot, ...]:
        """Cached SAME dispatched owner, never the newly selected device."""
        return cached_layer_journals(self._dispatched) if self._dispatched is not None else self._terminal_layers

    @property
    def hackrf_selected(self) -> bool:
        selection = self._sources.current()
        return bool(self._hackrf is not None and not selection.release_pending
                    and selection.selected is not None and selection.selected.family is DeviceFamily.HACKRF)

    @property
    def rtl_selected(self) -> bool:
        selection = self._sources.current()
        return bool(self._rtl is not None and not selection.release_pending
                    and selection.selected is not None and selection.selected.family is DeviceFamily.RTL_SDR)

    def rtl_controls_available(self, source_id: str, selection_revision: int) -> bool:
        selection = self._sources.current()
        choice = selection.selected
        return bool(self._rtl is not None and self._rtl.native_control_available() and choice is not None
                    and selection.revision == selection_revision and choice.device_id == source_id
                    and not selection.release_pending and selection.refusal is None
                    and choice.family is DeviceFamily.RTL_SDR
                    and choice.binding.rtl_session_route is not None
                    and choice.runtime is not None
                    and choice.runtime.availability is AdapterRuntimeAvailability.AVAILABLE)

    def rtl_candidate_stage_available(self, source_id: str, selection_revision: int) -> bool:
        """Default graph's cached candidate, not a selected Start permission."""
        selection = self._sources.current()
        if (self._rtl is None or not self._rtl.native_control_available()
                or selection.release_pending or selection.refusal is not None
                or selection.revision != selection_revision):
            return False
        choice = next((value for value in selection.choices if value.device_id == source_id), None)
        return bool(choice is not None and choice.family is DeviceFamily.RTL_SDR
                    and choice.binding.adapter_id == RTL_ADAPTER_ID
                    and choice.runtime is not None
                    and choice.runtime.availability is AdapterRuntimeAvailability.AVAILABLE)

    def _selected_port(self) -> NativeRtbwPort | HackrfRtbwPort | RtlRtbwPort:
        if self.hackrf_selected:
            assert self._hackrf is not None
            return self._hackrf
        if self.rtl_selected:
            assert self._rtl is not None
            return self._rtl
        self._sources.require_ad936x_controls()
        return self._native

    def stage_hackrf(self, patch: HackrfConfigurationPatch) -> LiveSnapshot:
        if not self.hackrf_selected or self._hackrf is None:
            raise LiveAdmissionRejected("HackRF RTBW runtime is not composed for this source")
        return self._hackrf.stage(patch)

    def preflight_hackrf(self, request: HackrfLiveRequest) -> None:
        if not self.hackrf_selected or self._hackrf is None:
            raise LiveAdmissionRejected("HackRF RTBW runtime is not composed for this source")
        self._hackrf.preflight(request)

    def stage_rtl(self, patch: RtlConfigurationPatch) -> LiveSnapshot:
        if not self.rtl_selected or self._rtl is None:
            raise LiveAdmissionRejected("RTL RTBW runtime is not composed for this source")
        return self._rtl.stage(patch)

    def preflight_rtl(self, request: RtlLiveRequest) -> None:
        if not self.rtl_selected or self._rtl is None:
            raise LiveAdmissionRejected("RTL RTBW runtime is not composed for this source")
        self._rtl.preflight(request)

    def current_snapshot(self) -> LiveSnapshot:
        port = self._dispatched or self._selected_port()
        return self._native.latest_snapshot() if port is self._native else cast(HackrfRtbwPort | RtlRtbwPort, port).current_snapshot()

    def start(self) -> LiveSnapshot:
        if self._dispatched is not None:
            raise LiveAdmissionRejected("Release the existing RTBW port before another Start")
        port = self._selected_port()
        self._terminal_layers = ()  # Retired scalar evidence cannot shadow a new owner.
        self._dispatched = port  # Capture BEFORE SDK effects; Stop uses this exact owner.
        try:
            return self._native.start_admitted() if port is self._native else cast(HackrfRtbwPort | RtlRtbwPort, port).start()
        except LiveAdmissionRejected:
            self._dispatched = None  # Contract means refusal before ANY resource acquisition.
            raise

    def stop(self) -> LiveSnapshot:
        port = self._dispatched or self._selected_port()
        snapshot = port.stop()
        if snapshot.error is None and not snapshot.stop_required and not port.is_running():
            try:
                self._terminal_layers = cached_layer_journals(port)
            except Exception:  # noqa: BLE001 - optional diagnostics cannot undo confirmed Stop.
                self._terminal_layers = ()
            self._dispatched = None
        return snapshot

    def is_running(self) -> bool:
        # Analyzer checks BEFORE dispatch even when no source is selected.
        return (self._native.is_running() or bool(self._hackrf and self._hackrf.is_running())
                or bool(self._rtl and self._rtl.is_running()))

    def poll_frames(self) -> list[LiveSnapshot]:
        if self._dispatched is not None:
            return self._dispatched.poll_frames()
        if self.hackrf_selected and self._hackrf is not None:
            return self._hackrf.poll_frames()
        if self.rtl_selected and self._rtl is not None:
            return self._rtl.poll_frames()
        return self._native.poll_frames() if self._sources.current().ad936x_controls_available else []


__all__ = ["AnalyzerRtbwRouter"]

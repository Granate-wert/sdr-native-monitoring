"""Sweep family dispatch BELOW the single common Analyzer lifecycle."""

from typing import Protocol

from ..domain.analyzer_display import ContinuousSweepDisplaySnapshot
from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.live import LiveAdmissionRejected
from ..domain.tinysa_analyzer import TinySaSweepRequest, TinySaSweepRunIdentity
from .analyzer_sources import AnalyzerSourceSelectionApplicationService


class NativeSweepPort(Protocol):
    def start(self, request: ContinuousSweepPlanRequest) -> None: ...
    def stop(self) -> None: ...
    def poll_latest(self) -> ContinuousSweepDisplaySnapshot: ...


class InstrumentSweepPort(Protocol):
    def start(self, request: TinySaSweepRequest, selection: AnalyzerSourceSelection) -> None: ...
    def stop(self) -> None: ...
    def poll_latest(self) -> ContinuousSweepDisplaySnapshot: ...


class AnalyzerSweepRouter:
    def __init__(self, native: NativeSweepPort, sources: AnalyzerSourceSelectionApplicationService | None,
                 instrument: InstrumentSweepPort | None) -> None:
        self._native, self._sources, self._instrument = native, sources, instrument
        self._dispatched: NativeSweepPort | InstrumentSweepPort | None = None
        self._terminal: NativeSweepPort | InstrumentSweepPort | None = None

    @property
    def instrument_run_identity(self) -> TinySaSweepRunIdentity | None:
        value = getattr(self._dispatched or self._terminal, "instrument_run_identity", None)
        return value if isinstance(value, TinySaSweepRunIdentity) else None

    def start(self, request: ContinuousSweepPlanRequest | TinySaSweepRequest) -> None:
        port: NativeSweepPort | InstrumentSweepPort
        if self._dispatched is not None:
            raise LiveAdmissionRejected("Release the current Sweep owner before Start")
        if isinstance(request, TinySaSweepRequest):
            if self._instrument is None or self._sources is None:
                raise LiveAdmissionRejected("Common instrument Sweep is not composed")
            port = self._instrument
        else:
            if self._sources is not None:
                self._sources.require_ad936x_controls()
            port = self._native
        self._dispatched = self._terminal = port  # exact owner captured BEFORE effects
        try:
            if isinstance(request, TinySaSweepRequest):
                assert self._instrument is not None and self._sources is not None
                self._instrument.start(request, self._sources.current())
            else:
                self._native.start(request)
        except LiveAdmissionRejected:
            self._dispatched = self._terminal = None
            raise

    def stop(self) -> None:
        if self._dispatched is not None:
            self._dispatched.stop()
            self._dispatched = None  # only on confirmed Stop; terminal path remains captured

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        port = self._dispatched or self._terminal
        if port is None:
            raise RuntimeError("Sweep has no captured publication owner")
        return port.poll_latest()

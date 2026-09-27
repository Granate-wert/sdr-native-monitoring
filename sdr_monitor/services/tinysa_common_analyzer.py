"""One instrument pass beneath the common Analyzer owner and renderer.

No alternate backend/provider, continuous reopen loop, fake Live profile or
serial work on Qt. A complete pass ends through the SAME explicit Stop/join
path. A failed close retains both the exact serial owner and graph token.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

import numpy as np

from ..domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.live import LiveAdmissionRejected
from ..domain.sweep_lines import SweepLineFrame, SweepLineGapReason, SweepLineState, SweepQualitySchema
from ..domain.tinysa_analyzer import TinySaSweepProvenance, TinySaSweepRequest, TinySaSweepRunIdentity
from .source_capability_admission import admit_source_request
from .source_capability_catalog import SourceCapabilityCatalog
from .tinysa_capability_adapter import TinySaModel
from .tinysa_owned_acquisition import TinySaOwnedAcquisition
from .tinysa_serial_trace_collector import TinySaScanRawRequest, TinySaTraceCollectionCancelled


class InstrumentExclusionPort(Protocol):
    def claim_external_analyzer_rx(self, owner: object) -> None: ...
    def release_external_analyzer_rx(self, owner: object) -> None: ...


class TinySaCommonAnalyzerService:
    def __init__(self, catalog: SourceCapabilityCatalog, exclusion: InstrumentExclusionPort, *,
                 monotonic: Callable[[], float] = time.monotonic,
                 monotonic_ns: Callable[[], int] = time.monotonic_ns) -> None:
        self._catalog, self._exclusion = catalog, exclusion
        self._clock, self._clock_ns = monotonic, monotonic_ns
        self._operation = threading.Lock()
        self._lock = threading.Lock()
        self._owner: TinySaOwnedAcquisition | None = None
        self._thread: threading.Thread | None = None
        self._claimed = False
        self._generation = 0
        self._request: TinySaSweepRequest | None = None
        self._run_identity: TinySaSweepRunIdentity | None = None
        self._snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())

    @property
    def stop_required(self) -> bool:
        return self._owner is not None or self._claimed or self._thread is not None

    @property
    def instrument_run_identity(self) -> TinySaSweepRunIdentity | None:
        return self._run_identity  # cached; no serial I/O or operation lock

    def start(self, request: TinySaSweepRequest, selection: AnalyzerSourceSelection) -> None:
        if not self._operation.acquire(blocking=False):
            raise LiveAdmissionRejected("Instrument control operation is pending")
        try:
            if self.stop_required:
                raise LiveAdmissionRejected("Release the previous instrument pass before Start")
            if (not isinstance(request, TinySaSweepRequest) or selection.release_pending
                    or selection.selected is not request.source or selection.revision != request.selection_revision):
                raise LiveAdmissionRejected("Instrument selection changed; rebuild the request")
            inventory = self._catalog.snapshot()
            source = request.source
            if (inventory.binding_for_source(source.device_id) is not source.binding
                    or inventory.runtime_for_adapter(source.binding.adapter_id) is not source.runtime):
                raise LiveAdmissionRejected("Instrument catalog changed; select again")
            assert source.binding.snapshot is not None and source.runtime is not None
            scan = TinySaScanRawRequest(TinySaModel(source.binding.snapshot.model_id or ""), request.start_hz,
                                       request.stop_hz, request.points, request.timeout_s)
            admitted = admit_source_request(inventory, source.device_id, "sweep", scan)
            if not admitted.accepted or self._generation >= (1 << 64) - 1:
                raise LiveAdmissionRejected("Instrument request is not admitted")
            # Inert preparation is retained BEFORE graph acquisition. A later
            # claim/start failure therefore requires Stop, not admission reset.
            self._owner = self._catalog.prepare_tinysa_acquisition(source.binding, source.runtime)
            self._request = request
            self._generation += 1
            self._run_identity = TinySaSweepRunIdentity(request, self._generation)
            with self._lock:
                self._snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())
            try:
                self._exclusion.claim_external_analyzer_rx(self)
                self._claimed = True
                thread = threading.Thread(target=self._collect, args=(scan,), name="sdr-tinysa-sweep", daemon=True)
                self._thread = thread
                thread.start()
            except Exception:  # noqa: BLE001 - retain partial Start owner and redact boundary details.
                raise RuntimeError("Instrument Start failed; Stop is required") from None
        finally:
            self._operation.release()

    def _line(self, values: np.ndarray, *, zero: float | None, elapsed: float,
              cancelled: bool = False) -> SweepLineFrame:
        request = self._request
        assert request is not None and request.source.binding.snapshot is not None
        identity = request.source.binding.calibration_identity
        assert identity is not None
        p = TinySaSweepProvenance(request.selection_revision, self._generation,
            request.source.binding.snapshot.model_id or "", identity.device_identity_key,
            identity.firmware_fingerprint, request.start_hz, request.stop_hz, request.points, zero, elapsed)
        grid = request.start_hz + np.arange(request.points, dtype=np.float64) * (
            (request.stop_hz - request.start_hz) // request.points)
        return SweepLineFrame(0, request.epoch, self._clock_ns(), request.source.device_id,
            SweepLineState.GAP if cancelled else SweepLineState.COMPLETE, grid, values,
            np.zeros(request.points, dtype=np.uint16), np.full(request.points, -1, dtype=np.int32),
            (), (), (SweepLineGapReason.CANCELLATION,) if cancelled else (), "dBm",
            quality_schema=SweepQualitySchema.INSTRUMENT_V1, instrument=p)

    def _collect(self, scan: TinySaScanRawRequest) -> None:
        owner = self._owner
        assert owner is not None
        started = self._clock()
        try:
            result = owner.collect(scan)
            line = self._line(result.trace.values_dbm, zero=result.trace.scanraw_zero_offset_db,
                              elapsed=self._clock() - started)
            snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(
                completed_lines=1, acquisition_finished=True))
        except TinySaTraceCollectionCancelled:
            line = self._line(np.full(scan.points, np.nan, dtype=np.float32), zero=None,
                              elapsed=self._clock() - started, cancelled=True)
            snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(gapped_lines=1))
        except Exception:  # noqa: BLE001 - never publish serial routes/vendor exception strings.
            snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(
                has_error=True, error="tinySA acquisition failed; Stop/release required"))
        with self._lock:
            self._snapshot = snapshot

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        with self._lock:
            return self._snapshot  # bounded cached publication; no serial I/O

    def stop(self) -> None:
        if not self._operation.acquire(blocking=False):
            raise RuntimeError("Instrument lifecycle operation is pending")
        try:
            owner, thread = self._owner, self._thread
            if owner is not None:
                owner.cancel()
            if thread is not None and thread.ident is not None:
                thread.join(timeout=3.0)
                if thread.is_alive():
                    raise RuntimeError("tinySA worker did not join; owner retained")
            if owner is not None:
                owner.close()  # exact retained object, including partial-open failure
                if owner.cleanup_pending:
                    raise RuntimeError("tinySA close unconfirmed; owner retained")
            if self._claimed:
                self._exclusion.release_external_analyzer_rx(self)
                self._claimed = False
            self._thread = self._owner = None
            with self._lock:
                self._snapshot = replace(self._snapshot,
                    metrics=replace(self._snapshot.metrics, acquisition_finished=False))
        finally:
            self._operation.release()

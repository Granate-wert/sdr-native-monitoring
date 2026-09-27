"""One-shot or explicit repeated instrument Sweep beneath the SAME Analyzer.

No alternate backend/provider, continuous reopen loop, fake Live profile or
serial work on Qt. One-shot completion or explicit repeated Stop uses the
SAME Stop/join path. Failed close retains the exact serial owner and graph token.
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
from .tinysa_serial_trace_collector import TinySaScanRawRequest, TinySaTraceCollectionCancelled, TinySaTracePass


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
        self._completed = self._gapped = self._sequence = 0
        self._first_completion: float | None = None
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
            self._completed = self._gapped = self._sequence = 0
            self._first_completion = None
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
        return SweepLineFrame(self._sequence, request.epoch, self._clock_ns(), request.source.device_id,
            SweepLineState.GAP if cancelled else SweepLineState.COMPLETE, grid, values,
            np.zeros(request.points, dtype=np.uint16), np.full(request.points, -1, dtype=np.int32),
            (), (), (SweepLineGapReason.CANCELLATION,) if cancelled else (), "dBm",
            quality_schema=SweepQualitySchema.INSTRUMENT_V1, instrument=p)

    def _collect(self, scan: TinySaScanRawRequest) -> None:
        owner = self._owner
        assert owner is not None
        started = self._clock()
        try:
            request = self._request
            assert request is not None
            if request.repeat_until_stop:
                owner.collect_repeated(scan, self._publish_pass, interval_s=request.interval_s)
                return  # Repeated collection normally exits by explicit Stop.
            result = owner.collect(scan)
            if owner.cancellation_requested:
                raise TinySaTraceCollectionCancelled("tinySA trace collection was cancelled")
            line = self._line(result.trace.values_dbm, zero=result.trace.scanraw_zero_offset_db,
                              elapsed=self._clock() - started)
            snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(
                completed_lines=1, acquisition_finished=True))
        except TinySaTraceCollectionCancelled:
            if self._completed and not owner.measurement_pending:
                return  # Stop between passes keeps the last complete trace, no fake gap.
            elapsed = owner.current_pass_elapsed_s
            line = self._line(np.full(scan.points, np.nan, dtype=np.float32), zero=None,
                              elapsed=elapsed if elapsed is not None else self._clock() - started, cancelled=True)
            self._gapped += 1
            snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(
                completed_lines=self._completed, gapped_lines=self._gapped))
        except Exception:  # noqa: BLE001 - never publish serial routes/vendor exception strings.
            failure = owner.failure
            suffix = f" [{failure.phase.value}/{failure.reason.value}]" if failure is not None else ""
            with self._lock:
                previous = self._snapshot
            snapshot = replace(previous, metrics=replace(previous.metrics,
                has_error=True, acquisition_finished=False,
                error="tinySA acquisition failed; Stop/release required" + suffix))
        with self._lock:
            self._snapshot = snapshot

    def _publish_pass(self, result: TinySaTracePass) -> None:
        """One latest immutable response; no accumulation or queue on the producer."""
        if self._sequence >= (1 << 64) - 1 or not result.prompt_confirmed:
            raise RuntimeError("tinySA repeated publication contract exhausted")
        if self._owner is not None and self._owner.cancellation_requested:
            raise TinySaTraceCollectionCancelled("tinySA trace collection was cancelled")
        line = self._line(result.trace.values_dbm, zero=result.trace.scanraw_zero_offset_db,
                          elapsed=result.elapsed_seconds)
        now = self._clock()
        self._completed += 1
        first = self._first_completion
        rate = ((self._completed - 1) / (now - first)
                if first is not None and now > first else 0.0)
        if first is None:
            self._first_completion = now
        with self._lock:
            self._snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(
                completed_lines=self._completed, completed_line_lps=rate))
        self._sequence += 1

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
                # Runs on the shared off-Qt lifecycle worker. Do not strand a
                # slow firmware response by closing its port at cancellation.
                # No RF abort setting/command is implicitly enabled or sent.
                timeout = self._request.timeout_s + 5.0 if self._request is not None else 3.0
                thread.join(timeout=timeout)
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

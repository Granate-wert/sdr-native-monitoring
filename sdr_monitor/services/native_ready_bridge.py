"""Worker-side bounded scalar clock samples and actual native receipt mapping.

One sample per existing owner poll, never per FFT or in cached/UI reads. No
hardware/SDK call, clock-origin guessing, RF timestamp replacement or clipping.
Outside samples (including equality/quantization) stays unknown. Monotonicity
failures latch until an explicit new Start. No long-term drift extrapolation.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
import os
import time
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from ..domain.analytical_ready import (
    DetectorReadyReceipt, ReadyClockBracket, ReadyClockMapping, ReadyHostBounds,
)
from ..domain.identity import ConfigurationGeneration, SessionId, SourceId

if TYPE_CHECKING:
    from ..domain.analytical_journal import OwnerJournalSnapshot


class NativeReadyBridge:
    SAMPLE_CAPACITY = 64

    def __init__(self, native: object, *, host_clock: Callable[[], int] = time.perf_counter_ns) -> None:
        self._native = native
        self._host_clock = host_clock
        self._scope = uuid4().hex
        self._process_id = os.getpid()
        self._samples: deque[ReadyClockBracket] = deque(maxlen=self.SAMPLE_CAPACITY)
        self._failure: ReadyClockMapping | None = None

    @property
    def clock_scope_id(self) -> str:
        return self._scope

    @property
    def host_process_id(self) -> int:
        return self._process_id

    def _supported(self) -> bool:
        version = getattr(self._native, "ANALYTICAL_READY_CONTRACT_VERSION", None)
        return type(version) is int and version == 1 and callable(
            getattr(self._native, "analytical_ready_clock_ns", None))

    def begin(self) -> None:
        """Explicit Start only; resets mapping, not producer IDs/offer timestamps."""
        self._samples.clear()
        self._failure = None
        self.sample()

    def sample(self) -> None:
        if not self._supported() or self._failure is not None:
            return
        try:
            before = self._host_clock()
            native_ns = getattr(self._native, "analytical_ready_clock_ns")()
            after = self._host_clock()
        except Exception:  # noqa: BLE001 - telemetry failure must not stop acquisition.
            self._samples.clear()
            self._failure = ReadyClockMapping.PROBE_FAILED
            return
        try:
            bracket = ReadyClockBracket(native_ns, before, after)
            previous = self._samples[-1] if self._samples else None
            if previous is not None and (
                    bracket.native_ns < previous.native_ns or bracket.host_before_ns < previous.host_after_ns):
                raise ValueError("native/host probe order regressed")
        except ValueError:
            self._samples.clear()
            self._failure = ReadyClockMapping.PROBE_REGRESSED
            return
        self._samples.append(bracket)

    def convert(self, frame: Any, *, source_id: SourceId,
                config_generation: ConfigurationGeneration, receiver_id: str | None,
                acquisition_epoch: int | None, session_id: SessionId | None,
                owner_journal: OwnerJournalSnapshot | None = None) -> DetectorReadyReceipt | None:
        ref = getattr(frame, "analytical_ready", None)
        if ref is None:  # Historical/replay/vendor/mocks: not a fresh receipt.
            return None
        if not self._supported():
            raise ValueError("native ready receipt has no supported clock protocol")
        clocks = getattr(self._native, "AnalyticalReadyClock", None)
        states = getattr(self._native, "AnalyticalReadyClockState", None)
        steady = getattr(clocks, "NativeSteady", None)
        monotonic, regressed = getattr(states, "Monotonic", None), getattr(states, "Regressed", None)
        if (steady is None or getattr(ref, "clock", None) != steady
                or monotonic is None or regressed is None
                or ref.clock_state not in (monotonic, regressed)):
            raise ValueError("native ready receipt clock contract differs")
        # Never upgrade the native mapper's legacy fallback into ready evidence.
        if (getattr(getattr(frame, "source", None), "source_id", None) != source_id
                or type(frame.config_generation) is not int or type(ref.config_generation) is not int
                or frame.config_generation != config_generation
                or ref.config_generation != config_generation):
            raise ValueError("native ready receipt has foreign source/generation")
        owner_run_id = None
        if owner_journal is not None:
            from ..domain.analytical_journal import JournalState
            scope, counters = owner_journal.scope, owner_journal.counters
            if owner_journal.state is JournalState.ACTIVE and scope is not None and counters is not None:
                if (scope.clock_scope_id != self._scope or scope.host_process_id != self._process_id
                        or scope.source_id != source_id or scope.session_id != session_id
                        or scope.configuration_generation != config_generation
                        or scope.acquisition_epoch != acquisition_epoch
                        or counters.producer_instance_id != ref.producer_instance_id
                        or type(ref.offer_sequence) is not int or not 1 <= ref.offer_sequence <= counters.offered):
                    raise ValueError("ready receipt differs from the SAME admitted owner journal")
                owner_run_id = scope.owner_run_id
        mapping, bounds = self.map_native_clock(ref)
        return DetectorReadyReceipt(
            self._scope, self._process_id, ref.producer_instance_id, ref.offer_sequence,
            config_generation, ref.ready_native_ns, source_id, receiver_id,
            acquisition_epoch, session_id, mapping, bounds, owner_run_id,
        )

    def map_native_clock(self, ref: Any) -> tuple[ReadyClockMapping, ReadyHostBounds | None]:
        """Map an original SAME-library clock ref using retained probes only.

        Shared by detector and layer adapters. No sampling, cached-frame
        re-timestamping or unsupported-clock extrapolation occurs here.
        """
        if not self._supported():
            raise ValueError("native ready receipt has no supported clock protocol")
        clock = getattr(getattr(self._native, "AnalyticalReadyClock", None), "NativeSteady", None)
        states = getattr(self._native, "AnalyticalReadyClockState", None)
        monotonic, regressed = getattr(states, "Monotonic", None), getattr(states, "Regressed", None)
        if (clock is None or ref.clock != clock or monotonic is None or regressed is None
                or ref.clock_state not in (monotonic, regressed)
                or type(ref.ready_native_ns) is not int
                or not -(1 << 63) <= ref.ready_native_ns < (1 << 63)):
            raise ValueError("native ready receipt clock contract differs")
        bounds = None
        mapping = self._failure or ReadyClockMapping.OUTSIDE_SAMPLES
        if ref.clock_state == regressed:
            mapping = ReadyClockMapping.PRODUCER_REGRESSED
        elif self._failure is None:
            # Strict inequality intentionally avoids pretending a quantized
            # equality establishes event order. No offset/rate extrapolation.
            lower = next((sample for sample in reversed(self._samples)
                          if sample.native_ns < ref.ready_native_ns), None)
            upper = next((sample for sample in self._samples
                          if sample.native_ns > ref.ready_native_ns), None)
            if lower is not None and upper is not None:
                bounds = ReadyHostBounds(lower, upper)
                mapping = ReadyClockMapping.BOUNDED
        return mapping, bounds

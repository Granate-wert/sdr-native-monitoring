"""Opt-in APP-05 observer: overlap ordinary Visual SHA with mapping.

This is deliberately not product wiring. It keeps the normal single projection
Future, waits for the exact SHA before publication, and never queues a second
helper task while one is active. Rematerialization uses the unchanged path.
"""

from __future__ import annotations

import weakref
from concurrent.futures import CancelledError, ThreadPoolExecutor, wait
from dataclasses import replace
from threading import Event, Lock, current_thread, local, main_thread
from time import perf_counter_ns
from typing import Self
from unittest.mock import patch

from sdr_monitor.ui.v2.spectrum import persistence_projection as density_module
from sdr_monitor.ui.v2.spectrum.cancellation import check_cancelled
from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode


class OverlappedWitnessProbe:
    """One helper, one in-flight witness, no publication before its completion."""

    def __init__(self) -> None:
        self._original_prepare = density_module.prepare_persistence_image
        self._original_witness = density_module.persistence_input_witness
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app05-witness-probe")
        self._active = Lock()
        self._metrics_lock = Lock()
        self._local = local()
        self._patcher = patch.object(density_module, "persistence_input_witness", self._witness_shim)
        self._entered = False
        self._closed = False
        self.calls = 0
        self.sequential = 0
        self.rematerializations = 0
        self.parallel_started = 0
        self.parallel_completed = 0
        self.parallel_cancelled = 0
        self.parallel_failed = 0
        self.actual_overlap_calls = 0
        self.actual_overlap_ms_total = 0.0
        self.hash_ms_total = 0.0
        self.map_ms_total = 0.0
        self.helper_close_ms: float | None = None
        self.active_at_close = False

    def __enter__(self) -> Self:
        if self._entered or self._closed:
            raise RuntimeError("overlap probe cannot be entered twice")
        self._patcher.start()
        self._entered = True
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _witness_shim(self, view, *, cancelled=None):
        if getattr(self._local, "view", None) is view:
            check_cancelled(cancelled)
            return density_module.PersistenceInputWitness(weakref.ref(view.density), b"observer-pending")
        return self._original_witness(view, cancelled=cancelled)

    def prepare(self, request, *, cancelled=None):
        with self._metrics_lock:
            self.calls += 1
        if (request.policy.mode is not PersistenceRenderMode.VISUAL or request.rematerialize
                or current_thread() is main_thread()):
            with self._metrics_lock:
                self.sequential += 1
                self.rematerializations += int(request.rematerialize)
            return self._original_prepare(request, cancelled=cancelled)
        if not self._active.acquire(blocking=False):
            # No second helper submission or queue. A genuinely concurrent
            # caller retains exact stock behavior and is visible in metrics.
            with self._metrics_lock:
                self.sequential += 1
            return self._original_prepare(request, cancelled=cancelled)
        stop = Event()

        def combined_cancelled() -> bool:
            return stop.is_set() or (cancelled is not None and cancelled())

        def hash_work():
            began = perf_counter_ns()
            witness = self._original_witness(request.view, cancelled=combined_cancelled)
            return witness, began, perf_counter_ns()

        future = None
        with self._metrics_lock:
            self.parallel_started += 1
        try:
            future = self._executor.submit(hash_work)
            self._local.view = request.view
            map_began = perf_counter_ns()
            result = self._original_prepare(request, cancelled=combined_cancelled)
            map_ended = perf_counter_ns()
            witness, hash_began, hash_ended = future.result()  # worker only; never a GUI wait
            check_cancelled(combined_cancelled)
            published = replace(result, input_witness=witness)
            overlap_ns = max(0, min(map_ended, hash_ended) - max(map_began, hash_began))
            with self._metrics_lock:
                self.parallel_completed += 1
                self.actual_overlap_calls += int(overlap_ns > 0)
                self.actual_overlap_ms_total += overlap_ns / 1e6
                self.hash_ms_total += (hash_ended - hash_began) / 1e6
                self.map_ms_total += (map_ended - map_began) / 1e6
            return published
        except CancelledError:
            with self._metrics_lock:
                self.parallel_cancelled += 1
            raise
        except Exception:
            with self._metrics_lock:
                self.parallel_failed += 1
            raise
        finally:
            self._local.view = None
            stop.set()
            if future is not None:
                future.cancel()
                wait((future,))  # acknowledge helper without masking a primary error
            self._active.release()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.active_at_close = self._active.locked()
        began = perf_counter_ns()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.helper_close_ms = (perf_counter_ns() - began) / 1e6
        if self._entered:
            self._patcher.stop()
            self._entered = False

    def report(self) -> dict:
        with self._metrics_lock:
            counts = {"calls": self.calls, "sequential_calls": self.sequential,
                      "rematerializations": self.rematerializations,
                      "parallel_started": self.parallel_started,
                      "parallel_completed": self.parallel_completed,
                      "parallel_cancelled": self.parallel_cancelled,
                      "parallel_failed": self.parallel_failed,
                      "actual_overlap_calls": self.actual_overlap_calls,
                      "actual_overlap_ms_total": self.actual_overlap_ms_total,
                      "hash_ms_total": self.hash_ms_total, "map_ms_total": self.map_ms_total}
        return {**counts, "active_at_close": self.active_at_close,
                "helper_close_ms": self.helper_close_ms,
                "scope": "Observer-only one-helper normal Visual SHA/map overlap; exact witness "
                         "is awaited before result publication. Rematerialization is sequential. "
                         "Durations sum completed calls only. No product executor/API or DWM/RF speed claim."}

"""Two-phase product cleanup, polled by Qt without waiting on a Future.

Timeout means still owned, not killed or successfully closed. One lifecycle
worker serializes owners; completed owners are never repeated on explicit retry.
"""
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from math import isfinite
from time import monotonic


@dataclass(frozen=True, slots=True)
class CloseState:
    phase: str = "idle"
    detail: str = ""


class CloseLifecycle:
    def __init__(self, prepare: Callable[[], tuple[tuple[str, Callable[[], None]], ...]],
                 *, timeout_s: float = 5.0) -> None:
        if not isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("close timeout must be positive")
        self._prepare: Callable[[], tuple[tuple[str, Callable[[], None]], ...]] | None = prepare
        self.timeout_s = timeout_s
        self.state = CloseState()
        self._tasks: tuple[tuple[str, Callable[[], None]], ...] | None = None
        self._completed: set[str] = set()
        self._attempted: set[str] = set()
        self._errors: list[str] = []
        self._worker: ThreadPoolExecutor | None = None
        self._future: Future | None = None
        self._active = ""
        self._deadline = 0.0

    def request(self) -> CloseState:
        if self.state.phase not in {"idle", "failed"}:
            return self.poll()
        try:
            if self._tasks is None:
                assert self._prepare is not None
                tasks = self._prepare()  # GUI-only quiesce, no waits.
                names = [name for name, _ in tasks]
                if len(set(names)) != len(names):
                    raise ValueError("duplicate shutdown owner")
                if any(not name or not callable(operation) for name, operation in tasks):
                    raise ValueError("invalid shutdown owner")
                self._tasks = tasks
                # A validated plan is reused for retry. Its factory may be a
                # bound composition method; do not keep that owner alive here.
                self._prepare = None
        except Exception as error:
            self.state = CloseState("failed", str(error)[:2000])
            return self.state
        self._attempted = set(self._completed)
        self._errors = []
        self._deadline = monotonic() + self.timeout_s
        self.state = CloseState("pending")
        self._advance()
        return self.state

    def poll(self) -> CloseState:
        future = self._future
        if future is not None and future.done():
            self._future = None
            try:
                future.result()  # done() checked, no Qt-thread waiting.
            except Exception as error:
                self._errors.append(f"{self._active}: {error}"[:2000])
            else:
                self._completed.add(self._active)
                # Keep only operations that still require acknowledgement.
                # Completed names remain for diagnostics/exactly-once retry,
                # but their bound callbacks must not retain closed owners.
                assert self._tasks is not None
                self._tasks = tuple(task for task in self._tasks if task[0] != self._active)
            self._advance()
        if self._future is not None and monotonic() >= self._deadline:
            self.state = CloseState("timeout", self._active)
        return self.state

    def _advance(self) -> None:
        assert self._tasks is not None
        for name, operation in self._tasks:
            if name in self._attempted:
                continue
            self._attempted.add(name)
            self._active = name
            try:
                if self._worker is None:
                    self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-close")
                self._future = self._worker.submit(operation)
            except Exception as error:
                self._errors.append(f"{name}: {error}"[:2000])
                continue
            self.state = CloseState("pending", name)
            return
        if self._worker is not None:
            self._worker.shutdown(wait=False)  # All submitted tasks have acknowledged.
            self._worker = None
        self.state = (CloseState("failed", "\n".join(self._errors)) if self._errors
                      else CloseState("complete"))

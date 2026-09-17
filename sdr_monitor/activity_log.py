"""Bounded JSONL activity logging for the standalone SDR product.

Standalone product source must stay free of legacy-DFL references, so this
module re-implements the proven bounded activity-log mechanics (non-blocking
writer thread, newest-N retention, atomic snapshot rewrite, flush/close) as a
self-contained stdlib-only module. Structured one-record-per-line JSON makes
errors/defects easy to locate for an agent or a developer.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

DEFAULT_MAX_RECORDS = 10_000
DEFAULT_QUEUE_CAPACITY = 16_384
_FLUSH_TIMEOUT_SECONDS = 10.0

_LOGGER_NAME = "sdr_native_monitoring"
_SESSION_ID = secrets.token_hex(6)
_PROCESS_STARTED_MONOTONIC = time.monotonic()
_SEQUENCE_LOCK = threading.Lock()
_SEQUENCE = 0


def _next_sequence() -> int:
    global _SEQUENCE
    with _SEQUENCE_LOCK:
        _SEQUENCE += 1
        return _SEQUENCE


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def default_activity_log_path() -> Path:
    """Return the default activity log path (env override supported)."""
    override = os.environ.get("SDR_MONITOR_ACTIVITY_LOG")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "SDR Native Monitoring" / "logs" / "activity.jsonl"


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, tuple)):
        return list(value)
    return repr(value)


class JsonEventFormatter(logging.Formatter):
    """Format a log record as one compact JSON line with event semantics."""

    def format(self, record: logging.LogRecord) -> str:
        created = datetime.fromtimestamp(record.created, timezone.utc).astimezone()
        sequence = getattr(record, "event_sequence", None)
        if sequence is None:
            sequence = _next_sequence()
        payload: dict[str, Any] = {
            "sequence": sequence,
            "timestamp": created.isoformat(timespec="milliseconds"),
            "monotonic_ms": round((time.monotonic() - _PROCESS_STARTED_MONOTONIC) * 1000.0, 3),
            "session_id": _SESSION_ID,
            "process_id": os.getpid(),
            "thread": record.threadName,
            "level": record.levelname,
            "logger": record.name,
            "category": getattr(record, "event_category", "program"),
            "event": getattr(record, "event_name", "message"),
            "message": record.getMessage(),
            "details": getattr(record, "event_details", {}),
        }
        if record.levelno >= logging.WARNING:
            payload["source"] = {
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=_json_default, separators=(",", ":"))


class _FlushRequest:
    def __init__(self) -> None:
        self.done = threading.Event()


class _StopRequest:
    pass


class BoundedJsonlHandler(logging.Handler):
    """Persist log records without blocking the caller; retain newest N lines."""

    def __init__(
        self,
        path: Path,
        max_records: int = DEFAULT_MAX_RECORDS,
        snapshot_interval_s: float = 1.0,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
    ) -> None:
        super().__init__()
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self.path = Path(path)
        self.max_records = int(max_records)
        self.queue_capacity = max(1, int(queue_capacity))
        self.snapshot_interval_s = max(0.01, float(snapshot_interval_s))
        self.setFormatter(JsonEventFormatter())
        self._queue: queue.Queue[str | _FlushRequest | _StopRequest] = queue.Queue(maxsize=self.queue_capacity)
        self._closed = False
        self._dropped_pending = 0
        self._dropped_total = 0
        self._overflow_lock = threading.Lock()
        self._records, trimmed = self._read_existing()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if trimmed:
            self._rewrite_snapshot()
        elif not self.path.exists():
            self.path.touch()
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="sdr-activity-log",
            daemon=True,
        )
        self._thread.start()

    def _read_existing(self) -> tuple[deque[str], bool]:
        records: deque[str] = deque(maxlen=self.max_records)
        count = 0
        if self.path.is_file():
            with self.path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    text = line.rstrip("\r\n")
                    if text:
                        records.append(text)
                        count += 1
        return records, count > self.max_records

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(self.format(record))
        except queue.Full:
            # Never block acquisition or rendering on diagnostics. A single
            # synthetic warning is emitted once the writer has capacity.
            with self._overflow_lock:
                self._dropped_pending += 1
                self._dropped_total += 1
        except Exception:
            self.handleError(record)

    @property
    def dropped_records(self) -> int:
        with self._overflow_lock:
            return self._dropped_total

    def _overflow_record(self) -> str | None:
        with self._overflow_lock:
            dropped = self._dropped_pending
            self._dropped_pending = 0
            total = self._dropped_total
        if dropped <= 0:
            return None
        payload = {
            "sequence": _next_sequence(),
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "monotonic_ms": round((time.monotonic() - _PROCESS_STARTED_MONOTONIC) * 1000.0, 3),
            "session_id": _SESSION_ID,
            "process_id": os.getpid(),
            "thread": threading.current_thread().name,
            "level": "WARNING",
            "logger": _LOGGER_NAME,
            "category": "logging",
            "event": "activity_log_queue_overflow",
            "message": "activity_log_queue_overflow",
            "details": {
                "dropped_since_last_notice": dropped,
                "dropped_total": total,
                "queue_capacity": self.queue_capacity,
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _append_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            for line in lines:
                stream.write(line)
                stream.write("\n")
            stream.flush()

    def _rewrite_snapshot(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".part")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                for line in self._records:
                    stream.write(line)
                    stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _writer_loop(self) -> None:
        snapshot_dirty = False
        next_snapshot = 0.0
        stopping = False
        while not stopping:
            timeout = None
            if snapshot_dirty:
                timeout = max(0.0, next_snapshot - time.monotonic())
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                self._rewrite_snapshot()
                snapshot_dirty = False
                continue

            if isinstance(item, _StopRequest):
                stopping = True
            elif isinstance(item, _FlushRequest):
                if snapshot_dirty:
                    self._rewrite_snapshot()
                    snapshot_dirty = False
                item.done.set()
            else:
                overflow = self._overflow_record()
                if overflow is not None:
                    was_full = len(self._records) >= self.max_records
                    self._records.append(overflow)
                    if was_full:
                        if not snapshot_dirty:
                            next_snapshot = time.monotonic() + self.snapshot_interval_s
                        snapshot_dirty = True
                    else:
                        self._append_lines([overflow])
                was_full = len(self._records) >= self.max_records
                self._records.append(item)
                if was_full:
                    if not snapshot_dirty:
                        next_snapshot = time.monotonic() + self.snapshot_interval_s
                    snapshot_dirty = True
                else:
                    self._append_lines([item])

        if snapshot_dirty:
            self._rewrite_snapshot()

    def flush(self) -> None:
        if self._closed or not hasattr(self, "_thread") or not self._thread.is_alive():
            return
        request = _FlushRequest()
        self._queue.put(request, timeout=_FLUSH_TIMEOUT_SECONDS)
        request.done.wait(_FLUSH_TIMEOUT_SECONDS)

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._closed = True
        self._queue.put(_StopRequest(), timeout=_FLUSH_TIMEOUT_SECONDS)
        self._thread.join(_FLUSH_TIMEOUT_SECONDS)
        super().close()


def log_event(
    logger: logging.Logger,
    category: str,
    event: str,
    *,
    level: int = logging.INFO,
    **details: Any,
) -> None:
    logger.log(
        level,
        event,
        extra={
            "event_category": category,
            "event_name": event,
            "event_details": details,
        },
    )


def install_activity_file_logging(
    logger: logging.Logger,
    path: Path | None = None,
    max_records: int | None = None,
    queue_capacity: int | None = None,
) -> BoundedJsonlHandler:
    for handler in logger.handlers:
        if isinstance(handler, BoundedJsonlHandler):
            return handler
    resolved_max = max_records if max_records is not None else _env_int(
        "SDR_MONITOR_LOG_MAX_RECORDS", DEFAULT_MAX_RECORDS
    )
    resolved_queue = queue_capacity if queue_capacity is not None else _env_int(
        "SDR_MONITOR_LOG_QUEUE_CAPACITY", DEFAULT_QUEUE_CAPACITY
    )
    handler = BoundedJsonlHandler(
        path or default_activity_log_path(),
        max_records=resolved_max,
        queue_capacity=resolved_queue,
    )
    logger.addHandler(handler)
    configured_level = os.environ.get("SDR_MONITOR_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, configured_level, logging.INFO))
    return handler


__all__ = [
    "BoundedJsonlHandler",
    "DEFAULT_QUEUE_CAPACITY",
    "DEFAULT_MAX_RECORDS",
    "JsonEventFormatter",
    "default_activity_log_path",
    "install_activity_file_logging",
    "log_event",
]
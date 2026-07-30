from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


DEFAULT_MAX_RECORDS = 10_000
_FLUSH_TIMEOUT_SECONDS = 10.0


def default_activity_log_path() -> Path:
    override = os.environ.get("ESW_DFL_ACTIVITY_LOG")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "R&S DFL parcer" / "logs" / "activity.jsonl"


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, tuple)):
        return list(value)
    return repr(value)


class JsonEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        created = datetime.fromtimestamp(record.created, timezone.utc).astimezone()
        payload: dict[str, Any] = {
            "timestamp": created.isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "category": getattr(record, "event_category", "program"),
            "event": getattr(record, "event_name", "message"),
            "message": record.getMessage(),
            "details": getattr(record, "event_details", {}),
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
    """Persist log records without blocking the caller and retain the newest N lines."""

    def __init__(
        self,
        path: Path,
        max_records: int = DEFAULT_MAX_RECORDS,
        snapshot_interval_s: float = 1.0,
    ) -> None:
        super().__init__()
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self.path = Path(path)
        self.max_records = int(max_records)
        self.snapshot_interval_s = max(0.01, float(snapshot_interval_s))
        self.setFormatter(JsonEventFormatter())
        self._queue: queue.Queue[str | _FlushRequest | _StopRequest] = queue.Queue()
        self._closed = False
        self._records, trimmed = self._read_existing()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if trimmed:
            self._rewrite_snapshot()
        elif not self.path.exists():
            self.path.touch()
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="esw-dfl-activity-log",
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
        except Exception:
            self.handleError(record)

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
        self._queue.put(request)
        request.done.wait(_FLUSH_TIMEOUT_SECONDS)

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._closed = True
        self._queue.put(_StopRequest())
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
    max_records: int = DEFAULT_MAX_RECORDS,
) -> BoundedJsonlHandler:
    for handler in logger.handlers:
        if isinstance(handler, BoundedJsonlHandler):
            return handler
    handler = BoundedJsonlHandler(path or default_activity_log_path(), max_records=max_records)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return handler

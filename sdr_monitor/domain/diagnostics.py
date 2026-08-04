"""Privacy-aware diagnostics contracts for standalone SDR."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DiagnosticStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DiagnosticCard:
    card_id: str
    title: str
    status: DiagnosticStatus
    version: str
    last_test: str
    detail: str
    primary_action: str


@dataclass(frozen=True, slots=True)
class DiagnosticError:
    error_id: str
    summary: str
    reason: str
    recommendation: str
    technical_detail: str
    timestamp: str
    source: str


@dataclass(frozen=True, slots=True)
class SelfTestResult:
    name: str
    status: DiagnosticStatus
    detail: str
    duration_ms: float


@dataclass(frozen=True, slots=True)
class SupportBundleOptions:
    include_platform: bool = True
    include_self_tests: bool = True
    include_errors: bool = True
    include_metrics: bool = True
    include_paths: bool = False
    include_raw_data: bool = False

    def __post_init__(self) -> None:
        if self.include_raw_data:
            raise ValueError("raw IQ/calibration data is never included by default support bundle")


@dataclass(frozen=True, slots=True)
class SupportBundleResult:
    path: str
    files: tuple[str, ...]
    redacted: bool


@dataclass(frozen=True, slots=True)
class DiagnosticsSnapshot:
    platform: dict[str, Any]
    cards: tuple[DiagnosticCard, ...]
    errors: tuple[DiagnosticError, ...]
    metrics: dict[str, Any] = field(default_factory=dict)


class BoundedLog:
    def __init__(self, capacity: int = 256) -> None:
        if capacity <= 0:
            raise ValueError("log capacity must be positive")
        self.capacity = capacity
        self._items: list[dict[str, Any]] = []

    def append(self, item: dict[str, Any]) -> None:
        self._items.append(dict(item))
        del self._items[:-self.capacity]

    def items(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._items)


def redact_path(value: str) -> str:
    if not value:
        return value
    return "<redacted-path>"

__all__ = ["BoundedLog", "DiagnosticCard", "DiagnosticError", "DiagnosticStatus", "DiagnosticsSnapshot", "SelfTestResult", "SupportBundleOptions", "SupportBundleResult", "redact_path"]

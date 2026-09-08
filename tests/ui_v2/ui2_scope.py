"""Deterministic UI V2 diff-scope policy.

The policy intentionally lives beside the UI V2 tests so it can protect each
small presentation-only package before review.  It does not decide whether a
feature is correct; it rejects an accidental backend edit first.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable


ALLOWED_PREFIXES = (
    "sdr_monitor/ui/v2/",
    "sdr_monitor/resources/ui_v2/",
    "tests/ui_v2/",
    "docs/ui_v2/",
)

# These are composition-only seams.  A UI2 package may use one only after its
# corresponding review decision records why the new shell must be registered.
ALLOWED_EXACT = frozenset(
    {
        "sdr_monitor/main.py",
        "sdr_monitor/ui/__init__.py",
        "sdr_monitor/ui/app_shell.py",
        "README.md",
    }
)

PROTECTED_PREFIXES = (
    "native/",
    "sdr_monitor/application/",
    "sdr_monitor/domain/",
    "sdr_monitor/services/",
    "sdr_monitor/shared/",
    "sdr_monitor/ui/presenters/",
)


@dataclass(frozen=True, slots=True)
class ScopeViolation:
    """One changed path that needs explicit owner approval."""

    path: str
    reason: str


def normalize_path(path: str) -> str:
    """Return the repository-relative POSIX spelling used by Git."""
    return str(PurePosixPath(path.replace("\\", "/"))).lstrip("./")


def classify_paths(paths: Iterable[str]) -> tuple[ScopeViolation, ...]:
    """Return all forbidden or out-of-scope paths in deterministic order."""
    violations: list[ScopeViolation] = []
    for raw_path in sorted({normalize_path(path) for path in paths}):
        if raw_path.startswith(PROTECTED_PREFIXES):
            violations.append(ScopeViolation(raw_path, "protected backend path"))
        elif raw_path in ALLOWED_EXACT or raw_path.startswith(ALLOWED_PREFIXES):
            continue
        else:
            violations.append(ScopeViolation(raw_path, "outside UI V2 scope"))
    return tuple(violations)

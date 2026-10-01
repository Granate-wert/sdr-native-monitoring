"""Small immutable GUI-control receipts, never a capture owner or RF clock."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RfControlCompletion:
    token: object
    phase: str
    value: object | None = None
    error: str | None = None


__all__ = ["RfControlCompletion"]

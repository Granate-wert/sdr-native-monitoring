"""Immutable measurement-card snapshots used by the P16 presentation layer."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import MeasurementQuality


@dataclass(frozen=True, slots=True)
class MeasurementCardSnapshot:
    measurement_id: str
    title: str
    value: str
    unit: str
    quality: MeasurementQuality
    uncertainty: str
    frame: str
    timestamp: str
    warnings: tuple[str, ...] = ()
    source: str = ""
    calibration: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MeasurementWorkspaceSnapshot:
    cards: tuple[MeasurementCardSnapshot, ...] = ()
    frame_sequence: int | None = None
    config_generation: int | None = None
    source_id: str | None = None
    error: str | None = None

    @property
    def warning_count(self) -> int:
        return sum(len(card.warnings) for card in self.cards)


__all__ = ["MeasurementCardSnapshot", "MeasurementWorkspaceSnapshot"]

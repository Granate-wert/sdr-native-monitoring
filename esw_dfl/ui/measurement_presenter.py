"""Qt-free formatting and provenance guard for measurement presentation."""

from __future__ import annotations

from collections.abc import Iterable
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
from typing import Any

from ..sdr.measurements import LiveMeasurementResult
from .i18n import LocaleId
from .measurement_state import MeasurementCardSnapshot, MeasurementWorkspaceSnapshot
from .units import format_frequency_hz, format_level


class MeasurementPresenter:
    """Convert service results into cards without changing their semantics."""

    def __init__(self, *, locale: LocaleId = LocaleId.RU) -> None:
        self._locale = locale
        self._cards: dict[str, MeasurementCardSnapshot] = {}
        self._identity: tuple[str, int, int] | None = None
        self._snapshot = MeasurementWorkspaceSnapshot()

    @property
    def snapshot(self) -> MeasurementWorkspaceSnapshot:
        return self._snapshot

    def clear(self) -> MeasurementWorkspaceSnapshot:
        self._cards.clear()
        self._identity = None
        self._snapshot = MeasurementWorkspaceSnapshot()
        return self._snapshot

    def publish(self, result: LiveMeasurementResult[Any], *, measurement_id: str | None = None) -> bool:
        identity = (result.source_id, int(result.frame_sequence), int(result.config_generation))
        if self._identity is not None and identity != self._identity:
            self._snapshot = MeasurementWorkspaceSnapshot(
                cards=tuple(self._cards.values()),
                frame_sequence=self._identity[1],
                config_generation=self._identity[2],
                source_id=self._identity[0],
                error="Результат относится к другой версии кадра; карточка отклонена",
            )
            return False
        self._identity = identity
        card = self.card_from_result(result, measurement_id=measurement_id)
        self._cards[card.measurement_id] = card
        self._snapshot = MeasurementWorkspaceSnapshot(
            cards=tuple(self._cards.values()),
            frame_sequence=identity[1],
            config_generation=identity[2],
            source_id=identity[0],
        )
        return True

    def set_results(self, results: Iterable[LiveMeasurementResult[Any]]) -> bool:
        values = tuple(results)
        if not values:
            self.clear()
            return True
        identity = (values[0].source_id, int(values[0].frame_sequence), int(values[0].config_generation))
        if any((item.source_id, int(item.frame_sequence), int(item.config_generation)) != identity for item in values[1:]):
            self._snapshot = MeasurementWorkspaceSnapshot(error="Нельзя объединить измерения разных кадров")
            return False
        self._cards.clear()
        self._identity = identity
        for item in values:
            card = self.card_from_result(item)
            self._cards[card.measurement_id] = card
        self._snapshot = MeasurementWorkspaceSnapshot(
            cards=tuple(self._cards.values()),
            frame_sequence=identity[1],
            config_generation=identity[2],
            source_id=identity[0],
        )
        return True

    def copy_text(self) -> str:
        lines = ["Measurement\tValue\tUnit\tQuality\tUncertainty\tFrame\tTimestamp\tWarnings\tSource\tCalibration"]
        for card in self._cards.values():
            lines.append("\t".join((
                card.title, card.value, card.unit, card.quality.value, card.uncertainty,
                card.frame, card.timestamp, "; ".join(card.warnings), card.source, card.calibration,
            )))
        return "\n".join(lines)

    def export_csv(self, path: str | os.PathLike[str]) -> Path:
        """Write the visible cards atomically; incomplete exports are removed."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent)
            temporary = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("measurement", "value", "unit", "quality", "uncertainty", "frame", "timestamp", "warnings", "source", "calibration"))
                for card in self._cards.values():
                    writer.writerow((card.title, card.value, card.unit, card.quality.value, card.uncertainty, card.frame, card.timestamp, " | ".join(card.warnings), card.source, card.calibration))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
            return target
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def card_from_result(self, result: LiveMeasurementResult[Any], *, measurement_id: str | None = None) -> MeasurementCardSnapshot:
        value, unit, detail = _render_value(result, self._locale)
        uncertainty = format_level(result.uncertainty_db, "dB", locale=self._locale) if result.uncertainty_db is not None else "—"
        timestamp = datetime.fromtimestamp(result.timestamp_ns / 1.0e9, tz=timezone.utc).isoformat(timespec="milliseconds")
        warnings = tuple(item.message for item in result.warnings)
        return MeasurementCardSnapshot(
            measurement_id=measurement_id or _measurement_key(result),
            title=result.kind,
            value=value,
            unit=unit,
            quality=result.quality,
            uncertainty=uncertainty,
            frame=f"{result.frame_sequence} / config {result.config_generation}",
            timestamp=timestamp,
            warnings=warnings,
            source=result.source_id,
            calibration=result.calibration_status.value,
            detail=detail,
        )


def _measurement_key(result: LiveMeasurementResult[Any]) -> str:
    return f"{result.kind}:{result.frame_sequence}:{result.config_generation}"


def _render_value(result: LiveMeasurementResult[Any], locale: LocaleId) -> tuple[str, str, str]:
    value = result.value
    source_unit = result.unit.value
    if value is None:
        return "—", source_unit, "Измерение недоступно"
    kind = result.kind.casefold()
    if kind == "peak":
        peaks = tuple(value)
        if not peaks:
            return "—", source_unit, "Пиков не найдено"
        peak = peaks[0]
        return (
            f"{format_frequency_hz(float(peak.frequency_hz), locale=locale)} / {format_level(float(peak.level_db), peak.unit.value, locale=locale)}",
            "frequency / level",
            f"Пиков: {len(peaks)}",
        )
    if kind == "channel power":
        integrated = getattr(value, "integrated", None)
        return format_level(getattr(integrated, "power_dbm", None), "dBm", locale=locale), "dBm", "Интегральная мощность"
    if kind == "acpr / aclr":
        adjacent = tuple(getattr(value, "adjacent", ()))
        rendered = ", ".join(format_level(getattr(item, "aclr_db", None), "dB", locale=locale) for item in adjacent)
        return rendered or "—", "dB", f"Каналов: {len(adjacent)}"
    if kind == "occupied bandwidth":
        return format_frequency_hz(getattr(value, "bandwidth_hz", None) or float("nan"), locale=locale), "Hz", "OBW"
    if kind == "noise floor":
        return format_level(getattr(value, "mean_dbm", None), "dBm", locale=locale), "dBm", "Средний уровень шума"
    if kind == "snr":
        return format_level(getattr(value, "snr_db", None), "dB", locale=locale), "dB", "Signal-to-noise ratio"
    return str(value), source_unit, ""


__all__ = ["MeasurementPresenter"]

"""Typed, validated identity, timestamp and loss metadata contracts.

These values cross acquisition, DSP, recording, replay and UI boundaries.
They remain lightweight Python primitives at runtime, but the constructors
make an absent/invalid identity or silently negative counter impossible at a
domain boundary.
"""

from __future__ import annotations

from enum import StrEnum
from numbers import Integral
from typing import Iterable, NewType


SourceId = NewType("SourceId", str)
SessionId = NewType("SessionId", str)
ConfigurationGeneration = NewType("ConfigurationGeneration", int)
FrameSequence = NewType("FrameSequence", int)
TimestampNs = NewType("TimestampNs", int)


class TimestampQuality(StrEnum):
    """Provenance of a frame timestamp; never infer hardware timing."""

    HARDWARE = "hardware"
    ESTIMATED = "estimated"
    REPLAY = "replay"
    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


class LossReason(StrEnum):
    """First-class explanation of an observable discontinuity or drop."""

    SOURCE = "source"
    ACQUISITION_QUEUE = "acquisition_queue"
    DSP = "dsp"
    SNAPSHOT = "snapshot"
    RENDER = "render"
    RECORDER = "recorder"
    SHUTDOWN = "shutdown"
    UNKNOWN = "unknown"


def _non_empty_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{label} must be a non-negative integer")
    normalized = int(value)
    if normalized < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return normalized


def as_source_id(value: object) -> SourceId:
    return SourceId(_non_empty_text(value, "source_id"))


def as_session_id(value: object) -> SessionId:
    return SessionId(_non_empty_text(value, "session_id"))


def as_configuration_generation(value: object) -> ConfigurationGeneration:
    return ConfigurationGeneration(_non_negative_int(value, "configuration generation"))


def as_frame_sequence(value: object) -> FrameSequence:
    return FrameSequence(_non_negative_int(value, "frame sequence"))


def as_timestamp_ns(value: object) -> TimestampNs:
    return TimestampNs(_non_negative_int(value, "timestamp_ns"))


def as_timestamp_quality(value: object) -> TimestampQuality:
    if isinstance(value, TimestampQuality):
        return value
    if not isinstance(value, str):
        raise ValueError(f"unknown timestamp quality: {value!r}")
    try:
        return TimestampQuality(value)
    except ValueError as error:
        raise ValueError(f"unknown timestamp quality: {value!r}") from error


def normalize_loss_reasons(values: Iterable[LossReason | str]) -> tuple[LossReason, ...]:
    """Return a stable, duplicate-free loss-reason tuple for serialization."""

    result: list[LossReason] = []
    for value in values:
        try:
            reason = LossReason(value)
        except ValueError as error:
            raise ValueError(f"unknown loss reason: {value!r}") from error
        if reason not in result:
            result.append(reason)
    return tuple(result)


__all__ = [
    "ConfigurationGeneration",
    "FrameSequence",
    "LossReason",
    "SessionId",
    "SourceId",
    "TimestampNs",
    "TimestampQuality",
    "as_configuration_generation",
    "as_frame_sequence",
    "as_session_id",
    "as_source_id",
    "as_timestamp_ns",
    "as_timestamp_quality",
    "normalize_loss_reasons",
]

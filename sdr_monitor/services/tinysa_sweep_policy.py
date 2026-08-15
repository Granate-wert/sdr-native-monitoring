"""Pure tinySA sweep policy derived from the admitted firmware source.

This module issues no command and imports no serial transport.  It separates
the tinySA screen point limit from the host ``scanraw`` product limit and
records which expert settings have a stable console surface in firmware
``26fc821``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .tinysa_capability_adapter import TinySaModel

TINYSA_FIRMWARE_SOURCE_COMMIT = "26fc821ad3432f929630718cd290314dbc711f48"
TINYSA_BASIC_DISPLAY_POINTS_MAX = 290
TINYSA_ULTRA_DISPLAY_POINTS_MAX = 450
TINYSA_PRODUCT_SCANRAW_POINTS_MAX = 10_001
TINYSA_SCANRAW_RECORD_BYTES = 3
TINYSA_SCANRAW_FRAME_OVERHEAD_BYTES = 2
TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX = 30 * 1_024


class TinySaSweepAccuracy(StrEnum):
    """Acquisition policies exposed by ``sweep <mode>``."""

    UNCHANGED = "unchanged"
    NORMAL = "normal"
    PRECISE = "precise"
    FAST = "fast"
    NOISE_SOURCE = "noise_source"


class TinySaParameterAccess(StrEnum):
    """How safely the exact observed firmware can be controlled by a host."""

    SCANRAW_ARGUMENT = "scanraw_argument"
    SHELL_WRITE_NO_READBACK = "shell_write_no_readback"
    SHELL_READBACK = "shell_readback"
    UI_ONLY_NO_STABLE_SHELL = "ui_only_no_stable_shell"


@dataclass(frozen=True, slots=True)
class TinySaScanRawGeometry:
    start_frequency_hz: int
    requested_stop_frequency_hz: int
    point_count: int
    frequency_step_hz: int
    last_frequency_hz: int
    payload_bytes: int


@dataclass(frozen=True, slots=True)
class TinySaSweepAccuracySemantics:
    mode: TinySaSweepAccuracy
    shell_command: str | None
    relative_minimum_time: float | None
    expected_noise_penalty_db: float | None
    intended_signal: str
    safe_automatic_default: bool
    stable_readback_available: bool = False


TINYSA_26FC821_PARAMETER_ACCESS = {
    "points": TinySaParameterAccess.SCANRAW_ARGUMENT,
    "accuracy_mode": TinySaParameterAccess.SHELL_WRITE_NO_READBACK,
    "zero_offset": TinySaParameterAccess.SHELL_READBACK,
    "rbw": TinySaParameterAccess.SHELL_READBACK,
    "sweep_time": TinySaParameterAccess.SHELL_READBACK,
    "nspeedup": TinySaParameterAccess.UI_ONLY_NO_STABLE_SHELL,
    "wspeedup": TinySaParameterAccess.UI_ONLY_NO_STABLE_SHELL,
    "repeat": TinySaParameterAccess.SHELL_WRITE_NO_READBACK,
    "spur_removal": TinySaParameterAccess.SHELL_WRITE_NO_READBACK,
    "lna": TinySaParameterAccess.SHELL_WRITE_NO_READBACK,
    "attenuation": TinySaParameterAccess.SHELL_READBACK,
}


def display_point_limit(model: TinySaModel) -> int:
    normalized = TinySaModel(model)
    if normalized is TinySaModel.BASIC:
        return TINYSA_BASIC_DISPLAY_POINTS_MAX
    return TINYSA_ULTRA_DISPLAY_POINTS_MAX


def scanraw_geometry(
    start_frequency_hz: int,
    stop_frequency_hz: int,
    point_count: int,
) -> TinySaScanRawGeometry:
    """Return the integer-Hz, stop-exclusive geometry used by firmware."""

    if isinstance(start_frequency_hz, bool) or not isinstance(start_frequency_hz, int):
        raise TypeError("tinySA scanraw start must be integer Hz")
    if isinstance(stop_frequency_hz, bool) or not isinstance(stop_frequency_hz, int):
        raise TypeError("tinySA scanraw stop must be integer Hz")
    if isinstance(point_count, bool) or not isinstance(point_count, int):
        raise TypeError("tinySA scanraw point count must be an integer")
    if not 0 <= start_frequency_hz < stop_frequency_hz:
        raise ValueError("tinySA scanraw frequency range is invalid")
    if not 2 <= point_count <= TINYSA_PRODUCT_SCANRAW_POINTS_MAX:
        raise ValueError("tinySA scanraw point count is outside product policy")
    payload_bytes = (
        TINYSA_SCANRAW_FRAME_OVERHEAD_BYTES
        + TINYSA_SCANRAW_RECORD_BYTES * point_count
    )
    if payload_bytes > TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX:
        raise ValueError("tinySA scanraw frame exceeds the product byte bound")
    step_hz = (stop_frequency_hz - start_frequency_hz) // point_count
    if step_hz <= 0:
        raise ValueError("tinySA scanraw frequency step must be at least one hertz")
    return TinySaScanRawGeometry(
        start_frequency_hz=start_frequency_hz,
        requested_stop_frequency_hz=stop_frequency_hz,
        point_count=point_count,
        frequency_step_hz=step_hz,
        last_frequency_hz=start_frequency_hz + (point_count - 1) * step_hz,
        payload_bytes=payload_bytes,
    )


def sweep_accuracy_semantics(mode: TinySaSweepAccuracy) -> TinySaSweepAccuracySemantics:
    normalized = TinySaSweepAccuracy(mode)
    if normalized is TinySaSweepAccuracy.UNCHANGED:
        return TinySaSweepAccuracySemantics(
            normalized, None, None, None, "preserve current instrument policy", True
        )
    if normalized is TinySaSweepAccuracy.NORMAL:
        return TinySaSweepAccuracySemantics(
            normalized, "sweep normal", 1.0, 0.0, "general spectrum measurement", False
        )
    if normalized is TinySaSweepAccuracy.PRECISE:
        return TinySaSweepAccuracySemantics(
            normalized, "sweep precise", 4.0, 0.0, "lower-noise level measurement", False
        )
    if normalized is TinySaSweepAccuracy.FAST:
        return TinySaSweepAccuracySemantics(
            normalized, "sweep fast", None, 10.0, "latency-prioritized exploratory scan", False
        )
    return TinySaSweepAccuracySemantics(
        normalized,
        "sweep noise",
        None,
        None,
        "broadband noise source only",
        False,
    )


def validate_firmware_speedup(value: int) -> int:
    """Validate the exact firmware UI field, not a host command.

    Commit ``26fc821`` displays ``2..20, 0=disable``.  There is no direct
    NSPEEDUP/WSPEEDUP shell command or stable readback in that firmware.
    """

    if isinstance(value, bool) or not isinstance(value, int) or value not in {0, *range(2, 21)}:
        raise ValueError("tinySA firmware speedup must be 0 or an integer from 2 to 20")
    return value


__all__ = [
    "TINYSA_26FC821_PARAMETER_ACCESS",
    "TINYSA_BASIC_DISPLAY_POINTS_MAX",
    "TINYSA_FIRMWARE_SOURCE_COMMIT",
    "TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX",
    "TINYSA_PRODUCT_SCANRAW_POINTS_MAX",
    "TINYSA_ULTRA_DISPLAY_POINTS_MAX",
    "TinySaParameterAccess",
    "TinySaScanRawGeometry",
    "TinySaSweepAccuracy",
    "TinySaSweepAccuracySemantics",
    "display_point_limit",
    "scanraw_geometry",
    "sweep_accuracy_semantics",
    "validate_firmware_speedup",
]

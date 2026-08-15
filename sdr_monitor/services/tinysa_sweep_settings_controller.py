"""Version-pinned, software-first tinySA runtime settings controller.

The module has no serial import and no concrete hardware port.  It compiles a
small explicit command plan for firmware ``26fc821`` and can exercise that plan
only through an injected fake/test port.  Command acknowledgement is never
promoted to state readback, rollback, persistence or metrological evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .tinysa_sweep_policy import (
    TINYSA_FIRMWARE_SOURCE_COMMIT,
    TinySaSweepAccuracy,
    validate_firmware_speedup,
)

R11W_TINYSA_SETTINGS_CONFIRMATION = "R11W-TINYSA-APPLY-RUNTIME-SWEEP-SETTINGS"
MAX_TINYSA_SETTINGS_COMMANDS = 7
MAX_TINYSA_SETTINGS_RESPONSE_BYTES = 512
_GENERIC_APPLY_FAILURE = "tinySA runtime settings application failed closed"


class TinySaSweepPreset(StrEnum):
    PRESERVE = "preserve"
    NORMAL = "normal"
    PRECISE = "precise"
    FAST = "fast"
    NOISE_SOURCE = "noise_source"


class TinySaRbwMode(StrEnum):
    UNCHANGED = "unchanged"
    AUTO = "auto"
    MANUAL = "manual"


class TinySaSwitchPolicy(StrEnum):
    UNCHANGED = "unchanged"
    OFF = "off"
    ON = "on"


class TinySaSpurPolicy(StrEnum):
    UNCHANGED = "unchanged"
    OFF = "off"
    ON = "on"
    AUTO = "auto"


class TinySaAttenuationMode(StrEnum):
    UNCHANGED = "unchanged"
    AUTO = "auto"
    MANUAL = "manual"


class TinySaSettingsApplyStatus(StrEnum):
    UNCHANGED = "unchanged_no_transport"
    ACKNOWLEDGED_UNVERIFIED = "commands_acknowledged_state_unverified"


class TinySaSettingsUnsupported(ValueError):
    """Requested setting has no admitted stable shell/readback contract."""


class TinySaSettingsApplyError(RuntimeError):
    """Redacted finite execution failure over an injected port."""


class TinySaSettingsCommandPort(Protocol):
    def command(self, command: bytes) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TinySaSweepSettingsPlan:
    """Immutable runtime-only request; every field defaults to unchanged."""

    firmware_source_commit: str = TINYSA_FIRMWARE_SOURCE_COMMIT
    accuracy: TinySaSweepAccuracy = TinySaSweepAccuracy.UNCHANGED
    rbw_mode: TinySaRbwMode = TinySaRbwMode.UNCHANGED
    rbw_hz: int | None = None
    sweep_time_ms: int | None = None
    spur_removal: TinySaSpurPolicy = TinySaSpurPolicy.UNCHANGED
    lna: TinySaSwitchPolicy = TinySaSwitchPolicy.UNCHANGED
    attenuation_mode: TinySaAttenuationMode = TinySaAttenuationMode.UNCHANGED
    attenuation_db: int | None = None
    repeat_count: int | None = None
    nspeedup: int | None = None
    wspeedup: int | None = None

    def __post_init__(self) -> None:
        if self.firmware_source_commit != TINYSA_FIRMWARE_SOURCE_COMMIT:
            raise ValueError("tinySA settings plan is not bound to firmware 26fc821")
        object.__setattr__(self, "accuracy", TinySaSweepAccuracy(self.accuracy))
        object.__setattr__(self, "rbw_mode", TinySaRbwMode(self.rbw_mode))
        object.__setattr__(self, "spur_removal", TinySaSpurPolicy(self.spur_removal))
        object.__setattr__(self, "lna", TinySaSwitchPolicy(self.lna))
        object.__setattr__(
            self, "attenuation_mode", TinySaAttenuationMode(self.attenuation_mode)
        )
        _validate_rbw(self.rbw_mode, self.rbw_hz)
        _validate_optional_integer(self.sweep_time_ms, "sweep time", 3, 60_000)
        _validate_attenuation(self.attenuation_mode, self.attenuation_db)
        _validate_optional_integer(self.repeat_count, "repeat count", 1, 1_000)
        if self.nspeedup is not None:
            validate_firmware_speedup(self.nspeedup)
        if self.wspeedup is not None:
            validate_firmware_speedup(self.wspeedup)

    @property
    def commands(self) -> tuple[str, ...]:
        if self.nspeedup is not None or self.wspeedup is not None:
            raise TinySaSettingsUnsupported(
                "NSPEEDUP/WSPEEDUP have no admitted stable shell command in firmware 26fc821"
            )
        commands: list[str] = []
        if self.attenuation_mode is TinySaAttenuationMode.AUTO:
            commands.append("attenuate auto")
        elif self.attenuation_mode is TinySaAttenuationMode.MANUAL:
            commands.append(f"attenuate {self.attenuation_db}")
        if self.lna is not TinySaSwitchPolicy.UNCHANGED:
            commands.append(f"lna {self.lna.value}")
        if self.spur_removal is not TinySaSpurPolicy.UNCHANGED:
            commands.append(f"spur {self.spur_removal.value}")
        if self.rbw_mode is TinySaRbwMode.AUTO:
            commands.append("rbw auto")
        elif self.rbw_mode is TinySaRbwMode.MANUAL:
            commands.append(f"rbw {_format_tenths(self.rbw_hz, divisor=1_000)}")
        if self.repeat_count is not None:
            commands.append(f"repeat {self.repeat_count}")
        if self.sweep_time_ms is not None:
            commands.append(f"sweeptime {_format_thousandths(self.sweep_time_ms)}")
        if self.accuracy is not TinySaSweepAccuracy.UNCHANGED:
            mode = "noise" if self.accuracy is TinySaSweepAccuracy.NOISE_SOURCE else self.accuracy.value
            commands.append(f"sweep {mode}")
        if len(commands) > MAX_TINYSA_SETTINGS_COMMANDS:
            raise AssertionError("tinySA settings command plan exceeded its fixed bound")
        return tuple(commands)

    @property
    def warnings(self) -> tuple[str, ...]:
        if not self.commands:
            return ()
        warnings = [
            "runtime_state_acknowledged_not_read_back",
            "previous_runtime_state_not_restorable",
            "no_saveconfig_or_persistent_write",
        ]
        if self.accuracy is TinySaSweepAccuracy.FAST:
            warnings.append("fast_mode_may_raise_noise_about_10_db_and_reduce_level_accuracy")
        if self.accuracy is TinySaSweepAccuracy.NOISE_SOURCE:
            warnings.append("noise_source_mode_may_leave_frequency_coverage_gaps")
        if self.lna is TinySaSwitchPolicy.ON:
            warnings.append("lna_reduces_strong_signal_headroom")
        return tuple(warnings)


@dataclass(frozen=True, slots=True)
class TinySaSettingsApplyResult:
    status: TinySaSettingsApplyStatus
    commands: tuple[str, ...]
    warnings: tuple[str, ...]
    command_acknowledgements: int
    port_factory_invoked: bool
    port_closed: bool
    state_verified: bool = False
    previous_state_restored: bool = False
    readback_commands: int = 0
    rollback_commands: int = 0
    persistent_configuration_writes: int = 0
    retries: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", TinySaSettingsApplyStatus(self.status))
        if len(self.commands) > MAX_TINYSA_SETTINGS_COMMANDS:
            raise ValueError("tinySA settings result exceeds the command bound")
        if self.command_acknowledgements != len(self.commands):
            raise ValueError("tinySA settings acknowledgement count is inconsistent")
        if not self.port_closed:
            raise ValueError("tinySA settings result cannot retain an open port")
        if any(
            (
                self.state_verified,
                self.previous_state_restored,
                self.readback_commands,
                self.rollback_commands,
                self.persistent_configuration_writes,
                self.retries,
            )
        ):
            raise ValueError("tinySA settings result cannot imply readback, rollback or persistence")
        if self.status is TinySaSettingsApplyStatus.UNCHANGED:
            if self.commands or self.port_factory_invoked:
                raise ValueError("unchanged tinySA settings must not open a port")
        elif not self.commands or not self.port_factory_invoked:
            raise ValueError("changed tinySA settings require an injected port")


def tinysa_sweep_preset_plan(preset: TinySaSweepPreset) -> TinySaSweepSettingsPlan:
    normalized = TinySaSweepPreset(preset)
    if normalized is TinySaSweepPreset.PRESERVE:
        return TinySaSweepSettingsPlan()
    mapping = {
        TinySaSweepPreset.NORMAL: TinySaSweepAccuracy.NORMAL,
        TinySaSweepPreset.PRECISE: TinySaSweepAccuracy.PRECISE,
        TinySaSweepPreset.FAST: TinySaSweepAccuracy.FAST,
        TinySaSweepPreset.NOISE_SOURCE: TinySaSweepAccuracy.NOISE_SOURCE,
    }
    return TinySaSweepSettingsPlan(accuracy=mapping[normalized])


def apply_tinysa_sweep_settings(
    plan: TinySaSweepSettingsPlan,
    *,
    confirmation: str = "",
    port_factory: Callable[[], TinySaSettingsCommandPort] | None = None,
) -> TinySaSettingsApplyResult:
    """Apply a plan only through an injected port and retain no response text."""

    if not isinstance(plan, TinySaSweepSettingsPlan):
        raise TypeError("tinySA settings application requires a validated plan")
    commands = plan.commands
    if not commands:
        return TinySaSettingsApplyResult(
            status=TinySaSettingsApplyStatus.UNCHANGED,
            commands=(),
            warnings=(),
            command_acknowledgements=0,
            port_factory_invoked=False,
            port_closed=True,
        )
    if confirmation != R11W_TINYSA_SETTINGS_CONFIRMATION:
        raise ValueError("tinySA runtime settings require the exact confirmation phrase")
    if not callable(port_factory):
        raise TypeError("tinySA runtime settings require an injected command port")

    port: TinySaSettingsCommandPort | None = None
    acknowledgements = 0
    failed = False
    close_failed = False
    try:
        port = port_factory()
        if not _is_command_port(port):
            failed = True
        else:
            for command in commands:
                response = port.command((command + "\r").encode("ascii"))
                _validate_acknowledgement(response)
                acknowledgements += 1
    except Exception:  # noqa: BLE001 - device-side command failures must fail closed.
        failed = True
    finally:
        if port is not None:
            try:
                port.close()
            except Exception:  # noqa: BLE001 - an unclosed command port invalidates the action.
                close_failed = True
    if failed or close_failed or port is None or acknowledgements != len(commands):
        raise TinySaSettingsApplyError(_GENERIC_APPLY_FAILURE)
    return TinySaSettingsApplyResult(
        status=TinySaSettingsApplyStatus.ACKNOWLEDGED_UNVERIFIED,
        commands=commands,
        warnings=plan.warnings,
        command_acknowledgements=acknowledgements,
        port_factory_invoked=True,
        port_closed=True,
    )


def _validate_acknowledgement(response: object) -> None:
    if not isinstance(response, bytes) or not response or len(response) > MAX_TINYSA_SETTINGS_RESPONSE_BYTES:
        raise TinySaSettingsApplyError(_GENERIC_APPLY_FAILURE)
    if response.count(b"ch> ") != 1:
        raise TinySaSettingsApplyError(_GENERIC_APPLY_FAILURE)
    prompt_end = response.find(b"ch> ") + len(b"ch> ")
    if any(value not in b"\t\r\n " for value in response[prompt_end:]):
        raise TinySaSettingsApplyError(_GENERIC_APPLY_FAILURE)
    try:
        response[:prompt_end].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise TinySaSettingsApplyError(_GENERIC_APPLY_FAILURE) from error


def _validate_rbw(mode: TinySaRbwMode, value: object) -> None:
    if mode is not TinySaRbwMode.MANUAL:
        if value is not None:
            raise ValueError("tinySA unchanged/auto RBW must not carry a manual value")
        return
    rbw_hz = _required_integer(value, "RBW")
    if not 200 <= rbw_hz <= 850_000 or rbw_hz % 100 != 0:
        raise ValueError("tinySA Ultra manual RBW must be 200..850000 Hz in 100-Hz steps")


def _validate_attenuation(mode: TinySaAttenuationMode, value: object) -> None:
    if mode is not TinySaAttenuationMode.MANUAL:
        if value is not None:
            raise ValueError("tinySA unchanged/auto attenuation must not carry a manual value")
        return
    _validate_optional_integer(value, "attenuation", 0, 31, required=True)


def _required_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"tinySA {label} must be an integer")
    return value


def _validate_optional_integer(
    value: object,
    label: str,
    minimum: int,
    maximum: int,
    *,
    required: bool = False,
) -> None:
    if value is None and not required:
        return
    normalized = _required_integer(value, label)
    if not minimum <= normalized <= maximum:
        raise ValueError(f"tinySA {label} is outside the fixed bound")


def _format_tenths(value: int | None, *, divisor: int) -> str:
    normalized = _required_integer(value, "RBW")
    whole, remainder = divmod(normalized, divisor)
    return str(whole) if remainder == 0 else f"{whole}.{remainder // (divisor // 10)}"


def _format_thousandths(value: int | None) -> str:
    normalized = _required_integer(value, "sweep time")
    whole, remainder = divmod(normalized, 1_000)
    if remainder == 0:
        return str(whole)
    return f"{whole}.{remainder:03d}".rstrip("0")


def _is_command_port(value: object) -> bool:
    return hasattr(value, "command") and hasattr(value, "close")


__all__ = [
    "MAX_TINYSA_SETTINGS_COMMANDS",
    "MAX_TINYSA_SETTINGS_RESPONSE_BYTES",
    "R11W_TINYSA_SETTINGS_CONFIRMATION",
    "TinySaAttenuationMode",
    "TinySaRbwMode",
    "TinySaSettingsApplyError",
    "TinySaSettingsApplyResult",
    "TinySaSettingsApplyStatus",
    "TinySaSettingsCommandPort",
    "TinySaSettingsUnsupported",
    "TinySaSpurPolicy",
    "TinySaSweepPreset",
    "TinySaSweepSettingsPlan",
    "TinySaSwitchPolicy",
    "apply_tinysa_sweep_settings",
    "tinysa_sweep_preset_plan",
]

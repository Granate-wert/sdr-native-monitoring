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

from ..domain.tinysa_settings import (
    MAX_TINYSA_SETTINGS_COMMANDS,
    TinySaAttenuationMode,
    TinySaRbwMode,
    TinySaSettingsUnsupported,
    TinySaSpurPolicy,
    TinySaSweepAccuracy,
    TinySaSweepPreset,
    TinySaSweepSettingsPlan,
    TinySaSwitchPolicy,
)

R11W_TINYSA_SETTINGS_CONFIRMATION = "R11W-TINYSA-APPLY-RUNTIME-SWEEP-SETTINGS"
MAX_TINYSA_SETTINGS_RESPONSE_BYTES = 512
_GENERIC_APPLY_FAILURE = "tinySA runtime settings application failed closed"


class TinySaSettingsApplyStatus(StrEnum):
    UNCHANGED = "unchanged_no_transport"
    ACKNOWLEDGED_UNVERIFIED = "commands_acknowledged_state_unverified"


class TinySaSettingsApplyError(RuntimeError):
    """Redacted finite execution failure over an injected port."""


class TinySaSettingsCommandPort(Protocol):
    def command(self, command: bytes) -> bytes: ...

    def close(self) -> None: ...


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

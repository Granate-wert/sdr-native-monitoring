"""Pure tinySA runtime-setting intent; no serial, Qt or service imports."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

TINYSA_FIRMWARE_SOURCE_COMMIT = "26fc821ad3432f929630718cd290314dbc711f48"
MAX_TINYSA_SETTINGS_COMMANDS = 7
TINYSA_RUNTIME_CONTROL_CONTRACT = "tinysa-shell-26fc821-v1"
_REVISION_SUFFIX = re.compile(r"(?:^|[-_\s])g?26fc821(?:ad3432f929630718cd290314dbc711f48)?$", re.ASCII)
# cmd_version/main.c at the SAME admitted source commit emits this second
# line for TINYSA4. The product version probe joins lines with " | ". Keep
# all original text in firmware identity; only contract recognition splits it.
_ULTRA_HW_LINE = re.compile(
    r"HW Version:(?:V0\.4\.5\.1(?:\.1)?|V0\.4\.6|V0\.5\.4|Unknown)(?: max2871)?\Z", re.ASCII)


def tinysa_control_contract(firmware_version: str) -> str | None:
    """Map an observed version suffix to a SOURCE contract, not firmware attestation."""
    version, separator, hardware = firmware_version.partition(" | ")
    if separator and (not version.startswith("tinySA4") or not _ULTRA_HW_LINE.fullmatch(hardware)):
        return None
    return TINYSA_RUNTIME_CONTROL_CONTRACT if _REVISION_SUFFIX.search(version) else None


class TinySaInputMode(StrEnum):
    PRESERVE = "preserve"
    LOW = "low"
    HIGH = "high"

class TinySaSweepAccuracy(StrEnum):
    """Acquisition policies exposed by ``sweep <mode>``."""

    UNCHANGED = "unchanged"
    NORMAL = "normal"
    PRECISE = "precise"
    FAST = "fast"
    NOISE_SOURCE = "noise_source"


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


class TinySaSettingsUnsupported(ValueError):
    """Requested setting has no admitted stable shell/readback contract."""


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


def validate_firmware_speedup(value: int) -> int:
    """Validate the exact firmware UI field, not a host command.

    Commit ``26fc821`` displays ``2..20, 0=disable``.  There is no direct
    NSPEEDUP/WSPEEDUP shell command or stable readback in that firmware.
    """

    if isinstance(value, bool) or not isinstance(value, int) or value not in {0, *range(2, 21)}:
        raise ValueError("tinySA firmware speedup must be 0 or an integer from 2 to 20")
    return value


def compile_tinysa_runtime_settings(
    plan: TinySaSweepSettingsPlan, input_mode: TinySaInputMode, *, model_id: str,
    control_contract: str | None, start_hz: int, stop_hz: int, readback: bool = False,
) -> tuple[str, ...]:
    """Pure preflight over the EXISTING snapshot and exact immutable setting intent.

    Preserve is explicit compatibility, never observed input/settings evidence.
    No output/ultra/config/abort command is inferred from a frequency or label.
    """
    if not isinstance(plan, TinySaSweepSettingsPlan) or not isinstance(input_mode, TinySaInputMode):
        raise TypeError("tinySA runtime settings require typed intent")
    if type(readback) is not bool or model_id not in {"tinysa_basic", "tinysa_ultra"}:
        raise ValueError("tinySA model/readback policy is invalid")
    commands = (() if input_mode is TinySaInputMode.PRESERVE else (f"mode {input_mode.value} input",)) + plan.commands
    if (commands or readback) and control_contract != TINYSA_RUNTIME_CONTROL_CONTRACT:
        raise TinySaSettingsUnsupported("tinySA firmware has no admitted runtime-settings contract")
    if model_id == "tinysa_basic":
        if input_mode is TinySaInputMode.LOW and not 100_000 <= start_hz < stop_hz <= 350_000_000:
            raise ValueError("tinySA Basic low input range is 0.1..350 MHz")
        if input_mode is TinySaInputMode.HIGH and not 240_000_000 <= start_hz < stop_hz <= 960_000_000:
            raise ValueError("tinySA Basic high input range is 240..960 MHz")
        if plan.lna is not TinySaSwitchPolicy.UNCHANGED or plan.spur_removal is TinySaSpurPolicy.AUTO:
            raise TinySaSettingsUnsupported("tinySA Basic has no admitted extra LNA or automatic spur command")
        if plan.rbw_mode is TinySaRbwMode.MANUAL and not 2_000 <= (plan.rbw_hz or 0) <= 600_000:
            raise ValueError("tinySA Basic target RBW must be 2000..600000 Hz")
        # Basic HIGH uses a frequency-dependent switched attenuator, not 0..31 dB.
        # A preserved unknown input cannot safely expose LOW-only attenuation.
        if plan.attenuation_mode is not TinySaAttenuationMode.UNCHANGED and input_mode is not TinySaInputMode.LOW:
            raise TinySaSettingsUnsupported("tinySA Basic 0..31 dB attenuation requires explicit low input")
    elif input_mode is TinySaInputMode.HIGH:
        raise TinySaSettingsUnsupported("tinySA Ultra does not have Basic high-input mode")
    if plan.lna is TinySaSwitchPolicy.ON and plan.attenuation_mode is not TinySaAttenuationMode.UNCHANGED:
        raise ValueError("tinySA LNA and explicit attenuation are mutually exclusive requests")
    return commands


@dataclass(frozen=True, slots=True)
class TinySaSettingsObservation:
    """Post-pass host queries; ACK is NOT whole-profile or RF-state verification.

    RBW can be quantized/dynamic. Sweeptime is the instrument screen-sweep
    readout, not measured scanraw duration. Input/LNA/spur/accuracy/repeat have
    no stable readback in this contract, even with every prompt acknowledged.
    """
    plan: TinySaSweepSettingsPlan
    input_mode: TinySaInputMode
    acknowledged_commands: tuple[str, ...]
    actual_rbw_hz: float | None = None
    actual_attenuation_db: float | None = None
    screen_sweep_time_s: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, TinySaSweepSettingsPlan) or not isinstance(self.input_mode, TinySaInputMode):
            raise TypeError("tinySA setting observations require exact typed intent")
        expected = (() if self.input_mode is TinySaInputMode.PRESERVE else
                    (f"mode {self.input_mode.value} input",)) + self.plan.commands
        if type(self.acknowledged_commands) is not tuple or self.acknowledged_commands != expected:
            raise ValueError("tinySA ACK accounting must match the exact finite request")
        for value, bound in ((self.actual_rbw_hz, 2_000_000),
                             (self.actual_attenuation_db, 40), (self.screen_sweep_time_s, 120)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or not 0 <= value <= bound):
                raise ValueError("tinySA readback is outside the finite physical bound")
        if self.actual_rbw_hz is not None and self.actual_rbw_hz <= 0:
            raise ValueError("tinySA actual RBW must be positive")

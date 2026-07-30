"""Controlled import boundary for the optional ``esw_dfl._sdr_native`` module."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any


NATIVE_MODULE_NAME = "esw_dfl._sdr_native"
_REQUIRED_BUILD_INFO_FIELDS = frozenset(
    {
        "version",
        "compiler",
        "platform",
        "architecture",
        "build_type",
        "cuda_compiled",
        "pluto_compiled",
    }
)
NativeImporter = Callable[[str], ModuleType]


@dataclass(frozen=True, slots=True)
class NativeAvailability:
    """Result of probing the optional native extension."""

    available: bool
    reason: str | None
    build_info: dict[str, object]


class NativeModuleUnavailableError(RuntimeError):
    """Raised only when a caller explicitly requires the missing native module."""


def _reason_from_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def probe_native(
    module_name: str = NATIVE_MODULE_NAME,
    *,
    importer: NativeImporter = importlib.import_module,
) -> tuple[NativeAvailability, ModuleType | None]:
    """Probe a native module without making package import depend on its presence."""

    try:
        module = importer(module_name)
    except (ImportError, OSError) as exc:
        return NativeAvailability(False, _reason_from_exception(exc), {}), None

    try:
        raw_info: Any = module.build_info()
        info = dict(raw_info)
    except Exception as exc:  # native ABI/schema errors must become controlled status
        return NativeAvailability(False, f"build_info failed: {_reason_from_exception(exc)}", {}), None

    missing = sorted(_REQUIRED_BUILD_INFO_FIELDS.difference(info))
    if missing:
        reason = f"build_info is missing required fields: {', '.join(missing)}"
        return NativeAvailability(False, reason, info), None
    return NativeAvailability(True, None, info), module


_AVAILABILITY, _NATIVE_MODULE = probe_native()


def native_availability() -> NativeAvailability:
    """Return an immutable status with a defensive copy of build metadata."""

    return NativeAvailability(
        available=_AVAILABILITY.available,
        reason=_AVAILABILITY.reason,
        build_info=dict(_AVAILABILITY.build_info),
    )


def require_native() -> ModuleType:
    """Return the native module or raise a deliberate adapter-level exception."""

    if _NATIVE_MODULE is None:
        reason = _AVAILABILITY.reason or "native module is unavailable"
        raise NativeModuleUnavailableError(reason)
    return _NATIVE_MODULE


def build_info() -> dict[str, object]:
    """Return native build metadata, requiring an available extension."""

    return dict(require_native().build_info())


def available_backends() -> tuple[str, ...]:
    """Return compiled runtime backends as an immutable tuple."""

    return tuple(str(item) for item in require_native().available_backends())


def run_self_test() -> dict[str, object]:
    """Run the small native bootstrap self-test."""

    return dict(require_native().run_self_test())

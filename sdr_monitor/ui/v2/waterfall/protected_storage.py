"""Admission for the optional, already-loaded native Waterfall storage ABI."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.machinery import ExtensionFileLoader
import os
import sys
from types import BuiltinFunctionType, ModuleType


_MODULE_NAME = "sdr_monitor._sdr_native"
_STORAGE_PROTOCOL = "WATERFALL_PROTECTED_STORAGE_PROTOCOL_VERSION"
_ACCOUNTING_PROTOCOL = "WATERFALL_PROTECTED_ACCOUNTING_PROTOCOL_VERSION"
_FACTORY_ISSUER = object()


@dataclass(frozen=True, slots=True)
class WaterfallStorageSupport:
    """Exact classes from a verified loaded extension; owns no storage payload."""

    module: ModuleType
    controller_type: type
    float_exporter_type: type
    revision_exporter_type: type
    _issued_token: object = field(default=None, init=False, repr=False, compare=False)


def is_issued_waterfall_storage_support(value: object) -> bool:
    """O(1) provenance check; public construction is deliberately unissued."""
    return (type(value) is WaterfallStorageSupport
            and object.__getattribute__(value, "_issued_token") is _FACTORY_ISSUER)


def _same_origin(module: ModuleType) -> bool:
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None)
    filename = getattr(module, "__file__", None)
    if not isinstance(origin, str) or not isinstance(filename, str):
        return False
    try:
        return os.path.normcase(os.path.realpath(origin)) == os.path.normcase(os.path.realpath(filename))
    except (OSError, ValueError):
        return False


def admit_loaded_waterfall_storage(module: object) -> WaterfallStorageSupport | None:
    """Admit only a coherent, genuine extension exposing the exact ABI types."""
    if (type(module) is not ModuleType or module.__name__ != _MODULE_NAME
            or sys.modules.get(_MODULE_NAME) is not module):
        return None
    spec = getattr(module, "__spec__", None)
    if spec is None:
        return None
    loader = getattr(spec, "loader", None)
    if (type(loader) is not ExtensionFileLoader or loader.name != _MODULE_NAME
            or not _same_origin(module)):
        return None
    if (type(getattr(module, _STORAGE_PROTOCOL, None)) is not int
            or getattr(module, _STORAGE_PROTOCOL) != 1
            or type(getattr(module, _ACCOUNTING_PROTOCOL, None)) is not int
            or getattr(module, _ACCOUNTING_PROTOCOL) != 1):
        return None
    probe = getattr(module, "waterfall_storage_types", None)
    if type(probe) is not BuiltinFunctionType:
        return None
    controller = getattr(module, "_WaterfallProtectedStorage", None)
    float_exporter = getattr(module, "_WaterfallFloatBuffer", None)
    revision_exporter = getattr(module, "_WaterfallRevisionBuffer", None)
    try:
        probed = probe()
    except Exception:  # A malformed/old runtime is an optional-path refusal.
        return None
    if (type(probed) is not tuple or len(probed) != 3
            or probed[0] is not controller or probed[1] is not float_exporter
            or probed[2] is not revision_exporter
            or not all(isinstance(value, type) for value in probed)):
        return None
    support = WaterfallStorageSupport(module, controller, float_exporter, revision_exporter)
    object.__setattr__(support, "_issued_token", _FACTORY_ISSUER)
    return support


def loaded_waterfall_storage_support() -> WaterfallStorageSupport | None:
    """Inspect the canonical loaded slot only; never import native code here."""
    return admit_loaded_waterfall_storage(sys.modules.get(_MODULE_NAME))

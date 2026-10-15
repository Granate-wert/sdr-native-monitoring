"""Optional admission for genuine native-owned persistence buffers."""

from __future__ import annotations

from importlib.machinery import EXTENSION_SUFFIXES, ExtensionFileLoader
import sys
from types import BuiltinFunctionType, ModuleType
from typing import Any

import numpy as np

from ..domain.persistence_storage import PersistenceStorageReceipt, _issue_persistence_storage_receipt


def _native_extension_module(value: object) -> bool:
    if type(value) is not ModuleType:
        return False
    spec = getattr(value, "__spec__", None)
    origin = getattr(spec, "origin", None)
    loader = getattr(spec, "loader", None)
    return (sys.modules.get("sdr_monitor._sdr_native") is value
            and type(loader) is ExtensionFileLoader
            and type(origin) is str and any(origin.endswith(suffix) for suffix in EXTENSION_SUFFIXES)
            and getattr(value, "__name__", None) == "sdr_monitor._sdr_native")


def native_persistence_arrays(
    native: object, snapshot: Any
) -> tuple[np.ndarray, np.ndarray, PersistenceStorageReceipt | None]:
    """Return density, axes, optional exact ownership receipt.

    Legacy field access deliberately remains frequencies first, then density.
    All optional ownership inspection is gated by the exact extension and its
    builtin probe before a snapshot ownership field is touched.
    """
    if _native_extension_module(native):
        try:
            protocol = getattr(native, "PERSISTENCE_IMMUTABLE_STORAGE_PROTOCOL_VERSION", None)
            probe = getattr(native, "_persistence_storage_builtin_type_probe", None)
            snapshot_type = getattr(native, "PersistenceSnapshot", None)
            storage_type = getattr(native, "PersistenceStorage", None)
            if (type(protocol) is int and protocol == 1 and type(probe) is BuiltinFunctionType
                    and isinstance(snapshot_type, type) and isinstance(storage_type, type)
                    and type(snapshot) is snapshot_type and probe(snapshot) is True):
                storage = snapshot.immutable_storage
                if type(storage) is storage_type:
                    axes = storage.frequencies()
                    density = storage.density()
                    if storage.matches(density, axes) is True:
                        receipt = _issue_persistence_storage_receipt(storage, density, axes)
                        return density, axes, receipt
        except Exception:  # noqa: BLE001 - optional protocol never breaks legacy valid data.
            pass
    axes = snapshot.frequencies_hz
    density = snapshot.density
    return density, axes, None

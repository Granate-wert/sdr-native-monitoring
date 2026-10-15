"""Internal ownership receipt for native immutable persistence storage.

This is deliberately not numerical validation or RF admission.  Only the
native service adapter may issue a receipt after its exact extension protocol
and array-owner checks have succeeded.
"""

from __future__ import annotations

from typing import Any

_ISSUER = object()


class PersistenceStorageReceipt:
    __slots__ = ("_storage",)

    def __init__(self, issuer: object, storage: Any) -> None:
        if issuer is not _ISSUER:
            raise TypeError("persistence storage receipts are issued by the native adapter")
        object.__setattr__(self, "_storage", storage)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("persistence storage receipts are immutable")

    def matches(self, density: object, frequencies_hz: object) -> bool:
        try:
            return self._storage.matches(density, frequencies_hz) is True
        except Exception:  # noqa: BLE001 - optional ownership is fail-closed.
            return False


def _issue_persistence_storage_receipt(storage: Any, density: object,
                                       frequencies_hz: object) -> PersistenceStorageReceipt:
    receipt = PersistenceStorageReceipt(_ISSUER, storage)
    if not receipt.matches(density, frequencies_hz):
        raise ValueError("native persistence arrays do not match their immutable storage")
    return receipt


def is_persistence_storage_receipt(value: object) -> bool:
    return type(value) is PersistenceStorageReceipt

"""Cooperative presentation cancellation; never a hardware cancel command."""
from collections.abc import Callable
from concurrent.futures import CancelledError

CancelCheck = Callable[[], bool] | None


def check_cancelled(cancelled: CancelCheck) -> None:
    if cancelled is not None and cancelled():
        raise CancelledError("obsolete spectrum presentation")

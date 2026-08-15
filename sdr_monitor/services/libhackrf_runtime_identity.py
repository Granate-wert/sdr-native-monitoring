"""Official-lib HackRF enumeration-only identity port for R11-O.

Loading this optional port has no side effect.  ``probe`` binds exactly the
official library/list symbols needed to identify one HackRF One and never
opens a device, changes configuration, starts RX, or starts TX.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

from .hackrf_capability_adapter import HackrfBoardKind
from .hackrf_activation_preflight import HackrfRuntimeIdentityProbe


_HACKRF_SUCCESS = 0
_USB_BOARD_ID_HACKRF_ONE = 0x6089


class _DeviceList(ctypes.Structure):
    _fields_ = [
        ("serial_numbers", ctypes.POINTER(ctypes.c_char_p)),
        ("usb_board_ids", ctypes.POINTER(ctypes.c_int)),
        ("usb_device_index", ctypes.POINTER(ctypes.c_int)),
        ("devicecount", ctypes.c_int),
        ("usb_devices", ctypes.POINTER(ctypes.c_void_p)),
        ("usb_devicecount", ctypes.c_int),
    ]


class LibhackrfRuntimeIdentityPort:
    """One official enumeration owner with an intentionally tiny symbol allowlist."""

    def __init__(self, dll_path: Path, dependency_directory: Path) -> None:
        self._dll_path = Path(dll_path)
        self._dependency_directory = Path(dependency_directory)
        self._dll: Any | None = None
        self._list: Any | None = None
        self._initialized = False
        self._closed = False
        self._dependency_handle: Any | None = None

    def probe(self) -> HackrfRuntimeIdentityProbe:
        if self._closed:
            raise RuntimeError("HackRF identity context is closed")
        if self._dll is None:
            self._open_enumeration_context()
        assert self._dll is not None
        assert self._list is not None
        listed = self._list.contents
        if listed.devicecount != 1 or not listed.serial_numbers or not listed.usb_board_ids:
            raise RuntimeError("finite HackRF identity observation is unavailable")
        if listed.usb_board_ids[0] != _USB_BOARD_ID_HACKRF_ONE:
            raise RuntimeError("listed device is not HackRF One")
        raw_serial = listed.serial_numbers[0]
        if not raw_serial:
            raise RuntimeError("HackRF enumeration has no stable serial")
        return HackrfRuntimeIdentityProbe(
            board_kind=HackrfBoardKind.HACKRF_ONE,
            serial_words=_serial_words(raw_serial),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure = False
        dll = self._dll
        if dll is not None and self._list is not None:
            try:
                dll.hackrf_device_list_free(self._list)
            except Exception:
                failure = True
            self._list = None
        if dll is not None and self._initialized:
            try:
                failure = dll.hackrf_exit() != _HACKRF_SUCCESS
            except Exception:
                failure = True
            self._initialized = False
        self._dll = None
        if self._dependency_handle is not None:
            try:
                self._dependency_handle.close()
            except Exception:
                failure = True
            self._dependency_handle = None
        if failure:
            raise RuntimeError("HackRF identity context failed to close")

    def _open_enumeration_context(self) -> None:
        if not self._dll_path.is_file() or not self._dependency_directory.is_dir():
            raise RuntimeError("portable libhackrf runtime is unavailable")
        try:
            self._dependency_handle = os_add_dll_directory(self._dependency_directory)
            self._dll = ctypes.CDLL(str(self._dll_path))
            self._bind_allowlist()
            self._check(self._dll.hackrf_init())
            self._initialized = True
            self._list = self._dll.hackrf_device_list()
            if not self._list:
                raise RuntimeError("HackRF enumeration failed")
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise RuntimeError("portable HackRF identity context failed closed") from None

    def _bind_allowlist(self) -> None:
        assert self._dll is not None
        dll = self._dll
        dll.hackrf_init.argtypes = []
        dll.hackrf_init.restype = ctypes.c_int
        dll.hackrf_exit.argtypes = []
        dll.hackrf_exit.restype = ctypes.c_int
        dll.hackrf_device_list.argtypes = []
        dll.hackrf_device_list.restype = ctypes.POINTER(_DeviceList)
        dll.hackrf_device_list_free.argtypes = [ctypes.POINTER(_DeviceList)]
        dll.hackrf_device_list_free.restype = None

    @staticmethod
    def _check(result: int) -> None:
        if result != _HACKRF_SUCCESS:
            raise RuntimeError("libhackrf identity call failed")


def _serial_words(raw_serial: bytes) -> tuple[int, int, int, int]:
    try:
        serial = raw_serial.decode("ascii", errors="strict").strip().casefold()
    except UnicodeDecodeError:
        raise RuntimeError("HackRF enumeration serial is invalid") from None
    if len(serial) != 32 or any(character not in "0123456789abcdef" for character in serial):
        raise RuntimeError("HackRF enumeration serial is not canonical")
    return tuple(int(serial[index : index + 8], 16) for index in range(0, 32, 8))  # type: ignore[return-value]


def os_add_dll_directory(path: Path) -> Any:
    """Isolated wrapper retained for fake-free unit checks on Windows."""

    import os

    if not hasattr(os, "add_dll_directory"):
        raise RuntimeError("portable DLL directories are unsupported")
    return os.add_dll_directory(str(path))


__all__ = ["LibhackrfRuntimeIdentityPort"]

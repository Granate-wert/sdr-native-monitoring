"""Minimal ctypes owner for the R11-E official-lib read-only allowlist."""

from __future__ import annotations

import ctypes
import threading
from pathlib import Path
from typing import Any

from .hackrf_capability_adapter import HackrfBoardKind, HackrfReadOnlyProbe


_HACKRF_SUCCESS = 0
_USB_BOARD_ID_HACKRF_ONE = 0x6089
_BOARD_ID_HACKRF_ONE_VALUES = frozenset((2, 4))


class _PartIdSerial(ctypes.Structure):
    _fields_ = [
        ("part_id", ctypes.c_uint32 * 2),
        ("serial_no", ctypes.c_uint32 * 4),
    ]


class _DeviceList(ctypes.Structure):
    _fields_ = [
        ("serial_numbers", ctypes.POINTER(ctypes.c_char_p)),
        ("usb_board_ids", ctypes.POINTER(ctypes.c_int)),
        ("usb_device_index", ctypes.POINTER(ctypes.c_int)),
        ("devicecount", ctypes.c_int),
        ("usb_devices", ctypes.POINTER(ctypes.c_void_p)),
        ("usb_devicecount", ctypes.c_int),
    ]


class LibhackrfReadOnlyPort:
    """Own library/list/device lifetime; no stream or configuration symbol is bound."""

    def __init__(self, dll_path: Path, dependency_directory: Path) -> None:
        # Return the owner before SDK acquisition so even partial-open failures
        # remain reachable by the retained read-only provider.
        self._dll_path = Path(dll_path)
        self._dependency_directory = Path(dependency_directory)
        self._dll: Any | None = None
        self._list: Any | None = None
        self._device = ctypes.c_void_p()
        self._initialized = False
        self._closed = False
        self._release_started = False
        self._probe_failed = False
        self._device_close_ambiguous = False
        self._lock = threading.RLock()
        self._dependency_handle: Any | None = None

    def _open_read_only_context(self) -> None:
        if not self._dll_path.is_file() or not self._dependency_directory.is_dir():
            raise RuntimeError("portable libhackrf runtime is unavailable")
        self._dependency_handle = os_add_dll_directory(self._dependency_directory)
        self._dll = ctypes.CDLL(str(self._dll_path))
        self._bind_allowlist()
        self._check(self._dll.hackrf_init())
        self._initialized = True
        self._list = self._dll.hackrf_device_list()
        if not self._list or self._list.contents.devicecount != 1 or not self._list.contents.usb_board_ids:
            raise RuntimeError("finite HackRF observation requires exactly one device")
        if self._list.contents.usb_board_ids[0] != _USB_BOARD_ID_HACKRF_ONE:
            raise RuntimeError("listed device is not HackRF One")
        self._check(self._dll.hackrf_device_list_open(self._list, 0, ctypes.byref(self._device)))
        if not self._device.value:
            raise RuntimeError("HackRF device handle is unavailable")

    def probe(self) -> HackrfReadOnlyProbe:
        with self._lock:
            if self._closed or self._release_started or self._probe_failed:
                raise RuntimeError("HackRF read-only context requires explicit release")
            try:
                if self._dll is None:
                    self._open_read_only_context()
                return self._probe_owned()
            except Exception:  # noqa: BLE001 - retain partial resources for the caller's explicit close.
                self._probe_failed = True
                raise RuntimeError("portable HackRF read-only context failed closed") from None

    def _probe_owned(self) -> HackrfReadOnlyProbe:
        if self._closed or self._dll is None or not self._device.value:
            raise RuntimeError("HackRF read-only context is closed")
        board = ctypes.c_uint8(0xFF)
        part_serial = _PartIdSerial()
        version = ctypes.create_string_buffer(256)
        usb_api = ctypes.c_uint16()
        self._check(self._dll.hackrf_board_id_read(self._device, ctypes.byref(board)))
        if board.value not in _BOARD_ID_HACKRF_ONE_VALUES:
            raise RuntimeError("opened board is not HackRF One")
        self._check(
            self._dll.hackrf_board_partid_serialno_read(
                self._device, ctypes.byref(part_serial)
            )
        )
        self._check(self._dll.hackrf_version_string_read(self._device, version, 255))
        self._check(self._dll.hackrf_usb_api_version_read(self._device, ctypes.byref(usb_api)))
        try:
            firmware = version.value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RuntimeError("HackRF firmware text is invalid") from None
        return HackrfReadOnlyProbe(
            HackrfBoardKind.HACKRF_ONE,
            (int(part_serial.serial_no[0]), int(part_serial.serial_no[1]),
             int(part_serial.serial_no[2]), int(part_serial.serial_no[3])),
            firmware,
            int(usb_api.value),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._release_started = True
            if self._device_close_ambiguous:
                raise RuntimeError("portable HackRF close outcome is indeterminate")
            dll = self._dll
            try:
                if dll is not None and self._device.value:
                    try:
                        status = dll.hackrf_close(self._device)
                    except Exception:  # Foreign exception has no ownership guarantee.
                        self._device_close_ambiguous = True
                        raise
                    # Official libhackrf frees the device even on an error
                    # return. Never retry that consumed pointer. Report the
                    # status first; explicit cleanup resumes other phases.
                    self._device = ctypes.c_void_p()
                    self._check(status)
                if dll is not None and self._list:
                    dll.hackrf_device_list_free(self._list)
                    self._list = None
                if dll is not None and self._initialized:
                    self._check(dll.hackrf_exit())
                    self._initialized = False
                if self._dependency_handle is not None:
                    self._dependency_handle.close()
                    self._dependency_handle = None
            except Exception:  # noqa: BLE001 - stop at the failed phase; keep remaining resources.
                raise RuntimeError("portable HackRF read-only context failed to close") from None
            self._dll = None
            self._closed = True

    def _bind_allowlist(self) -> None:
        assert self._dll is not None
        dll = self._dll
        dll.hackrf_init.argtypes = []
        dll.hackrf_init.restype = ctypes.c_int
        dll.hackrf_exit.argtypes = []
        dll.hackrf_exit.restype = ctypes.c_int
        dll.hackrf_device_list.argtypes = []
        dll.hackrf_device_list.restype = ctypes.POINTER(_DeviceList)
        dll.hackrf_device_list_open.argtypes = [
            ctypes.POINTER(_DeviceList),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        dll.hackrf_device_list_open.restype = ctypes.c_int
        dll.hackrf_device_list_free.argtypes = [ctypes.POINTER(_DeviceList)]
        dll.hackrf_device_list_free.restype = None
        dll.hackrf_close.argtypes = [ctypes.c_void_p]
        dll.hackrf_close.restype = ctypes.c_int
        dll.hackrf_board_id_read.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8)]
        dll.hackrf_board_id_read.restype = ctypes.c_int
        dll.hackrf_board_partid_serialno_read.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_PartIdSerial),
        ]
        dll.hackrf_board_partid_serialno_read.restype = ctypes.c_int
        dll.hackrf_version_string_read.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_uint8,
        ]
        dll.hackrf_version_string_read.restype = ctypes.c_int
        dll.hackrf_usb_api_version_read.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint16),
        ]
        dll.hackrf_usb_api_version_read.restype = ctypes.c_int

    @staticmethod
    def _check(result: int) -> None:
        if result != _HACKRF_SUCCESS:
            raise RuntimeError("libhackrf read-only call failed")


def os_add_dll_directory(path: Path) -> Any:
    """Isolated wrapper retained for unit injection on Windows."""

    import os

    if not hasattr(os, "add_dll_directory"):
        raise RuntimeError("portable DLL directories are unsupported")
    return os.add_dll_directory(str(path))


__all__ = ["LibhackrfReadOnlyPort"]

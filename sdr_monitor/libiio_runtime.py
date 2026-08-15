"""Fail-closed app-local libiio runtime binding for frozen standalone builds.

This helper deliberately knows nothing about contexts, discovery, receivers or
I/Q. Its sole responsibility is to bind a future native Pluto metadata loader
to the exact DLL closure carried beside the frozen canonical extension.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

LIBIIO_RUNTIME_COMPONENTS: tuple[str, ...] = (
    "libiio.dll",
    "libserialport-0.dll",
    "libusb-1.0.dll",
    "libxml2-2.dll",
    "libiconv-2.dll",
    "liblzma-5.dll",
    "zlib1.dll",
)


class PackagedLibiioRuntimeError(RuntimeError):
    """The frozen extension does not have its complete app-local DLL closure."""


def frozen_libiio_runtime_components(native_module: Any) -> tuple[Path, ...]:
    """Return package-local runtime components, or an empty tuple outside EXE.

    Returned paths are not loaded. A future metadata-only native command must
    explicitly call :func:`configure_frozen_libiio_runtime` before it opens
    libiio. No context, scan or receive operation is possible here.
    """

    if not bool(getattr(__import__("sys"), "frozen", False)):
        return ()
    module_file = getattr(native_module, "__file__", None)
    if not isinstance(module_file, str) or not module_file:
        raise PackagedLibiioRuntimeError("frozen canonical native module has no file path")
    package_dir = Path(module_file).resolve().parent
    components = tuple(package_dir / component for component in LIBIIO_RUNTIME_COMPONENTS)
    missing = [str(component) for component in components if not component.is_file()]
    if missing:
        raise PackagedLibiioRuntimeError(
            "frozen libiio runtime closure is incomplete: " + ", ".join(missing)
        )
    return components


def configure_frozen_libiio_runtime(native_module: Any) -> tuple[Path, ...]:
    """Select the verified app-local libiio DLL in a frozen process only.

    A caller-supplied ``LIBIIO_DLL_PATH`` is overwritten rather than honoured.
    This function does not call a loader; it changes only the current-process
    environment after every required package-local component is present.
    """

    components = frozen_libiio_runtime_components(native_module)
    if components:
        os.environ["LIBIIO_DLL_PATH"] = str(components[0])
    return components


__all__ = [
    "LIBIIO_RUNTIME_COMPONENTS",
    "PackagedLibiioRuntimeError",
    "configure_frozen_libiio_runtime",
    "frozen_libiio_runtime_components",
]

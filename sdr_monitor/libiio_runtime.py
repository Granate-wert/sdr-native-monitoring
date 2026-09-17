"""Fail-closed app-local libiio runtime binding for frozen standalone builds.

The helper deliberately knows nothing about contexts, discovery, receivers or
I/Q.  Its only responsibility is to bind the native Pluto loader to the exact
DLL closure carried beside the frozen canonical extension.  Source-tree use is
unchanged: the existing developer/runtime search policy remains outside this
frozen-package admission boundary.
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

    The returned paths are not loaded.  Callers that need the native loader to
    use them must invoke :func:`configure_frozen_libiio_runtime` explicitly.
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
    """Select only the verified app-local ``libiio.dll`` in a frozen process.

    This overwrites a caller-provided ``LIBIIO_DLL_PATH`` rather than silently
    accepting an external runtime.  It performs no DLL load itself; the native
    load-only metadata command remains the sole caller that opens the library.
    """

    components = frozen_libiio_runtime_components(native_module)
    if components:
        os.environ["LIBIIO_DLL_PATH"] = str(components[0])
    return components

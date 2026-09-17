"""Compatibility entry point for the canonical native-build preflight.

Historically this script loaded the extension under esw_dfl._sdr_native.
That creates a second pybind11 identity and is prohibited by the canonical
standalone/legacy boundary. Keep the filename for callers, but delegate all
arguments and validation to the canonical preflight.
"""

from __future__ import annotations

from preflight_sdr_native_build import main


if __name__ == "__main__":
    raise SystemExit(main())

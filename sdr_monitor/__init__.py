"""Package marker for the standalone SDR Native Monitoring product.

This package is intentionally side-effect free at import time: Qt and heavy
SDR modules are only imported inside entry points, never here.  The
structure below mirrors S00 boundary: this package does not contain DFL
parser/spectrogram internals; those remain in the DFL Analyzer tree.
"""

from __future__ import annotations

from ._version import __version__

__all__ = ["__version__"]

# Entry point for the standalone SDR Native Monitoring product.
#
# This module lives in the ``sdr_native_monitoring`` product namespace and
# is independent from the historical ``main.py`` (which can still boot the
# legacy ``ESW_DFL_Analyzer`` GUI today). This boots ONLY the SDR product;
# no legacy DFL GUI and no legacy native decoder loads here.

from __future__ import annotations

import logging
import sys


def main() -> int:
    # Do not import anything from the DFL legacy tree here.
    # Keep the entry point simple so what lives here is what runs SDR.
    logger = logging.getLogger("sdr_native_monitoring")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logging.basicConfig(stream=sys.stderr)

    if len(sys.argv) > 1:
        from esw_dfl.sdr.cli import main as sdr_cli_main

        return sdr_cli_main(sys.argv[1:])

    # GUI path: always the new AppShell; no legacy fallback in this product.
    from esw_dfl.gui import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

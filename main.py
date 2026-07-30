from __future__ import annotations

import logging
import sys

from esw_dfl.activity_log import install_activity_file_logging, log_event


def main() -> int:
    logger = logging.getLogger("esw_dfl")
    handler = install_activity_file_logging(logger)
    mode = "cli" if len(sys.argv) > 1 else "gui"
    from esw_dfl.spectrogram import native_decoder_available

    log_event(
        logger,
        "program",
        "application_started",
        mode=mode,
        arguments=sys.argv[1:],
        native_sgram_decoder=native_decoder_available(),
    )
    if len(sys.argv) > 1:
        from esw_dfl.cli import main as cli_main

        try:
            result = cli_main(sys.argv[1:])
            log_event(logger, "program", "cli_completed", exit_code=result)
            return result
        except Exception:
            logger.exception("CLI завершился с ошибкой")
            raise
        finally:
            handler.flush()
    from esw_dfl.gui import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



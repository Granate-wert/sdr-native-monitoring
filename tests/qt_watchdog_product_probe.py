"""Isolated ORIGINAL mock V2 graph lifecycle diagnostic; no physical SDK/RX.

Inject a held session lock only AFTER a normal user Start/Stop. The original
stopped _refresh/can_close path blocks until a bounded fixture thread releases
it. No patched UI methods, fake traceback, process termination or lease reset.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from threading import Event, Thread
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer

from sdr_monitor.services.qt_stall_watchdog import QtStallWatchdog
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from tests.ui_v2.test_app07_start_refresh_responsive import StartRefreshResponsiveTests
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


def main() -> None:
    StartRefreshResponsiveTests.setUpClass()
    fixture = StartRefreshResponsiveTests()
    # The responsiveness fixture intentionally uses an empty-serial choice;
    # its fake opener requires an exact serial. For a successful Start witness
    # use the SAME original mock graph with an explicit coherent identity.
    with patch("tests.ui_v2.test_app07_start_refresh_responsive._ad_graph",
               side_effect=lambda **_kwargs: _ad_graph(serial="diagnostic-mock-ad")), \
            fixture._owned_surface() as product:
        product.surface.start_all.click()
        fixture._wait(lambda: product.handle.pump.snapshot()[0].phase is PanePumpPhase.RUNNING)
        product.surface.stop_all.click()
        fixture._wait(product.handle.can_close)
        acquired, release = Event(), Event()
        phases: list[str] = []

        def hold_actual_session_lock() -> None:
            with product.handle.session._state_lock:
                acquired.set()
                release.wait(.5)

        holder = Thread(target=hold_actual_session_lock, name="diagnostic-mock-session-lock")
        holder.start()
        try:
            if not acquired.wait(2):
                raise AssertionError("fixture session lock was not acquired")

            def original_refresh() -> None:
                phases.append("entered")
                product.surface._refresh()
                phases.append("returned")

            with QtStallWatchdog(sys.stderr, .08) as watchdog:
                # Prove a consumed earlier whole-run one-shot does not prevent
                # a SECOND native dump during the original product Qt phase.
                watchdog.rearm_phase("pre_refresh_observation")
                Event().wait(.13)
                watchdog.rearm_phase("stopped_original_refresh")
                QTimer.singleShot(0, original_refresh)
                fixture.app.processEvents()
            if phases != ["entered", "returned"]:
                raise AssertionError(f"original Qt callback did not return: {phases}")
        finally:
            release.set()
            holder.join(2)
        if holder.is_alive():
            raise AssertionError("diagnostic holder did not join")
    # _owned_surface asserts workers/resources/futures/queue/QObject retirement.
    print("PRODUCT_START_STOP_NORMAL_CLEANUP", flush=True)


if __name__ == "__main__":
    main()

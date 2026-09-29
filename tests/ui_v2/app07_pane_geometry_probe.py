"""Inert 3+Empty UI V2 board geometry at one Qt scale and logical size.

The probe creates no application owner, SDK, serial port or RX. It measures
the real pane widgets after Qt layout, not RF or Windows per-monitor DPI.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--scale", required=True)
    parser.add_argument("--locale", choices=("ru", "en"), required=True)
    parser.add_argument("--png", type=Path)
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = args.scale

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFrame, QScrollArea

    from sdr_monitor.domain.pane_scheduler import (
        CaptureEpochCost, PaneCaptureProfile, PaneLayoutSlot,
        SpectrumTracePaneProfile, compile_pane_layout,
    )
    from sdr_monitor.domain.receiver_topology import (
        AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection,
        ReceiverEndpoint, SpectrumTraceEndpoint, SweepPaneRequest,
    )
    from sdr_monitor.ui.v2.i18n import UiLocale, set_active_locale
    from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
    from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
    from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer

    app = QApplication([])
    set_active_locale(UiLocale.RU if args.locale == "ru" else UiLocale.EN)
    groups = (
        AcquisitionGroup("ad", "ad-resource", (
            ReceiverEndpoint("ad-rx1", "ad-source", "ad-resource", ReceiverChainSelection.RX1),)),
        AcquisitionGroup("hf", "hf-resource", (
            ReceiverEndpoint("hf-rx1", "hf-source", "hf-resource", ReceiverChainSelection.RX1),)),
        AcquisitionGroup("ts", "ts-resource", (
            SpectrumTraceEndpoint("ts-trace", "ts-source", "ts-resource"),)),
    )
    slots = (
        PaneLayoutSlot(1, SweepPaneRequest("ad-pane", "ad-rx1", 100e6, 108e6,
                                           "ad-profile", requested_binding_mode=ReceiverBindingMode.DEDICATED_PARALLEL)),
        PaneLayoutSlot(2, SweepPaneRequest("hf-pane", "hf-rx1", 140e6, 148e6,
                                           "hf-profile", requested_binding_mode=ReceiverBindingMode.DEDICATED_PARALLEL)),
        PaneLayoutSlot(3, SweepPaneRequest("ts-pane", "ts-trace", 200e6, 210e6,
                                           "ts-profile", requested_binding_mode=ReceiverBindingMode.DEDICATED_PARALLEL)),
        PaneLayoutSlot(4),
    )
    cost = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)
    iq_profile = PaneCaptureProfile(20e6, 10e6, "manual", 20.0, 4096, 2048,
                                    "hann", "sample", None, 10e6, cost)
    layout = compile_pane_layout(slots, groups, {
        "ad-profile": iq_profile,
        "hf-profile": iq_profile,
        "ts-profile": SpectrumTracePaneProfile(101, 10e6, "inert", cost),
    })
    preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget())
    with TemporaryDirectory(prefix="app07-pane-geometry-") as temporary:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setMinimumSize(0, 0)
        board = IndependentPaneBoardV2(
            preparer, settings=QSettings(str(Path(temporary) / "ui.ini"), QSettings.Format.IniFormat),
            source_labels={"ad-source": "AD936x USB", "hf-source": "HackRF USB",
                           "ts-source": "tinySA Ultra"},
        )
        scroll.setWidget(board)
        try:
            scroll.resize(args.width, args.height)
            scroll.show()
            for _ in range(4):
                app.processEvents()
            samples = []
            for number in (1, 2, 3):
                pane = board.pane(number)
                assert pane is not None
                cell = board.findChild(QFrame, f"independentPaneCell{number}")
                assert cell is not None
                label = board._timing_labels[number]
                board.set_pane_timing(number, "Data age: <1 s · revisit 0.45 s / model 0.16 s"
                                      if args.locale == "en" else
                                      "Возраст: <1 с · возврат 0.45 с / модель 0.16 с",
                                      "host timing")
                app.processEvents()
                samples.append({
                    "slot": number,
                    "cell": [cell.width(), cell.height()],
                    "cell_pos": [cell.x(), cell.y()],
                    "pane": [pane.width(), pane.height()],
                    "spectrum": [pane.spectrum_scene.width(), pane.spectrum_scene.height()],
                    "waterfall": [pane.waterfall_pane.width(), pane.waterfall_pane.height()],
                    "timing_width": label.width(),
                    "timing_text_width": label.fontMetrics().horizontalAdvance(label.text()),
                    "timing_fits": label.fontMetrics().horizontalAdvance(label.text()) <= label.width(),
                    "cell_inside_board": board.rect().contains(cell.mapTo(board, cell.rect().topLeft()))
                                         and board.rect().contains(cell.mapTo(board, cell.rect().bottomRight())),
                })
            scroll.ensureWidgetVisible(board._cells[4])
            app.processEvents()
            last_corner = board._cells[4].mapTo(scroll.viewport(), board._cells[4].rect().bottomRight())
            last_slot_reachable = (0 <= last_corner.y() <= scroll.viewport().height()
                                   and 0 <= last_corner.x() <= scroll.viewport().width())
            print(json.dumps({
                "requested": [args.width, args.height],
                "viewport": [scroll.viewport().width(), scroll.viewport().height()],
                "scroll_actual": [scroll.width(), scroll.height()],
                "board_actual": [board.width(), board.height()],
                "stacked": board.stacked_layout,
                "scroll_max": [scroll.horizontalScrollBar().maximum(),
                               scroll.verticalScrollBar().maximum()],
                "scale": args.scale, "locale": args.locale,
                "board_minimum": [board.minimumSizeHint().width(), board.minimumSizeHint().height()],
                "panes": samples, "empty_slots": list(board.empty_slots),
                "last_slot_reachable": last_slot_reachable,
                "grab_nonnull": not scroll.grab().isNull(),
            }, ensure_ascii=False))
            if args.png is not None and not scroll.grab().save(str(args.png)):
                raise RuntimeError("Qt geometry screenshot was not saved")
        finally:
            board.release_presentation_after_shutdown()
            scroll.close()
            preparer.clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

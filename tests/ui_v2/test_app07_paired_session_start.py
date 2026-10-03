"""Initial paired Start confirmation precedes every resource enqueue."""

from __future__ import annotations

from concurrent.futures import Future
import os
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2


class _Pump:
    def __init__(self, startable: tuple[str, ...]) -> None:
        self.startable = startable
        self.started: list[str] = []

    def startable_resource_ids(self) -> tuple[str, ...]:
        return self.startable

    def start_resource(self, resource_id: str) -> Future[object]:
        self.started.append(resource_id)
        future: Future[object] = Future()
        future.set_result(None)
        return future


class PairedSessionStartTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance() or QApplication([]))

    def _surface(self, *, paired: frozenset[str] = frozenset({"paired"}),
                 startable: tuple[str, ...] = ("paired", "peer")) -> tuple[IndependentPaneSessionV2, _Pump]:
        # Exercise the command methods with typed handle facts, without a
        # graph, receiver, delivery timer, or physical QWidget display.
        surface = IndependentPaneSessionV2.__new__(IndependentPaneSessionV2)
        QWidget.__init__(surface)
        pump = _Pump(startable)
        bindings = {
            "pane-1": SimpleNamespace(slot_number=1, physical_stream_resource_id="paired"),
            "pane-2": SimpleNamespace(slot_number=2, physical_stream_resource_id="paired"),
            "pane-3": SimpleNamespace(slot_number=3, physical_stream_resource_id="peer"),
        }
        surface.handle = SimpleNamespace(pump=pump, preparer=SimpleNamespace(
            paired_resource_ids=paired, bindings=bindings))
        surface._futures = []
        surface._rf_phase = None
        surface._rf_fault_resource = None
        surface._terminal_released = False
        surface._error_key = "retained"
        surface._selected_resource = lambda: ("pane-1", "paired")
        surface._refresh = lambda: None
        surface._confirm_paired_start = lambda _impact: False
        return surface, pump

    def test_selected_pair_requires_confirmation_but_single_shared_rx_does_not(self) -> None:
        surface, pump = self._surface()
        impacts: list[tuple[int, ...]] = []
        surface._confirm_paired_start = lambda impact: impacts.append(impact) or False
        surface._start_selected()
        self.assertEqual(impacts, [(1, 2)])
        self.assertEqual(pump.started, [])
        self.assertEqual(surface._error_key, "retained")
        surface._confirm_paired_start = lambda impact: impacts.append(impact) or True
        surface._start_selected()
        self.assertEqual(pump.started, ["paired"])
        pump.startable = ("peer",)
        surface._start_selected()
        self.assertEqual(len(impacts), 2)  # Already-running pair never prompts again.
        surface.deleteLater()

        shared, single_pump = self._surface(paired=frozenset(), startable=("paired",))
        shared._confirm_paired_start = lambda _impact: self.fail("single RX is not a dual pair")
        shared._start_selected()
        self.assertEqual(single_pump.started, ["paired"])
        shared.deleteLater()

    def test_start_all_cancel_is_atomic_for_pair_and_independent_peer(self) -> None:
        surface, pump = self._surface()
        impacts: list[tuple[int, ...]] = []
        surface._confirm_paired_start = lambda impact: impacts.append(impact) or False
        surface._start_all()
        self.assertEqual(impacts, [(1, 2)])
        self.assertEqual(pump.started, [])
        self.assertEqual(surface._error_key, "retained")
        surface._confirm_paired_start = lambda impact: impacts.append(impact) or True
        surface._start_all()
        self.assertEqual(pump.started, ["paired", "peer"])
        self.assertEqual(impacts, [(1, 2), (1, 2)])
        surface.deleteLater()

    def test_modal_state_change_cannot_authorize_stale_start(self) -> None:
        for changed in ("rf", "released", "startable"):
            with self.subTest(changed=changed):
                surface, pump = self._surface()

                def confirm(_impact: tuple[int, ...]) -> bool:
                    if changed == "rf":
                        surface._rf_phase = "entry"
                    elif changed == "released":
                        surface._terminal_released = True
                    else:
                        pump.startable = ("peer",)
                    return True

                surface._confirm_paired_start = confirm
                surface._start_all()
                self.assertEqual(pump.started, [])
                surface.deleteLater()
        selected, pump = self._surface()
        selected._confirm_paired_start = lambda _impact: setattr(selected, "_terminal_released", True) or True
        selected._start_selected()
        self.assertEqual(pump.started, [])
        selected.deleteLater()

    def test_default_dialog_cancel_is_the_default_button(self) -> None:
        surface, _pump = self._surface()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertFalse(surface._ask_paired_start((1, 2)))
        self.assertEqual(question.call_args.args[-1], QMessageBox.StandardButton.Cancel)
        self.assertIn("1, 2", question.call_args.args[2])
        surface.deleteLater()


if __name__ == "__main__":
    unittest.main()

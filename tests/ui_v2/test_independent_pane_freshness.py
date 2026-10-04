"""Presented-pane freshness follows successful GUI admission, not host input."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint
from sdr_monitor.services.pane_resource_session import PaneActivation, PaneDelivery, PaneHostTiming
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase, PanePumpResourceState

from tests.test_app07_pane_resource_session import live_frame
from tests.test_app07_shared_capture_schedule import pane, profile


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class IndependentPaneFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self._old_locale = current_locale()
        self.clock = _Clock()
        set_active_locale(UiLocale.EN)
        self.groups = (
            AcquisitionGroup("paired:group", "paired", (
                ReceiverEndpoint("rx1", "paired:source", "paired", ReceiverChainSelection.RX1),
                ReceiverEndpoint("rx2", "paired:source", "paired", ReceiverChainSelection.RX2),
            )),
            AcquisitionGroup("peer:group", "peer", (
                ReceiverEndpoint("peer-rx", "peer:source", "peer", ReceiverChainSelection.RX1),
            )),
        )
        slots = (
            PaneLayoutSlot(1, pane("left", "rx1", 2_440e6, 2_450e6)),
            PaneLayoutSlot(2, pane("right", "rx2", 2_450e6, 2_460e6)),
            PaneLayoutSlot(3, pane("peer-pane", "peer-rx", 100e6, 108e6,
                                   ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(4),
        )
        layout = compile_pane_layout(slots, self.groups,
            {"capture": profile(36e6, CaptureMeasurementMode.RTBW)})
        self.preparer = PaneDeliveryPreparer(layout, self.groups, PresentationAllocationBudget(),
            admitted_producer_source_id=lambda resource, endpoint:
                endpoint if resource == "paired" else "peer:source")
        settings = QSettings(str(Path(self.temp.name) / "pane.ini"), QSettings.Format.IniFormat)
        self.board = IndependentPaneBoardV2(self.preparer, settings=settings,
                                            monotonic_clock=self.clock)
        self.input_age = {"left": 0.0, "right": 0.0, "peer-pane": 0.0}
        surface = IndependentPaneSessionV2.__new__(IndependentPaneSessionV2)
        surface.handle = SimpleNamespace(session=SimpleNamespace(
            pane_host_timing=lambda pane_id: PaneHostTiming(self.input_age[pane_id], 0.25)))
        surface.board = self.board
        surface._error_key = None
        self.surface = surface

    def tearDown(self) -> None:
        set_active_locale(self._old_locale)
        self.board.release_presentation_after_shutdown()
        self.board.close()
        self.preparer.clear()
        self.temp.cleanup()

    def _prepared(self, pane_id: str, sequence: int, *, activation: int = 1,
                  run: int = 1, epoch: int = 7, sync_epoch: int = 1):
        binding = self.preparer.bindings[pane_id]
        receiver = {"left": "RX1", "right": "RX2"}.get(pane_id)
        center = 104e6 if pane_id == "peer-pane" else 2_450e6
        bundle = live_frame(binding.source_id if receiver is None else binding.receiver_endpoint_id,
                            f"session-{run}", epoch, center_hz=center, receiver_id=receiver)
        frame = replace(bundle.spectrum, sequence=sequence, timestamp_ns=sequence,
                         acquisition_epoch=epoch)
        if receiver is not None:
            from sdr_monitor.domain.analyzer import PairedCaptureMetadata
            bundle = replace(bundle, spectrum=frame,
                paired_capture=PairedCaptureMetadata(sync_epoch, sequence * 4096, sync_epoch - 1),
                identity=None)
        else:
            bundle = replace(bundle, spectrum=frame, identity=None)
        delivery = PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
            binding.receiver_endpoint_id, activation, binding.crop, bundle, float(sequence), run)
        return self.preparer.prepare(delivery)

    def _prefix(self, slot: int, pane_id: str, phase: PanePumpPhase,
                *, activation: int | None, has_frame: bool) -> str:
        current = None if activation is None else PaneActivation(
            "paired" if pane_id in {"left", "right"} else "peer",
            "capture", 0, activation, None, None)
        state = PanePumpResourceState("paired" if pane_id in {"left", "right"} else "peer",
                                     phase=phase, activation=current)
        phase_key = {
            PanePumpPhase.IDLE: "stopped" if has_frame else "stopped_empty",
            PanePumpPhase.STARTING: "starting",
            PanePumpPhase.STOPPING: "stopping",
            PanePumpPhase.STOP_REQUIRED: "stop_required",
            PanePumpPhase.STOPPED: "stopped" if has_frame else "stopped_empty",
            PanePumpPhase.RUNNING: "continuous",
        }[phase]
        summary = self.surface._timing_summary_text(
            slot, pane_id, state, has_retained_frame=has_frame,
            details=text("analyzer.independent.timing." + phase_key))
        self.board.set_pane_timing(slot, summary,
                                   text("analyzer.independent.timing.scope"))
        return summary

    def test_host_input_can_advance_while_presented_plot_age_exceeds_twenty_seconds(self) -> None:
        for pane_id in ("left", "right", "peer-pane"):
            self.assertTrue(self.board.apply_prepared(self._prepared(pane_id, 1)))
        displayed = self.board.pane(3).last_bundle
        self.clock.now = 20.5
        self.input_age["peer-pane"] = 0.4  # Host routing continues during a Qt delivery pause.
        prefix = self._prefix(3, "peer-pane", PanePumpPhase.RUNNING, activation=1, has_frame=True)
        self.assertIs(self.board.pane(3).last_bundle, displayed)
        self.assertIn("Host input age: <1 s", prefix)
        self.assertIn("Plot last updated: 20 s", prefix)

    def test_new_admission_resets_only_affected_pane_and_stale_packet_cannot_reset(self) -> None:
        for pane_id in ("left", "right", "peer-pane"):
            self.assertTrue(self.board.apply_prepared(self._prepared(pane_id, 1)))
        self.clock.now = 10.0
        left_age_before = self.board.plot_update_age_s(1)
        peer_age_before = self.board.plot_update_age_s(3)
        self.assertTrue(self.board.apply_prepared(self._prepared("peer-pane", 2)))
        self.assertEqual(self.board.plot_update_age_s(3), 0.0)
        self.assertEqual(self.board.plot_update_age_s(1), left_age_before)
        self.clock.now = 35.0
        stale = self._prepared("peer-pane", 1)
        self.assertFalse(self.board.apply_prepared(stale))
        self.assertEqual(self.board.plot_update_age_s(3), 25.0)
        self.assertGreater(self.board.plot_update_age_s(3), peer_age_before)

    def test_restart_keeps_prior_activation_age_until_new_packet_is_admitted(self) -> None:
        self.assertTrue(self.board.apply_prepared(self._prepared("peer-pane", 1, activation=1)))
        self.clock.now = 12.0
        old_bundle = self.board.pane(3).last_bundle
        starting = self._prefix(3, "peer-pane", PanePumpPhase.STARTING,
                                activation=None, has_frame=True)
        self.assertEqual(self.board._timing_labels[3].text(), starting)
        self.assertIn("retained from prior activation", starting)
        self.assertIn("Plot last updated: 12 s", starting)
        stopped = self._prefix(3, "peer-pane", PanePumpPhase.STOPPED,
                               activation=1, has_frame=True)
        self.assertNotIn("retained from prior activation", stopped)
        after_rearm = self._prefix(3, "peer-pane", PanePumpPhase.RUNNING,
                                   activation=2, has_frame=True)
        self.assertEqual(self.board._timing_labels[3].text(), after_rearm)
        self.assertIn("retained from prior activation", after_rearm)
        self.assertIs(self.board.pane(3).last_bundle, old_bundle)
        self.assertEqual(self.board.plot_update_age_s(3), 12.0)
        self.clock.now = 13.0
        self.assertTrue(self.board.apply_prepared(self._prepared("peer-pane", 1,
                                                    activation=2, run=2, epoch=8)))
        fresh = self._prefix(3, "peer-pane", PanePumpPhase.RUNNING,
                             activation=2, has_frame=True)
        self.assertNotIn("retained from prior activation", fresh)
        self.assertEqual(self.board.plot_update_age_s(3), 0.0)

    def test_paired_epoch_clear_drops_only_cleared_peer_plot_age(self) -> None:
        for pane_id in ("left", "right", "peer-pane"):
            self.assertTrue(self.board.apply_prepared(self._prepared(pane_id, 1)))
        self.clock.now = 10.0
        self.assertTrue(self.board.apply_prepared(self._prepared(
            "left", 2, sync_epoch=2)))
        self.assertEqual(self.board.plot_update_age_s(1), 0.0)
        self.assertIsNone(self.board.plot_update_age_s(2))
        self.assertEqual(self.board.plot_update_age_s(3), 10.0)

    def test_en_ru_labels_and_scope_distinguish_host_input_from_plot_acceptance(self) -> None:
        for locale, expected_host, expected_plot, expected_marker, scope_host, scope_plot in (
            (UiLocale.EN, "Host input age", "Plot last updated", "retained from prior activation",
             "Host input age", "Plot last updated"),
            (UiLocale.RU, "Вход хоста", "График обновлён", "прошлого запуска", "входа хоста",
             "обновления графика"),
        ):
            set_active_locale(locale)
            self.board.set_locale()
            prefix = self._prefix(3, "peer-pane", PanePumpPhase.STARTING,
                                  activation=None, has_frame=True)
            self.assertIn(expected_host, prefix)
            self.assertIn(expected_plot, prefix)
            set_active_locale(locale)
            marker = self._prefix(3, "peer-pane", PanePumpPhase.STARTING,
                                  activation=None, has_frame=True)
            self.assertIn(expected_marker, marker)
            scope = text("analyzer.independent.timing.scope")
            self.assertIn(scope_host.lower(), scope.lower())
            self.assertIn(scope_plot.lower(), scope.lower())

    def test_unknown_and_long_age_first_rows_fit_with_width_reserve_in_en_and_ru(self) -> None:
        self.board.resize(1366, 768)
        self.board.show()
        self.clock.now = 0.0
        self.assertTrue(self.board.apply_prepared(self._prepared("left", 1)))
        self._prefix(1, "left", PanePumpPhase.RUNNING, activation=1, has_frame=True)
        self.app.processEvents()
        label = self.board._timing_labels[1]
        self.assertGreaterEqual(label.width(), 600)
        self.assertGreaterEqual(label.contentsRect().height(), label.fontMetrics().lineSpacing() * 2)
        for locale in (UiLocale.EN, UiLocale.RU):
            with self.subTest(locale=locale):
                set_active_locale(locale)
                self.input_age["left"] = 1_000.0
                self.clock.now = 1_000.0
                long_age = self._prefix(1, "left", PanePumpPhase.RUNNING,
                                        activation=1, has_frame=True).splitlines()[0]
                self.assertIn(text("analyzer.independent.timing.age_over_limit"), long_age)
                self.assertLessEqual(label.fontMetrics().horizontalAdvance(long_age), label.width() - 10)
                self.input_age["right"] = None
                unknown = self._prefix(2, "right", PanePumpPhase.RUNNING,
                                       activation=None, has_frame=False).splitlines()[0]
                self.assertIn(text("analyzer.independent.timing.age_unknown"), unknown)
                self.assertLessEqual(label.fontMetrics().horizontalAdvance(unknown),
                                     self.board._timing_labels[2].width() - 10)


if __name__ == "__main__":
    unittest.main()

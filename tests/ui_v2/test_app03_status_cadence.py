"""Numerical cadence must never delay lifecycle, loss, identity or errors."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from sdr_monitor.ui.v2.state.analyzer_status_cadence import AnalyzerStatusCadence
from sdr_monitor.ui.v2.state.live_view_state import LiveLossSummary
from sdr_monitor.ui.v2.state.live_view_state import _loss_summary
import tests.ui_v2.test_app02_analyzer_readouts as readouts_fixture


class StatusCadenceTests(unittest.TestCase):
    def test_aggregate_publication_loss_does_not_bypass_numeric_pacing(self):
        state = readouts_fixture.AnalyzerReadoutTests().state()
        cadence = AnalyzerStatusCadence()
        admitted = []
        for index in range(1000):
            performance = replace(state.live.snapshot.performance, source_blocks_dropped=0,
                                  snapshots_superseded=index + 1)
            quality = SimpleNamespace(dropped_blocks=index + 1)
            loss = _loss_summary(quality, performance, state.bundle.spectrum)
            update = replace(state, live=replace(state.live, loss=loss,
                snapshot=replace(state.live.snapshot, performance=performance)))
            if cadence.admit(update, index / 1000):
                admitted.append(index)
        self.assertEqual(admitted, [0, 250, 500, 750])
        # A separately reported physical loss still bypasses the interval.
        changed = replace(update, live=replace(update.live, snapshot=replace(
            update.live.snapshot, performance=replace(performance, source_blocks_dropped=1))))
        self.assertTrue(cadence.admit(changed, 0.9991))

    def test_hundreds_of_numeric_updates_have_four_admissions_per_second(self):
        state = readouts_fixture.AnalyzerReadoutTests().state()
        cadence = AnalyzerStatusCadence()
        admitted = []
        for index in range(1000):
            update = replace(state, live=replace(state.live, data_age_ms=float(index % 100),
                                                 data_age_label=str(index)))
            if cadence.admit(update, index / 1000):
                admitted.append(index)
        self.assertEqual(admitted, [0, 250, 500, 750])

    def test_urgent_changes_bypass_interval(self):
        state = readouts_fixture.AnalyzerReadoutTests().state()
        for update in (
            replace(state, error="device disconnected"), replace(state, running=False),
            replace(state, stopping=True), replace(state, starting=True),
            replace(state, live=replace(state.live, loss=LiveLossSummary(fft_frames=1))),
            replace(state, live=replace(state.live, data_age_ms=2000)),
            replace(state, bundle=replace(state.bundle, coherence_issues=("bad unit",))),
        ):
            with self.subTest(update=update.error):
                cadence = AnalyzerStatusCadence()
                self.assertTrue(cadence.admit(state, 0))
                self.assertTrue(cadence.admit(update, 0.001))

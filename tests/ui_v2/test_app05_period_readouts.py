"""UI keeps applied Fs, host buffer period, pass period and paint period separate."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.live import LivePerformance
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.state.analyzer_readouts import analyzer_periods
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests.ui_v2.test_app02_analyzer_readouts import AnalyzerReadoutTests


class PeriodReadoutTests(unittest.TestCase):
    def test_mean_ingress_period_not_reciprocal_Fs_or_publication_rate(self):
        locale = current_locale()
        self.addCleanup(set_active_locale, locale)
        state = AnalyzerReadoutTests().state()
        metrics = LivePerformance(iq_block_rate_hz=25, rate_observation_interval_s=1,
                                  analytical_fft_rate_hz=900, spectrum_snapshot_rate_hz=300)
        state = replace(state, running=True, live=replace(state.live, snapshot=replace(
            state.live.snapshot, performance=metrics)))
        for language in (UiLocale.RU, UiLocale.EN):
            set_active_locale(language)
            label = analyzer_periods(state, 80)
            self.assertIn("≈80", label)
            self.assertIn("≈40", label)
            self.assertNotIn("900", label)
            self.assertNotIn("300", label)
            for rate in (None, 0, -1, float("nan"), True):
                missing = replace(state, live=replace(state.live, snapshot=replace(
                    state.live.snapshot, performance=replace(metrics, iq_block_rate_hz=rate))))
                self.assertIn("≈—", analyzer_periods(missing, None))

    def test_stopped_is_not_zero_or_a_continuing_measurement(self):
        state = replace(AnalyzerReadoutTests().state(), running=False)
        label = analyzer_periods(state, 100)
        self.assertNotIn("100", label)

    def test_sweep_period_requires_a_completed_pass_and_is_not_rtbw_period(self):
        state = replace(AnalyzerReadoutTests().state(), mode=AnalyzerMode.SWEEP, running=True,
            sweep_snapshot=SimpleNamespace(metrics=SimpleNamespace(completed_lines=3, completed_line_lps=5)))
        self.assertIn("≈200", analyzer_periods(state, 80))
        state = replace(state, sweep_snapshot=SimpleNamespace(metrics=SimpleNamespace(
            completed_lines=0, completed_line_lps=5)))
        self.assertIn("≈—", analyzer_periods(state, 80))


if __name__ == "__main__":
    unittest.main()

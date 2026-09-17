"""Published rates remain distinct; default/invalid values remain unknown."""
from dataclasses import replace
import unittest

from sdr_monitor.domain.live import LivePerformance
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.state.analyzer_readouts import analyzer_status
import tests.ui_v2.test_app02_analyzer_readouts as fixture


class RateLabelTests(unittest.TestCase):
    def test_distinct_rates_and_unknown_defaults_in_actual_status(self):
        locale = current_locale()
        self.addCleanup(set_active_locale, locale)
        state = fixture.AnalyzerReadoutTests().state()
        for language in (UiLocale.RU, UiLocale.EN):
            set_active_locale(language)
            metrics = LivePerformance(analytical_fft_rate_hz=451,
                spectrum_snapshot_rate_hz=40, iq_sample_rate_hz=13e6,
                rate_observation_interval_s=1)
            live = replace(state.live, snapshot=replace(state.live.snapshot, performance=metrics))
            label = analyzer_status(replace(state, live=live))
            self.assertIn("451", label)
            self.assertIn("40", label)
            self.assertIn("I/Q MS/s: 13", label)
            self.assertNotIn("FPS", label)
            for invalid in (None, 0, float("nan")):
                missing = replace(metrics, rate_observation_interval_s=invalid)
                unknown = replace(live, snapshot=replace(live.snapshot, performance=missing))
                self.assertIn("I/Q MS/s: —", analyzer_status(replace(state, live=unknown)))

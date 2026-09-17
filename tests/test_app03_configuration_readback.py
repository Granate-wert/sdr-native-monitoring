"""Missing native fields must never turn a staged configuration into readback."""

from types import SimpleNamespace
import unittest

from sdr_monitor.domain.live import AppliedLiveConfiguration, BackendKind, LiveConfiguration
from sdr_monitor.services.native_live import _domain_applied_configuration
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.state.configuration_readouts import configuration_prefix


class ConfigurationReadbackTests(unittest.TestCase):
    def test_stage_and_partial_native_reply_do_not_claim_full_rf_readback(self):
        locale = current_locale()
        self.addCleanup(set_active_locale, locale)
        request = LiveConfiguration(sample_rate_hz=3e6)
        stage = AppliedLiveConfiguration(request, request)
        for language in (UiLocale.RU, UiLocale.EN):
            set_active_locale(language)
            self.assertEqual(configuration_prefix(stage), text("analyzer.prepared_prefix"))
            partial = _domain_applied_configuration(stage,
                native_applied=SimpleNamespace(sample_rate_hz=3e6), active_backend=BackendKind.CPU)
            self.assertEqual(partial.readback_fields, ("sample_rate_hz",))
            self.assertEqual(configuration_prefix(partial), text("analyzer.prepared_prefix"))

    def test_complete_reply_preserves_requested_values_and_names_real_adjustment(self):
        request = LiveConfiguration(center_hz=2400e6, sample_rate_hz=3e6, gain_db=18)
        actual = _domain_applied_configuration(AppliedLiveConfiguration(request, request),
            native_applied=SimpleNamespace(center_frequency_hz=2400e6, sample_rate_hz=2999999.,
                                          analog_bandwidth_hz=3e6, manual_gain_db=18.),
            active_backend=BackendKind.CPU)
        self.assertEqual(actual.requested.sample_rate_hz, 3e6)
        self.assertEqual(actual.applied.sample_rate_hz, 2999999.)
        self.assertIn("sample rate adjusted by device", actual.adjustments)
        self.assertEqual(configuration_prefix(actual), text("analyzer.rf_readback_prefix"))

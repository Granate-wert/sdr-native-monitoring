"""Missing native fields must never turn a staged configuration into readback."""

from types import SimpleNamespace
import unittest

from sdr_monitor.domain.live import AppliedLiveConfiguration, BackendKind, LiveConfiguration, ObservedGainMode
from sdr_monitor.services.native_live import _domain_applied_configuration
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.state.configuration_readouts import configuration_prefix


class ConfigurationReadbackTests(unittest.TestCase):
    def test_each_native_gain_policy_is_preserved_without_changing_gain(self):
        request = LiveConfiguration(gain_db=18)
        for mode in ObservedGainMode:
            with self.subTest(mode=mode):
                actual = _domain_applied_configuration(
                    AppliedLiveConfiguration(request, request),
                    native_applied=SimpleNamespace(manual_gain_db=17., gain_mode=SimpleNamespace(name=mode.name)),
                    active_backend=BackendKind.CPU,
                )
                self.assertIs(actual.observed_gain_mode, mode)
                self.assertEqual(actual.readback_fields, ("gain_db", "gain_mode"))
                self.assertEqual(actual.applied.gain_db, 17.)
                self.assertEqual(actual.requested.gain_db, 18.)

    def test_missing_or_unknown_gain_policy_does_not_inherit_previous_readback(self):
        request = LiveConfiguration()
        provisional = AppliedLiveConfiguration(
            request, request, readback_fields=("gain_mode",), observed_gain_mode=ObservedGainMode.MANUAL,
        )
        for raw in (None, "MANUAL", 0, SimpleNamespace(name="UNKNOWN"), SimpleNamespace(name="manual")):
            with self.subTest(raw=raw):
                actual = _domain_applied_configuration(
                    provisional, native_applied=SimpleNamespace(manual_gain_db=18., gain_mode=raw),
                    active_backend=BackendKind.CPU,
                )
                self.assertIsNone(actual.observed_gain_mode)
                self.assertEqual(actual.readback_fields, ("gain_db",))

    def test_gain_readback_contract_rejects_untyped_policy(self):
        request = LiveConfiguration()
        with self.assertRaises(ValueError):
            AppliedLiveConfiguration(request, request, observed_gain_mode="manual")

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

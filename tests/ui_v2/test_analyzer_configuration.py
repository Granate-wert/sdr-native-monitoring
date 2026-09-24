"""Fake-only contract tests for the Analyzer configuration drawer."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.live_configuration_patch import LiveConfigurationPatch
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerViewModel
from sdr_monitor.ui.v2.workspaces.analyzer_configuration import AnalyzerConfigurationDrawer
from tests.ui_v2.test_app02_analyzer_view_model import Live, Sweep


class AnalyzerConfigurationDrawerTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.live, self.sweep = Live(), Sweep()
        self.model = AnalyzerViewModel(self.live, self.sweep)
        self.drawer = AnalyzerConfigurationDrawer(self.model)

    def tearDown(self) -> None:
        self.drawer.close()
        self.drawer.deleteLater()
        self.model.dispose()
        self.app.processEvents()

    def test_initial_apply_is_refused_without_selected_source_and_uri_is_validated(self) -> None:
        self.assertFalse(self.drawer.can_apply)
        self.drawer._uri.setText("bad")
        self.drawer._use_uri.click()
        self.assertEqual(self.live.calls, [])
        self.drawer._uri.setText("usb:")
        self.drawer._use_uri.click()
        self.assertEqual(self.live.calls, [("uri", "usb:")])
        self.drawer._gain.setValue(27.0)
        self.assertFalse(self.drawer.can_apply)
        self.drawer._apply.click()
        self.assertEqual(self.live.calls, [("uri", "usb:")])
        self.assertFalse(self.drawer.pending)

    def test_lost_source_identity_disables_apply_even_after_local_edits(self) -> None:
        device = self._device("source-a")
        self._publish_snapshot(self._snapshot(device, 3, LiveConfiguration()))
        self.drawer._gain.setValue(22.0)
        self.assertTrue(self.drawer.can_apply)
        snapshot = self._snapshot(None, 3, LiveConfiguration())
        self._publish_snapshot(snapshot)
        self.assertFalse(self.drawer.can_apply)
        self.drawer._gain.setValue(23.0)
        self.assertFalse(self.drawer.can_apply)
        self.drawer.apply_draft()
        self.assertEqual(self.live.calls, [])
        self.assertFalse(self.drawer.pending)
        self.assertIn(text("analyzer.settings.select_source"), self.drawer._status.text())

    def test_locked_state_disables_runtime_controls_without_constructing_hardware(self) -> None:
        self.live.state = replace(self.live.state, busy=True)
        for callback in tuple(self.live.callbacks):
            callback(self.live.state)
        self.assertFalse(self.drawer._apply.isEnabled())
        self.assertFalse(self.drawer._use_uri.isEnabled())

    def test_initial_apply_uses_full_configuration_and_waits_for_matching_confirmation(self) -> None:
        device = SimpleNamespace(
            device_id="source-a",
            capabilities=SimpleNamespace(supported_backends=(BackendKind.AUTO, BackendKind.CPU)),
        )
        self._publish_snapshot(SimpleNamespace(
            session_id="session-a", generation=3, device=device, applied=None,
        ))
        self.assertEqual(self.drawer._backend.currentData(), BackendKind.AUTO.value)
        self.assertTrue(self.drawer._apply.isEnabled())
        self.drawer._backend.setCurrentIndex(self.drawer._backend.findData(BackendKind.CPU.value))
        self.drawer._apply.click()
        kind, payload = self.live.calls[-1]
        self.assertEqual(kind, "apply")
        self.assertIsInstance(payload, LiveConfiguration)
        self.assertNotIsInstance(payload, LiveConfigurationPatch)
        self.assertIs(payload.backend, BackendKind.CPU)
        self.assertTrue(self.drawer.pending)
        self._publish_snapshot(SimpleNamespace(
            session_id="session-a", generation=4, device=device,
            applied=SimpleNamespace(applied=payload),
        ))
        self.assertFalse(self.drawer.pending)
        self.assertFalse(self.drawer.dirty)

    def test_unpublished_backend_is_retained_but_not_silently_reselected(self) -> None:
        applied = LiveConfiguration(backend=BackendKind.CUDA)
        device = SimpleNamespace(
            device_id="source-a",
            capabilities=SimpleNamespace(supported_backends=(BackendKind.AUTO, BackendKind.CPU)),
        )
        self._publish_snapshot(SimpleNamespace(
            session_id="session-a", generation=3, device=device,
            applied=SimpleNamespace(applied=applied),
        ))
        self.assertEqual(self.drawer._backend.currentData(), BackendKind.CUDA.value)
        self.assertFalse(self.drawer._backend.isEnabled())
        self.assertEqual(self.drawer._status.text(), text("live_state.backend.empty"))

    def test_draft_changed_reports_local_state_without_snapshot_feedback_loop(self) -> None:
        changes: list[bool] = []
        self.drawer.draft_changed.connect(lambda: changes.append(self.drawer.dirty))
        self.drawer._gain.setValue(27.0)
        self.assertEqual(changes, [True])
        self.drawer._cancel.click()
        self.assertEqual(changes, [True, False])

    def test_normalized_newer_readback_resolves_pending_to_backend_truth(self) -> None:
        device = self._device("source-a")
        self._publish_snapshot(self._snapshot(device, 3, LiveConfiguration()))
        self.drawer._sample_rate.setValue(12.345)
        self.drawer._apply.click()
        normalized = LiveConfiguration(sample_rate_hz=12_000_000.0)
        self._publish_snapshot(self._snapshot(device, 4, normalized))
        self.assertFalse(self.drawer.pending)
        self.assertFalse(self.drawer.dirty)
        self.assertEqual(self.drawer._base, normalized)
        self.assertFalse(self.model.state.configuration_pending)

    def test_sample_rate_only_patch_preserves_independent_rf_bandwidth(self) -> None:
        device = self._device("source-a", bandwidths=(20e6, 40e6))
        applied = LiveConfiguration(sample_rate_hz=20e6, analog_bandwidth_hz=20e6)
        self._publish_snapshot(self._snapshot(device, 3, applied))
        self.assertEqual(self.drawer._rf_bandwidth.currentData(), 20e6)
        self.drawer._sample_rate.setValue(61.44)
        self.drawer.apply_draft()
        patch = self.live.calls[-1][1]
        self.assertIsInstance(patch, LiveConfigurationPatch)
        self.assertEqual(dict(patch.changes), {"sample_rate_hz": 61.44e6})
        self._publish_snapshot(self._snapshot(device, 4, replace(applied, sample_rate_hz=61.44e6)))
        self.assertFalse(self.drawer.pending)
        self.assertEqual(self.drawer.preview_configuration().analog_bandwidth_hz, 20e6)
        self.assertIn("RF BW 20 MHz", self.drawer._applied.text())

    def test_rf_bandwidth_patch_waits_for_authoritative_normalized_readback(self) -> None:
        device = self._device("source-a", bandwidths=(20e6, 40e6, 56e6))
        applied = LiveConfiguration(analog_bandwidth_hz=20e6)
        self._publish_snapshot(self._snapshot(device, 3, applied))
        control = self.drawer._rf_bandwidth
        self.assertTrue(control.isEnabled())
        control.setCurrentIndex(control.findData(40e6))
        self.drawer.apply_draft()
        patch = self.live.calls[-1][1]
        self.assertIsInstance(patch, LiveConfigurationPatch)
        self.assertEqual(dict(patch.changes), {"analog_bandwidth_hz": 40e6})
        self.assertTrue(self.drawer.pending)
        self.assertFalse(control.isEnabled())
        # Native RF-filter readback can differ from an adapter preset.
        normalized = replace(applied, analog_bandwidth_hz=39.5e6)
        self._publish_snapshot(self._snapshot(device, 4, normalized))
        self.assertFalse(self.drawer.pending)
        self.assertFalse(self.drawer.dirty)
        self.assertEqual(self.drawer.preview_configuration().analog_bandwidth_hz, 39.5e6)
        self.assertIn("39.5", control.currentText())

    def test_unpublished_rf_bandwidth_is_retained_read_only(self) -> None:
        device = self._device("source-a")
        applied = LiveConfiguration(analog_bandwidth_hz=19.8e6)
        self._publish_snapshot(self._snapshot(device, 3, applied))
        control = self.drawer._rf_bandwidth
        self.assertFalse(control.isEnabled())
        self.assertEqual(control.currentData(), 19.8e6)
        self.assertEqual(control.count(), 2)  # Follow-Fs and applied readback only.
        self.assertEqual(self.drawer._draft_changes(), {})

    def test_rf_bandwidth_source_switch_and_cancel_do_not_carry_old_draft(self) -> None:
        source_a = self._device("source-a", bandwidths=(20e6, 40e6))
        source_b = self._device("source-b", bandwidths=(10e6, 20e6))
        self._publish_snapshot(self._snapshot(source_a, 3, LiveConfiguration(analog_bandwidth_hz=40e6)))
        self.drawer._rf_bandwidth.setCurrentIndex(self.drawer._rf_bandwidth.findData(20e6))
        self.assertTrue(self.drawer.dirty)
        self.drawer.cancel_draft()
        self.assertEqual(self.drawer._rf_bandwidth.currentData(), 40e6)
        self.drawer._rf_bandwidth.setCurrentIndex(self.drawer._rf_bandwidth.findData(20e6))
        self._publish_snapshot(SimpleNamespace(
            session_id="session-b", generation=0, device=source_b, applied=None,
        ))
        self.assertFalse(self.drawer.dirty)
        self.assertEqual(self.drawer._rf_bandwidth.currentData(), None)
        self.assertEqual(self.drawer._rf_bandwidth.findData(40e6), -1)

    def test_rf_bandwidth_labels_retranslate_without_changing_selection(self) -> None:
        original = current_locale()
        self.addCleanup(set_active_locale, original)
        device = self._device("source-a", bandwidths=(20e6, 40e6))
        self._publish_snapshot(self._snapshot(device, 3, LiveConfiguration(analog_bandwidth_hz=20e6)))
        for locale in UiLocale:
            set_active_locale(locale)
            self.drawer.set_locale()
            self.assertEqual(self.drawer._rf_bandwidth.accessibleName(), text("analyzer.rf_bandwidth"))
            self.assertEqual(self.drawer._rf_bandwidth.accessibleDescription(), text("analyzer.rf_bandwidth.help"))
            self.assertEqual(self.drawer._rf_bandwidth.itemText(0), text("analyzer.rf_bandwidth.auto"))
            self.assertEqual(self.drawer._rf_bandwidth.currentData(), 20e6)

    def test_same_generation_error_releases_pending_and_preserves_draft(self) -> None:
        device = self._device("source-a")
        self._publish_snapshot(self._snapshot(device, 3, LiveConfiguration()))
        self.drawer._gain.setValue(27.0)
        self.drawer._apply.click()
        self.live.state = replace(self.live.state, error_label="gain rejected")
        for callback in tuple(self.live.callbacks):
            callback(self.live.state)
        self.assertFalse(self.drawer.pending)
        self.assertTrue(self.drawer.dirty)
        self.assertEqual(self.drawer._gain.value(), 27.0)
        self.assertFalse(self.model.state.configuration_pending)

    def test_synchronous_confirmation_cannot_arrive_before_pending_latch(self) -> None:
        device = self._device("source-a")
        self._publish_snapshot(self._snapshot(device, 3, LiveConfiguration()))
        self.drawer._gain.setValue(22.0)
        original = self.live.apply_configuration

        def apply_and_confirm(value):
            result = original(value)
            expected = replace(self.drawer._base, gain_db=22.0)
            self._publish_snapshot(self._snapshot(device, 4, expected))
            return result

        self.live.apply_configuration = apply_and_confirm
        self.drawer._apply.click()
        self.assertFalse(self.drawer.pending)
        self.assertFalse(self.model.state.configuration_pending)
        self.assertEqual(self.drawer._base.gain_db, 22.0)

    def test_new_unconfigured_source_resets_patch_base_for_initial_apply(self) -> None:
        source_a = self._device("source-a")
        source_b = self._device("source-b")
        self._publish_snapshot(self._snapshot(source_a, 3, LiveConfiguration(gain_db=17.0)))
        self._publish_snapshot(SimpleNamespace(
            session_id="session-b", generation=0, device=source_b, applied=None,
        ))
        self.assertFalse(self.drawer._has_applied)
        self.assertEqual(self.drawer._base_identity, ("session-b", 0, "source-b"))
        self.drawer._gain.setValue(9.0)
        self.drawer._apply.click()
        self.assertIsInstance(self.live.calls[-1][1], LiveConfiguration)

    def test_cancel_conflict_restores_latest_applied_configuration(self) -> None:
        device = self._device("source-a")
        first = LiveConfiguration(gain_db=10.0)
        latest = LiveConfiguration(gain_db=31.0)
        self._publish_snapshot(self._snapshot(device, 3, first))
        self.drawer._gain.setValue(20.0)
        self._publish_snapshot(self._snapshot(device, 4, latest))
        self.assertTrue(self.drawer._conflicted)
        self.drawer._cancel.click()
        self.assertEqual(self.drawer._base, latest)
        self.assertEqual(self.drawer._gain.value(), 31.0)

    def test_gain_only_edit_does_not_patch_display_rounded_frequencies(self) -> None:
        device = self._device("source-a")
        applied = LiveConfiguration(center_hz=100_000_321.0, sample_rate_hz=5_000_321.0)
        self._publish_snapshot(self._snapshot(device, 3, applied))
        self.drawer._gain.setValue(27.0)
        self.drawer._apply.click()
        patch = self.live.calls[-1][1]
        self.assertIsInstance(patch, LiveConfigurationPatch)
        self.assertEqual(dict(patch.changes), {"gain_db": 27.0})

    def test_pending_request_locks_draft_fields(self) -> None:
        device = self._device("source-a")
        self._publish_snapshot(self._snapshot(device, 3, LiveConfiguration()))
        self.drawer._gain.setValue(27.0)
        self.drawer._apply.click()
        self.assertTrue(self.drawer.pending)
        self.assertFalse(self.drawer._gain.isEnabled())
        self.assertFalse(self.drawer._rf_bandwidth.isEnabled())
        self.assertFalse(self.drawer._uri.isEnabled())
        self.assertTrue(self.model.state.configuration_pending)

    @staticmethod
    def _device(identifier: str, *, bandwidths: tuple[float, ...] = ()) -> SimpleNamespace:
        return SimpleNamespace(
            device_id=identifier,
            capabilities=SimpleNamespace(supported_backends=(BackendKind.AUTO, BackendKind.CPU),
                                         analog_bandwidths_hz=bandwidths),
        )

    @staticmethod
    def _snapshot(device: object, generation: int, configuration: LiveConfiguration) -> SimpleNamespace:
        return SimpleNamespace(
            session_id="session-a", generation=generation, device=device,
            applied=SimpleNamespace(applied=configuration),
        )

    def _publish_snapshot(self, snapshot: object) -> None:
        self.live.state = replace(self.live.state, snapshot=snapshot)
        for callback in tuple(self.live.callbacks):
            callback(self.live.state)


if __name__ == "__main__":
    unittest.main()

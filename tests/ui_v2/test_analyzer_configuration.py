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
from sdr_monitor.ui.v2.i18n import text
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
        self.drawer._uri.setText("bad")
        self.drawer._use_uri.click()
        self.assertEqual(self.live.calls, [])
        self.drawer._uri.setText("usb:")
        self.drawer._use_uri.click()
        self.assertEqual(self.live.calls, [("uri", "usb:")])
        self.drawer._gain.setValue(27.0)
        self.drawer._apply.click()
        self.assertEqual(self.live.calls, [("uri", "usb:")])
        self.assertFalse(self.drawer.pending)

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
        self.assertFalse(self.drawer._uri.isEnabled())
        self.assertTrue(self.model.state.configuration_pending)

    @staticmethod
    def _device(identifier: str) -> SimpleNamespace:
        return SimpleNamespace(
            device_id=identifier,
            capabilities=SimpleNamespace(supported_backends=(BackendKind.AUTO, BackendKind.CPU)),
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

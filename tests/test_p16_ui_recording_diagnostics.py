"""P16UI-08 recording/replay and diagnostics presentation tests."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from esw_dfl.sdr.contracts import ComputeBackendKind
from esw_dfl.ui.app_shell import AppShell
from esw_dfl.ui.diagnostics_presenter import DiagnosticsPresenter
from esw_dfl.ui.diagnostics_workspace import DiagnosticsWorkspace
from esw_dfl.ui.recording_presenter import RecordingPresenter
from esw_dfl.ui.recording_state import (
    RecordingRunState,
    ReplayRunState,
    ReplaySourceKind,
)
from esw_dfl.ui.recording_workspace import RecordingWorkspace
from esw_dfl.ui.state import WorkspaceId


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_for(predicate, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met in time")


class RecordingPresenterTests(unittest.TestCase):
    def test_configure_rejects_empty_output(self) -> None:
        presenter = RecordingPresenter()
        errors = presenter.configure(output_uri="", record_iq=True, duration_s=5.0)
        self.assertTrue(errors)
        self.assertIn("output_uri", errors[0])
        presenter.close()

    def test_configure_forecast_and_insufficient_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            presenter = RecordingPresenter()
            errors = presenter.configure(
                output_uri=Path(tmp) / "big.cap",
                record_iq=True,
                duration_s=10_000_000.0,
            )
            self.assertEqual(errors, [])
            snapshot = presenter.poll()
            self.assertIsNotNone(snapshot.setup)
            self.assertIsNotNone(snapshot.setup.estimated_bytes)
            self.assertEqual(snapshot.setup.sufficient, "no")
            self.assertTrue(snapshot.confirmation_required)
            presenter.close()

    def test_record_then_replay_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "session.cap"
            presenter = RecordingPresenter(block_samples=16_384)
            errors = presenter.configure(output_uri=target, record_iq=True, duration_s=0.15)
            self.assertEqual(errors, [])
            self.assertEqual(presenter.start_recording(), [])
            _wait_for(lambda: presenter.poll().recording_state is RecordingRunState.COMPLETED)
            health = presenter.poll().health
            self.assertIsNotNone(health)
            self.assertNotEqual(health.written_iq_samples, "0")
            errors = presenter.open_replay(target, kind=ReplaySourceKind.IQ)
            self.assertEqual(errors, [])
            replay = presenter.poll().replay
            self.assertIsNotNone(replay)
            self.assertNotEqual(replay.sample_count, "0")
            self.assertNotIn(str(tmp), replay.name)
            presenter.close()

    def test_replay_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "session.cap"
            presenter = RecordingPresenter(block_samples=8_192)
            presenter.configure(output_uri=target, record_iq=True, duration_s=0.1)
            presenter.start_recording()
            _wait_for(lambda: presenter.poll().recording_state is RecordingRunState.COMPLETED)
            presenter.open_replay(target, kind=ReplaySourceKind.IQ)
            presenter.play()
            _wait_for(lambda: presenter.poll().replay_state is ReplayRunState.FINISHED)
            presenter.pause()
            presenter.seek_fraction(0.5)
            presenter.stop_replay()
            self.assertIn(
                presenter.poll().replay_state,
                (ReplayRunState.LOADED, ReplayRunState.FINISHED),
            )
            presenter.close()

    def test_replay_open_missing_recording_fails_cleanly(self) -> None:
        presenter = RecordingPresenter()
        errors = presenter.open_replay("does_not_exist", kind=ReplaySourceKind.IQ)
        self.assertTrue(errors)
        self.assertIs(presenter.poll().replay_state, ReplayRunState.FAILED)
        presenter.close()

    def test_recover_partial_recording(self) -> None:
        from esw_dfl.sdr.contracts import QualityFlag, SampleFormat
        from esw_dfl.sdr.recording import IqBlock, IqRecordingWriter
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "crashed.cap"
            writer = IqRecordingWriter(target)
            writer.start()
            raw = np.zeros(2 * 1024, dtype=np.float32).view(np.uint8)
            block = IqBlock(
                source_sequence=0,
                first_sample_index=0,
                timestamp_ns=1,
                center_frequency_hz=1.0e6,
                sample_rate_hz=1.0e6,
                sample_format=SampleFormat.COMPLEX_FLOAT32_LE,
                sample_count=1024,
                flags=QualityFlag.NONE,
                samples=raw,
                config_generation=0,
            )
            writer.write_block(block)
            writer.abort("crash")
            presenter = RecordingPresenter()
            errors = presenter.recover_partial(target)
            self.assertEqual(len(errors), 1)
            self.assertIn("recovered", errors[0].lower())
            presenter.close()

    def test_reprocess_rejects_auto_backend(self) -> None:
        presenter = RecordingPresenter()
        errors = presenter.reprocess_iq("x", backend=ComputeBackendKind.AUTO)
        self.assertTrue(errors)
        presenter.close()


class DiagnosticsPresenterTests(unittest.TestCase):
    def test_collect_sections_has_platform_and_native(self) -> None:
        presenter = DiagnosticsPresenter()
        titles = [section.title for section in presenter.poll().sections]
        self.assertEqual(titles, ["Platform", "Native core", "Backends"])
        presenter.close()

    def test_self_test_returns_message(self) -> None:
        presenter = DiagnosticsPresenter()
        messages = presenter.run_self_tests()
        self.assertEqual(len(messages), 1)
        self.assertIn("native self-test", messages[0])
        presenter.close()

    def test_support_bundle_anonymized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            presenter = DiagnosticsPresenter()
            self.assertEqual(presenter.export_support_bundle(tmp), [])
            _wait_for(lambda: presenter.poll().support_bundle is not None, timeout_s=15.0)
            bundle = presenter.poll().support_bundle
            self.assertIsNotNone(bundle)
            self.assertNotIn(str(tmp), bundle.path_hint)
            content = (Path(tmp) / "support_bundle.json").read_text(encoding="utf-8")
            self.assertNotIn(str(Path.home()), content)
            presenter.close()

    def test_validation_reports_cuda_unavailable(self) -> None:
        presenter = DiagnosticsPresenter()
        presenter.run_validation()
        _wait_for(lambda: presenter.poll().validation_state.name == "COMPLETED", timeout_s=90.0)
        rows = presenter.poll().validation_rows
        by_name = {row.name: row.status for row in rows}
        self.assertIn("cpu.precision", by_name)
        cuda = by_name.get("cuda.parity")
        if cuda is not None:
            self.assertEqual(cuda, "NOT_VERIFIED")
        presenter.close()


class RecordingWorkspaceUiTests(unittest.TestCase):
    def test_workspace_renders_and_shuts_down(self) -> None:
        app = _app()
        presenter = RecordingPresenter()
        workspace = RecordingWorkspace(presenter=presenter)
        workspace.show()
        app.processEvents()
        self.assertEqual(workspace.objectName(), "p16RecordingWorkspace")
        workspace.request_shutdown()
        self.assertFalse(presenter.poll().stale)

    def test_app_shell_lazy_attach_recording_and_diagnostics(self) -> None:
        app = _app()
        shell = AppShell(
            recording_workspace_factory=RecordingWorkspace,
            diagnostics_workspace_factory=DiagnosticsWorkspace,
        )
        shell.show()
        app.processEvents()
        shell.set_active_workspace(WorkspaceId.RECORDING_REPLAY)
        app.processEvents()
        self.assertIsNotNone(shell.attached_workspace(WorkspaceId.RECORDING_REPLAY))
        shell.set_active_workspace(WorkspaceId.DIAGNOSTICS)
        app.processEvents()
        self.assertIsNotNone(shell.attached_workspace(WorkspaceId.DIAGNOSTICS))
        recording = shell.attached_workspace(WorkspaceId.RECORDING_REPLAY)
        diagnostics = shell.attached_workspace(WorkspaceId.DIAGNOSTICS)
        self.assertIsNotNone(recording)
        self.assertIsNotNone(diagnostics)
        recording.request_shutdown()
        diagnostics.request_shutdown()
        shell.close()

    def test_diagnostics_workspace_renders(self) -> None:
        app = _app()
        presenter = DiagnosticsPresenter()
        workspace = DiagnosticsWorkspace(presenter=presenter)
        workspace.show()
        app.processEvents()
        self.assertEqual(workspace.objectName(), "p16DiagnosticsWorkspace")
        self.assertGreaterEqual(workspace._sections_layout.count(), 1)
        workspace.request_shutdown()


if __name__ == "__main__":
    unittest.main()

"""UI2-10C fake/offscreen coverage for deferred spectrum-only replay."""

from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import (
    IQBlock,
    RecordingIndex,
    ReplayIndexEntry,
    ReplayKind,
    ReplayPosition,
    ReprocessResult,
    SpectrumFrame,
)
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.shell import AppShellV2
from sdr_monitor.ui.v2.workspaces import ReplayWorkspaceV2
from tests.ui_v2.test_live_product_composition import FakePresenter


class _Signal:
    def __init__(self) -> None:
        self._callbacks: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        self._callbacks.append(callback)

    def disconnect(self, callback: Callable[..., None]) -> None:
        self._callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class _ReplayPresenter:
    def __init__(self) -> None:
        self.index_ready = _Signal()
        self.position_changed = _Signal()
        self.frame_ready = _Signal()
        self.reprocess_ready = _Signal()
        self.task_failed = _Signal()
        self.busy_changed = _Signal()
        self.open_calls: list[tuple[Path, ReplayKind]] = []
        self.seek_calls: list[float] = []
        self.read_calls = 0
        self.reprocess_calls: list[tuple[Path, str]] = []
        self.cancel_calls = 0
        self.shutdown_calls = 0

    def open(self, path: Path, kind: ReplayKind = ReplayKind.ALL) -> None:
        self.open_calls.append((path, kind))
        self.busy_changed.emit(True)
        self.index_ready.emit(
            RecordingIndex(
                str(path),
                (
                    ReplayIndexEntry(0, 16, 64, "spectrum", 41, 1_000),
                    ReplayIndexEntry(1, 80, 64, "iq", 42, 2_000),
                ),
                1_000,
                144,
            )
        )
        self.position_changed.emit(ReplayPosition(0, 0.0, 1_000))
        self.busy_changed.emit(False)

    def seek(self, fraction: float) -> None:
        self.seek_calls.append(fraction)
        self.busy_changed.emit(True)
        self.position_changed.emit(ReplayPosition(0, fraction, 1_000))
        self.busy_changed.emit(False)

    def read_next(self) -> None:
        self.read_calls += 1
        self.busy_changed.emit(True)
        self.frame_ready.emit(
            SpectrumFrame(
                41,
                1_000,
                np.linspace(100_000_000.0, 101_000_000.0, 4),
                np.asarray((-102.0, -91.0, -75.0, -96.0)),
                "dBFS/bin",
                "historical-recording",
            )
        )
        self.position_changed.emit(ReplayPosition(0, 1.0, 2_000))
        self.busy_changed.emit(False)

    def reprocess(self, path: Path, backend: str) -> None:
        self.reprocess_calls.append((path, backend))
        self.busy_changed.emit(True)
        self.reprocess_ready.emit(
            ReprocessResult(str(path), backend, "cpu", "completed", 9, str(path) + ".reprocessed.jsonl")
        )
        self.busy_changed.emit(False)

    def cancel_reprocess(self) -> None:
        self.cancel_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class Ui2ReplayProductTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._settings_dir = tempfile.TemporaryDirectory()
        self.live = FakePresenter()
        self.factory_calls = 0
        self.presenter: _ReplayPresenter | None = None

        def factory() -> _ReplayPresenter:
            self.factory_calls += 1
            self.presenter = _ReplayPresenter()
            return self.presenter

        self.composition = compose_v2_live_product(
            self.live,
            replay_presenter_factory=factory,
            now_ns=lambda: 1,
        )
        self.shell = AppShellV2(
            context=self.composition.context,
            settings=QSettings(
                os.path.join(self._settings_dir.name, "ui2.ini"),
                QSettings.Format.IniFormat,
            ),
        )
        self.shell.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        if self.shell.isVisible():
            self.shell.close()
        self.shell.deleteLater()
        self.app.processEvents()
        self._settings_dir.cleanup()

    def test_navigation_is_inert_then_explicit_spectrum_open_and_next_frame(self) -> None:
        home = self.shell._workspace_pages["home"]
        home._replay_card.button.click()
        self.app.processEvents()
        workspace = self._workspace()
        self.assertEqual(self.factory_calls, 0)
        self.assertFalse(self.composition.replay_view_model.open_spectrum_recording(""))
        self.assertEqual(self.factory_calls, 0)
        workspace._path.setText(r"C:\private\capture.sdrrec")
        workspace._open.click()
        self.app.processEvents()
        presenter = self._presenter()
        self.assertEqual(presenter.open_calls, [(Path(r"C:\private\capture.sdrrec"), ReplayKind.SPECTRUM)])
        self.assertNotIn("private", workspace._index_summary.text().casefold())
        workspace._next.click()
        self.app.processEvents()
        self.assertEqual(presenter.read_calls, 1)
        frame = workspace._scene.latest_frame
        self.assertIsInstance(frame, SpectrumFrame)
        self.assertEqual(frame.unit, "dBFS/bin")
        self.assertIn("1/1", workspace._position.text())

    def test_iq_is_rejected_before_qt_and_reprocess_result_redacts_paths(self) -> None:
        workspace = self._open_workspace()
        presenter = self._presenter()
        workspace._next.click()
        self.app.processEvents()
        displayed = workspace._scene.latest_frame
        presenter.frame_ready.emit(
            IQBlock(99, 9_999, np.asarray((1 + 1j, 2 + 2j)), 1_000_000.0, "replay")
        )
        self.app.processEvents()
        self.assertIs(workspace._scene.latest_frame, displayed)
        self.assertIn("spectra only", self.composition.replay_view_model.state.error)
        self.assertTrue(self.composition.replay_view_model.reprocess_iq("cuda"))
        self.app.processEvents()
        self.assertEqual(presenter.reprocess_calls[-1][1], "cuda")
        self.assertIn("capture.sdrrec.reprocessed.jsonl", workspace._reprocess_summary.text())
        self.assertNotIn("private", workspace._reprocess_summary.text().casefold())

    def test_close_refuses_busy_replay_then_shuts_deferred_presenter_once(self) -> None:
        self._open_workspace()
        presenter = self._presenter()
        presenter.busy_changed.emit(True)
        self.shell.close()
        self.assertTrue(self.shell.isVisible())
        self.assertEqual(presenter.shutdown_calls, 0)
        presenter.busy_changed.emit(False)
        self.shell.close()
        self.assertEqual(presenter.shutdown_calls, 1)
        self.assertEqual(self.live.shutdown_calls, 1)

    def _open_workspace(self) -> ReplayWorkspaceV2:
        self.shell._workspace_pages["home"]._replay_card.button.click()
        self.app.processEvents()
        workspace = self._workspace()
        workspace._path.setText(r"C:\private\capture.sdrrec")
        workspace._open.click()
        self.app.processEvents()
        return workspace

    def _workspace(self) -> ReplayWorkspaceV2:
        workspace = self.shell._workspace_pages["replay"]
        self.assertIsInstance(workspace, ReplayWorkspaceV2)
        return workspace

    def _presenter(self) -> _ReplayPresenter:
        self.assertIsNotNone(self.presenter)
        return self.presenter


if __name__ == "__main__":
    unittest.main()

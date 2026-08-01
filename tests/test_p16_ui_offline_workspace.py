"""P16UI-06 Offline DFL workspace, presenter wiring and shell tests.

The fixture mirrors ``tests/test_heatmap_integration.py``: an offscreen Qt
application, a synthetic session with a hand-built ``SpectrogramIndex`` and a
fake DFL file readable by ``SpectrogramFrameReader`` through a sector chain.
Worker synchronization is event-driven (``_wait_until`` pumps the event loop).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import ClassVar
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl.domain import MeasurementMetadata, MeasurementSession, SpectrumTrace, WaterfallData
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import SpectrogramFrameReader, SpectrogramIndex, SpectrogramRow
from esw_dfl.ui.app_shell import AppShell
from esw_dfl.ui.i18n import LocaleId, Translator, validate_catalogs
from esw_dfl.ui.offline_presenter import OfflineDflPresenter
from esw_dfl.ui.offline_workspace import OfflineDflWorkspace
from esw_dfl.ui.state import WorkspaceId

FRAME_COUNT = 40
FREQ_BINS = 32
SIGNAL_BIN = 16
SIGNAL_POWER = -30.0
NOISE_POWER = -90.0

# ChannelPowerResult has nine dataclass fields; results_snapshot() flattens
# result.values.items(), so one channel-power measurement produces nine rows.
CHANNEL_POWER_ROW_COUNT = 9


def _make_frames() -> list[np.ndarray]:
    frames = []
    for index in range(FRAME_COUNT):
        values = np.full(FREQ_BINS, NOISE_POWER - (index % 4), dtype=np.float32)
        values[SIGNAL_BIN] = SIGNAL_POWER
        frames.append(values)
    return frames


def _write_fake_dfl(
    root: Path, name: str, frames: list[np.ndarray]
) -> tuple[Path, SpectrogramInfo, SpectrogramIndex]:
    """Build a fake CFB-less file readable by SpectrogramFrameReader via a sector chain."""
    sector_size = 512
    stream = bytearray()
    offsets: list[int] = []
    lengths: list[int] = []
    for index, values in enumerate(frames):
        payload = base64.b64encode(np.ascontiguousarray(values, dtype="<f4").tobytes()).decode("ascii")
        line = (
            f'<SgramLine Line="{index}"><DataBlock Block="0" Data="' + payload + '"/></SgramLine>'
        ).encode("ascii")
        offsets.append(len(stream))
        lengths.append(len(line))
        stream += line
    sector_count = (len(stream) + sector_size - 1) // sector_size
    stream += b"\x00" * (sector_count * sector_size - len(stream))
    path = root / name
    path.write_bytes(b"\x00" * sector_size + bytes(stream))
    point_count = int(frames[0].size)
    info = SpectrogramInfo(
        key="waterfall",
        title="Waterfall",
        mode="RT",
        measurement="Spectrum",
        measurement_type="Spectrogram",
        source_stream="stream",
        line_count=len(frames),
        point_count=point_count,
        start_hz=100.0,
        stop_hz=100.0 + 100.0 * (point_count - 1),
    )
    index = SpectrogramIndex(
        info=info,
        line_indices=np.arange(len(frames), dtype=np.int64),
        timestamps=np.arange(len(frames), dtype=np.float64),
        offsets=np.asarray(offsets, dtype=np.int64),
        lengths=np.asarray(lengths, dtype=np.int32),
        sector_chain=np.arange(sector_count, dtype=np.int32),
        sector_size=sector_size,
    )
    return path, info, index


class OfflineWorkspaceTests(unittest.TestCase):
    """The offscreen workspace renders presenter snapshots and forwards intent."""

    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="p16ui06-offline-")
        root = Path(self._temporary.name)
        self._frames = _make_frames()
        self._dfl_path, self._info, self._index = _write_fake_dfl(root, "fake.dfl", self._frames)
        self._presenter = OfflineDflPresenter()
        self._session_id = self._add_session(self._presenter, "session-a", self._dfl_path, self._frames, self._index)
        self._workspace: OfflineDflWorkspace | None = None

    def tearDown(self) -> None:
        if self._workspace is not None:
            self._workspace.close()
            self._workspace.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self._presenter.close()
        # The heatmap controller's background worker runs on the global thread
        # pool and holds the fake DFL open until it exits; drain it before
        # removing the temporary directory (same recipe as shutdown_window).
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().waitForDone(5000)
        self.app.processEvents()
        self._temporary.cleanup()

    def _make(self) -> OfflineDflWorkspace:
        workspace = OfflineDflWorkspace(presenter=self._presenter, locale=LocaleId.EN)
        self._workspace = workspace
        return workspace

    @staticmethod
    def _add_session(
        presenter: OfflineDflPresenter,
        session_id: str,
        path: Path,
        frames: list[np.ndarray],
        index: SpectrogramIndex,
    ) -> str:
        session = MeasurementSession(session_id, path, session_id, MeasurementMetadata())
        waterfall = WaterfallData(
            "waterfall",
            "Waterfall",
            len(frames),
            int(frames[0].size),
            float(index.info.start_hz),
            float(index.info.stop_hz),
            (float(index.info.stop_hz) - float(index.info.start_hz)) / max(1, int(frames[0].size) - 1),
            "stream",
        )
        values = np.stack(frames)
        waterfall.set_preview(
            values, np.arange(len(frames), dtype=np.float64), np.arange(len(frames))
        )
        session.waterfalls[waterfall.waterfall_id] = waterfall
        session.active_waterfall_id = waterfall.waterfall_id
        trace = SpectrumTrace(
            "trace-1",
            "Trace 1",
            float(index.info.start_hz),
            float(index.info.stop_hz),
            waterfall.frequency_step_hz,
            frames[-1].copy(),
        )
        session.traces[trace.trace_id] = trace
        session.active_trace_id = trace.trace_id
        presenter.repository.add(session)
        presenter._spectrogram_indexes[(session_id, "waterfall")] = index
        presenter._frame_readers[(session_id, "waterfall")] = SpectrogramFrameReader(path, index)
        for frame, row_values in enumerate(values):
            presenter._frame_loader._cache[(session_id, "waterfall", frame)] = SpectrogramRow(
                frame, float(frame), row_values.copy()
            )
        return session_id

    def _wait_until(self, predicate, timeout_s: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    # ------------------------------------------------------------------
    # Tree and navigation
    # ------------------------------------------------------------------
    def test_tree_populated_with_session_trace_and_waterfall(self) -> None:
        workspace = self._make()

        self.assertEqual(workspace.tree.topLevelItemCount(), 1)
        top = workspace.tree.topLevelItem(0)
        self.assertEqual(top.data(0, Qt.ItemDataRole.UserRole), self._session_id)
        self.assertEqual(top.childCount(), 2)

    def test_frame_spin_reflects_frame_count_and_navigates(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)

        self.assertEqual(workspace.frame_spin.maximum(), FRAME_COUNT)
        self.assertEqual(workspace.frame_spin.value(), 1)

        workspace.frame_spin.setValue(6)
        self.assertTrue(self._wait_until(lambda: self._presenter._frame_nav.requested_frame == 5))
        self.assertEqual(self._presenter._frame_nav.requested_frame, 5)

    # ------------------------------------------------------------------
    # Markers
    # ------------------------------------------------------------------
    def test_marker_buttons_forward_to_presenter(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)

        workspace.add_marker_button.click()
        workspace.add_peak_marker_button.click()
        workspace.add_delta_marker_button.click()

        self.assertEqual(len(self._presenter.markers_snapshot()), 3)

    def test_remove_marker_uses_markers_table_selection(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)
        workspace.add_marker_button.click()
        workspace.add_marker_button.click()
        self.assertEqual(len(self._presenter.markers_snapshot()), 2)

        workspace._markers_table.selectRow(1)
        workspace.remove_marker_button.click()

        self.assertEqual(len(self._presenter.markers_snapshot()), 1)
        self.assertNotIn(
            workspace._markers_table.currentRow(),
            self._presenter.markers_snapshot(),
        )

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def test_playback_toggle_and_stop(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)

        workspace._play_button.click()
        self.assertTrue(self._presenter.playback_timer.isActive())
        workspace._play_button.click()
        self.assertFalse(self._presenter.playback_timer.isActive())
        workspace._play_button.click()
        workspace._stop_button.click()
        self.assertFalse(self._presenter.playback_timer.isActive())

    def test_loop_and_no_skip_checkboxes_forward(self) -> None:
        workspace = self._make()

        workspace._loop_check.setChecked(True)
        self.assertTrue(self._presenter._playback_loop)
        workspace._no_skip_check.setChecked(True)
        self.assertTrue(self._presenter._playback_no_skip)

    # ------------------------------------------------------------------
    # Heatmap
    # ------------------------------------------------------------------
    def test_heatmap_enable_disable_via_buttons(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)

        workspace.heatmap_enable_button.click()
        self.assertTrue(self._wait_until(lambda: self._presenter.snapshot().heatmap.enabled))
        workspace.heatmap_disable_button.click()
        self.assertFalse(self._presenter.snapshot().heatmap.enabled)

    # ------------------------------------------------------------------
    # Measurements / results
    # ------------------------------------------------------------------
    def test_channel_power_measurement_populates_results_table(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)

        self._presenter.measure_channel_power(0.0001, 0.0003)
        self.assertTrue(
            self._wait_until(lambda: len(self._presenter.results_snapshot()) > 0)
        )

        self.assertEqual(len(self._presenter.results_snapshot()), CHANNEL_POWER_ROW_COUNT)
        self.assertEqual(workspace._results_table.rowCount(), CHANNEL_POWER_ROW_COUNT)

    def test_remove_result_uses_results_table_selection(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)
        self._presenter.measure_channel_power(0.0001, 0.0003)
        self.assertTrue(
            self._wait_until(lambda: len(self._presenter.results_snapshot()) > 0)
        )
        self.assertEqual(len(self._presenter.results_snapshot()), CHANNEL_POWER_ROW_COUNT)

        workspace._results_table.selectRow(0)
        workspace.remove_result_button.click()

        self.assertEqual(len(self._presenter.results_snapshot()), 0)
        self.assertEqual(workspace._results_table.rowCount(), 0)

    def test_clear_results_forwards_to_presenter(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)
        self._presenter.measure_channel_power(0.0001, 0.0003)
        self.assertTrue(
            self._wait_until(lambda: len(self._presenter.results_snapshot()) > 0)
        )

        workspace.clear_results_button.click()

        self.assertEqual(len(self._presenter.results_snapshot()), 0)

    # ------------------------------------------------------------------
    def test_peak_marker_at_limit_does_not_mutate_existing_marker(self) -> None:
        self._make()
        self._presenter.set_active_session(self._session_id)
        for _ in range(10):
            self._presenter.add_marker()
        before = tuple((marker.marker_id, marker.marker_type) for marker in self._presenter.active_session().markers)

        self._presenter.add_peak_marker()

        after = tuple((marker.marker_id, marker.marker_type) for marker in self._presenter.active_session().markers)
        self.assertEqual(after, before)

    def test_time_gated_request_uses_one_public_worker_pipeline(self) -> None:
        class CachedService:
            def __init__(self) -> None:
                self.cache = SimpleNamespace(get=lambda _request: object())

            def analyze(self, request, _frequencies, _rows, _override, _cancel):
                return SimpleNamespace(
                    request=request,
                    calculation_quality=SimpleNamespace(value="exact"),
                    frame_count_valid=FRAME_COUNT,
                    events=(),
                )

        self._presenter.time_gated_service = CachedService()
        self._presenter.set_active_session(self._session_id)
        self.assertTrue(self._presenter.request_time_gated_power())
        self.assertTrue(self._wait_until(lambda: bool(self._presenter._channel_power_results)))
        self.assertIsNotNone(self._presenter._current_time_gated_result())

    def test_heatmap_cancel_is_disabled_when_idle(self) -> None:
        workspace = self._make()
        self.assertFalse(workspace.heatmap_cancel_button.isEnabled())
    # Export and shutdown
    # ------------------------------------------------------------------
    def test_export_menu_contains_actions(self) -> None:
        workspace = self._make()

        self.assertTrue(workspace._export_menu.actions())

    def test_close_stops_playback_timer(self) -> None:
        workspace = self._make()
        self._presenter.set_active_session(self._session_id)
        workspace._play_button.click()
        self.assertTrue(self._presenter.playback_timer.isActive())

        workspace.close()

        self.assertFalse(self._presenter.playback_timer.isActive())


class OfflineAppShellIntegrationTests(unittest.TestCase):
    """The shell lazily attaches one factory-built Offline DFL workspace."""

    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        if hasattr(self, "_workspace"):
            self._workspace.close()
            self._workspace.deleteLater()
        if hasattr(self, "_shell"):
            self._shell.close()
            self._shell.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_offline_dfl_factory_attaches_once(self) -> None:
        self._workspace = OfflineDflWorkspace(locale=LocaleId.EN)
        self._shell = AppShell(offline_dfl_factory=lambda: self._workspace)

        self._shell.set_active_workspace(WorkspaceId.OFFLINE_DFL)
        first = self._shell.attached_workspace(WorkspaceId.OFFLINE_DFL)
        self._shell.set_active_workspace(WorkspaceId.OFFLINE_DFL)

        self.assertIsInstance(first, OfflineDflWorkspace)
        self.assertIs(first, self._workspace)
        self.assertIs(self._shell.attached_workspace(WorkspaceId.OFFLINE_DFL), first)

    def test_offline_dfl_workspace_keeps_placeholder_without_factory(self) -> None:
        self._shell = AppShell()

        self._shell.set_active_workspace(WorkspaceId.OFFLINE_DFL)

        self.assertIsNone(self._shell.attached_workspace(WorkspaceId.OFFLINE_DFL))


class OfflineWorkspaceI18nTests(unittest.TestCase):
    """Translation catalogs stay complete for the Offline DFL workspace."""

    def test_catalogs_validate(self) -> None:
        validate_catalogs()

    def test_offline_keys_translate_in_both_locales(self) -> None:
        keys = {
            "offline.files",
            "offline.sessions",
            "offline.open_files",
            "offline.playback",
            "offline.frame",
            "offline.heatmap",
            "offline.heatmap_enable",
            "offline.markers",
            "offline.results",
            "offline.properties",
            "offline.export",
            "offline.add_marker",
            "offline.remove_marker",
            "offline.clear_results",
            "offline.export_trace",
            "offline.export_heatmap_png",
        }
        for locale in (LocaleId.RU, LocaleId.EN):
            translator = Translator(locale)
            for key in keys:
                self.assertTrue(translator.text(key), f"{key!r} missing in {locale}")


if __name__ == "__main__":
    unittest.main()

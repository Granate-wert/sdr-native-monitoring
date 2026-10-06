"""One V2 renderer consumes both actual domain publication variants."""
import os
import time
import unittest
from unittest.mock import patch

import numpy as np
import shiboken6

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer import AnalyzerPublicationKind, bundle_from_live, bundle_from_sweep
from sdr_monitor.domain.live import LiveSnapshot, LiveSpectrumFrame, LiveSessionState
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.native_continuous_sweep import (
    ContinuousSweepDisplaySnapshot, ContinuousSweepDisplayMetrics,
)
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.spectrum.contracts import TraceKind


class _ObservedScene(SpectrumScene):
    def __init__(self):
        super().__init__()
        self.close_calls = 0
        self.delete_calls = 0
        self.release_calls = 0

    def close(self):
        self.close_calls += 1
        return super().close()

    def deleteLater(self):
        self.delete_calls += 1
        return super().deleteLater()

    def release_graphics_after_shutdown(self):
        self.release_calls += 1
        return super().release_graphics_after_shutdown()


class AnalyzerBundleSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def retire_scene(self, scene, *, presenter=None):
        """Finish the direct fixture after its producer, before Qt deletes axes."""
        if presenter is not None:
            presenter.shutdown()
            self.assertTrue(presenter._closed)
        plot = scene.plot_item
        view_box = scene.view_box
        self.assertTrue(shiboken6.isValid(scene))
        self.assertFalse(scene._graphics_terminal_released)
        fresh_plot = plot.ctrlMenu is not None
        axis = plot.getAxis("left") if plot.axes is not None else None
        axis_label = axis.label if axis is not None else None
        predelete_child_count = len(view_box.childGroup.childItems())
        predelete_added_count = len(view_box.addedItems)
        if fresh_plot:
            self.assertIsNotNone(plot.axes)
            self.assertIsNotNone(axis)
            self.assertIsNotNone(axis.label)
        scene.release_graphics_after_shutdown()
        self.assertTrue(scene._graphics_terminal_released)
        self.assertFalse(scene._projection_timer.isActive())
        self.assertIsNone(plot.ctrlMenu)
        self.assertIsNone(plot.axes)
        self.assertIsNone(plot.vb)
        self.assertEqual(plot.items, [])
        self.assertEqual(plot.dataItems, [])
        self.assertEqual(plot.curves, [])
        self.assertFalse(shiboken6.isValid(view_box))
        self.assertGreaterEqual(predelete_child_count + predelete_added_count, 0)
        if axis is not None:
            self.assertFalse(shiboken6.isValid(axis))
        if axis_label is not None:
            self.assertFalse(shiboken6.isValid(axis_label))
        scene.release_graphics_after_shutdown()  # Terminal idempotence.
        scene.close()
        scene.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertFalse(shiboken6.isValid(scene))

    def _finish_injected_fixture(self, scene, presenter=None):
        """Test-owned release after fault assertions, never a helper auto-retry."""
        if presenter is not None and not presenter._closed:
            presenter.shutdown()
        if shiboken6.isValid(scene):
            if not scene._graphics_terminal_released:
                scene.release_graphics_after_shutdown()
            scene.close()
            scene.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_rtbw_then_gapped_sweep_use_the_same_scene(self):
        live = LiveSpectrumFrame(
            sequence=1, timestamp_ns=1, center_frequency_hz=101.,
            sample_rate_hz=3., fft_size=3, hop_size=1,
            frequencies_hz=np.array([100., 101., 102.]),
            values=np.array([-80., -40., -70.]), unit="dBFS/Hz",
        )
        sweep = SweepLineFrame(
            sequence=1, epoch=2, completed_at_ns=900, source_id="sweep",
            state=SweepLineState.GAP, frequencies_hz=np.array([200., 201., 202.]),
            values_db=np.array([-90., np.nan, -30.]),
            quality_flags=np.array([0, 1, 0], dtype=np.uint16),
            source_segment_indices=np.array([0, -1, 1]),
            missing_segment_indices=(2,), segment_config_generations=((0, 1), (1, 2)),
            gap_reasons=(), unit="dBFS/bin",
        )
        bundles = (
            bundle_from_live(LiveSnapshot(
                generation=1, sequence=1, state=LiveSessionState.RUNNING, spectrum=live,
            )),
            bundle_from_sweep(sweep),
        )
        scene = SpectrumScene()
        self.addCleanup(self.retire_scene, scene)
        for bundle in bundles:
            scene.set_frame(bundle)
            envelope = scene.trace_envelope(TraceKind.CURRENT)
            np.testing.assert_array_equal(envelope.values, bundle.values)
            np.testing.assert_array_equal(envelope.frequencies_hz, bundle.frequencies_hz)
            self.assertEqual(scene._latest_view.unit_label, bundle.unit)
            self.assertIs(scene._latest_view.source_frame, bundle)
        self.assertTrue(np.isnan(scene.trace_envelope(TraceKind.CURRENT).values[1]))

    def test_presenter_renders_progress_before_terminal_line(self):
        frequency = np.array([200., 201., 202.])
        values = np.array([-90., -80., np.nan], dtype=np.float32)
        quality = np.array([0, 0, 4096], dtype=np.uint32)
        indices = np.array([0, 0, -1], dtype=np.int32)
        for array in (frequency, values, quality, indices):
            array.setflags(write=False)
        progress = SweepProgressFrame(
            source_id="test", sequence=1, epoch=2, revision=1, unit="dBFS/bin",
            frequencies_hz=frequency, values_db=values, quality_flags=quality,
            source_segment_indices=indices,
            acquired_segment_generations=((0, 1),), pending_segment_indices=(1,),
        )
        class Service:
            def start(self, _config):
                pass

            def poll_latest(self):
                return ContinuousSweepDisplaySnapshot(
                    None, ContinuousSweepDisplayMetrics(), progress,
                )

            def stop(self):
                pass

            def close(self):
                pass

        presenter = ContinuousSweepPresenter(Service())
        scene = SpectrumScene()
        self.addCleanup(self.retire_scene, scene, presenter=presenter)
        lines = []
        presenter.line_ready.connect(lines.append)
        presenter.analyzer_ready.connect(scene.set_frame)
        presenter.start(object())
        deadline = time.monotonic() + 1
        while presenter.is_starting and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        self.assertFalse(presenter.is_starting)
        presenter._poll()
        while scene.latest_frame is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        self.assertEqual(lines, [])
        envelope = scene.trace_envelope(TraceKind.CURRENT)
        np.testing.assert_array_equal(envelope.values, values)
        self.assertEqual(scene._latest_view.source_frame.spectrum.revision, 1)
        self.assertIs(scene._latest_view.source_frame.publication_kind,
                      AnalyzerPublicationKind.SWEEP_PROGRESS)
        self.assertFalse(scene._latest_view.source_frame.terminal_sweep)
        self.assertEqual(scene._latest_view.unit_label, "dBFS/bin")

    def test_failed_presenter_join_retains_same_scene_for_explicit_retry(self):
        class Service:
            close_calls = 0

            def close(self):
                self.close_calls += 1
                if self.close_calls == 1:
                    raise RuntimeError("producer close not acknowledged")

        service = Service()
        presenter = ContinuousSweepPresenter(service)
        scene = _ObservedScene()
        presenter.analyzer_ready.connect(scene.set_frame)
        try:
            with self.assertRaisesRegex(RuntimeError, "producer close not acknowledged"):
                self.retire_scene(scene, presenter=presenter)
            self.assertFalse(presenter._closed)
            self.assertTrue(shiboken6.isValid(scene))
            self.assertFalse(scene._graphics_terminal_released)
            self.assertEqual((scene.release_calls, scene.close_calls, scene.delete_calls), (0, 0, 0))
            self.assertIsNotNone(scene.plot_item.axes)
            self.retire_scene(scene, presenter=presenter)  # Explicit second attempt, same objects.
            self.assertEqual((service.close_calls, scene.close_calls, scene.delete_calls), (2, 1, 1))
            self.assertTrue(presenter._closed)
            self.assertFalse(shiboken6.isValid(scene))
        finally:
            self._finish_injected_fixture(scene, presenter)

    def test_partial_graphics_retirement_retains_same_scene_for_explicit_retry(self):
        scene = _ObservedScene()
        axis = scene.plot_item.getAxis("left")
        original_close = axis.close
        attempts = []

        def fail_once():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("axis retirement not acknowledged")
            original_close()

        try:
            with patch.object(axis, "close", side_effect=fail_once):
                with self.assertRaisesRegex(RuntimeError, "axis retirement not acknowledged"):
                    self.retire_scene(scene)
                self.assertTrue(shiboken6.isValid(scene))
                self.assertFalse(scene._graphics_terminal_released)
                self.assertEqual((scene.close_calls, scene.delete_calls), (0, 0))
                self.assertIsNotNone(scene.plot_item.axes)
                self.retire_scene(scene)  # Partial PlotItem close resumes on same scene.
            self.assertEqual((scene.close_calls, scene.delete_calls), (1, 1))
            self.assertFalse(shiboken6.isValid(scene))
        finally:
            self._finish_injected_fixture(scene)

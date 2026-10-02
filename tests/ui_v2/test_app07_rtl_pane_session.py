"""RTL pane readout never promotes a retained or different-activation frame."""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, RtbwFrameMetadata
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneCrop
from sdr_monitor.services.pane_resource_session import PaneActivation
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from sdr_monitor.ui.v2_pane_presentation import PanePresentationBinding
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase, PanePumpResourceState


def _frame_bundle() -> AnalyzerFrameBundle:
    center, rate, fft = 101_000_000.0, 2_400_000.0, 1024
    frequencies = center + (np.arange(fft, dtype=np.float64) - fft // 2) * (rate / fft)
    frame = LiveSpectrumFrame(
        sequence=1, timestamp_ns=1, center_frequency_hz=center, sample_rate_hz=rate,
        fft_size=fft, hop_size=fft // 2, frequencies_hz=frequencies,
        values=np.zeros(fft, dtype=np.float32), source_id="rtl-source",
        config_generation=3, receiver_id="rtl-rx", acquisition_epoch=2,
        unit="dBFS/bin",
    )
    return AnalyzerFrameBundle(
        frame, "rtl-session", "rtl-rx", 2, RtbwFrameMetadata(center, rate, fft, fft // 2))


def _binding() -> PanePresentationBinding:
    return PanePresentationBinding(
        4, "pane-4", "pane-resource-4", "rtl-capture", "rtl-endpoint", "rtl-source",
        PaneCrop("pane-4", "rtl-endpoint", 100_500_000, 101_500_000),
        100_500_000, 101_500_000, AnalyzerMode.RTBW, CaptureMeasurementMode.RTBW,
        "dBFS/bin",
    )


def _state(phase: PanePumpPhase, serial: int) -> PanePumpResourceState:
    activation = PaneActivation("pane-resource-4", "rtl-capture", 0, serial, None, None)
    return PanePumpResourceState("pane-resource-4", phase, activation)


class RtlPaneSessionReadoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_locale = current_locale()
        set_active_locale(UiLocale.EN)

    def tearDown(self) -> None:
        set_active_locale(self.original_locale)

    def test_same_activation_accepted_frame_reports_actual_center_and_fs(self) -> None:
        result = IndependentPaneSessionV2._rtl_actual_readout(
            _frame_bundle(), _state(PanePumpPhase.RUNNING, 7), _binding(), 7)
        self.assertIn("actual Fs 2.4 MS/s", result)
        self.assertIn("center 101 MHz", result)
        self.assertIn("this Start", result)

    def test_new_activation_cannot_reuse_retained_readback(self) -> None:
        bundle, binding = _frame_bundle(), _binding()
        for phase, serial, accepted in (
            (PanePumpPhase.RUNNING, 8, 7),
            (PanePumpPhase.STARTING, 8, 7),
            (PanePumpPhase.RUNNING, 8, None),
        ):
            with self.subTest(phase=phase, serial=serial, accepted=accepted):
                result = IndependentPaneSessionV2._rtl_actual_readout(
                    bundle, _state(phase, serial), binding, accepted)
                self.assertIn("unknown", result)
                self.assertNotIn("actual Fs 2.4", result)

    def test_stopped_retained_frame_is_explicitly_not_live_actual(self) -> None:
        result = IndependentPaneSessionV2._rtl_actual_readout(
            _frame_bundle(), _state(PanePumpPhase.STOPPED, 7), _binding(), 7)
        self.assertIn("retained after Stop", result)
        self.assertIn("unknown", result)

    def test_mismatched_source_capture_epoch_or_unit_refuses_actual(self) -> None:
        bundle = _frame_bundle()
        variants = (
            (bundle, replace(_binding(), source_id="other")),
            (bundle, replace(_binding(), capture_id="other")),
            (cast(AnalyzerFrameBundle, SimpleNamespace(
                spectrum=bundle.spectrum, identity=bundle.identity, rtbw=bundle.rtbw,
                session_id="other", acquisition_epoch=bundle.acquisition_epoch)), _binding()),
            (cast(AnalyzerFrameBundle, SimpleNamespace(
                spectrum=bundle.spectrum, identity=bundle.identity, rtbw=bundle.rtbw,
                session_id=bundle.session_id, acquisition_epoch=3)), _binding()),
            (bundle, replace(_binding(), unit="dBm")),
        )
        for candidate, binding in variants:
            with self.subTest(candidate=candidate, binding=binding):
                result = IndependentPaneSessionV2._rtl_actual_readout(
                    candidate, _state(PanePumpPhase.RUNNING, 7), binding, 7)
                self.assertIn("unknown", result)

    def test_board_exposes_only_accepted_delivery_serial(self) -> None:
        board = SimpleNamespace(_last_order={4: (7, 2, 1, 0, 0)})
        self.assertEqual(IndependentPaneBoardV2.accepted_activation_serial(board, 4), 7)
        self.assertIsNone(IndependentPaneBoardV2.accepted_activation_serial(board, 1))


if __name__ == "__main__":
    unittest.main()

"""APP-07 fair bounded UI handoff; supersession is not analytical RF loss."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneCrop
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2_pane_presentation import PanePresentationBinding, PreparedPaneDelivery

from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal


def packet(pane_id: str, frame, *, serial: int = 1) -> PreparedPaneDelivery:
    source_id = f"{pane_id}:source"
    frame = replace(frame, source_id=source_id)
    bundle = bundle_from_sweep(frame)
    crop = PaneCrop(pane_id, f"{pane_id}:rx", 100e6, 103e6)
    binding = PanePresentationBinding(
        1, pane_id, f"{pane_id}:physical", f"{pane_id}:capture",
        crop.receiver_endpoint_id, source_id, crop, 100e6, 103e6,
        AnalyzerMode.SWEEP, CaptureMeasurementMode.SWEEP, "dBFS/bin")
    delivery = PaneDelivery(binding.physical_stream_resource_id,
                            binding.capture_id, binding.receiver_endpoint_id,
                            serial, crop, bundle, 1.0)
    return PreparedPaneDelivery(binding, delivery, PreparedSpectrumFrame(bundle),
                                waterfall_line_from_sweep(frame), None)


class PaneFairDeliveryQueueTests(unittest.TestCase):
    def test_terminal_pass_survives_newer_progress_in_same_bounded_pane(self) -> None:
        queue = PaneFairDeliveryQueue(("one",))
        line = packet("one", terminal(1))
        preview = packet("one", progress(2))
        self.assertTrue(queue.offer(line))
        self.assertTrue(queue.offer(preview))
        self.assertEqual(queue.pending_count, 2)
        self.assertIs(queue.drain(max_items=1)[0], line)
        self.assertIs(queue.drain(max_items=1)[0], preview)
        self.assertEqual(queue.metrics().terminal_superseded, 0)
        self.assertEqual(queue.metrics().delivered, 2)

    def test_late_terminal_precedes_already_queued_next_pass(self) -> None:
        queue = PaneFairDeliveryQueue(("one",))
        preview = packet("one", progress(2))
        line = packet("one", terminal(1))
        self.assertTrue(queue.offer(preview))
        self.assertTrue(queue.offer(line))
        self.assertEqual(queue.drain(max_items=1), (line,))
        self.assertEqual(queue.drain(max_items=1), (preview,))

    def test_latest_bursts_are_bounded_and_round_robin_is_fair(self) -> None:
        queue = PaneFairDeliveryQueue(("one", "two", "three"))
        for sequence in range(1, 101):
            self.assertTrue(queue.offer(packet("one", progress(sequence))))
        second = packet("two", progress(1))
        third = packet("three", progress(1))
        self.assertTrue(queue.offer(second))
        self.assertTrue(queue.offer(third))
        self.assertEqual(queue.pending_count, 3)
        first_batch = queue.drain(max_items=2)
        self.assertEqual(tuple(item.delivery.pane_id for item in first_batch), ("one", "two"))
        self.assertEqual(first_batch[0].bundle.spectrum.sequence, 100)
        self.assertEqual(queue.drain(max_items=1), (third,))
        self.assertEqual(queue.metrics().latest_superseded, 99)
        self.assertEqual(queue.metrics().delivered, 3)

    def test_terminal_pressure_is_counted_not_silent_or_called_rf_loss(self) -> None:
        queue = PaneFairDeliveryQueue(("one",))
        for sequence in (1, 2, 3):
            self.assertTrue(queue.offer(packet("one", terminal(sequence))))
        self.assertEqual(queue.pending_count, 1)
        self.assertEqual(queue.metrics().terminal_superseded, 2)
        self.assertEqual(queue.drain()[0].bundle.spectrum.sequence, 3)
        self.assertFalse(queue.offer(packet("one", terminal(2))))
        self.assertEqual(queue.metrics().stale_rejected, 1)

    def test_clear_after_stop_keeps_stale_rejection_and_unknown_pane_refuses(self) -> None:
        queue = PaneFairDeliveryQueue(("one", "two"))
        first = packet("one", progress(1))
        second = packet("two", progress(1))
        queue.offer(first)
        queue.offer(second)
        queue.clear("one")
        self.assertEqual(queue.pending_count, 1)
        self.assertEqual(queue.metrics().cleared_on_stop, 1)
        self.assertEqual(queue.drain(), (second,))
        self.assertFalse(queue.offer(second))
        with self.assertRaisesRegex(ValueError, "unknown pane"):
            queue.clear("absent")
        with self.assertRaisesRegex(ValueError, "no queued pane"):
            PaneFairDeliveryQueue(("other",)).offer(first)


if __name__ == "__main__":
    unittest.main()

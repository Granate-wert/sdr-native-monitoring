"""Final native presentation disposal; not hardware or all-offers paint proof."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.analytical_journal import JournalState
from sdr_monitor.services.native_owner_journal import discard_terminal_owner_presentation
from tests import test_app07_native_presentation as fixture


class OwnerReleaseTests(unittest.TestCase):
    def test_legacy_is_unknown_without_call(self):
        owner = SimpleNamespace(discard_terminal_spectrum_frames=lambda: self.fail("legacy call"))
        self.assertIsNone(discard_terminal_owner_presentation(SimpleNamespace(), owner))

    def test_protocol_is_strict_and_missing_method_refuses(self):
        for version in (True, "1", 0, 2):
            with self.subTest(version=version), self.assertRaises(ValueError):
                discard_terminal_owner_presentation(
                    SimpleNamespace(OWNER_PRESENTATION_RELEASE_CONTRACT_VERSION=version), object())
        with self.assertRaises(ValueError):
            discard_terminal_owner_presentation(
                SimpleNamespace(OWNER_PRESENTATION_RELEASE_CONTRACT_VERSION=1), object())

    def test_actual_receipt_not_synthetic_zero(self):
        native = SimpleNamespace(OWNER_PRESENTATION_RELEASE_CONTRACT_VERSION=1)
        for value in (True, -1, 1 << 64, "0", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                discard_terminal_owner_presentation(native,
                    SimpleNamespace(discard_terminal_spectrum_frames=lambda: value))
        for value in (0, 4, (1 << 64) - 1):
            self.assertEqual(discard_terminal_owner_presentation(native,
                SimpleNamespace(discard_terminal_spectrum_frames=lambda: value)), value)

    def test_native_refusal_propagates_from_pure_helper(self):
        def refuse():
            raise RuntimeError("Stop not acknowledged")
        with self.assertRaises(RuntimeError):
            discard_terminal_owner_presentation(SimpleNamespace(OWNER_PRESENTATION_RELEASE_CONTRACT_VERSION=1),
                SimpleNamespace(discard_terminal_spectrum_frames=refuse))

    def test_disposal_precedes_terminal_snapshot_capture(self):
        consumer, _, _, outcomes, read = fixture.NativePresentationTests().make(count=5)
        order = []

        def dispose():
            order.append("dispose")
            outcomes["cancelled"] = 5
            return 5

        def capture(limit):
            order.append("capture")
            return read(limit)

        consumer.finish(capture, before_capture=dispose)
        snapshot = consumer.current()
        self.assertEqual(order[0], "dispose")
        self.assertEqual(snapshot.state, JournalState.FINAL)
        self.assertEqual(snapshot.native_presentation.cancelled, 5)
        self.assertEqual(snapshot.native_owner_handoffs_unclassified, 0)
        self.assertFalse(snapshot.native_presentation_release_failed)

    def test_disposal_failure_keeps_native_counters_and_incomplete(self):
        consumer, _, _, _, read = fixture.NativePresentationTests().make(count=5)

        def refuse():
            raise RuntimeError("ambiguous disposal")

        consumer.finish(read, before_capture=refuse)
        snapshot = consumer.current()
        self.assertEqual(snapshot.state, JournalState.INCOMPLETE)
        self.assertTrue(snapshot.native_stop_confirmed)
        self.assertTrue(snapshot.native_presentation_release_failed)
        self.assertEqual(snapshot.counters.handed_off, 5)
        self.assertEqual(snapshot.native_owner_handoffs_unclassified, 5)
        with self.assertRaises(ValueError):
            replace(snapshot, state=JournalState.FINAL)
        consumer.finish(read)
        self.assertTrue(consumer.current().native_presentation_release_failed)
        self.assertEqual(consumer.current().state, JournalState.INCOMPLETE)

    def test_snapshot_release_failure_flag_is_typed(self):
        consumer, _, _, _, _ = fixture.NativePresentationTests().make()
        with self.assertRaises(ValueError):
            replace(consumer.current(), native_presentation_release_failed=1)

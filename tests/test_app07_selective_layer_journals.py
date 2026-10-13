"""Exact spectrum-only optional journal-read boundary; no hardware or Qt."""
from dataclasses import fields, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.services.pane_resource_session import PaneDelivery, PaneResourceSession
from tests import test_app07_pane_analytical_identity as analytic
from tests import test_app07_pane_resource_session as graph


class SelectiveLayerJournalTests(unittest.TestCase):
    def setUp(self):
        helper = graph.PaneResourceSessionTests()
        helper.setUp()
        self.owner = analytic.ScopedOwner()
        groups = (graph.group("device", "rx"),)
        panes = (
            graph.pane("first", "rx", 100e6, 108e6, ReceiverBindingMode.SHARED_CAPTURE),
            graph.pane("second", "rx", 120e6, 128e6),
        )
        self.session = helper.session(groups, panes, {"device": self.owner},
                                      mode=CaptureMeasurementMode.RTBW)
        self.session.apply()
        self.activation = self.session.start_resource("device")
        self.owner.layer_journal_snapshots = Mock(return_value=())
        self.addCleanup(self.session.stop_all)

    def bundle(self):
        base = graph.live_frame(self.owner.scope.source_id, self.owner.scope.session_id,
                                self.owner.scope.acquisition_epoch, center_hz=114e6)
        spectrum = replace(base.spectrum, detector_ready=analytic.ready(self.owner.scope))
        return replace(base, spectrum=spectrum)

    def assert_original_shared_refs(self, deliveries):
        self.assertEqual(tuple(item.pane_id for item in deliveries), ("first", "second"))
        self.assertTrue(all(item.obligation_ref is not None for item in deliveries))
        self.assertTrue(all(len(item.layer_obligation_refs) == 1 for item in deliveries))
        self.assertIsNot(deliveries[0].obligation_ref, deliveries[1].obligation_ref)
        self.assertIsNot(deliveries[0].layer_obligation_refs[0],
                         deliveries[1].layer_obligation_refs[0])

    def test_owner_poll_spectrum_only_preserves_two_panes_and_skips_unused_read(self):
        bundle = self.bundle()
        self.owner.publications.append(("rx", bundle))
        deliveries = self.session.poll_resource("device")
        self.assert_original_shared_refs(deliveries)
        self.assertTrue(all(item.bundle is bundle for item in deliveries))
        self.owner.layer_journal_snapshots.assert_not_called()

    def test_direct_shared_fanout_preserves_separate_refs_and_skips_unused_read(self):
        deliveries = self.session.accept_frame(self.activation, "rx", self.bundle())
        self.assert_original_shared_refs(deliveries)
        self.owner.layer_journal_snapshots.assert_not_called()

    def test_empty_exact_binding_has_no_getter_call(self):
        self.assertEqual(self.session._bind_delivery_obligations(()), ())
        self.owner.layer_journal_snapshots.assert_not_called()

    def test_generic_container_falls_back_to_original_getter(self):
        pending = self.session._accept_frame(self.activation, "rx", self.bundle(), prepare_pair=True)
        self.assertEqual(len(pending), 2)
        bound = self.session._bind_delivery_obligations(list(pending))
        self.assert_original_shared_refs(bound)
        self.assertEqual(self.owner.layer_journal_snapshots.call_count, 1)

    def test_pane_delivery_subclass_falls_back_to_original_getter(self):
        class ChildDelivery(PaneDelivery):
            pass

        item = self.session._accept_frame(self.activation, "rx", self.bundle(), prepare_pair=True)[0]
        child = ChildDelivery(
            item.physical_stream_resource_id, item.capture_id, item.receiver_endpoint_id,
            item.host_activation_serial, item.crop, item.bundle, item.host_received_monotonic_s,
            item.host_run_serial, item.analytical_identity,
        )
        bound = self.session._bind_delivery_obligations((child,))
        self.assertEqual(len(bound), 1)
        self.assertIsInstance(bound[0], ChildDelivery)
        self.assertIsNotNone(bound[0].obligation_ref)
        self.assertEqual(self.owner.layer_journal_snapshots.call_count, 1)

    def test_mixed_exact_batch_keeps_whole_prepass_for_all_resources_in_both_orders(self):
        pending = self.session._accept_frame(self.activation, "rx", self.bundle(), prepare_pair=True)
        spectrum = pending[0]
        sweep_bundle = graph.frame("sweep-source", 3, 100e6, 128e6)
        sweep = PaneDelivery("sweep-resource", "sweep-capture", "rx", 1, spectrum.crop,
                             sweep_bundle, 0.0, 1)
        events = []
        spectrum_owner = self.owner
        spectrum_owner.layer_journal_snapshots = Mock(
            side_effect=lambda: (events.append("spectrum") or ()))
        sweep_owner = SimpleNamespace(layer_journal_snapshots=
                                      Mock(side_effect=lambda: (events.append("sweep") or ())))
        original_runtimes = self.session._runtimes
        original_ledger = self.session._delivery_ledger
        self.session._runtimes = {
            **original_runtimes,
            "sweep-resource": SimpleNamespace(owner=sweep_owner, admission=None),
        }
        self.addCleanup(setattr, self.session, "_runtimes", original_runtimes)
        self.addCleanup(setattr, self.session, "_delivery_ledger", original_ledger)

        for inputs, expected in (((spectrum, sweep), ["spectrum", "sweep"]),
                                 ((sweep, spectrum), ["sweep", "spectrum"])):
            with self.subTest(order=expected):
                self.session._delivery_ledger = PaneDeliveryLedger(("first", "second"))
                events.clear()
                spectrum_owner.layer_journal_snapshots.reset_mock()
                sweep_owner.layer_journal_snapshots.reset_mock()
                result = PaneResourceSession._bind_delivery_obligations(self.session, inputs)
                self.assertEqual(events, expected)
                self.assertEqual(tuple(item.physical_stream_resource_id for item in result),
                                 tuple(item.physical_stream_resource_id for item in inputs))
                self.assertIs(result[expected.index("spectrum")].bundle, spectrum.bundle)
                self.assertIs(result[expected.index("sweep")].bundle, sweep_bundle)
                spectrum_result = result[expected.index("spectrum")]
                self.assertIs(spectrum_result.obligation_ref.identity, spectrum.analytical_identity)
                self.assertEqual(len(spectrum_result.layer_obligation_refs), 1)
                self.assertIs(spectrum_result.layer_obligation_refs[0].identity,
                              spectrum.analytical_identity)
                self.assertIsNot(spectrum_result.obligation_ref,
                                 spectrum_result.layer_obligation_refs[0])
                spectrum_owner.layer_journal_snapshots.assert_called_once_with()
                sweep_owner.layer_journal_snapshots.assert_called_once_with()

    def test_nested_subclasses_and_custom_bundle_error_fall_back_after_prepass(self):
        pending = self.session._accept_frame(self.activation, "rx", self.bundle(), prepare_pair=True)[0]

        class ChildBundle(AnalyzerFrameBundle):
            __slots__ = ()

        class ChildSpectrum(type(pending.bundle.spectrum)):
            __slots__ = ()

        child_spectrum = ChildSpectrum(**{
            field.name: getattr(pending.bundle.spectrum, field.name)
            for field in fields(pending.bundle.spectrum)
        })
        spectrum_subclass_bundle = replace(pending.bundle, spectrum=child_spectrum)
        nested_bundle = ChildBundle(**{
            field.name: getattr(pending.bundle, field.name)
            for field in fields(AnalyzerFrameBundle)
        })
        events = []
        owner = self.owner
        owner.layer_journal_snapshots = Mock(side_effect=lambda: (events.append("getter") or ()))
        for candidate in (replace(pending, bundle=spectrum_subclass_bundle,
                                  analytical_identity=None),
                          replace(pending, bundle=nested_bundle, analytical_identity=None)):
            with self.subTest(bundle_type=type(candidate.bundle).__name__):
                events.clear()
                owner.layer_journal_snapshots.reset_mock()
                PaneResourceSession._bind_delivery_obligations(self.session, (candidate,))
                owner.layer_journal_snapshots.assert_called_once_with()
                self.assertEqual(events, ["getter"])

        class ChildTuple(tuple):
            pass

        owner.layer_journal_snapshots.reset_mock()
        PaneResourceSession._bind_delivery_obligations(self.session, ChildTuple((pending,)))
        owner.layer_journal_snapshots.assert_called_once_with()

        class ExplodingBundle:
            @property
            def spectrum(self):
                events.append("bundle.spectrum")
                raise RuntimeError("custom bundle access")

        custom = PaneDelivery(pending.physical_stream_resource_id, pending.capture_id,
            pending.receiver_endpoint_id, pending.host_activation_serial, pending.crop,
            ExplodingBundle(), pending.host_received_monotonic_s, pending.host_run_serial)
        events.clear()
        owner.layer_journal_snapshots.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "custom bundle access"):
            PaneResourceSession._bind_delivery_obligations(self.session, (custom,))
        self.assertEqual(events, ["getter", "bundle.spectrum"])
        owner.layer_journal_snapshots.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

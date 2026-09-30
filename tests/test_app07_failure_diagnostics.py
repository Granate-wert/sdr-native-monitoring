"""Finite first-cause propagation; fake owners only, no RF or vendor text."""

from dataclasses import FrozenInstanceError
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from sdr_monitor.services.pane_resource_diagnostics import (
    PaneDiagnosticError, PaneFailureReason, PaneFailureStage, PaneResourceFailure,
    pane_failure_from_exception,
)
from sdr_monitor.services.tinysa_owned_acquisition import TinySaAcquisitionFailure, TinySaAcquisitionPhase
from sdr_monitor.services.tinysa_serial_trace_collector import TinySaTraceFailureReason
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase

from tests import test_app07_pane_resource_pump as pump_fixture
from tests.test_app07_pane_resource_session import frame


class PaneFailureCodeTests(unittest.TestCase):
    def test_codes_are_immutable_and_reject_strings_or_exception_objects(self):
        failure = PaneResourceFailure(PaneFailureStage.OWNER_POLL)
        with self.assertRaises(FrozenInstanceError):
            failure.stage = PaneFailureStage.STOP
        for args in (("owner_poll",), (PaneFailureStage.START, "operation_failed"),
                     (PaneFailureStage.START, PaneFailureReason.INSTRUMENT_FAILURE, RuntimeError("PRIVATE"))):
            with self.subTest(args=args), self.assertRaises(TypeError):
                PaneResourceFailure(*args)

    def test_unknown_exception_is_not_formatted_or_retained(self):
        class HostileError(RuntimeError):
            def __str__(self):
                raise AssertionError("must not format vendor text")
        error = HostileError("PRIVATE ip:10.0.0.1 COM31 serial bytes")
        failure = pane_failure_from_exception(error, PaneFailureStage.PREPARE)
        self.assertEqual(failure, PaneResourceFailure(PaneFailureStage.PREPARE))
        self.assertNotIn("PRIVATE", repr(failure))
        self.assertIsNone(failure.instrument)

    def test_exact_carrier_preserves_first_boundary_and_cached_tinysa_codes(self):
        failure = PaneResourceFailure(PaneFailureStage.OWNER_POLL, PaneFailureReason.INSTRUMENT_FAILURE,
            TinySaAcquisitionFailure(TinySaAcquisitionPhase.SCAN, TinySaTraceFailureReason.FRAMING))
        error = PaneDiagnosticError("PRIVATE", failure=failure)
        self.assertIs(pane_failure_from_exception(error, PaneFailureStage.TRANSACTION), failure)
        error.failure = "PRIVATE corrupted carrier"
        self.assertEqual(pane_failure_from_exception(error, PaneFailureStage.TRANSACTION),
                         PaneResourceFailure(PaneFailureStage.TRANSACTION))


class PaneFirstCausePumpTests(unittest.TestCase):
    def setUp(self):
        _, self.session, self.leases, self.owners, self.queue, self.pump = pump_fixture.PaneResourcePumpTests.make_plan(1)
        self.owner = self.owners["device-1"]
        self.session.apply()
        self.pump.activate()

    def tearDown(self):
        self.owner.fail_stop = False
        for future in self.pump.stop_all().values():
            future.result(timeout=3)
        self.pump.join_after_stop(3)
        self.assertEqual(self.leases.active_resource_count, 0)

    def start(self):
        return self.pump.start_resource("device-1").result(timeout=3)

    def failed(self):
        pump_fixture.PaneResourcePumpTests.wait_until(
            lambda: self.pump.snapshot()[0].phase is PanePumpPhase.STOP_REQUIRED)
        return self.pump.snapshot()[0]

    def publish(self):
        self.owner.publish(frame(self.owner.admission_source_id, 7, 100e6, 108e6))

    def test_start_failure_differs_from_invalid_admission(self):
        self.owner.fail_start = True
        with self.assertRaisesRegex(RuntimeError, "Start did not confirm"):
            self.start()
        self.assertEqual(self.failed().first_failure, PaneResourceFailure(PaneFailureStage.START))
        self.assertEqual(self.leases.active_resource_count, 1)

    def test_invalid_admission_has_validation_reason(self):
        self.owner.admission_source_id = "PRIVATE mismatched source"
        with self.assertRaisesRegex(RuntimeError, "Start did not confirm"):
            self.start()
        self.assertEqual(self.failed().first_failure,
            PaneResourceFailure(PaneFailureStage.ADMISSION, PaneFailureReason.INVALID_ADMISSION))

    def test_first_poll_failure_survives_failed_and_successful_explicit_stop(self):
        self.start()
        self.owner.fail_poll = True
        first = self.failed().first_failure
        self.assertEqual(first, PaneResourceFailure(PaneFailureStage.OWNER_POLL))
        self.owner.fail_stop = True
        with self.assertRaisesRegex(RuntimeError, "Stop did not confirm"):
            self.pump.stop_resource("device-1").result(timeout=3)
        state = self.failed()
        self.assertIs(state.first_failure, first)
        self.assertEqual(state.cleanup_failure, PaneResourceFailure(PaneFailureStage.STOP))
        self.owner.fail_stop = False
        self.pump.stop_resource("device-1").result(timeout=3)
        self.assertIs(self.pump.snapshot()[0].first_failure, first)
        self.assertEqual(self.pump.snapshot()[0].phase, PanePumpPhase.STOPPED)

    def test_explicit_new_start_clears_history_not_poll_or_stop(self):
        def fresh_owner():
            owner = pump_fixture._SafeOwner("device-1")
            owner.admission_epoch = 20
            return owner
        self.session._owner_factories["device-1"] = fresh_owner
        self.start()
        self.owner.fail_poll = True
        self.failed()
        self.pump.stop_resource("device-1").result(timeout=3)
        self.assertIsNotNone(self.pump.snapshot()[0].first_failure)
        self.start()
        state = self.pump.snapshot()[0]
        self.assertEqual(state.phase, PanePumpPhase.RUNNING)
        self.assertIsNone(state.first_failure)
        self.assertIsNone(state.cleanup_failure)

    def test_malformed_batch_is_publication_validation_not_owner_poll(self):
        self.start()
        with patch.object(self.owner, "poll_bundles", return_value=["PRIVATE"]):
            state = self.failed()
        self.assertEqual(state.first_failure,
            PaneResourceFailure(PaneFailureStage.PUBLICATION_VALIDATION, PaneFailureReason.INVALID_PUBLICATION))

    def test_preparation_failure_has_own_stage(self):
        self.start()
        with patch.object(self.pump._workers["device-1"].preparer, "prepare",
                          side_effect=RuntimeError("PRIVATE ip:10.0.0.1")):
            self.publish()
            state = self.failed()
        self.assertEqual(state.first_failure,
            PaneResourceFailure(PaneFailureStage.PREPARE, PaneFailureReason.INVALID_PUBLICATION))
        self.assertEqual(self.queue.pending_count, 0)

    def test_queue_failure_has_own_stage(self):
        self.start()
        with patch.object(self.queue, "offer", side_effect=RuntimeError("PRIVATE packet")):
            self.publish()
            state = self.failed()
        self.assertEqual(state.first_failure, PaneResourceFailure(PaneFailureStage.QUEUE))

    def test_control_release_failure_does_not_report_receiver_stop(self):
        self.start()
        with patch.object(self.owner, "release_control_claim", side_effect=RuntimeError("PRIVATE claim")):
            with self.assertRaisesRegex(RuntimeError, "Stop did not confirm"):
                self.pump.stop_resource("device-1").result(timeout=3)
        self.assertEqual(self.failed().first_failure, PaneResourceFailure(PaneFailureStage.CONTROL_RELEASE))
        self.assertFalse(self.owner.running)
        self.assertEqual(self.leases.active_resource_count, 1)

    def test_lease_release_failure_retains_same_lease_for_explicit_cleanup(self):
        self.start()
        lease = self.session._runtimes["device-1"].lease
        with patch.object(type(lease), "release", side_effect=RuntimeError("PRIVATE lease")):
            with self.assertRaisesRegex(RuntimeError, "Stop did not confirm"):
                self.pump.stop_resource("device-1").result(timeout=3)
        self.assertEqual(self.failed().first_failure, PaneResourceFailure(PaneFailureStage.LEASE_RELEASE))
        self.assertIs(self.session._runtimes["device-1"].lease, lease)
        self.assertEqual(self.leases.active_resource_count, 1)

    def test_transaction_failure_has_own_code_and_does_not_open_receiver(self):
        @contextmanager
        def bad_transaction():
            raise RuntimeError("PRIVATE control transaction")
            yield  # pragma: no cover - contextmanager protocol
        with patch.object(self.owner, "control_transaction", bad_transaction):
            with self.assertRaisesRegex(RuntimeError, "Start did not confirm"):
                self.start()
        self.assertEqual(self.failed().first_failure, PaneResourceFailure(PaneFailureStage.TRANSACTION))
        self.assertFalse(self.owner.running)
        self.assertEqual(self.owner.events, [])

    def test_inert_rearm_failure_does_not_claim_start_or_open_receiver(self):
        self.session._owner_factories["device-1"] = lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE adapter"))
        self.start()
        self.pump.stop_resource("device-1").result(timeout=3)
        before = list(self.owner.events)
        with self.assertRaisesRegex(RuntimeError, "Start did not confirm"):
            self.start()
        self.assertEqual(self.failed().first_failure, PaneResourceFailure(PaneFailureStage.REARM))
        self.assertEqual(self.leases.active_resource_count, 0)
        self.assertEqual(self.owner.events, before)

    def test_first_and_changed_cleanup_logs_only_finite_codes_once(self):
        worker = self.pump._workers["device-1"]
        with patch("sdr_monitor.ui.v2_pane_runtime.log_event") as log:
            worker._mark_failed(RuntimeError("PRIVATE endpoint"), PaneFailureStage.OWNER_POLL, "fixed")
            worker._mark_failed(RuntimeError("PRIVATE again"), PaneFailureStage.PREPARE, "fixed")
            worker._mark_failed(RuntimeError("PRIVATE close"), PaneFailureStage.STOP, "fixed", cleanup=True)
            worker._mark_failed(RuntimeError("PRIVATE close"), PaneFailureStage.STOP, "fixed", cleanup=True)
        self.assertEqual(log.call_count, 2)
        self.assertEqual(log.call_args_list[0].kwargs["resource_number"], 1)
        self.assertEqual(log.call_args_list[0].kwargs["stage"], "owner_poll")
        self.assertEqual(log.call_args_list[1].kwargs["stage"], "stop")
        self.assertNotIn("PRIVATE", repr(log.call_args_list))
        self.assertNotIn("device-1", repr(log.call_args_list))

    def test_broken_log_handler_cannot_strand_stop_future(self):
        self.start()
        self.owner.fail_stop = True
        with patch("sdr_monitor.ui.v2_pane_runtime.log_event", side_effect=RuntimeError("PRIVATE handler")):
            future = self.pump.stop_resource("device-1")
            with self.assertRaisesRegex(RuntimeError, "Stop did not confirm"):
                future.result(timeout=3)
        self.assertEqual(self.failed().first_failure, PaneResourceFailure(PaneFailureStage.STOP))

"""Typed Stage/Apply refusals: safe text, cleanup precedence, before-RF guards."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.pane_scheduler import PaneRevisitEstimate
from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, PaneUserPlanError
from sdr_monitor.ui.v2_pane_user_stage import (
    PaneUserStageError, apply_user_pane_session, prepare_user_pane_session,
)


class PaneUserRefusalTests(unittest.TestCase):
    def make_pool(self):
        source = SimpleNamespace(label="source", device_id="source")
        live = Mock()
        live.current_source_selection.return_value = SimpleNamespace(selected=source, revision=1, release_pending=False)
        live.current_snapshot.return_value = SimpleNamespace(state=LiveSessionState.CONNECTED)
        live.is_running.return_value = False
        pool = Mock()
        pool.stage.return_value = source
        pool.graph_for.return_value = SimpleNamespace(live=live)
        return pool, live, source

    def test_codes_are_finite_unique_stable_strings_and_errors_require_enum(self):
        self.assertEqual(len(PaneUserRefusal), len({item.value for item in PaneUserRefusal}))
        self.assertEqual(PaneUserRefusal("paired_profile_conflict"), PaneUserRefusal.PAIRED_PROFILE_CONFLICT)
        self.assertIs(PaneUserPlanError("legacy").reason, PaneUserRefusal.INVALID_PLAN)
        self.assertIs(PaneUserStageError("legacy").reason, PaneUserRefusal.STAGE_NOT_CONFIRMED)
        for error_type in (PaneUserPlanError, PaneUserStageError):
            with self.subTest(error=error_type), self.assertRaises(TypeError):
                error_type("typed only", reason="selection_changed")
        with self.assertRaises(TypeError):
            PaneUserStageError("typed only", failed_reason="selection_changed")

    def test_known_plan_refusals_survive_stage_without_raw_error_text(self):
        for reason in PaneUserRefusal:
            pool, live, _source = self.make_pool()
            with self.subTest(reason=reason), patch(
                "sdr_monitor.ui.v2_pane_user_stage.compile_user_pane_plan",
                side_effect=PaneUserPlanError("private-sdk-path/password", reason=reason),
            ), self.assertRaises(PaneUserStageError) as caught:
                prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 108e6),), pool_factory=lambda: pool)
            self.assertIs(caught.exception.reason, reason)
            self.assertIsNone(caught.exception.pool)
            self.assertIsNone(caught.exception.failed_reason)
            self.assertNotIn("private-sdk", str(caught.exception))
            pool.close.assert_called_once_with()
            pool.compose.assert_not_called()
            live.apply_configuration.assert_not_called()

    def test_unknown_sdk_failure_is_generic_and_not_classified_by_text(self):
        pool, live, _source = self.make_pool()
        pool.stage.side_effect = RuntimeError("paired_profile_conflict private-sdk-path")
        with self.assertRaises(PaneUserStageError) as caught:
            prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 108e6),), pool_factory=lambda: pool)
        self.assertIs(caught.exception.reason, PaneUserRefusal.STAGE_NOT_CONFIRMED)
        self.assertNotIn("private-sdk", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        live.apply_configuration.assert_not_called()
        pool.close.assert_called_once_with()

    def test_revisit_estimates_survive_successful_and_failed_cleanup(self):
        violations = (PaneRevisitEstimate("pane-1", "resource", ReceiverBindingMode.TIME_SLICED, 1, 2., .5),)
        for cleanup_fails in (False, True):
            pool, _live, _source = self.make_pool()
            if cleanup_fails:
                pool.close.side_effect = RuntimeError("private-cleanup")
            with self.subTest(cleanup_fails=cleanup_fails), patch(
                "sdr_monitor.ui.v2_pane_user_stage.compile_user_pane_plan",
                side_effect=PaneUserPlanError("fixed deadline", reason=PaneUserRefusal.REVISIT_INFEASIBLE,
                                             revisit_violations=violations),
            ), self.assertRaises(PaneUserStageError) as caught:
                prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 108e6),), pool_factory=lambda: pool)
            self.assertIs(caught.exception.revisit_violations, violations)
            self.assertIs(caught.exception.reason, PaneUserRefusal.CLEANUP_REQUIRED if cleanup_fails
                          else PaneUserRefusal.REVISIT_INFEASIBLE)
            self.assertIs(caught.exception.failed_reason, PaneUserRefusal.REVISIT_INFEASIBLE if cleanup_fails else None)

    def test_unknown_failure_and_cleanup_failure_do_not_invent_paired_reason(self):
        pool, _live, _source = self.make_pool()
        pool.stage.side_effect = RuntimeError("paired_profile_conflict private-driver")
        pool.close.side_effect = RuntimeError("private-cleanup")
        with self.assertRaises(PaneUserStageError) as caught:
            prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 108e6),), pool_factory=lambda: pool)
        self.assertIs(caught.exception.reason, PaneUserRefusal.CLEANUP_REQUIRED)
        self.assertIs(caught.exception.failed_reason, PaneUserRefusal.STAGE_NOT_CONFIRMED)
        self.assertIs(caught.exception.pool, pool)

    def test_cleanup_failure_retains_pool_and_prior_reason_without_sdk_text(self):
        pool, live, _source = self.make_pool()
        pool.close.side_effect = RuntimeError("private-cleanup-path")
        with patch("sdr_monitor.ui.v2_pane_user_stage.compile_user_pane_plan", side_effect=PaneUserPlanError(
            "private-first-path", reason=PaneUserRefusal.PAIRED_PROFILE_CONFLICT,
        )), self.assertRaises(PaneUserStageError) as caught:
            prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 108e6),), pool_factory=lambda: pool)
        error = caught.exception
        self.assertIs(error.reason, PaneUserRefusal.CLEANUP_REQUIRED)
        self.assertIs(error.failed_reason, PaneUserRefusal.PAIRED_PROFILE_CONFLICT)
        self.assertIs(error.pool, pool)
        self.assertNotIn("private", str(error))
        self.assertTrue(error.__suppress_context__)
        live.apply_configuration.assert_not_called()

    def test_network_conflict_survives_stage_and_no_device_stage_occurs(self):
        pool, _live, _source = self.make_pool()
        with self.assertRaises(PaneUserStageError) as caught:
            prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 104e6),
                                       PaneSlotDraft(2, "source", 104e6, 108e6, network_discovery=True)),
                                      pool_factory=lambda: pool)
        self.assertIs(caught.exception.reason, PaneUserRefusal.NETWORK_INTENT_CONFLICT)
        pool.stage.assert_not_called()
        pool.close.assert_called_once_with()

    def test_changed_selection_during_stage_is_explicit_and_closes_pool(self):
        pool, live, _source = self.make_pool()
        live.current_source_selection.return_value = None
        with self.assertRaises(PaneUserStageError) as caught:
            prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 108e6),), pool_factory=lambda: pool)
        self.assertIs(caught.exception.reason, PaneUserRefusal.SELECTION_CHANGED)
        pool.compose.assert_not_called()
        pool.close.assert_called_once_with()

    def make_prepared(self):
        pool, live, source = self.make_pool()
        receipt = Mock(source=source)
        handle = SimpleNamespace(applied=False, shutdown_complete=False, pool=pool, apply=Mock())
        plan = SimpleNamespace(paired_selections=(receipt,), resource_sources=(("resource", "source"),),
                               initial_ad_configurations=(("resource", "configuration"),))
        return SimpleNamespace(handle=handle, plan=plan, preview=()), live, receipt

    def test_all_receipts_preflight_before_any_resource_configuration(self):
        prepared, live, first = self.make_prepared()
        second = Mock(source=SimpleNamespace(device_id="second"))
        second.validate_current.side_effect = PaneUserPlanError("private identity", reason=PaneUserRefusal.SELECTION_CHANGED)
        prepared.plan.paired_selections = (first, second)
        prepared.plan.resource_sources += (("resource2", "second"),)
        prepared.plan.initial_ad_configurations += (("resource2", "configuration2"),)
        with self.assertRaises(PaneUserStageError) as caught:
            apply_user_pane_session(prepared)
        self.assertIs(caught.exception.reason, PaneUserRefusal.SELECTION_CHANGED)
        first.validate_current.assert_called_once()
        second.validate_current.assert_called_once()
        live.apply_configuration.assert_not_called()
        prepared.handle.apply.assert_not_called()

    def test_apply_owner_must_be_stopped_and_selection_present(self):
        for cause, reason in (("running", PaneUserRefusal.OWNER_NOT_STOPPED),
                              ("disconnected", PaneUserRefusal.OWNER_NOT_STOPPED),
                              ("selection", PaneUserRefusal.SELECTION_CHANGED),
                              ("release", PaneUserRefusal.SELECTION_CHANGED)):
            prepared, live, _receipt = self.make_prepared()
            if cause == "running":
                live.is_running.return_value = True
            elif cause == "disconnected":
                live.current_snapshot.return_value = SimpleNamespace(state=LiveSessionState.DISCONNECTED)
            elif cause == "release":
                live.current_source_selection.return_value.release_pending = True
            else:
                live.current_source_selection.return_value = None
            with self.subTest(cause=cause), self.assertRaises(PaneUserStageError) as caught:
                apply_user_pane_session(prepared)
            self.assertIs(caught.exception.reason, reason)
            live.apply_configuration.assert_not_called()
            prepared.handle.apply.assert_not_called()

    def test_apply_conflicts_and_configuration_failure_keep_explicit_codes(self):
        for cause, reason in (("applied", PaneUserRefusal.PLAN_ALREADY_APPLIED_OR_CLOSED),
                              ("closed", PaneUserRefusal.PLAN_ALREADY_APPLIED_OR_CLOSED),
                              ("recording", PaneUserRefusal.RECORDING_CONFLICT),
                              ("configuration", PaneUserRefusal.CONFIGURATION_NOT_CONFIRMED)):
            prepared, live, _receipt = self.make_prepared()
            if cause == "applied":
                prepared.handle.applied = True
            elif cause == "closed":
                prepared.handle.shutdown_complete = True
            elif cause == "recording":
                prepared.preview = (SimpleNamespace(recording_conflict=True),)
            else:
                live.apply_configuration.return_value = SimpleNamespace(error="private-config", applied=None)
            with self.subTest(cause=cause), self.assertRaises(PaneUserStageError) as caught:
                apply_user_pane_session(prepared)
            self.assertIs(caught.exception.reason, reason)
            self.assertNotIn("private", str(caught.exception))
            prepared.handle.apply.assert_not_called()
            if cause != "configuration":
                live.apply_configuration.assert_not_called()


if __name__ == "__main__":
    unittest.main()

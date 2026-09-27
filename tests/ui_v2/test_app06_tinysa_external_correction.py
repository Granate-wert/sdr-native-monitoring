"""Same profile store/common Analyzer correction; fake serial, not RF proof."""

import hashlib
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.calibration import (
    CalibrationPoint,
    CalibrationProfile,
    CalibrationProfileError,
    CalibrationSignature,
    CalibrationStatus,
    apply_calibration,
    check_applicability,
)
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.domain.tinysa_correction import correct_tinysa_values, tinysa_correction_signature
from sdr_monitor.domain.tinysa_settings import TinySaInputMode
from sdr_monitor.services.calibration_service import CalibrationService
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from sdr_monitor.ui.v2.state.prepared_sweep import prepare_sweep_snapshot
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerViewModel
from sdr_monitor.ui.v2.view_models.calibration_view_model import CalibrationProfileViewState
from sdr_monitor.ui.v2.workspaces.analyzer_tinysa_settings import TinySaSettingsDrawer
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.ui_v2 import test_app06_tinysa_runtime_settings as settings


def curve(request, *, points=None, plane="rf_input"):
    signature = tinysa_correction_signature(request, rbw_hz=30_000, attenuation_db=12)
    signature = replace(signature, reference_plane=plane)
    return CalibrationProfile("coax-compensation", 1, signature,
        points or (CalibrationPoint(request.start_hz, 2, .2), CalibrationPoint(request.stop_hz, 4, .4)),
        reference_plane=plane, created_at="2026-09-27T00:00:00+00:00",
        reference_equipment="declared test curve; NOT RF evidence")


class TinySaExternalCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("fake correction lifecycle timeout")
            time.sleep(.001)
        self.app.processEvents()

    def selected(self):
        g = settings.settings_graph()
        self.addCleanup(g.application.shutdown)
        self.addCleanup(g.instrument.stop)
        g.application.select_device(g.application.discover()[0].device_id)
        selection = g.sources.current()
        request = TinySaSweepRequest(selection.selected, selection.revision, 87_500_000, 108_000_000, 3,
            input_mode=TinySaInputMode.LOW, readback_settings=True, frontend_chain="coax-A")
        return g, request

    def requested(self):
        g, request = self.selected()
        return g, replace(request, external_correction=curve(request))

    def grid(self, request):
        return request.start_hz + np.arange(request.points, dtype=np.float64) * (
            (request.stop_hz - request.start_hz) // request.points)

    def test_schema2_reuses_store_csv_and_has_no_fabricated_sdr_fields(self):
        _g, request = self.requested()
        profile = request.external_correction
        self.assertEqual(profile.to_dict()["schema_version"], 2)
        self.assertIsNone(profile.signature.sample_rate_hz)
        self.assertIsNone(profile.signature.fft_unit_convention)
        self.assertEqual(profile.signature.device_serial, "unknown")
        self.assertEqual(set(profile.to_dict()["points"][0]), {"frequency_hz", "correction_db", "uncertainty_db"})
        self.assertEqual(CalibrationProfile.from_dict(profile.to_dict()), profile)
        with tempfile.TemporaryDirectory() as folder:
            service = CalibrationService(CalibrationProfileStore(Path(folder)))
            preview = service.preview_csv("frequency_hz,correction_db,uncertainty_db\n87500000,2,.2\n108000000,4,.4")
            signature = replace(profile.signature, reference_plane="antenna")
            loaded = service.finalize_preview(preview, signature)
            self.assertEqual(loaded.signature, signature)
            self.assertEqual(loaded.reference_plane, "antenna")
            self.assertEqual(service.list_profiles(), (loaded,))
            self.assertEqual(service.store.load(loaded.profile_id, loaded.profile_version).fingerprint, loaded.fingerprint)
        invalid = profile.to_dict()
        invalid["points"][0]["measured_dbfs"] = 0
        with self.assertRaises(CalibrationProfileError):
            CalibrationProfile.from_dict(invalid)

    def test_legacy_signature_schema_and_fingerprint_bytes_unchanged(self):
        signature = CalibrationSignature()
        self.assertNotIn("instrument_context", signature.to_dict())
        legacy = CalibrationProfile("legacy", 1, signature,
            (CalibrationPoint(1e6, 2, .2), CalibrationPoint(2e6, 3, .3)), created_at="fixed")
        self.assertEqual(legacy.to_dict()["schema_version"], 1)
        self.assertEqual(legacy.fingerprint, hashlib.sha256(json.dumps(legacy.to_dict(),
            sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        with patch("sdr_monitor.domain.calibration.json.dumps", side_effect=AssertionError("UI must not reserialize")):
            self.assertEqual(legacy.fingerprint, legacy.fingerprint)
        self.assertEqual(CalibrationProfile.from_dict(legacy.to_dict()), legacy)
        invalid = legacy.to_dict()
        invalid["schema_version"] = 2
        with self.assertRaises(CalibrationProfileError):
            CalibrationProfile.from_dict(invalid)

    def test_instrument_signature_rejects_fake_fft_unknown_chain_and_sdr_apply(self):
        _g, request = self.requested()
        signature = request.external_correction.signature
        for changes in ({"sample_rate_hz": 1}, {"manual_gain_db": 0}, {"fft_unit_convention": "dBFS/bin"},
                        {"frontend_chain": "unknown"}, {"rf_port_path": "high"}, {"device_family": "ad936x"}):
            with self.subTest(changes=changes), self.assertRaises(CalibrationProfileError):
                replace(signature, **changes)
        with self.assertRaises(CalibrationProfileError):
            apply_calibration(np.ones(3), self.grid(request), request.external_correction, signature)
        self.assertFalse(check_applicability(request.external_correction, CalibrationSignature()).applicable)

    def test_static_mismatches_refuse_before_any_serial_effect(self):
        g, request = self.requested()
        signature = request.external_correction.signature
        for field in ("device_identity_key", "firmware_fingerprint"):
            foreign = replace(request.external_correction,
                signature=replace(signature, **{field: "sha256:" + "e" * 64}))
            with self.subTest(field=field), self.assertRaises(CalibrationProfileError):
                replace(request, external_correction=foreign)
        for changes in ({"frontend_chain": "coax-B"}, {"input_mode": TinySaInputMode.PRESERVE},
                        {"readback_settings": False}, {"settings": settings.full_plan()},
                        {"external_correction": replace(request.external_correction,
                         signature=CalibrationSignature())}):
            with self.subTest(changes=changes), self.assertRaises(CalibrationProfileError):
                replace(request, **changes)
        self.assertEqual(g.serials, [])

    def test_exact_interpolation_originals_immutable_and_curve_uncertainty_separate(self):
        _g, request = self.requested()
        raw = np.asarray([-100, -90, np.nan], dtype=np.float32)
        original = raw.copy()
        observed = correct_tinysa_values(request, self.grid(request), raw, rbw_hz=30_000, attenuation_db=12)
        expected = np.interp(self.grid(request), [request.start_hz, request.stop_hz], [2, 4]).astype(np.float32)
        np.testing.assert_array_equal(raw, original)
        np.testing.assert_array_equal(observed.device_values_dbm, original)
        np.testing.assert_allclose(observed.correction_db, expected)
        np.testing.assert_allclose(observed.display_values(), raw + expected, equal_nan=True)
        self.assertEqual(observed.status, CalibrationStatus.INTERPOLATED)
        for array in (observed.device_values_dbm, observed.correction_db, observed.curve_uncertainty_db):
            with self.assertRaises(ValueError):
                array.setflags(write=True)
        raw[0] = 0
        self.assertEqual(observed.device_values_dbm[0], -100)

    def test_dynamic_mismatch_keeps_raw_device_dbm_without_fake_curve(self):
        _g, request = self.requested()
        raw = np.full(3, -100, dtype=np.float32)
        observed = correct_tinysa_values(request, self.grid(request), raw, rbw_hz=20_000, attenuation_db=12)
        self.assertFalse(observed.applied)
        self.assertEqual(observed.reason, "settings_mismatch")
        self.assertIsNone(observed.correction_db)
        self.assertIsNone(observed.curve_uncertainty_db)
        np.testing.assert_array_equal(observed.display_values(), raw)
        self.assertIsNone(observed.display_key)

    def test_extrapolation_explicit_and_invalid_uncertainty_refused(self):
        _g, base = self.selected()
        narrower = curve(base, points=(CalibrationPoint(90e6, 2, .2), CalibrationPoint(100e6, 3, .3)))
        with self.assertRaises(CalibrationProfileError):
            replace(base, external_correction=narrower)
        request = replace(base, external_correction=narrower, allow_correction_extrapolation=True)
        observed = correct_tinysa_values(request, self.grid(request), np.zeros(3, np.float32),
                                         rbw_hz=30_000, attenuation_db=12)
        self.assertEqual(observed.status, CalibrationStatus.EXTRAPOLATED)
        bad = curve(base, points=(CalibrationPoint(100e6, 2, 0), CalibrationPoint(110e6, 3, 1)))
        bad_request = replace(base, external_correction=bad, allow_correction_extrapolation=True)
        invalid = correct_tinysa_values(bad_request, self.grid(base), np.zeros(3, np.float32),
                                        rbw_hz=30_000, attenuation_db=12)
        self.assertFalse(invalid.applied)
        self.assertEqual(invalid.reason, "invalid_curve")

    def test_actual_owned_one_pass_same_queries_no_calibration_writes(self):
        g, request = self.requested()
        g.instrument.start(request, g.sources.current())
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        line = g.instrument.poll_latest().line
        observation = line.instrument.external_correction
        self.assertIs(observation.profile, request.external_correction)
        np.testing.assert_array_equal(line.values_db, observation.display_values())
        self.assertEqual(line.unit, "dBm")
        self.assertEqual(line.instrument.calibration_provenance, "device_reported_builtin")
        commands = settings.writes(g.serials[0])
        self.assertEqual(commands, [b"version\r", b"mode low input\r", b"zero ?\r",
            b"scanraw 87500000 108000000 3 0\r", b"rbw ?\r", b"attenuate ?\r", b"sweeptime ?\r"])
        g.instrument.stop()
        self.assertFalse(g.catalog.cleanup_pending)
        self.assertIsNone(g.live._external_analyzer_owner)

    def test_same_run_readout_mismatch_resets_value_history_not_acquisition_epoch(self):
        g, request = self.requested()
        g.instrument.start(request, g.sources.current())
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        corrected = g.instrument.poll_latest().line
        owner = g.instrument._owner
        owner._settings_observation = replace(owner.settings_observation, actual_attenuation_db=13)
        raw = g.instrument._line(np.full(3, -100, np.float32), zero=174, elapsed=1)
        self.assertFalse(raw.instrument.external_correction.applied)
        self.assertEqual(raw.epoch, corrected.epoch)
        self.assertNotEqual(bundle_from_sweep(raw).identity.accumulation_id,
                            bundle_from_sweep(corrected).identity.accumulation_id)
        self.assertNotEqual(waterfall_line_from_sweep(raw).row.grid_signature,
                            waterfall_line_from_sweep(corrected).row.grid_signature)
        g.instrument.stop()

    def test_gap_carries_only_prior_display_plane_not_correction_or_readback(self):
        g, request = self.requested()
        g.instrument.start(request, g.sources.current())
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        corrected = g.instrument.poll_latest().line
        gap = g.instrument._line(np.full(3, np.nan, np.float32), zero=None, elapsed=1, cancelled=True)
        self.assertIsNone(gap.instrument.settings)
        self.assertIsNone(gap.instrument.external_correction)
        self.assertEqual(gap.instrument.gap_value_context, corrected.instrument.value_context_key)
        self.assertEqual(waterfall_line_from_sweep(gap).row.grid_signature,
                         waterfall_line_from_sweep(corrected).row.grid_signature)
        self.assertTrue(np.all(np.isnan(gap.values_db)))
        g.instrument.stop()

    def test_profile_and_curve_bound_rejects_oversized_or_nonfinite_data(self):
        _g, request = self.requested()
        points = tuple(CalibrationPoint(1e6 + i, 1, .2) for i in range(10002))
        with self.assertRaises(CalibrationProfileError):
            replace(request.external_correction, points=points, valid_start_hz=None, valid_stop_hz=None)
        with self.assertRaises(CalibrationProfileError):
            correct_tinysa_values(request, self.grid(request), np.asarray([1, np.inf, 3], np.float32),
                                 rbw_hz=30_000, attenuation_db=12)

    def test_maximum_10001_point_curve_and_scan_exact_vector_result(self):
        g, base = self.selected()
        base = replace(base, points=10001)
        grid = self.grid(base)
        points = tuple(CalibrationPoint(float(f), float(i) / 5000, .2) for i, f in enumerate(grid))
        request = replace(base, external_correction=curve(base, points=points))
        raw = np.full(10001, -100, np.float32)
        observed = correct_tinysa_values(request, grid, raw, rbw_hz=30_000, attenuation_db=12)
        self.assertEqual(observed.status, CalibrationStatus.CALIBRATED)
        np.testing.assert_array_equal(observed.correction_db, (np.arange(10001) / 5000).astype(np.float32))
        np.testing.assert_array_equal(observed.display_values(), raw + observed.correction_db)
        self.assertEqual(CalibrationProfile.from_dict(request.external_correction.to_dict()), request.external_correction)
        self.assertEqual(g.serials, [])

    def test_request_grid_and_size_refused_before_copy(self):
        _g, request = self.requested()
        with (patch("sdr_monitor.domain.tinysa_correction._freeze", side_effect=AssertionError("no oversized copy")),
              self.assertRaises(CalibrationProfileError)):
            correct_tinysa_values(request, np.arange(10002), np.zeros(10002), rbw_hz=30_000, attenuation_db=12)
        for grid in (self.grid(request) + 1, self.grid(request)[::-1], self.grid(request).reshape(1, 3)):
            with self.subTest(grid=grid), self.assertRaises(CalibrationProfileError):
                correct_tinysa_values(request, grid, np.zeros(3), rbw_hz=30_000, attenuation_db=12)

    def test_profile_refresh_failure_retains_snapshot_and_locked_refresh_defers(self):
        _g, request = self.requested()
        drawer = TinySaSettingsDrawer()
        self.addCleanup(drawer.release_profiles)
        self.addCleanup(drawer.close)
        drawer._on_profiles(CalibrationProfileViewState(profiles=(request.external_correction,)))
        drawer.correction.setCurrentIndex(1)
        selected = drawer.correction_profile()
        drawer._on_profiles(CalibrationProfileViewState(error="C:/private/profile-store/vendor-error"))
        self.assertIs(drawer.correction_profile(), selected)
        self.assertNotIn("C:/private", drawer.correction_scope.text())
        self.assertNotIn("vendor-error", drawer.correction_scope.text())
        drawer._profiles_locked = True
        newer = replace(selected, profile_version=2)
        drawer._on_profiles(CalibrationProfileViewState(profiles=(newer,)))
        self.assertIs(drawer.correction_profile(), selected)
        self.assertEqual(drawer.correction.count(), 2)
        self.assertEqual(drawer._pending_profiles.profiles, (newer,))

    def test_actual_v2_profile_refresh_draft_apply_disable_locale_and_foreign_guard(self):
        g = settings.settings_graph()
        captures = []
        def capture(*args, **kwargs):
            c = compose_v2_live_product(*args, **kwargs)
            captures.append(c)
            return c
        with tempfile.TemporaryDirectory() as folder:
            service = CalibrationService(CalibrationProfileStore(Path(folder)))
            services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_tinysa=g.instrument,
                analyzer_hackrf=None, sweep=Mock(), calibration=service, diagnostics=Mock(), replay=Mock())
            with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
                shell = build_v2_shell(services)
            c = captures[0]
            page = shell._workspace_pages["analyzer"]
            old_locale = current_locale()
            try:
                shell.show()
                page.discover.click()
                self.wait(lambda: page.source.count() == 2 and not c.view_model.state.busy)
                page.source.setCurrentIndex(1)
                self.wait(lambda: c.analyzer_view_model.state.tinysa_controls_available and not c.view_model.state.busy)
                drawer = page.tinysa_bar.settings_drawer
                drawer.input.setCurrentIndex(1)
                drawer.frontend_chain.setText("coax-A")
                draft = page.tinysa_bar.request()
                saved = service.finalize_profile(curve(draft, plane="<b>antenna</b>"))
                active = service.active_profile()
                drawer.refresh_profiles.click()
                self.wait(lambda: drawer.correction.count() == 2 and not c.calibration_view_model.state.busy)
                drawer.correction.setCurrentIndex(1)
                self.assertTrue(drawer.frontend_chain.isEnabled())
                self.assertTrue(drawer.extrapolate.isEnabled())
                selected = page.tinysa_bar.request()
                self.assertEqual(selected.external_correction.fingerprint, saved.fingerprint)
                self.assertEqual(g.serials, [])
                for locale in (UiLocale.EN, UiLocale.RU):
                    set_active_locale(locale)
                    page.set_locale()
                    self.assertEqual(page.tinysa_bar.request(), selected)
                page.primary.click()
                self.wait(lambda: c.analyzer_view_model.state.bundle is not None and not c.analyzer_view_model.state.controls_locked)
                line = c.analyzer_view_model.state.bundle.spectrum
                self.assertTrue(line.instrument.external_correction.applied)
                self.assertIn(saved.fingerprint[:8], page.applied.text())
                self.assertIn("<b>antenna</b>", page.applied.text())
                self.assertEqual(page.applied.textFormat(), Qt.TextFormat.PlainText)
                self.assertIs(service.active_profile(), active)  # no global SDR profile mutation
                run = g.instrument.instrument_run_identity
                port = SimpleNamespace(prepares_snapshots=True, instrument_run_identity=run,
                    prepared_snapshot_ready=Mock(), snapshot_ready=Mock(), task_failed=Mock(),
                    running_changed=Mock(), starting_changed=Mock(), stopping_changed=Mock(), can_close=lambda: True)
                guard = AnalyzerViewModel(c.view_model, port)
                try:
                    guard._instrument_request = replace(run.request, epoch=0)
                    guard._running = True
                    snapshot = g.instrument.poll_latest()
                    prepared = prepare_sweep_snapshot(snapshot, snapshot.analyzer_bundle)
                    guard._on_sweep_snapshot(prepared)
                    self.assertIs(guard.state.bundle.spectrum, line)
                    # Same data/profile id but a different immutable artifact
                    # is not the exact captured request/profile owner.
                    foreign_correction = replace(line.instrument.external_correction,
                                                  profile=replace(saved, notes="foreign artifact"))
                    foreign_line = replace(line, instrument=replace(line.instrument, external_correction=foreign_correction))
                    foreign = replace(snapshot, line=foreign_line)
                    guard._on_sweep_snapshot(prepare_sweep_snapshot(foreign, foreign.analyzer_bundle))
                    self.assertIs(guard.state.bundle.spectrum, line)
                    self.assertIn("Rejected stale/foreign", guard.state.error)
                finally:
                    guard.dispose()
                first = line.instrument.external_correction
                drawer.correction.setCurrentIndex(0)
                self.assertFalse(drawer.frontend_chain.isEnabled())
                self.assertFalse(drawer.extrapolate.isEnabled())
                self.assertIsNone(page.tinysa_bar.request().external_correction)
                page.primary.click()
                self.wait(lambda: c.analyzer_view_model.state.bundle is not None
                    and c.analyzer_view_model.state.bundle.spectrum is not line
                    and not c.analyzer_view_model.state.controls_locked)
                raw = c.analyzer_view_model.state.bundle.spectrum
                self.assertIsNone(raw.instrument.external_correction)
                np.testing.assert_array_equal(raw.values_db, first.device_values_dbm)
            finally:
                set_active_locale(old_locale)
                shell.close()
                self.wait(lambda: shell._is_closed)
                c.shutdown()
                self.assertIsNone(drawer._unsubscribe_profiles)
                self.assertIsNone(drawer._profiles_model)
                self.assertIsNone(drawer._pending_profiles)
                self.assertEqual(drawer.correction.count(), 1)
                self.assertFalse(g.catalog.cleanup_pending)
                self.assertIsNone(g.live._external_analyzer_owner)

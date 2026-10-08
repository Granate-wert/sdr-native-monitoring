"""Offscreen existing-renderer witnesses; not visible Windows/DWM/RF proof."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from threading import Event
from tempfile import mkdtemp

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from sdr_monitor.ui.v2.i18n import UiLocale, set_active_locale, text
from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
from sdr_monitor.ui.v2.spectrum.projection import ProjectionRequest, project_spectrum
from tests.ui_v2.test_live_calibration_composition import CalibrationCompositionFixture


class LiveCalibrationWorkspaceTests(CalibrationCompositionFixture):
    def test_current_scene_is_lazy_until_bound_and_visible_and_reused_after_hide(self):
        widget = self.composition._analyzer_workspace_ref()
        plot = widget.calibrated_current
        self.assertIsNone(plot.scene)
        self.shell.select_workspace("analyzer")
        self.shell.show()
        self.app.processEvents()
        self.assertIsNone(plot.scene)  # Visibility alone never admits calibration.
        self.shell.select_workspace("calibration")
        self.bind()
        self.select_profile()
        self.assertIsNone(plot.scene)  # Bound but hidden: no graphics allocation.
        self.assertIsNone(self.model.state.displayed)
        for locale in UiLocale:
            set_active_locale(locale)
            widget.set_locale()
            self.assertIsNone(plot.scene)
        self.shell.select_workspace("analyzer")
        self.wait(lambda: self.model.state.displayed is self.model.state.current)
        scene = plot.scene
        self.assertIsNotNone(scene)
        self.assertIs(scene.displayed_frame, self.model.state.current.frame)
        current = self.model.state.current
        request = ProjectionRequest(scene._projection_owner, scene._projection_generation,
                                    scene._viewport(), tuple(scene._trace_views.items()),
                                    prepared=current.spectrum)
        delayed = project_spectrum(request)
        self.shell.select_workspace("calibration")
        self.app.processEvents()
        self.assertIsNone(scene.latest_frame)
        self.assertIsNone(self.model.state.displayed)
        with patch.object(scene, "_request_projection", wraps=scene._request_projection) as retry:
            scene._accept_projection(delayed)
            retry.assert_not_called()
        self.assertIsNone(scene.displayed_frame)
        self.assertIsNone(self.model.state.displayed)  # Hidden late delivery cannot acknowledge display.
        self.shell.select_workspace("analyzer")
        self.wait(lambda: self.model.state.displayed is self.model.state.current)
        self.assertIs(plot.scene, scene)
        self.model.close_binding()
        self.assertIsNone(scene.latest_frame)
        self.assertIsNone(scene.displayed_frame)
        plot.release()
        plot.release()
        self.assertEqual(self.port.actions, [])

    def test_uncreated_current_scene_release_is_idempotent_and_does_not_construct(self):
        plot = self.composition._analyzer_workspace_ref().calibrated_current
        self.assertIsNone(plot.scene)
        self.assertIsNone(self.composition._calibration_projector._future)
        plot.release()
        plot.release()
        self.shell.select_workspace("analyzer")
        self.shell.show()
        self.app.processEvents()
        self.assertIsNone(plot.scene)
        self.assertIsNone(self.model.state.binding)
        self.assertEqual(self.port.actions, [])

    def test_strict_en_ru_eight_states_and_persistent_frontend_roles_fhd_qhd(self):
        from sdr_monitor.ui.v2.workspaces.calibration_profiles import CalibrationProfilesWorkspaceV2, _inspector
        from sdr_monitor.ui.v2.design import ThemeId
        source = self.facts.frame
        grid = source.center_frequency_hz + (np.arange(source.fft_size) - source.fft_size // 2) * (
            source.sample_rate_hz / source.fft_size)
        self.facts.frame = replace(source, frequencies_hz=grid, values=np.linspace(-30., -10., source.fft_size))
        self.facts.current = replace(self.facts.current, spectrum=self.facts.frame)
        self.port.current = self.facts.current
        self.shell.select_workspace("analyzer")
        self.shell.resize(1920, 1080)
        self.shell.show()
        self.worker._offer_preparation(self.port.current, self.worker._control_revision)
        self.wait(lambda: self.composition.analyzer_view_model.state.live.has_spectrum
                  and self.worker._preparation_future is None and not self.worker._projection_in_flight)
        browser = self.composition.calibration_view_model
        profile = self.facts.profile(self.facts.signature())
        from sdr_monitor.domain.calibration import CalibrationPoint
        profile = replace(profile, valid_start_hz=None, valid_stop_hz=None,
                          points=(CalibrationPoint(float(grid[0]), 1., .2),
                                  CalibrationPoint(float(grid[-1]), 2., .3)))
        self.store.save(profile)
        browser.refresh()
        self.wait(lambda: bool(browser.state.profiles) and not browser.state.busy)
        evidence = Path(mkdtemp(prefix="UI105_FAIRNESS_UX_STATES_",
                                dir=Path(__file__).resolve().parents[2] / "docs/codex/sessions"))
        for locale in UiLocale:
            set_active_locale(locale)
            self.model.close_binding()
            browser.select(None)
            self.wait(lambda: not browser.state.busy)
            page = CalibrationProfilesWorkspaceV2(browser, live_calibration=self.model)
            inspector = _inspector(browser, ThemeId.DARK)
            try:
                values = (self.frontend.rf_port_path, self.frontend.frontend_chain, self.frontend.reference_plane)
                for field, value in zip(page._frontend_fields, values):
                    field.setText(value)
                def capture(name):
                    for width, height in ((1920, 1080), (2560, 1440)):
                        page.resize(width, height)
                        page.show()
                        self.app.processEvents()
                        labels = [label.text() for label in page.findChildren(QLabel)]
                        self.assertIn(text("calibration.header.detail", locale), labels)
                        self.assertIn(text("calibration.applicability.boundary", locale), labels)
                        self.assertIn(text("live_calibration.boundary", locale), labels)
                        self.assertIn(text("calibration.inspector.boundary", locale),
                                      [label.text() for label in inspector.findChildren(QLabel)])
                        for key, label, field in zip(("port", "chain", "plane"), page._frontend_labels,
                                                     page._frontend_fields):
                            self.assertEqual(label.text(), text("live_calibration." + key, locale))
                            self.assertIs(label.buddy(), field)
                            self.assertTrue(label.isVisible())
                            self.assertTrue(field.text())
                            self.assertLessEqual(label.width(), field.width())
                        self.assertTrue(page.grab().save(str(evidence / f"UI105_FAIRNESS_{locale.value}_{width}_{name}.png")))
                self.assertIsNone(self.model.state.binding)
                capture("unbound")
                browser.select(profile)
                self.wait(lambda: browser.state.selected is not None and not browser.state.busy)
                self.assertEqual(page._state_chip.text, text("calibration.state.selected", locale))
                self.assertIsNone(self.model.state.binding)
                capture("inspection")
                page._bind.click()
                self.wait(lambda: self.model.state.current is not None and self.presenter._active is None)
                prior_acknowledgement = self.model.state.acknowledged
                page._preview.click()
                self.wait(lambda: self.model.state.preview is not None and not self.model.state.busy)
                self.assertEqual(self.model.state.acknowledged, prior_acknowledgement)
                self.assertTrue(page._select.isEnabled())
                capture("preview")
                lane = self.presenter._lane
                started, release = Event(), Event()
                original = lane.correct
                def held(handle):
                    started.set()
                    if not release.wait(3):
                        raise RuntimeError("CURRENT display hold timed out")
                    return original(handle)
                with patch.object(lane, "correct", side_effect=held):
                    try:
                        page._select.click()
                        self.wait(lambda: self.model.state.acknowledged == "select" and started.is_set())
                        self.assertIsNone(self.model.state.current)
                        self.assertIsNone(self.model.state.displayed)
                        self.assertIn(text("live_calibration.ack.select", locale), page._live_status.text())
                        capture("select_ack_not_displayed")
                    finally:
                        release.set()
                    self.wait(lambda: self.model.state.displayed is not None
                              and self.model.state.displayed is self.model.state.current)
                plot = self.composition._analyzer_workspace_ref().calibrated_current
                self.assertEqual(plot.scene.plot_item.getAxis("left").labelText, "dBm/bin")
                self.assertIn("current-frame", plot.status.text())
                self.assertIn("dBm/bin", plot.status.text())
                capture("accepted_current")
                self.assertTrue(self.shell.grab().save(str(evidence / f"UI105_FAIRNESS_{locale.value}_CURRENT.png")))
                outside = grid.copy()
                outside[0] -= 1.
                frame = replace(self.facts.frame, frequencies_hz=outside)
                self.port.current = replace(self.facts.current, spectrum=frame)
                previous = self.model.state.current
                self.presenter.request_current()
                self.wait(lambda: self.model.state.current is not previous and self.model.state.displayed is not None
                          and self.model.state.displayed is self.model.state.current)
                self.assertEqual(self.model.state.displayed.frame.unit, "dBFS/bin")
                self.assertIs(self.model.state.current.frame.publication.analytical.raw, frame)
                capture("raw_fallback")
                service = self.registry.for_device(self.facts.facts.device, self.model.state.binding.endpoint)
                service.clear_active_profile()
                plot.scene._graphics.viewport().repaint()
                self.wait(lambda: self.model.state.current is None and self.model.state.displayed is None)
                capture("stale")
                page._clear.click()
                self.wait(lambda: self.model.state.acknowledged == "clear" and self.model.state.displayed is not None)
                self.assertEqual(self.model.state.displayed.frame.unit, "dBFS/bin")
                self.assertIn(text("live_calibration.ack.clear", locale), page._live_status.text())
                capture("clear")
                # Every role stays labeled while populated AND editing; each
                # actual textEdited signal detaches, requiring explicit rebind.
                for index, field in enumerate(page._frontend_fields):
                    field.setFocus()
                    QTest.keyClick(field, Qt.Key.Key_End)
                    QTest.keyClicks(field, "-edited")
                    self.assertIsNone(self.model.state.binding)
                    self.assertFalse(page._select.isEnabled())
                    self.assertTrue(page._frontend_labels[index].isVisible())
                    field.setText(values[index])
                    page._bind.click()
                    self.wait(lambda: self.model.state.current is not None and self.presenter._active is None)
                page._frontend_fields[0].setFocus()
                for following in (*page._frontend_fields[1:], page._bind):
                    QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Tab)
                    self.assertIs(self.app.focusWidget(), following)
                self.assertEqual(self.port.actions, [])
            finally:
                page.close()
                inspector.close()
                page.deleteLater()
                inspector.deleteLater()
                self.app.processEvents()
                self.port.current = self.facts.current

    def test_valid_current_stale_viewport_retries_but_invalid_authority_never_does(self):
        self.shell.select_workspace("analyzer")
        self.shell.resize(1920, 1080)
        self.shell.show()
        self.bind()
        self.select_profile()
        self.wait(lambda: self.model.state.displayed is self.model.state.current)
        plot = self.composition._analyzer_workspace_ref().calibrated_current
        scene = plot.scene
        current = self.model.state.current
        request = ProjectionRequest(scene._projection_owner, scene._projection_generation,
                                    scene._viewport(), tuple(scene._trace_views.items()),
                                    prepared=current.spectrum)
        delayed = project_spectrum(request)
        changed = (*request.viewport[:2], request.viewport[2] + 1)
        self.model.display_invalidated()
        with patch.object(scene, "_viewport", return_value=changed), \
             patch.object(scene, "_request_projection", wraps=scene._request_projection) as retry:
            scene._accept_projection(delayed)
            retry.assert_called_once_with()
            self.assertIsNone(self.model.state.displayed)  # A retry is not a display acknowledgement.
        # Real resize churn still converges through the existing bounded port.
        for width in (1800, 2000, 1920):
            self.shell.resize(width, 1080)
            self.app.processEvents()
        self.wait(lambda: scene.displayed_frame is current.frame
                  and self.composition._calibration_projector._future is None)
        service = self.registry.for_device(self.facts.facts.device, self.model.state.binding.endpoint)
        service.clear_active_profile()
        with patch.object(scene, "_viewport", return_value=changed), \
             patch.object(scene, "_request_projection", wraps=scene._request_projection) as retry:
            for _ in range(10):
                scene._accept_projection(delayed)
            retry.assert_not_called()
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        self.assertIsNone(scene.displayed_frame)
        self.assertIsNone(self.model.state.displayed)

    def test_controls_inspection_not_activation_and_frontend_edit_requires_rebind(self):
        self.shell.select_workspace("calibration")
        from sdr_monitor.ui.v2.workspaces.calibration_profiles import CalibrationProfilesWorkspaceV2
        page = self.shell.findChild(CalibrationProfilesWorkspaceV2)
        self.assertIsNotNone(page)
        profile = self.facts.profile(self.facts.signature())
        self.store.save(profile)
        page._refresh_button.click()
        self.wait(lambda: bool(self.composition.calibration_view_model.state.profiles))
        page._profiles.setCurrentRow(0)
        self.wait(lambda: not self.composition.calibration_view_model.state.busy)
        self.assertIsNone(self.model.state.binding)
        frontend = self.frontend
        for field, value in zip(page._frontend_fields, (frontend.rf_port_path, frontend.frontend_chain, frontend.reference_plane)):
            field.setText(value)
        page._bind.click()
        self.wait(lambda: self.model.state.current is not None)
        page._preview.click()
        self.wait(lambda: not self.model.state.busy)
        self.assertIsNone(self.model.state.acknowledged)
        self.assertTrue(page._select.isEnabled())
        page._select.click()
        self.wait(lambda: self.model.state.acknowledged == "select" and self.model.state.current is not None)
        field = page._frontend_fields[0]
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_End)
        QTest.keyClicks(field, "-edited")
        self.assertIsNone(self.model.state.binding)
        self.assertFalse(page._select.isEnabled())
        self.assertEqual(self.port.actions, [])

    def test_actual_separate_current_axis_and_late_projection_repaint_validity(self):
        self.shell.select_workspace("analyzer")
        self.shell.resize(1920, 1080)
        self.shell.show()
        self.app.processEvents()
        widget = self.composition._analyzer_workspace_ref()
        plot = widget.calibrated_current
        self.bind()
        self.select_profile()
        self.wait(lambda: self.model.state.displayed is self.model.state.current)
        current = self.model.state.displayed
        self.assertEqual(plot.scene.plot_item.getAxis("left").labelText, "dBm/bin")
        self.assertIs(plot.scene.displayed_frame, current.frame)
        self.assertEqual(set(plot.scene._trace_views), {TraceKind.CURRENT})
        self.assertIs(current.frame.publication.analytical.raw, self.facts.frame)
        # Same-context native cadence advancement is NOT latest-frame equality.
        frame = replace(self.facts.frame, sequence=2)
        self.port.current = replace(self.facts.current, spectrum=frame, sequence=2)
        plot.scene._request_projection()
        self.app.processEvents()
        self.assertTrue(plot.scene.valid(current.frame))
        request = ProjectionRequest(plot.scene._projection_owner, plot.scene._projection_generation,
                                    plot.scene._viewport(), tuple(plot.scene._trace_views.items()),
                                    prepared=current.spectrum)
        delayed = project_spectrum(request)
        # Registry ABA without a GUI notification must be caught before repaint.
        service = self.registry.for_device(self.facts.facts.device, self.model.state.binding.endpoint)
        service.clear_active_profile()
        plot.scene._graphics.viewport().repaint()
        self.app.processEvents()
        self.assertIsNone(plot.scene.displayed_frame)
        self.assertFalse(self.model.is_valid(current))
        plot.scene._accept_projection(delayed)
        self.assertIsNone(plot.scene.displayed_frame)
        self.assertIsNone(self.model.state.displayed)

    def test_bin_hz_and_raw_fallback_labels_preserve_frame_and_en_ru_layout(self):
        evidence = Path(mkdtemp(prefix="UI105_FAIRNESS_RAW_LAYOUT_",
                                dir=Path(__file__).resolve().parents[2] / "docs/codex/sessions"))
        frame = replace(self.facts.frame, unit="dBFS/Hz")
        self.port.current = replace(self.facts.current, spectrum=frame, unit=frame.unit)
        self.bind()
        self.select_profile()
        self.assertEqual(self.model.state.current.frame.unit, "dBm/Hz")
        self.model.clear()
        self.wait(lambda: self.model.state.acknowledged == "clear" and self.model.state.current is not None)
        self.assertEqual(self.model.state.current.frame.unit, "dBFS/Hz")
        self.assertIs(self.port.current.spectrum, frame)
        for locale in UiLocale:
            set_active_locale(locale)
            from sdr_monitor.ui.v2.workspaces.calibration_profiles import CalibrationProfilesWorkspaceV2
            page = CalibrationProfilesWorkspaceV2(self.composition.calibration_view_model,
                                                 live_calibration=self.model)
            try:
                for width, height in ((1920, 1080), (2560, 1440)):
                    page.resize(width, height)
                    page.show()
                    self.app.processEvents()
                    self.assertTrue(page._live_status.wordWrap())
                    self.assertEqual(page._live_status.property("ui2Role"), "secondary")
                    self.assertTrue(all(field.property("ui2Role") == "command-field"
                                        and "color:" in field.styleSheet() for field in page._frontend_fields))
                    self.assertEqual(page._bind.text(), text("live_calibration.bind", locale))
                    self.assertTrue(all(field.accessibleName() and field.toolTip() for field in page._frontend_fields))
                    self.assertTrue(page._live_status.geometry().height() > 0)
                    self.assertLessEqual(page._live_status.geometry().right(), page.width())
                    self.assertLessEqual(page._live_status.geometry().bottom(), page.height())
                    self.assertTrue(page.grab().save(str(evidence / f"CALIBRATION_UI_105_{locale.value}_{width}.png")))
            finally:
                page.close()
                page.deleteLater()
                self.app.processEvents()
        set_active_locale(UiLocale.RU)

    def test_incompatible_preview_and_outside_coverage_keep_raw_units(self):
        self.bind()
        wrong = self.facts.profile(replace(self.model.state.binding.signature, manual_gain_db=99.))
        self.model.preview(wrong)
        self.wait(lambda: not self.model.state.busy)
        self.assertFalse(self.model.state.preview.applicability.applicable)
        service = self.registry.for_device(self.facts.facts.device, self.model.state.binding.endpoint)
        before = service.selection_receipt()
        self.model.select()
        self.wait(lambda: not self.model.state.busy)
        self.assertIsNotNone(self.model.state.error)
        self.assertEqual(service.selection_receipt(), before)
        self.assertIs(self.port.current.spectrum, self.facts.frame)
        self.select_profile()
        frame = replace(self.facts.frame, frequencies_hz=np.array([99., 150., 200.]))
        self.port.current = replace(self.port.current, spectrum=frame)
        previous = self.model.state.current
        self.presenter.request_current()
        self.wait(lambda: self.model.state.current is not None and self.model.state.current is not previous)
        value = self.model.state.current.frame.publication.analytical
        self.assertEqual(value.result.unit, "dBFS/bin")
        np.testing.assert_array_equal(value.result.values, frame.values)
        self.assertIs(value.raw, frame)

"""Offscreen existing-renderer witnesses; not visible Windows/DWM/RF proof."""
from dataclasses import replace
from pathlib import Path

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sdr_monitor.ui.v2.i18n import UiLocale, set_active_locale, text
from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
from sdr_monitor.ui.v2.spectrum.projection import ProjectionRequest, project_spectrum
from tests.ui_v2.test_live_calibration_composition import CalibrationCompositionFixture


class LiveCalibrationWorkspaceTests(CalibrationCompositionFixture):
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
                    evidence = Path(__file__).resolve().parents[2] / "docs/codex/sessions"
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

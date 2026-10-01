"""Exact settings/ACK/readback through the actual V2 graph, fake serial only."""

import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.device_capabilities import CapabilityEvidenceOrigin, CapabilityField
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.domain.tinysa_settings import (
    TINYSA_RUNTIME_CONTROL_CONTRACT,
    TinySaAttenuationMode,
    TinySaInputMode,
    TinySaRbwMode,
    TinySaSettingsObservation,
    TinySaSettingsUnsupported,
    TinySaSpurPolicy,
    TinySaSweepAccuracy,
    TinySaSweepSettingsPlan,
    TinySaSwitchPolicy,
    compile_tinysa_runtime_settings,
    tinysa_control_contract,
)
from sdr_monitor.services import tinysa_sweep_settings_controller as legacy
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel, TinySaReadOnlyProbe
from sdr_monitor.services.tinysa_owned_acquisition import TinySaAcquisitionPhase, _settings_scalar
from sdr_monitor.services.tinysa_serial_version_probe import _parse_version
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollectionCancelled,
    TinySaTraceCollectionError,
    TinySaTraceFailureReason,
)
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.state.analyzer_readouts import tinysa_settings_readout
from sdr_monitor.ui.v2.state.prepared_sweep import prepare_sweep_snapshot
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerViewModel
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.ui_v2 import test_app06_tinysa_common_analyzer as common

VERSION = "tinySA4_v1.4-179-g26fc821"


def settings_graph(model=TinySaModel.ULTRA, *, version_text=None, readback_payloads=None):
    g = common.graph()
    version = version_text or (VERSION if model is TinySaModel.ULTRA else "tinySA_v1.4-179-g26fc821")
    class Probe:
        def __init__(self, endpoint):
            self.endpoint = endpoint
        def probe(self):
            return TinySaReadOnlyProbe(model, self.endpoint.identity_key, version)
        def close(self):
            pass
    g.provider._port_factory = Probe
    original = g.provider._acquisition_factory
    def acquisition(*args):
        owner = original(*args)
        factory = owner._factory
        def serial_factory(route):
            serial = factory(route)
            serial.version = (version.replace(" | ", "\r\n") + "\r\nch> ").encode("ascii")
            write = serial.write
            def settings_write(command):
                count = write(command)
                responses = {
                    b"rbw ?\r": b"rbw ?\r\nusage: rbw 0.2..850|auto\r\n30000.0Hz\r\nch> "
                        if model is TinySaModel.ULTRA else b"rbw ?\r\nrbw 2..600|auto\r\n30000.0Hz\r\nch> ",
                    b"attenuate ?\r": b"attenuate ?\r\nusage: attenuate 0..31|auto\r\n12.00\r\nch> ",
                    b"sweeptime ?\r": b"sweeptime ?\r\nusage: sweeptime 0.003..60\r\n0.123s\r\nch> ",
                }
                if readback_payloads is not None:
                    responses.update(readback_payloads)
                serial.response = responses.get(command, serial.response)
                return count
            serial.write = settings_write
            return serial
        owner._factory = serial_factory
        return owner
    g.provider._acquisition_factory = acquisition
    return g


def writes(serial):
    return [c[1] for c in serial.calls if isinstance(c, tuple) and c[0] == "write"]


def full_plan():
    return TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.PRECISE, rbw_mode=TinySaRbwMode.MANUAL,
        rbw_hz=10_000, sweep_time_ms=500, spur_removal=TinySaSpurPolicy.AUTO, lna=TinySaSwitchPolicy.OFF,
        attenuation_mode=TinySaAttenuationMode.MANUAL, attenuation_db=12, repeat_count=4)


class TinySaRuntimeSettingsTests(unittest.TestCase):
    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("tinySA fake worker timeout")
            time.sleep(.001)

    def selected(self, model=TinySaModel.ULTRA):
        g = settings_graph(model)
        self.addCleanup(g.application.shutdown)
        source = g.application.discover()[0]
        g.application.select_device(source.device_id)
        selection = g.sources.current()
        request = TinySaSweepRequest(selection.selected, selection.revision, 87_500_000, 108_000_000, 3)
        return g, request

    def test_legacy_reexports_are_same_types_not_second_settings_model(self):
        self.assertIs(legacy.TinySaSweepSettingsPlan, TinySaSweepSettingsPlan)
        self.assertIs(legacy.TinySaSwitchPolicy, TinySaSwitchPolicy)
        self.assertIs(legacy.TinySaSettingsUnsupported, TinySaSettingsUnsupported)

    def test_pinned_si_scalar_representations_are_base_units_not_targets(self):
        for value, unit, expected in (("300kHz", "Hz", 300_000), ("10.5kHz", "Hz", 10_500),
                ("200Hz", "Hz", 200), ("2MHz", "Hz", 2_000_000),
                ("123ms", "s", .123), ("0.123s", "s", .123),
                ("3ms", "s", .003), ("12.00", "", 12), ("500m", "", .5)):
            with self.subTest(value=value, unit=unit):
                self.assertAlmostEqual(_settings_scalar(value, unit), expected)

    def test_si_scalar_refuses_wrong_units_unknown_prefixes_and_arbitrary_text(self):
        for value, unit in (("300KHz", "Hz"), ("300kHz extra", "Hz"), ("300k", "Hz"),
                ("0.3GHz", "Hz"), ("0.3mHz", "Hz"), ("3us", "s"), ("3kHz", "s"),
                ("1.2e5Hz", "Hz"), ("NaNHz", "Hz"), ("-1Hz", "Hz"), ("+1Hz", "Hz"),
                ("12dB", ""), ("12k", ""), ("auto", "Hz"), ("12", "dB")):
            with self.subTest(value=value, unit=unit), self.assertRaises(TinySaTraceCollectionError) as caught:
                _settings_scalar(value, unit)
            self.assertEqual(caught.exception.reason, TinySaTraceFailureReason.FRAMING)

    def test_actual_owner_hardware_version_and_si_queries_keep_exact_intent(self):
        payloads = {b"rbw ?\r": b"rbw ?\r\nusage: rbw 0.2..850|auto\r\n300kHz\r\nch> ",
            b"attenuate ?\r": b"attenuate ?\r\nusage: attenuate 0..31|auto\r\n12\r\nch> ",
            b"sweeptime ?\r": b"sweeptime ?\r\nusage: sweeptime 0.003..60\r\n123ms\r\nch> "}
        g = settings_graph(version_text="tinySA4_v1.4-200-g26fc821 | HW Version:V0.5.4 max2871",
                           readback_payloads=payloads)
        self.addCleanup(g.application.shutdown)
        g.application.select_device(g.application.discover()[0].device_id)
        selection = g.sources.current()
        request = TinySaSweepRequest(selection.selected, selection.revision,
            100_000_000, 300_000_000, 1001,
            settings=TinySaSweepSettingsPlan(rbw_mode=TinySaRbwMode.MANUAL, rbw_hz=300_000),
            input_mode=TinySaInputMode.LOW, readback_settings=True)
        g.application.start_sweep(request)
        self.wait(lambda: g.instrument.poll_latest().line is not None or g.instrument.poll_latest().metrics.has_error)
        state = g.instrument.poll_latest()
        self.assertFalse(state.metrics.has_error)
        observation = state.line.instrument.settings
        self.assertEqual(observation.plan, request.settings)
        self.assertEqual(observation.acknowledged_commands, ("mode low input", "rbw 300"))
        self.assertEqual(observation.actual_rbw_hz, 300_000)
        self.assertEqual(observation.actual_attenuation_db, 12)
        self.assertAlmostEqual(observation.screen_sweep_time_s, .123)
        for command in (b"rbw ?\r", b"attenuate ?\r", b"sweeptime ?\r"):
            self.assertEqual(writes(g.serials[0]).count(command), 1)
        self.assertEqual(len(g.serials), 1)
        self.assertFalse(g.catalog.cleanup_pending)

    def test_si_readback_checks_scaled_bounds_before_any_publication(self):
        for command, unit, value in ((b"rbw ?\r", "Hz", "3MHz"),
                (b"attenuate ?\r", "", "41"), (b"sweeptime ?\r", "s", "121000ms")):
            with self.subTest(command=command):
                g = settings_graph(readback_payloads={command: command + b"\n" + value.encode("ascii") + b"\r\nch> "})
                try:
                    g.application.select_device(g.application.discover()[0].device_id)
                    selection = g.sources.current()
                    request = TinySaSweepRequest(selection.selected, selection.revision,
                        100_000_000, 300_000_000, 1001, readback_settings=True)
                    g.application.start_sweep(request)
                    self.wait(lambda: g.instrument.poll_latest().metrics.has_error)
                    self.assertIsNone(g.instrument.poll_latest().line)
                    self.assertEqual(g.instrument.acquisition_failure.phase, TinySaAcquisitionPhase.READBACK)
                    self.assertEqual(g.instrument.acquisition_failure.reason, TinySaTraceFailureReason.BOUND)
                    self.assertEqual(len(g.serials), 1)
                finally:
                    g.application.shutdown()

    def test_revision_contract_is_suffix_only_and_evidenced_on_existing_snapshot(self):
        for version in (VERSION, "tinySA v1.4 g26fc821", "tinySA4 " + "26fc821ad3432f929630718cd290314dbc711f48"):
            self.assertEqual(tinysa_control_contract(version), TINYSA_RUNTIME_CONTROL_CONTRACT)
        for version in ("tinySA4 newer", "tinySA4 g26fc821-dirty", "tinySA4 126fc821", "tinySA4 g26fc821 | other"):
            self.assertIsNone(tinysa_control_contract(version))
        g, request = self.selected()
        snapshot = request.source.binding.snapshot
        self.assertEqual(snapshot.runtime_control_contract, TINYSA_RUNTIME_CONTROL_CONTRACT)
        self.assertEqual(snapshot.evidence_for(CapabilityField.RUNTIME_CONTROL_CONTRACT).origin,
                         CapabilityEvidenceOrigin.VENDOR_DECLARATION)
        self.assertEqual(g.serials, [])
        with self.assertRaises(ValueError):
            replace(snapshot, runtime_control_contract=None)

    def test_source_evidenced_ultra_hw_metadata_does_not_hide_known_revision(self):
        for hardware in ("V0.4.5.1", "V0.4.5.1.1", "V0.4.6", "V0.5.4", "Unknown"):
            for suffix in ("", " max2871"):
                with self.subTest(hardware=hardware, suffix=suffix):
                    value = VERSION + " | HW Version:" + hardware + suffix
                    self.assertEqual(tinysa_control_contract(value), TINYSA_RUNTIME_CONTROL_CONTRACT)
        for value in (
            VERSION + " | other", VERSION + " | HW Version:V9.9.9", VERSION + " | HW Version:V0.5.4 extra",
            VERSION + " | HW Version:V0.5.4 max9999", VERSION + " | HW Version:V0.5.4 | other",
            "tinySA_v1.4-g26fc821 | HW Version:V0.5.4", "tinySA4-g9999999 | HW Version:V0.5.4",
            "tinySA4-g26fc821-dirty | HW Version:V0.5.4"):
            with self.subTest(value=value):
                self.assertIsNone(tinysa_control_contract(value))

    def test_observed_multiline_version_preserves_full_identity_through_same_owner_settings(self):
        normalized = "tinySA4_v1.4-200-g26fc821 | HW Version:V0.5.4 max2871"
        parsed = _parse_version(b"version\r\ntinySA4_v1.4-200-g26fc821\r\nHW Version:V0.5.4 max2871\r\nch> ")
        self.assertEqual(parsed.normalized_version, normalized)
        g = settings_graph(version_text=normalized)
        self.addCleanup(g.application.shutdown)
        source = g.application.discover()[0]
        g.application.select_device(source.device_id)
        selected = g.sources.current()
        self.assertEqual(selected.selected.binding.snapshot.runtime_control_contract, TINYSA_RUNTIME_CONTROL_CONTRACT)
        # Contract recognition must not erase the hardware line from identity.
        self.assertNotEqual(parsed.firmware_fingerprint, _parse_version(
            b"tinySA4_v1.4-200-g26fc821\r\nch> ").firmware_fingerprint)
        request = TinySaSweepRequest(selected.selected, selected.revision, 100_000_000, 300_000_000, 1001,
            settings=TinySaSweepSettingsPlan(rbw_mode=TinySaRbwMode.MANUAL, rbw_hz=300_000),
            input_mode=TinySaInputMode.LOW, readback_settings=True)
        g.application.start_sweep(request)
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        line = g.instrument.poll_latest().line
        self.assertIsNotNone(line)
        self.assertEqual(line.instrument.firmware_fingerprint, selected.selected.binding.calibration_identity.firmware_fingerprint)
        self.assertEqual(line.instrument.settings.plan.rbw_hz, 300_000)
        self.assertEqual(line.instrument.settings.actual_rbw_hz, 30_000)
        self.assertEqual(len(g.serials), 1)
        self.assertIn(b"rbw 300\r", writes(g.serials[0]))
        g.application.stop()
        self.assertFalse(g.serials[0].is_open)

    def test_model_input_settings_matrix_refuses_before_serial(self):
        cases = (
            (TinySaModel.ULTRA, {"input_mode": TinySaInputMode.HIGH}),
            (TinySaModel.BASIC, {"settings": TinySaSweepSettingsPlan(lna=TinySaSwitchPolicy.OFF)}),
            (TinySaModel.BASIC, {"settings": TinySaSweepSettingsPlan(spur_removal=TinySaSpurPolicy.AUTO)}),
            (TinySaModel.BASIC, {"input_mode": TinySaInputMode.HIGH}),
            (TinySaModel.BASIC, {"input_mode": TinySaInputMode.LOW, "stop_hz": 400_000_000}),
            (TinySaModel.BASIC, {"settings": TinySaSweepSettingsPlan(rbw_mode=TinySaRbwMode.MANUAL, rbw_hz=200)}),
            (TinySaModel.BASIC, {"settings": TinySaSweepSettingsPlan(attenuation_mode=TinySaAttenuationMode.AUTO)}),
            (TinySaModel.ULTRA, {"settings": TinySaSweepSettingsPlan(lna=TinySaSwitchPolicy.ON,
                                    attenuation_mode=TinySaAttenuationMode.MANUAL, attenuation_db=10)}),
        )
        for model, args in cases:
            with self.subTest(model=model, args=args):
                g, request = self.selected(model)
                with self.assertRaises(ValueError):
                    replace(request, **args)
                self.assertEqual(g.serials, [])
        g, request = self.selected(TinySaModel.BASIC)
        high = replace(request, input_mode=TinySaInputMode.HIGH, start_hz=500_000_000, stop_hz=600_000_000)
        self.assertEqual(compile_tinysa_runtime_settings(high.settings, high.input_mode, model_id="tinysa_basic",
            control_contract=TINYSA_RUNTIME_CONTROL_CONTRACT, start_hz=high.start_hz, stop_hz=high.stop_hz),
            ("mode high input",))

    def test_unknown_firmware_preserves_but_cannot_set_or_query_profile(self):
        g = common.graph()
        self.addCleanup(g.application.shutdown)
        g.application.select_device(g.application.discover()[0].device_id)
        selected = g.sources.current()
        request = TinySaSweepRequest(selected.selected, selected.revision, 87_500_000, 108_000_000, 3)
        self.assertIsNone(request.source.binding.snapshot.runtime_control_contract)
        for change in ({"readback_settings": True}, {"input_mode": TinySaInputMode.LOW},
                       {"settings": TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.FAST)}):
            with self.subTest(change=change), self.assertRaises(TinySaSettingsUnsupported):
                replace(request, **change)
        self.assertEqual(g.serials, [])

    def test_same_owner_exact_full_plan_post_pass_queries_and_quantized_readback(self):
        g, request = self.selected()
        request = replace(request, settings=full_plan(), input_mode=TinySaInputMode.LOW, readback_settings=True)
        g.application.start_sweep(request)
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        frame = g.instrument.poll_latest().line
        obs = frame.instrument.settings
        self.assertEqual(obs.plan, request.settings)
        self.assertEqual(obs.input_mode, TinySaInputMode.LOW)
        self.assertEqual(len(obs.acknowledged_commands), 8)
        self.assertEqual((obs.actual_rbw_hz, obs.actual_attenuation_db, obs.screen_sweep_time_s), (30000, 12, .123))
        self.assertEqual(obs.plan.rbw_hz, 10000)  # Actual != target, never relabel as exact applied profile.
        self.assertEqual(writes(g.serials[0]), [b"version\r"] +
            [(c + "\r").encode("ascii") for c in obs.acknowledged_commands] +
            [b"zero ?\r", b"scanraw 87500000 108000000 3 0\r", b"rbw ?\r", b"attenuate ?\r", b"sweeptime ?\r"])
        self.assertEqual(len(g.serials), 1)
        self.assertEqual(g.serials[0].calls.count("close"), 1)
        self.assertIn("30", tinysa_settings_readout(frame))
        g.application.stop()
        self.assertFalse(g.catalog.cleanup_pending)
        self.assertIsNone(g.live._external_analyzer_owner)

    def test_explicit_query_only_preserves_state_and_ack_only_cannot_imply_readback(self):
        g, request = self.selected()
        g.application.start_sweep(replace(request, readback_settings=True))
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        obs = g.instrument.poll_latest().line.instrument.settings
        self.assertEqual(obs.acknowledged_commands, ())
        self.assertEqual(writes(g.serials[0]), [b"version\r", b"zero ?\r", b"scanraw 87500000 108000000 3 0\r",
                                              b"rbw ?\r", b"attenuate ?\r", b"sweeptime ?\r"])
        g.application.stop()
        g.application.start_sweep(replace(request, settings=TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.FAST)))
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        obs = g.instrument.poll_latest().line.instrument.settings
        self.assertEqual(obs.acknowledged_commands, ("sweep fast",))
        self.assertIsNone(obs.actual_rbw_hz)
        self.assertIsNone(obs.actual_attenuation_db)
        self.assertIsNone(obs.screen_sweep_time_s)
        self.assertNotIn(b"rbw ?\r", writes(g.serials[1]))

    def test_settings_response_usage_is_refusal_not_ack_and_no_scan_retry(self):
        for fault in ("usage", "short", "changed_version", "readback"):
            with self.subTest(fault=fault):
                g, request = self.selected()
                factory = g.provider._acquisition_factory
                def bad(*args, factory=factory, fault=fault):
                    owner = factory(*args)
                    original = owner._factory
                    def serial(route):
                        raw = original(route)
                        if fault == "changed_version":
                            raw.version = b"tinySA4_new-g26fc821\r\nch> "
                        raw.short_command = b"sweep fast\r" if fault == "short" else None
                        write = raw.write
                        def broken(command):
                            count = write(command)
                            if fault == "usage" and command == b"sweep fast\r":
                                raw.response = b"usage: PRIVATE invalid sweep\r\nch> "
                            if fault == "readback" and command == b"rbw ?\r":
                                raw.response = b"PRIVATE not numeric\r\nch> "
                            return count
                        raw.write = broken
                        return raw
                    owner._factory = serial
                    return owner
                g.provider._acquisition_factory = bad
                g.application.start_sweep(replace(request, settings=TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.FAST),
                                                 readback_settings=True))
                self.wait(lambda g=g: g.instrument.poll_latest().metrics.has_error)
                self.assertIsNone(g.instrument.poll_latest().line)
                self.assertNotIn("PRIVATE", g.instrument.poll_latest().metrics.error)
                expected = TinySaAcquisitionPhase.READBACK if fault == "readback" else (
                    TinySaAcquisitionPhase.VERSION if fault == "changed_version" else TinySaAcquisitionPhase.SETTINGS)
                self.assertEqual(g.instrument._owner.failure.phase, expected)
                self.assertEqual(sum(c.startswith(b"scanraw ") for c in writes(g.serials[0])), int(fault == "readback"))
                g.application.stop()
                self.assertFalse(g.catalog.cleanup_pending)

    def test_repeat_sets_profile_once_queries_each_pass_and_keeps_one_serial(self):
        g, request = self.selected()
        source = request.source
        owner = g.catalog.prepare_tinysa_acquisition(source.binding, source.runtime)
        scan = TinySaScanRawRequest(TinySaModel.ULTRA, request.start_hz, request.stop_hz, 3)
        plan = TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.FAST)
        owner.configure_runtime_settings(scan, plan, TinySaInputMode.LOW, readback=True)
        passes = []
        def publish(result):
            passes.append((result, owner.settings_observation))
            if len(passes) == 2:
                owner.cancel()
        with self.assertRaises(TinySaTraceCollectionCancelled):
            owner.collect_repeated(scan, publish, interval_s=.05)
        sent = writes(g.serials[0])
        self.assertEqual((sent.count(b"mode low input\r"), sent.count(b"sweep fast\r")), (1, 1))
        self.assertEqual((sent.count(b"version\r"), sent.count(b"rbw ?\r")), (2, 2))
        self.assertEqual(len(passes), 2)
        self.assertIsNot(passes[0][1], passes[1][1])
        self.assertFalse(owner.cleanup_pending)
        self.assertEqual(g.serials[0].calls.count("close"), 1)

    def test_configured_request_cannot_change_before_collect(self):
        g, request = self.selected()
        source = request.source
        owner = g.catalog.prepare_tinysa_acquisition(source.binding, source.runtime)
        scan = TinySaScanRawRequest(TinySaModel.ULTRA, request.start_hz, request.stop_hz, 3)
        owner.configure_runtime_settings(scan, full_plan(), TinySaInputMode.LOW, readback=True)
        with self.assertRaises(ValueError):
            owner.collect(replace(scan, points=4))
        self.assertEqual(g.serials, [])
        owner.close()

    def test_configured_failed_close_keeps_same_owner_graph_and_never_reapplies_setters(self):
        g, request = self.selected()
        factory = g.provider._acquisition_factory
        def acquisition(*args):
            owner = factory(*args)
            original = owner._factory
            def serial(route):
                raw = original(route)
                raw.close_error = True
                return raw
            owner._factory = serial
            return owner
        g.provider._acquisition_factory = acquisition
        request = replace(request, settings=full_plan(), input_mode=TinySaInputMode.LOW, readback_settings=True)
        g.application.start_sweep(request)
        self.wait(lambda: g.instrument.poll_latest().metrics.has_error)
        serial = g.serials[0]
        before = writes(serial)
        self.assertTrue(g.catalog.cleanup_pending)
        self.assertIs(g.live._external_analyzer_owner, g.instrument)
        self.assertIsNone(g.instrument.poll_latest().line)
        serial.close_error = False
        g.application.stop()
        self.assertEqual(writes(serial), before)
        self.assertEqual(len(g.serials), 1)
        self.assertFalse(g.catalog.cleanup_pending)
        self.assertIsNone(g.live._external_analyzer_owner)

    def test_cancel_consumed_setter_or_readback_finishes_prompt_no_next_command(self):
        for target in (b"sweep fast\r", b"rbw ?\r"):
            with self.subTest(target=target):
                g, request = self.selected()
                source = request.source
                owner = g.catalog.prepare_tinysa_acquisition(source.binding, source.runtime)
                scan = TinySaScanRawRequest(TinySaModel.ULTRA, request.start_hz, request.stop_hz, 3)
                owner.configure_runtime_settings(scan, TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.FAST),
                                                 TinySaInputMode.PRESERVE, readback=True)
                factory = owner._factory
                def serial_factory(route, factory=factory, owner=owner, target=target):
                    serial = factory(route)
                    read = serial.read
                    def cancel_read(size):
                        chunk = read(min(3, size))
                        if writes(serial)[-1] == target:
                            owner.cancel()
                        return chunk
                    serial.read = cancel_read
                    return serial
                owner._factory = serial_factory
                with self.assertRaises(TinySaTraceCollectionCancelled):
                    owner.collect(scan)
                self.assertEqual(writes(g.serials[0])[-1], target)
                self.assertEqual(g.serials[0].response, b"")
                self.assertFalse(owner.cleanup_pending)
                self.assertIsNone(owner.settings_observation)

    def test_readback_bounds_and_original_absolute_deadline_fail_closed(self):
        g, request = self.selected()
        source = request.source
        owner = g.catalog.prepare_tinysa_acquisition(source.binding, source.runtime)
        scan = TinySaScanRawRequest(TinySaModel.ULTRA, request.start_hz, request.stop_hz, 3)
        owner.configure_runtime_settings(scan, TinySaSweepSettingsPlan(), TinySaInputMode.PRESERVE, readback=True)
        ticks = iter(range(1000))
        owner._monotonic = lambda: next(ticks) / 10
        original = owner._factory
        def factory(route):
            serial = original(route)
            write = serial.write
            def broken(command):
                count = write(command)
                if command == b"rbw ?\r":
                    serial.response = b"rbw ?\r\n30000.0Hz\r\n"  # No prompt; never a successful query.
                return count
            serial.write = broken
            return serial
        owner._factory = factory
        with self.assertRaises(TinySaTraceCollectionError):
            owner.collect(scan)
        self.assertEqual(owner.failure.reason, TinySaTraceFailureReason.DEADLINE)
        self.assertEqual(owner.failure.phase, TinySaAcquisitionPhase.READBACK)
        self.assertFalse(owner.cleanup_pending)
        for args in ({"actual_rbw_hz": 0}, {"actual_attenuation_db": 41}, {"screen_sweep_time_s": float("nan")},
                     {"acknowledged_commands": ("reset",)}):
            fields = {"plan": TinySaSweepSettingsPlan(), "input_mode": TinySaInputMode.PRESERVE, "acknowledged_commands": ()}
            fields.update(args)
            with self.subTest(args=args), self.assertRaises(ValueError):
                TinySaSettingsObservation(**fields)


class TinySaSettingsActualUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("tinySA actual composition timeout")
            time.sleep(.001)
        self.app.processEvents()

    def test_actual_root_drawer_draft_locale_escape_same_canvas_and_post_pass_readout(self):
        g = settings_graph()
        captures = []
        def capture(*args, **kwargs):
            composition = compose_v2_live_product(*args, **kwargs)
            captures.append(composition)
            return composition
        services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_tinysa=g.instrument,
            analyzer_hackrf=None, sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            shell = build_v2_shell(services)
        c = captures[0]
        page = shell._workspace_pages["analyzer"]
        old_locale = current_locale()
        try:
            shell.resize(1100, 750)
            shell.show()
            page.discover.click()
            self.wait(lambda: page.source.count() == 2 and not c.view_model.state.busy)
            page.source.setCurrentIndex(1)
            self.wait(lambda: c.analyzer_view_model.state.tinysa_controls_available and not c.view_model.state.busy)
            page.settings.click()
            drawer = page.tinysa_bar.settings_drawer
            self.assertFalse(drawer.isHidden())
            self.assertEqual(drawer.scroll_area.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.assertEqual(drawer.input.property("ui2Role"), "utility-select")
            self.assertEqual(drawer.rbw.property("ui2Role"), "range-control")
            self.assertEqual(drawer.readback.property("ui2Role"), "utility-toggle")
            self.assertEqual(page.tinysa_bar.start.property("ui2Role"), "range-control")
            self.assertEqual(page.tinysa_bar.summary.property("ui2Role"), "secondary")
            drawer.input.setCurrentIndex(1)
            drawer.accuracy.setCurrentIndex(3)
            drawer.rbw_mode.setCurrentIndex(2)
            drawer.rbw.setValue(10)
            drawer.average.setValue(4)
            page.tinysa_bar.points.setValue(3100)
            draft = page.tinysa_bar.request()
            self.assertTrue(draft.readback_settings)
            self.assertEqual(draft.settings.repeat_count, 4)
            self.assertEqual(g.serials, [])
            for locale in (UiLocale.EN, UiLocale.RU):
                set_active_locale(locale)
                page.set_locale()
                self.assertEqual(page.tinysa_bar.request(), draft)
                self.assertEqual(drawer.rbw.accessibleName(), text("tinysa.settings.rbw_target"))
            QTest.keyClick(drawer.accuracy, Qt.Key.Key_Escape)
            self.assertTrue(drawer.isHidden())
            self.assertEqual(g.serials, [])
            original_scene = page.visualization.spectrum_scene
            page.primary.click()
            self.wait(lambda: c.analyzer_view_model.state.bundle is not None and not c.analyzer_view_model.state.controls_locked)
            bundle = c.analyzer_view_model.state.bundle
            self.assertIs(page.visualization.spectrum_scene, original_scene)
            self.assertEqual(bundle.unit, "dBm")
            self.assertEqual(bundle.spectrum.instrument.settings.plan, draft.settings)
            self.assertEqual(bundle.spectrum.instrument.settings.actual_rbw_hz, 30_000)
            self.assertEqual(page.applied.text(), tinysa_settings_readout(bundle.spectrum))
            self.assertEqual(len(c.analyzer_view_model.state.prepared_sweep.waterfall_rows), 1)
            self.assertEqual(len(g.serials), 1)
            self.assertFalse(g.catalog.cleanup_pending)
            self.assertIsNone(g.live._external_analyzer_owner)
            # Cached preparation cannot invent/lose settings or belong to a
            # differently admitted profile while keeping the same source/grid.
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
                self.assertIs(guard.state.bundle.spectrum, snapshot.line)
                line = snapshot.line
                variants = (
                    replace(line.instrument, settings=None),
                    replace(line.instrument, settings=replace(line.instrument.settings, actual_rbw_hz=None)),
                    replace(line.instrument, settings=TinySaSettingsObservation(TinySaSweepSettingsPlan(),
                        TinySaInputMode.PRESERVE, ())),
                )
                for provenance in variants:
                    foreign = replace(snapshot, line=replace(line, instrument=provenance))
                    guard._on_sweep_snapshot(prepare_sweep_snapshot(foreign, foreign.analyzer_bundle))
                    self.assertIs(guard.state.bundle.spectrum, line)
                    self.assertIn("Rejected stale/foreign", guard.state.error)
                port.instrument_run_identity = replace(run, request=replace(run.request,
                    settings=TinySaSweepSettingsPlan()))
                guard._on_sweep_snapshot(prepared)
                self.assertIn("Rejected stale/foreign", guard.state.error)
            finally:
                guard.dispose()
        finally:
            set_active_locale(old_locale)
            shell.close()
            self.wait(lambda: shell._is_closed)
            c.shutdown()

    def test_basic_unknown_and_locked_drawer_controls_do_not_invent_support(self):
        from sdr_monitor.ui.v2.workspaces.analyzer_tinysa_configuration import TinySaConfigurationBar

        for model, known in ((TinySaModel.BASIC, True), (TinySaModel.ULTRA, False)):
            with self.subTest(model=model, known=known):
                g = settings_graph(model) if known else common.graph()
                g.application.select_device(g.application.discover()[0].device_id)
                selection = g.sources.current()
                state = SimpleNamespace(tinysa_controls_available=True, source_selection=selection, controls_locked=False)
                bar = TinySaConfigurationBar(SimpleNamespace(state=state))
                try:
                    bar.apply_view_state(state)
                    drawer = bar.settings_drawer
                    self.assertEqual(drawer.accuracy.isEnabled(), known)
                    self.assertFalse(drawer.lna.isEnabled())
                    if model is TinySaModel.BASIC:
                        self.assertEqual(bar.stop.maximum(), 960)
                        self.assertEqual((drawer.rbw.minimum(), drawer.rbw.maximum()), (2, 600))
                        self.assertFalse(drawer.spur.model().item(3).isEnabled())
                        drawer.input.setCurrentIndex(2)
                        self.assertFalse(bar.valid)  # Existing FM band is not a HIGH-input range.
                        bar.start.setValue(500)
                        bar.stop.setValue(600)
                        self.assertTrue(bar.valid)
                        drawer.attenuation_mode.setCurrentIndex(1)
                        self.assertFalse(bar.valid)  # Never treat HIGH as the 0..31 dB LOW attenuator.
                        drawer.attenuation_mode.setCurrentIndex(0)
                    else:
                        self.assertIn(text("tinysa.settings.unknown"), drawer.contract.text())
                        self.assertTrue(bar.valid)  # Explicit preserve compatibility, not input proof.
                    state.controls_locked = True
                    bar.apply_view_state(state)
                    self.assertFalse(drawer.input.isEnabled())
                    self.assertFalse(drawer.accuracy.isEnabled())
                    self.assertFalse(drawer.average.isEnabled())
                    self.assertFalse(drawer.readback.isEnabled())
                    self.assertEqual(g.serials, [])
                finally:
                    bar.settings_drawer.close()
                    bar.close()
                    g.application.shutdown()

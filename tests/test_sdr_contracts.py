from __future__ import annotations

import gc
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np

from esw_dfl.domain import MeasurementMetadata, MeasurementSession, SourceDescriptor
from esw_dfl.sdr import native_api
from esw_dfl.sdr.contracts import (
    CONTRACT_SCHEMA_NAME,
    CONTRACT_SCHEMA_VERSION,
    BackendKind,
    CalibrationStatus,
    ContractValidationError,
    DetectorType,
    DeviceCapabilities,
    DeviceConfig,
    DeviceState,
    DspConfig,
    EngineMetrics,
    GainMode,
    IqBlock,
    NumericRange,
    PersistenceConfig,
    PersistenceMode,
    PrecisionMode,
    QualityFlag,
    RecordingConfig,
    SampleFormat,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
    SweepConfig,
    WindowType,
    calibrated_unit,
    config_to_native,
    contract_from_dict,
    contract_from_json,
    contract_to_dict,
    contract_to_json,
    enum_wire_schema,
    source_descriptor_from_native,
    source_descriptor_to_native,
    validate_unit_calibration,
)


def source_descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        source_type=SourceType.SYNTHETIC,
        source_id="synthetic:test",
        display_name="Synthetic test",
        uri="synthetic:test",
        metadata={"purpose": "unit", "version": 1},
        backend_id=BackendKind.CPU.value,
    )


def valid_dsp(**changes: object) -> DspConfig:
    values: dict[str, object] = {
        "fft_size": 1024,
        "hop_size": 512,
        "window": WindowType.HANN,
        "detector": DetectorType.SAMPLE,
        "unit": SpectrumUnit.DBFS_BIN,
        "precision_mode": PrecisionMode.ACCURATE_F32_F64_ACCUM,
    }
    values.update(changes)
    return DspConfig(**values)


class PythonContractTests(unittest.TestCase):
    def test_schema_identity_and_all_required_enums(self) -> None:
        self.assertEqual(CONTRACT_SCHEMA_NAME, "sdr-native-contracts")
        self.assertEqual(CONTRACT_SCHEMA_VERSION, 3)
        schema = enum_wire_schema()
        for name in (
            "SourceType",
            "SpectrumUnit",
            "SampleFormat",
            "WindowType",
            "DetectorType",
            "GainMode",
            "DeviceState",
            "CalibrationStatus",
            "QualityFlag",
            "BackendKind",
            "PrecisionMode",
            "PersistenceMode",
            "EngineState",
            "OverflowPolicy",
            "EventSeverity",
        ):
            with self.subTest(name=name):
                self.assertIn(name, schema)
                self.assertTrue(schema[name])
        self.assertEqual(schema["SourceType"]["DFL_FILE"], "dfl_file")
        self.assertEqual(schema["SpectrumUnit"]["DBM_HZ"], "dBm/Hz")
        self.assertEqual(schema["QualityFlag"]["CUDA_FALLBACK"], 1 << 14)
        self.assertEqual(DeviceState.SHUTTING_DOWN.value, "shutting_down")

    def test_configs_are_frozen_and_validate_before_native_call(self) -> None:
        config = valid_dsp()
        with self.assertRaises(FrozenInstanceError):
            config.fft_size = 2048  # type: ignore[misc]
        invalid_cases = (
            {"fft_size": 0},
            {"fft_size": 1023},
            {"fft_size": 128},
            {"hop_size": 0},
            {"hop_size": 1025},
            {"kaiser_beta": float("nan")},
            {"window": "hann"},
        )
        for changes in invalid_cases:
            with self.subTest(changes=changes), self.assertRaises(ContractValidationError):
                valid_dsp(**changes)

        with self.assertRaises(ContractValidationError):
            DeviceConfig("source", "usb:", 100e6, -1.0, 1e6)
        with self.assertRaises(ContractValidationError):
            PersistenceConfig(
                enabled=True,
                mode=PersistenceMode.ROLLING_EXACT,
                power_min_db=-10.0,
                power_max_db=-10.0,
            )
        with self.assertRaises(ContractValidationError):
            SweepConfig(200e6, 100e6, 2e6, 1.5e6, 100e3, 1024, 512)
        with self.assertRaises(ContractValidationError):
            RecordingConfig(enabled=True, output_uri=None, record_iq=True)

    def test_fft_hop_window_property_matrix(self) -> None:
        valid_count = 0
        for fft_size in (256, 512, 1024, 2048, 4096, 8192):
            for hop_size in (1, fft_size):
                for window in WindowType:
                    config = valid_dsp(
                        fft_size=fft_size,
                        hop_size=hop_size,
                        window=window,
                    )
                    self.assertLessEqual(config.hop_size, config.fft_size)
                    valid_count += 1
            with self.assertRaises(ContractValidationError):
                valid_dsp(fft_size=fft_size, hop_size=fft_size + 1)
        self.assertEqual(valid_count, 6 * 2 * len(WindowType))

    def test_unit_semantics_reject_false_dbm(self) -> None:
        self.assertFalse(calibrated_unit(SpectrumUnit.DBFS_BIN))
        self.assertTrue(calibrated_unit(SpectrumUnit.DBM))
        with self.assertRaises(ContractValidationError):
            validate_unit_calibration(
                SpectrumUnit.DBM,
                CalibrationStatus.UNCALIBRATED,
                None,
            )
        with self.assertRaises(ContractValidationError):
            validate_unit_calibration(
                SpectrumUnit.DBFS_HZ,
                CalibrationStatus.APPLIED,
                "profile",
            )
        calibrated = valid_dsp(
            unit=SpectrumUnit.DBM_HZ,
            calibration_status=CalibrationStatus.APPLIED,
            calibration_profile_id="profile-1",
        )
        self.assertEqual(calibrated.unit, SpectrumUnit.DBM_HZ)

    def test_source_descriptor_is_immutable_and_session_is_backward_compatible(self) -> None:
        descriptor = source_descriptor()
        with self.assertRaises(TypeError):
            descriptor.metadata["new"] = "value"  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            descriptor.source_id = "changed"  # type: ignore[misc]

        legacy = MeasurementSession(
            "legacy",
            Path("sample.dfl"),
            "Sample",
            MeasurementMetadata(),
        )
        self.assertIsNone(legacy.source_descriptor)
        self.assertEqual(legacy.source_path, Path("sample.dfl"))
        live = MeasurementSession(
            "live",
            Path("."),
            "Live",
            MeasurementMetadata(),
            source_descriptor=descriptor,
        )
        self.assertIs(live.source_descriptor, descriptor)
        self.assertTrue(hasattr(live, "source_path"))

    def test_iq_and_spectrum_python_arrays_are_private_and_read_only(self) -> None:
        raw = np.arange(16, dtype=np.uint8)
        block = IqBlock(
            source_sequence=1,
            first_sample_index=0,
            timestamp_ns=1,
            center_frequency_hz=100e6,
            sample_rate_hz=2e6,
            sample_format=SampleFormat.COMPLEX_INT16_LE,
            sample_count=4,
            flags=QualityFlag.TIMESTAMP_ESTIMATED,
            samples=raw,
            config_generation=1,
        )
        self.assertFalse(block.samples.flags.writeable)
        raw[0] = 255
        self.assertEqual(int(block.samples[0]), 0)
        with self.assertRaises(ValueError):
            block.samples[0] = 1

        frequencies = np.linspace(99e6, 101e6, 4, dtype=np.float64)
        values = np.arange(4, dtype=np.float32)
        frame = SpectrumFrame(
            source=source_descriptor(),
            frame_sequence=1,
            first_sample_index=0,
            timestamp_ns=1,
            config_generation=1,
            center_frequency_hz=100e6,
            sample_rate_hz=2e6,
            analog_bandwidth_hz=1.5e6,
            fft_bin_width_hz=500e3,
            enbw_hz=750e3,
            nominal_rbw_hz=750e3,
            fft_size=4,
            hop_size=2,
            window=WindowType.HANN,
            detector=DetectorType.SAMPLE,
            precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
            unit=SpectrumUnit.DBFS_BIN,
            frequencies_hz=frequencies,
            values=values,
        )
        self.assertFalse(frame.frequencies_hz.flags.writeable)
        self.assertFalse(frame.values.flags.writeable)
        values[0] = 99.0
        self.assertEqual(float(frame.values[0]), 0.0)
        with self.assertRaises(ContractValidationError):
            SpectrumFrame(
                **{
                    item: getattr(frame, item)
                    for item in frame.__dataclass_fields__
                    if item not in {"frequencies_hz"}
                },
                frequencies_hz=np.array([1.0, float("nan"), 3.0, 4.0]),
            )

    def test_versioned_serialization_roundtrip_and_unknowns(self) -> None:
        capabilities = DeviceCapabilities(
            backend_id="synthetic",
            device_id="p02",
            serial="test",
            model="contract-source",
            firmware="none",
            tuning_range_hz=NumericRange(1e6, 6e9),
            sample_rate_ranges_hz=(NumericRange(1e3, 100e6),),
            analog_bandwidth_ranges_hz=(NumericRange(1e3, 56e6),),
            gain_range_db=NumericRange(-10.0, 73.0, 1.0),
            gain_modes=(GainMode.MANUAL, GainMode.SLOW_ATTACK),
            sample_formats=(SampleFormat.COMPLEX_INT16_LE,),
        )
        contracts = (
            source_descriptor(),
            NumericRange(1.0, 2.0, 0.1),
            DeviceConfig("source", "usb:", 100e6, 2e6, 1.5e6),
            valid_dsp(),
            PersistenceConfig(),
            SweepConfig(100e6, 200e6, 2e6, 1.5e6, 100e3, 1024, 512),
            RecordingConfig(),
            capabilities,
        )
        for original in contracts:
            with self.subTest(contract=type(original).__name__):
                payload = contract_to_dict(original)
                restored = contract_from_dict(json.loads(json.dumps(payload)))
                self.assertEqual(restored, original)
                self.assertEqual(contract_from_json(contract_to_json(original)), original)

        bad_version = contract_to_dict(valid_dsp())
        bad_version["schema_version"] = 999
        with self.assertRaises(ContractValidationError):
            contract_from_dict(bad_version)
        bad_enum = contract_to_dict(valid_dsp())
        bad_enum["data"]["window"] = "unknown"  # type: ignore[index]
        with self.assertRaises(ContractValidationError):
            contract_from_dict(bad_enum)

    def test_metrics_reject_negative_or_non_finite_values(self) -> None:
        self.assertEqual(EngineMetrics().fft_frames_computed, 0)
        with self.assertRaises(ContractValidationError):
            EngineMetrics(end_to_end_latency_ms=-0.1)
        with self.assertRaises(ContractValidationError):
            EngineMetrics(analytical_fft_rate=float("nan"))


@unittest.skipUnless(
    native_api.native_availability().available
    and hasattr(native_api.require_native(), "contract_schema"),
    "compiled P02 _sdr_native module is unavailable",
)
class NativeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = native_api.require_native()

    def test_native_and_python_enum_schemas_match_exactly(self) -> None:
        schema = dict(self.native.contract_schema())
        self.assertEqual(schema["schema"], CONTRACT_SCHEMA_NAME)
        self.assertEqual(schema["schema_version"], CONTRACT_SCHEMA_VERSION)
        native_enums = {
            str(name): dict(values)
            for name, values in dict(schema["enums"]).items()
        }
        self.assertEqual(native_enums, enum_wire_schema())

    def test_config_conversion_is_validated_and_native_objects_are_immutable(self) -> None:
        configs = (
            DeviceConfig("source", "usb:", 100e6, 2e6, 1.5e6),
            valid_dsp(),
            PersistenceConfig(),
            SweepConfig(100e6, 200e6, 2e6, 1.5e6, 100e3, 1024, 512),
            RecordingConfig(),
        )
        for config in configs:
            with self.subTest(config=type(config).__name__):
                native_config = config_to_native(config)
                self.assertEqual(native_config.schema_version, CONTRACT_SCHEMA_VERSION)
                with self.assertRaises(AttributeError):
                    native_config.schema_version = 2

        with self.assertRaises(self.native.ConfigurationError):
            self.native.DspConfig(
                0,
                0,
                self.native.WindowType.HANN,
                self.native.DetectorType.SAMPLE,
                self.native.SpectrumUnit.DBFS_BIN,
                self.native.PrecisionMode.ACCURATE_F32_F64_ACCUM,
                1,
                1,
                8.6,
                self.native.CalibrationStatus.UNCALIBRATED,
                "",
                1,
            )

    def test_source_descriptor_conversion_roundtrip(self) -> None:
        original = source_descriptor()
        native = source_descriptor_to_native(original)
        restored = source_descriptor_from_native(native)
        self.assertEqual(restored, original)
        with self.assertRaises(AttributeError):
            native.source_id = "mutated"

    def test_native_numpy_views_are_read_only_and_keep_old_snapshot_alive(self) -> None:
        first = self.native._make_test_spectrum_frame(64, 1)
        first_values = first.values
        first_frequencies = first.frequencies_hz
        expected = first_values.copy()
        self.assertFalse(first_values.flags.writeable)
        self.assertFalse(first_frequencies.flags.writeable)
        with self.assertRaises(ValueError):
            first_values[0] = 0.0

        second = self.native._make_test_spectrum_frame(64, 2)
        del first
        del second
        gc.collect()
        np.testing.assert_array_equal(first_values, expected)
        self.assertIsNotNone(first_values.base)

        mirror = SpectrumFrame.from_native(self.native._make_test_spectrum_frame(64, 3))
        self.assertFalse(mirror.values.flags.writeable)
        self.assertEqual(mirror.frame_sequence, 3)
        self.assertEqual(mirror.source.source_type, SourceType.SYNTHETIC)

    def test_native_iq_view_and_capabilities_contract(self) -> None:
        block = self.native._make_test_iq_block(
            4,
            self.native.SampleFormat.COMPLEX_INT16_LE,
        )
        self.assertEqual(block.samples.dtype, np.uint8)
        self.assertEqual(block.samples.size, 16)
        self.assertFalse(block.samples.flags.writeable)

        numeric = self.native.NumericRange(1.0, 2.0, 0.1)
        capabilities = self.native.DeviceCapabilities(
            "synthetic",
            "p02",
            "serial",
            "contract",
            "none",
            numeric,
            [numeric],
            [numeric],
            numeric,
            [self.native.GainMode.MANUAL],
            [self.native.SampleFormat.COMPLEX_INT16_LE],
            False,
            False,
            False,
            True,
            True,
            CONTRACT_SCHEMA_VERSION,
        )
        self.assertEqual(capabilities.backend_id, "synthetic")
        self.assertEqual(self.native.EngineMetrics().fft_frames_computed, 0)


if __name__ == "__main__":
    unittest.main()

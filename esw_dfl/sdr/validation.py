"""P15 deterministic validation, benchmark, soak, and hardware evidence helpers."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import tempfile
import time
from typing import Any

import numpy as np

from ..domain import SourceDescriptor
from .calibration_store import CalibrationPoint, CalibrationProfile, CalibrationSignature, apply_calibration
from .contracts import (
    CalibrationStatus,
    CONTRACT_SCHEMA_VERSION,
    ComputeBackendKind,
    DetectorType,
    DeviceConfig,
    DspConfig,
    EngineState,
    GainMode,
    IqBlock,
    PrecisionMode,
    QualityFlag,
    SampleFormat,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
    SweepConfig,
    WindowType,
)
from .fixed_band import FixedBandEngineService, FixedBandOptions
from .native_api import native_availability, require_native
from .recording import (
    IqReplay,
    RecordingOptions,
    RecordingQueuePolicy,
    RecordingService,
    SpectrumReplay,
    estimate_storage,
)
from .reference_dsp import reference_spectrum
from .stitching import stitch_sweep
from .sweep import (
    SweepExecutionResult,
    SweepExecutionStatus,
    SweepSegmentResult,
    SweepSegmentStatus,
    SweepTiming,
    plan_sweep,
)
from .synthetic import SyntheticConfig, SyntheticScenario, generate_scenario


class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    name: str
    status: ValidationStatus
    metrics: Mapping[str, object] = field(default_factory=dict)
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status.value,
            "metrics": _json_safe(self.metrics),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    started_utc: str
    completed_utc: str
    platform: Mapping[str, object]
    results: tuple[ValidationResult, ...]

    @property
    def passed(self) -> tuple[ValidationResult, ...]:
        return tuple(item for item in self.results if item.status is ValidationStatus.PASS)

    @property
    def failed(self) -> tuple[ValidationResult, ...]:
        return tuple(item for item in self.results if item.status is ValidationStatus.FAIL)

    @property
    def not_verified(self) -> tuple[ValidationResult, ...]:
        return tuple(item for item in self.results if item.status is ValidationStatus.NOT_VERIFIED)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "sdr-native-p15-validation",
            "schema_version": 1,
            "started_utc": self.started_utc,
            "completed_utc": self.completed_utc,
            "platform": _json_safe(self.platform),
            "summary": {
                "total": len(self.results),
                "passed": len(self.passed),
                "failed": len(self.failed),
                "not_verified": len(self.not_verified),
            },
            "results": [item.to_dict() for item in self.results],
        }

    def write_evidence(self, output_dir: str | Path) -> dict[str, Path]:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        json_path = root / "p15_validation.json"
        csv_path = root / "p15_validation.csv"
        log_path = root / "p15_validation.log"
        _write_json_atomic(json_path, self.to_dict())
        _write_csv_atomic(csv_path, self.results)
        _write_text_atomic(
            log_path,
            "\n".join(f"{item.status.value}\t{item.name}\t{item.reason or ''}" for item in self.results) + "\n",
        )
        paths = {"json": json_path, "csv": csv_path, "log": log_path}
        plot = _write_plot(root, self.results)
        if plot is not None:
            paths["plot"] = plot
        return paths


def _json_safe(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _write_text_atomic(path: Path, text: str) -> None:
    temporary = Path(str(path) + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    _write_text_atomic(path, json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n")


def _write_csv_atomic(path: Path, results: Iterable[ValidationResult]) -> None:
    temporary = Path(str(path) + ".part")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("name", "status", "reason", "metrics_json"))
        for item in results:
            writer.writerow(
                (
                    item.name,
                    item.status.value,
                    item.reason or "",
                    json.dumps(_json_safe(item.metrics), ensure_ascii=False, sort_keys=True),
                )
            )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_plot(root: Path, results: Sequence[ValidationResult]) -> Path | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    colors = {
        ValidationStatus.PASS: "#2e7d32",
        ValidationStatus.FAIL: "#c62828",
        ValidationStatus.NOT_VERIFIED: "#ef6c00",
    }
    labels = [item.name.replace(".", "\n") for item in results]
    values = [
        1.0 if item.status is ValidationStatus.PASS else 0.5 if item.status is ValidationStatus.NOT_VERIFIED else 0.0
        for item in results
    ]
    figure, axis = plt.subplots(figsize=(max(8.0, len(results) * 0.34), 4.5), dpi=120)
    axis.bar(np.arange(len(results)), values, color=[colors[item.status] for item in results])
    axis.set_ylim(0.0, 1.1)
    axis.set_ylabel("status score")
    axis.set_xticks(np.arange(len(results)), labels, rotation=75, ha="right", fontsize=7)
    axis.set_title("P15 validation evidence")
    figure.tight_layout()
    path = root / "p15_validation.png"
    temporary = Path(str(path) + ".part")
    figure.savefig(temporary, format="png")
    plt.close(figure)
    os.replace(temporary, path)
    return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _memory_bytes() -> int | None:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("page_faults", wintypes.DWORD),
                    ("peak_ws", ctypes.c_size_t),
                    ("ws", ctypes.c_size_t),
                    ("peak_paged", ctypes.c_size_t),
                    ("paged", ctypes.c_size_t),
                    ("peak_nonpaged", ctypes.c_size_t),
                    ("nonpaged", ctypes.c_size_t),
                    ("pagefile", ctypes.c_size_t),
                    ("peak_pagefile", ctypes.c_size_t),
                    ("private", ctypes.c_size_t),
                ]

            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return int(counters.private)
        except Exception:
            return None
        return None
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * (1024 if platform.system() != "Darwin" else 1)
    except Exception:
        return None


def _run_case(name: str, callback: Callable[[], Mapping[str, object]]) -> ValidationResult:
    try:
        return ValidationResult(name, ValidationStatus.PASS, callback())
    except Exception as exc:
        return ValidationResult(
            name,
            ValidationStatus.FAIL,
            {"error_type": type(exc).__name__},
            "validation raised an exception",
        )


def collect_platform_fingerprint() -> dict[str, object]:
    availability = native_availability()
    return {
        "os": platform.platform(aliased=True),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "native_available": availability.available,
        "native_reason": availability.reason,
        "native_build": availability.build_info,
    }


def validate_synthetic_generators() -> Mapping[str, object]:
    config = SyntheticConfig(sample_count=4096, seed=0x50315)
    rows: list[dict[str, object]] = []
    for scenario in SyntheticScenario:
        first = generate_scenario(scenario, config)
        second = generate_scenario(scenario, config)
        if (
            first.samples.flags.writeable
            or not np.all(np.isfinite(first.samples.real))
            or not np.all(np.isfinite(first.samples.imag))
        ):
            raise AssertionError(f"{scenario.value}: invalid samples")
        first_hash = hashlib.sha256(first.samples.tobytes()).hexdigest()
        if first_hash != hashlib.sha256(second.samples.tobytes()).hexdigest():
            raise AssertionError(f"{scenario.value}: generator is not deterministic")
        rows.append(
            {
                "scenario": scenario.value,
                "sample_count": int(first.samples.size),
                "sha256": first_hash,
                "quality_flags": int(first.quality_flags),
                "peak_abs": float(np.max(np.abs(first.samples))),
            }
        )
    return {"scenario_count": len(rows), "scenarios": rows}


def _native_dsp_config(native: Any, *, unit: Any, precision: Any, fft_size: int = 1024, batch_size: int = 1) -> Any:
    return native.DspConfig(
        fft_size,
        fft_size,
        native.WindowType.HANN,
        native.DetectorType.SAMPLE,
        unit,
        precision,
        batch_size,
        1,
        8.6,
        native.CalibrationStatus.UNCALIBRATED,
        "",
        CONTRACT_SCHEMA_VERSION,
    )


def _native_backend(native: Any, preference: ComputeBackendKind) -> Any:
    if preference is ComputeBackendKind.CPU:
        return native.CpuDspBackend()
    return native.make_dsp_backend(native.DspBackendSelectionOptions(preference=preference))


def _native_samples(signal: Any) -> np.ndarray:
    return np.ascontiguousarray(np.asarray(signal.samples, dtype=np.complex64))


def _native_missing(name: str) -> ValidationResult | None:
    availability = native_availability()
    if not availability.available:
        return ValidationResult(
            name, ValidationStatus.NOT_VERIFIED, {}, availability.reason or "native extension unavailable"
        )
    return None


def validate_cpu_precision() -> Mapping[str, object]:
    native = require_native()
    config = SyntheticConfig(
        sample_count=1024, sample_rate_hz=1_024_000.0, center_frequency_hz=100_000_000.0, seed=0x515
    )
    signal = generate_scenario(SyntheticScenario.EXACT_BIN_TONE, config)
    reference = reference_spectrum(
        signal.samples, config.sample_rate_hz, center_frequency_hz=config.center_frequency_hz, window=WindowType.HANN
    )
    backend = _native_backend(native, ComputeBackendKind.CPU)
    backend.configure(
        _native_dsp_config(native, unit=native.SpectrumUnit.DBFS_HZ, precision=native.PrecisionMode.REFERENCE_F64)
    )
    backend.push_samples(_native_samples(signal), config.sample_rate_hz, config.center_frequency_hz)
    frames = backend.poll_spectrum(0)
    if len(frames) != 1:
        raise AssertionError(f"expected one native frame, got {len(frames)}")
    frame = frames[0]
    actual_axis = np.asarray(frame.frequencies_hz, dtype=np.float64)
    actual_linear = np.power(10.0, np.asarray(frame.values, dtype=np.float64) / 10.0)
    expected_linear = np.asarray(reference.psd_dbfs_per_hz_linear, dtype=np.float64)
    axis_error = float(np.max(np.abs(actual_axis - reference.frequencies_hz)))
    relative_error = float(
        np.max(np.abs(actual_linear - expected_linear)) / max(float(np.max(expected_linear)), 1.0e-30)
    )
    if axis_error != 0.0 or relative_error > 2.0e-5:
        raise AssertionError(f"CPU parity exceeded tolerance: axis={axis_error}, relative={relative_error}")
    return {
        "fft_size": 1024,
        "axis_max_error_hz": axis_error,
        "linear_psd_max_relative_error": relative_error,
        "peak_dbfs_hz": float(np.max(np.asarray(frame.values))),
        "fft_frames_computed": int(backend.metrics().fft_frames_computed),
    }


def _benchmark_backend(native: Any, preference: ComputeBackendKind, repeats: int) -> Mapping[str, object]:
    if repeats <= 0:
        raise ValueError("repeats must be positive")
    config = SyntheticConfig(
        sample_count=1024 * 128, sample_rate_hz=1_024_000.0, center_frequency_hz=100_000_000.0, seed=0x1515
    )
    samples = _native_samples(generate_scenario(SyntheticScenario.BROADBAND_NOISE, config))
    native_config = _native_dsp_config(
        native, unit=native.SpectrumUnit.DBFS_BIN, precision=native.PrecisionMode.ACCURATE_F32_F64_ACCUM, batch_size=8
    )
    warmup = _native_backend(native, preference)
    warmup.configure(native_config)
    warmup.push_samples(samples, config.sample_rate_hz, config.center_frequency_hz)
    warmup.poll_spectrum(0)
    elapsed_ms: list[float] = []
    frame_counts: list[int] = []
    before = _memory_bytes()
    for _ in range(repeats):
        backend = _native_backend(native, preference)
        backend.configure(native_config)
        started = time.perf_counter()
        backend.push_samples(samples, config.sample_rate_hz, config.center_frequency_hz)
        frames = backend.poll_spectrum(0)
        elapsed_ms.append((time.perf_counter() - started) * 1000.0)
        frame_counts.append(len(frames))
    after = _memory_bytes()
    if min(frame_counts, default=0) <= 0:
        raise AssertionError("benchmark produced no FFT frames")
    median_ms = float(statistics.median(elapsed_ms))
    frame_count = int(statistics.median(frame_counts))
    return {
        "backend": preference.value,
        "repeats": repeats,
        "sample_count": int(samples.size),
        "fft_frames_per_run": frame_count,
        "median_elapsed_ms": median_ms,
        "p95_elapsed_ms": float(np.percentile(elapsed_ms, 95.0)),
        "samples_per_second": int(samples.size) / (median_ms / 1000.0),
        "fft_frames_per_second": frame_count / (median_ms / 1000.0),
        "private_memory_before": before,
        "private_memory_after": after,
        "private_memory_delta": None if before is None or after is None else after - before,
    }


def benchmark_cpu(repeats: int = 3) -> Mapping[str, object]:
    return _benchmark_backend(require_native(), ComputeBackendKind.CPU, repeats)


def validate_cuda(repeats: int = 3) -> ValidationResult:
    unavailable = _native_missing("cuda.parity")
    if unavailable is not None:
        return unavailable
    native = require_native()
    if not bool(native.build_info().get("cuda_compiled", False)):
        return ValidationResult(
            "cuda.parity", ValidationStatus.NOT_VERIFIED, {"cuda_compiled": False}, "native build has no CUDA backend"
        )
    try:
        config = SyntheticConfig(
            sample_count=1024,
            sample_rate_hz=1_024_000.0,
            center_frequency_hz=100_000_000.0,
            seed=0x515,
        )
        signal = generate_scenario(SyntheticScenario.EXACT_BIN_TONE, config)
        native_config = _native_dsp_config(
            native,
            unit=native.SpectrumUnit.DBFS_HZ,
            precision=native.PrecisionMode.REFERENCE_F64,
        )
        outputs: dict[ComputeBackendKind, Any] = {}
        for preference in (ComputeBackendKind.CPU, ComputeBackendKind.CUDA):
            backend = _native_backend(native, preference)
            backend.configure(native_config)
            backend.push_samples(_native_samples(signal), config.sample_rate_hz, config.center_frequency_hz)
            frames = backend.poll_spectrum(0)
            if len(frames) != 1:
                raise AssertionError(f"{preference.value} parity produced {len(frames)} frames")
            outputs[preference] = frames[0]
        cpu_frame = outputs[ComputeBackendKind.CPU]
        cuda_frame = outputs[ComputeBackendKind.CUDA]
        cpu_axis = np.asarray(cpu_frame.frequencies_hz, dtype=np.float64)
        cuda_axis = np.asarray(cuda_frame.frequencies_hz, dtype=np.float64)
        axis_error = float(np.max(np.abs(cpu_axis - cuda_axis)))
        cpu_linear = np.power(10.0, np.asarray(cpu_frame.values, dtype=np.float64) / 10.0)
        cuda_linear = np.power(10.0, np.asarray(cuda_frame.values, dtype=np.float64) / 10.0)
        relative_error = float(np.max(np.abs(cpu_linear - cuda_linear)) / max(float(np.max(cpu_linear)), 1.0e-30))
        if axis_error != 0.0 or relative_error > 2.0e-5:
            raise AssertionError(f"CPU/CUDA parity exceeded tolerance: axis={axis_error}, relative={relative_error}")
        return ValidationResult(
            "cuda.parity",
            ValidationStatus.PASS,
            {
                "axis_max_error_hz": axis_error,
                "linear_psd_max_relative_error": relative_error,
                "cpu": _benchmark_backend(native, ComputeBackendKind.CPU, 1),
                "cuda": _benchmark_backend(native, ComputeBackendKind.CUDA, repeats),
            },
        )
    except Exception as exc:
        return ValidationResult(
            "cuda.parity",
            ValidationStatus.NOT_VERIFIED,
            {"error": str(exc)},
            "CUDA runtime/device could not be validated",
        )


def validate_synthetic_fixed_band() -> Mapping[str, object]:
    native = require_native()
    dsp = _native_dsp_config(
        native, unit=native.SpectrumUnit.DBFS_BIN, precision=native.PrecisionMode.REFERENCE_F64, fft_size=256
    )
    engine = native.SyntheticEngine()
    engine.configure(
        native.EngineConfig(
            block_size_samples=1024, blocks_per_second=200, max_blocks=16, spectrum_queue_capacity=128, dsp=dsp
        )
    )
    engine.start()
    deadline = time.monotonic() + 5.0
    while engine.state() == native.EngineState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.005)
    engine.join()
    metrics = engine.metrics()
    queue = engine.queue_stats(native.QueueId.DSP)
    if (
        engine.state() != native.EngineState.STOPPED
        or metrics.fft_frames_computed <= 0
        or metrics.fft_frames_dropped != 0
    ):
        raise AssertionError("synthetic fixed-band reference did not produce healthy FFT output")
    if queue.high_water > queue.capacity:
        raise AssertionError("DSP queue exceeded capacity")
    return {
        "engine_state": str(engine.state()),
        "blocks_received": int(metrics.iq_blocks_received),
        "fft_frames_computed": int(metrics.fft_frames_computed),
        "fft_frames_dropped": int(metrics.fft_frames_dropped),
        "queue_high_water": int(queue.high_water),
        "queue_capacity": int(queue.capacity),
        "healthy": True,
    }


def _sweep_source() -> SourceDescriptor:
    return SourceDescriptor(
        source_type=SourceType.SYNTHETIC,
        source_id="p15-sweep-fixture",
        display_name="P15 sweep fixture",
        uri="synthetic:p15-sweep",
        metadata={"purpose": "validation"},
        backend_id="cpu",
    )


def _sweep_frame(segment: Any) -> SpectrumFrame:
    fft_size = int(segment.fft_size)
    frequencies = (
        segment.center_frequency_hz
        - segment.sample_rate_hz / 2.0
        + np.arange(fft_size, dtype=np.float64) * segment.sample_rate_hz / fft_size
    )
    return SpectrumFrame(
        source=_sweep_source(),
        frame_sequence=segment.segment_index,
        first_sample_index=0,
        timestamp_ns=1_000_000_000 + segment.segment_index,
        config_generation=1,
        center_frequency_hz=segment.center_frequency_hz,
        sample_rate_hz=segment.sample_rate_hz,
        analog_bandwidth_hz=segment.analog_bandwidth_hz,
        fft_bin_width_hz=segment.sample_rate_hz / fft_size,
        enbw_hz=segment.sample_rate_hz / fft_size,
        nominal_rbw_hz=segment.sample_rate_hz / fft_size,
        fft_size=fft_size,
        hop_size=segment.hop_size,
        window=WindowType.HANN,
        detector=DetectorType.SAMPLE,
        precision_mode=PrecisionMode.REFERENCE_F64,
        unit=SpectrumUnit.DBFS_BIN,
        frequencies_hz=frequencies,
        values=np.full(fft_size, -50.0 + segment.segment_index * 0.25, dtype=np.float32),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        quality_flags=QualityFlag.UNCALIBRATED,
    )


def validate_sweep_quality() -> Mapping[str, object]:
    plan = plan_sweep(SweepConfig(100_000_000.0, 102_200_000.0, 1_000_000.0, 800_000.0, 100_000.0, 256, 128))
    segments = tuple(
        SweepSegmentResult(
            plan=segment,
            status=SweepSegmentStatus.COMPLETED,
            frames=(_sweep_frame(segment),),
            timing=SweepTiming(total_s=0.01),
        )
        for segment in plan.segments
    )
    result = SweepExecutionResult(SweepExecutionStatus.COMPLETED, plan, segments, 1, 2, True, sweep_id=15)
    stitched = stitch_sweep(result)
    finite = np.isfinite(stitched.values)
    if not np.any(finite):
        raise AssertionError("stitched sweep has no finite bins")
    flags = np.asarray(stitched.quality_flags_per_bin, dtype=np.uint16)
    return {
        "segment_count": len(plan.segments),
        "target_bin_count": int(stitched.frequencies_hz.size),
        "finite_bins": int(np.count_nonzero(finite)),
        "missing_bins": int(np.count_nonzero(flags & np.uint16(QualityFlag.MISSING_SEGMENT))),
        "overlap_bins": int(np.count_nonzero(flags & np.uint16(QualityFlag.STITCH_OVERLAP))),
        "seam_count": len(stitched.seam_metrics),
        "coverage_gaps": list(plan.coverage_gaps_hz),
    }


def validate_calibration_residuals() -> Mapping[str, object]:
    settings = CalibrationSignature(
        "synthetic-device",
        "cpu",
        "rx0",
        1_000_000.0,
        800_000.0,
        "manual",
        10.0,
        "p05-v1",
        "dbfs-bin",
        "synthetic-chain",
    )
    frequencies = np.asarray([100_000_000.0, 101_000_000.0, 102_000_000.0], dtype=np.float64)
    raw = np.asarray([-50.0, -49.0, -48.0], dtype=np.float64)
    reference = np.asarray([-49.0, -47.5, -46.0], dtype=np.float64)
    profile = CalibrationProfile(
        profile_id="p15-synthetic-cal",
        profile_version=1,
        signature=settings,
        reference_plane="rf_input",
        points=tuple(
            CalibrationPoint(float(frequency), float(target), float(measured), float(target - measured), 0.1)
            for frequency, measured, target in zip(frequencies, raw, reference, strict=True)
        ),
        reference_equipment="synthetic-reference",
    )
    corrected = apply_calibration(frequencies, raw, profile=profile, settings=settings)
    residual = float(np.max(np.abs(corrected.values_db - reference)))
    if corrected.unit is not SpectrumUnit.DBM_BIN or residual > 1.0e-12:
        raise AssertionError(f"calibration residual {residual} or unit {corrected.unit}")
    mismatch = CalibrationSignature(
        "other-device",
        settings.backend,
        settings.rf_port_path,
        settings.sample_rate_hz,
        settings.analog_bandwidth_hz,
        settings.gain_mode,
        settings.manual_gain_db,
        settings.window_normalization_version,
        settings.fft_unit_convention,
        settings.frontend_chain,
    )
    rejected = apply_calibration(frequencies, raw, profile=profile, settings=mismatch)
    if rejected.unit is not SpectrumUnit.DBFS_BIN:
        raise AssertionError("inapplicable calibration changed unit")
    return {
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "residual_db": residual,
        "calibrated_unit": corrected.unit.value,
        "incompatible_unit": rejected.unit.value,
        "uncertainty_db": float(np.max(corrected.uncertainty_db)),
    }


def _recording_source() -> SourceDescriptor:
    return SourceDescriptor(
        source_type=SourceType.SYNTHETIC,
        source_id="p15-recording-fixture",
        display_name="P15 recording fixture",
        uri="synthetic:p15-recording",
        metadata={"purpose": "bounded recording validation"},
        backend_id="cpu",
    )


def _recording_frame(sequence: int) -> SpectrumFrame:
    size = 256
    frequencies = np.linspace(99_500_000.0, 100_500_000.0, size, dtype=np.float64)
    return SpectrumFrame(
        source=_recording_source(),
        frame_sequence=sequence,
        first_sample_index=sequence * 64,
        timestamp_ns=2_000_000_000 + sequence,
        config_generation=1,
        center_frequency_hz=100_000_000.0,
        sample_rate_hz=1_000_000.0,
        analog_bandwidth_hz=800_000.0,
        fft_bin_width_hz=1_000_000.0 / size,
        enbw_hz=1_000_000.0 / size,
        nominal_rbw_hz=1_000_000.0 / size,
        fft_size=size,
        hop_size=128,
        window=WindowType.HANN,
        detector=DetectorType.SAMPLE,
        precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
        unit=SpectrumUnit.DBFS_BIN,
        frequencies_hz=frequencies,
        values=np.full(size, -60.0 + sequence * 0.01, dtype=np.float32),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        quality_flags=QualityFlag.UNCALIBRATED,
    )


def validate_recording(recording_blocks: int = 256) -> Mapping[str, object]:
    if isinstance(recording_blocks, bool) or recording_blocks <= 0:
        raise ValueError("recording_blocks must be positive")
    before = _memory_bytes()
    with tempfile.TemporaryDirectory(prefix="sdr-p15-recording-") as directory:
        base = Path(directory) / "capture"
        forecast = estimate_storage(
            sample_rate_hz=1_000_000.0,
            duration_seconds=recording_blocks * 64 / 1_000_000.0,
            sample_format=SampleFormat.COMPLEX_FLOAT32_LE,
            spectrum_frames_per_second=1.0,
            spectrum_bins=256,
            record_iq=True,
            record_spectrum=True,
            output_uri=base,
        )
        service = RecordingService(
            RecordingOptions(
                output_uri=base,
                record_iq=True,
                record_spectrum=True,
                queue_capacity=8,
                overflow_policy=RecordingQueuePolicy.BLOCK,
            )
        )
        service.start(forecast=forecast)
        raw = np.frombuffer(np.asarray(np.ones(64, dtype=np.complex64), dtype="<c8").tobytes(), dtype=np.uint8)
        for index in range(recording_blocks):
            block = IqBlock(
                index,
                index * 64,
                3_000_000_000 + index,
                100_000_000.0,
                1_000_000.0,
                SampleFormat.COMPLEX_FLOAT32_LE,
                64,
                QualityFlag.NONE,
                raw,
                1,
            )
            if not service.submit_iq(block, timeout_s=10.0):
                raise AssertionError("recording service rejected a blocking submission")
            if index % 4 == 0 and not service.submit_spectrum(_recording_frame(index // 4), timeout_s=10.0):
                raise AssertionError("recording service rejected a spectrum submission")
        stats = service.stop()
        iq_count = sum(1 for _ in IqReplay(base))
        spectrum_count = sum(1 for _ in SpectrumReplay(base))
        paths = tuple(
            base.with_name(base.name + suffix)
            for suffix in (".sigmf-data", ".sigmf-meta", ".spectrum.jsonl", ".spectrum.json")
        )
        if any(Path(str(path) + ".part").exists() for path in paths):
            raise AssertionError("recording left a .part artifact after finalize")
        if not stats.finalized or stats.dropped_items or iq_count != recording_blocks:
            raise AssertionError(f"recording validation failed: {stats}")
        artifact_bytes = sum(path.stat().st_size for path in paths)
    after = _memory_bytes()
    return {
        "logical_iq_blocks": recording_blocks,
        "replayed_iq_blocks": iq_count,
        "replayed_spectrum_frames": spectrum_count,
        "written_iq_samples": int(stats.written_iq_samples),
        "artifact_bytes": artifact_bytes,
        "dropped_items": int(stats.dropped_items),
        "gap_count": int(stats.gap_count),
        "private_memory_before": before,
        "private_memory_after": after,
        "private_memory_delta": None if before is None or after is None else after - before,
    }


def run_offline_validation(*, benchmark_repeats: int = 3, recording_blocks: int = 256) -> ValidationReport:
    started = _utc_now()
    results: list[ValidationResult] = [
        ValidationResult("environment", ValidationStatus.PASS, collect_platform_fingerprint()),
        _run_case("synthetic.generators", validate_synthetic_generators),
    ]
    missing = _native_missing("cpu.precision")
    if missing is not None:
        results.extend(
            (
                missing,
                ValidationResult("cpu.performance", ValidationStatus.NOT_VERIFIED, {}, "native extension unavailable"),
                ValidationResult(
                    "fixed_band.synthetic", ValidationStatus.NOT_VERIFIED, {}, "native extension unavailable"
                ),
            )
        )
    else:
        results.extend(
            (
                _run_case("cpu.precision", validate_cpu_precision),
                _run_case("cpu.performance", lambda: benchmark_cpu(benchmark_repeats)),
                _run_case("fixed_band.synthetic", validate_synthetic_fixed_band),
            )
        )
    results.extend(
        (
            validate_cuda(benchmark_repeats),
            _run_case("sweep.synthetic", validate_sweep_quality),
            _run_case("calibration.residuals", validate_calibration_residuals),
            _run_case("recording.bounded", lambda: validate_recording(recording_blocks)),
        )
    )
    results.append(
        ValidationResult(
            "soak.hardware_profiles",
            ValidationStatus.NOT_VERIFIED,
            {"profiles_seconds": {"10m": 600, "1h": 3600, "8h": 28800}},
            "run the opt-in hardware harness for the requested duration",
        )
    )
    return ValidationReport(started, _utc_now(), collect_platform_fingerprint(), tuple(results))


def run_hardware_fixed_band(
    *,
    duration_seconds: float = 600.0,
    backends: Sequence[ComputeBackendKind] = (ComputeBackendKind.CPU, ComputeBackendKind.AUTO, ComputeBackendKind.CUDA),
    center_frequency_hz: float = 2_450_000_000.0,
    sample_rate_hz: float = 3_000_000.0,
    analog_bandwidth_hz: float = 1_500_000.0,
) -> tuple[ValidationResult, ...]:
    """Run opt-in RX-only fixed-band evidence without printing device IDs."""

    if os.name != "nt":
        return (
            ValidationResult(
                "hardware.discovery", ValidationStatus.NOT_VERIFIED, {}, "P15 Pluto harness is Windows-only"
            ),
        )
    if not math.isfinite(duration_seconds) or duration_seconds <= 0.0:
        raise ValueError("duration_seconds must be finite and positive")
    availability = native_availability()
    if not availability.available:
        return (
            ValidationResult(
                "hardware.discovery",
                ValidationStatus.NOT_VERIFIED,
                {},
                availability.reason or "native extension unavailable",
            ),
        )
    try:
        from .pluto import discover_pluto

        devices = tuple(discover_pluto())
    except Exception:
        return (
            ValidationResult(
                "hardware.discovery",
                ValidationStatus.NOT_VERIFIED,
                {},
                "device discovery failed; inspect local hardware logs",
            ),
        )
    usb_devices = tuple(item for item in devices if str(item.uri).startswith("usb:"))
    if not usb_devices:
        return (
            ValidationResult(
                "hardware.discovery",
                ValidationStatus.NOT_VERIFIED,
                {"discovered_contexts": len(devices)},
                "no USB Pluto context discovered",
            ),
            ValidationResult(
                "hardware.sweep",
                ValidationStatus.NOT_VERIFIED,
                {},
                "real-device sweep requires a discovered Pluto context",
            ),
        )
    uri = str(usb_devices[0].uri)
    results: list[ValidationResult] = [
        ValidationResult(
            "hardware.discovery",
            ValidationStatus.PASS,
            {"discovered_contexts": len(devices), "usb_contexts": len(usb_devices)},
        ),
        ValidationResult(
            "hardware.sweep",
            ValidationStatus.NOT_VERIFIED,
            {},
            "real-device sweep is not automated by the P15 fixed-band runner",
        ),
    ]
    for backend in backends:
        name = f"hardware.fixed_band.{backend.value}"
        if backend is ComputeBackendKind.CUDA and not bool(availability.build_info.get("cuda_compiled", False)):
            results.append(
                ValidationResult(
                    name, ValidationStatus.NOT_VERIFIED, {"cuda_compiled": False}, "native build has no CUDA backend"
                )
            )
            continue
        try:
            options = FixedBandOptions(
                device=DeviceConfig(
                    "p15-hardware",
                    uri,
                    center_frequency_hz,
                    sample_rate_hz,
                    analog_bandwidth_hz,
                    GainMode.MANUAL,
                    20.0,
                    0,
                    16_384,
                ),
                dsp=DspConfig(
                    4096,
                    2048,
                    WindowType.HANN,
                    DetectorType.SAMPLE,
                    SpectrumUnit.DBFS_BIN,
                    PrecisionMode.ACCURATE_F32_F64_ACCUM,
                    4,
                ),
                backend=backend,
                acquisition_queue_capacity=16,
                spectrum_queue_capacity=4,
                event_queue_capacity=64,
                snapshot_rate_hz=60.0,
                discard_blocks_after_start=2,
            )
            frames = 0
            before = _memory_bytes()
            with FixedBandEngineService(uri) as engine:
                applied = engine.configure(options)
                engine.start()
                started = time.monotonic()
                while time.monotonic() - started < duration_seconds:
                    frames += len(engine.poll_spectrum(8))
                    if engine.state is EngineState.ERROR:
                        break
                    time.sleep(0.05)
                engine.stop()
                metrics = engine.metrics()
            after = _memory_bytes()
            if frames <= 0 or not metrics.healthy:
                raise AssertionError("hardware run produced no healthy spectrum")
            results.append(
                ValidationResult(
                    name,
                    ValidationStatus.PASS,
                    {
                        "duration_seconds": duration_seconds,
                        "frames_polled": frames,
                        "active_backend": metrics.active_backend.value,
                        "requested_backend": metrics.requested_backend.value,
                        "fft_frames_computed": int(metrics.engine.fft_frames_computed),
                        "fft_frames_dropped": int(metrics.engine.fft_frames_dropped),
                        "iq_blocks_dropped": int(metrics.engine.iq_blocks_dropped),
                        "snapshot_dropped": int(metrics.spectrum_queue.dropped),
                        "queue_high_water": int(metrics.acquisition_queue.high_water),
                        "queue_capacity": int(metrics.acquisition_queue.capacity),
                        "private_memory_delta": None if before is None or after is None else after - before,
                        "applied_center_frequency_hz": applied.center_frequency_hz,
                        "applied_sample_rate_hz": applied.sample_rate_hz,
                        "applied_bandwidth_hz": applied.analog_bandwidth_hz,
                    },
                )
            )
        except Exception as exc:
            results.append(
                ValidationResult(
                    name,
                    ValidationStatus.FAIL,
                    {"error_type": type(exc).__name__},
                    "hardware run failed; inspect local hardware logs",
                )
            )
    return tuple(results)


__all__ = [
    "ValidationReport",
    "ValidationResult",
    "ValidationStatus",
    "benchmark_cpu",
    "collect_platform_fingerprint",
    "run_hardware_fixed_band",
    "run_offline_validation",
    "validate_calibration_residuals",
    "validate_cpu_precision",
    "validate_cuda",
    "validate_recording",
    "validate_sweep_quality",
    "validate_synthetic_fixed_band",
    "validate_synthetic_generators",
]

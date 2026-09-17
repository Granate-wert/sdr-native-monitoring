"""Read-only native replay and canonical I/Q reprocess adapters.

The native extension owns the bounded sparse index and binary decode.  This
module deliberately maps only published SpectrumFrame records into the
existing replay contract.  For R09 it also owns the offline reprocess session:
raw I/Q stays inside C++, is fed into the canonical native DSP backend, and
only the separately committed Spectrum capture becomes replay-visible.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from ..domain import (
    RecordingIndex,
    ReplayIndexEntry,
    ReplayKind,
    SpectrumFrame,
    TimestampQuality,
    as_configuration_generation,
    as_frame_sequence,
    as_source_id,
    as_timestamp_ns,
)


_NATIVE_ARTIFACT_SUFFIXES = (
    ".sigmf-meta",
    ".sigmf-index.jsonl",
    ".sigmf-gaps.jsonl",
    ".sdr-spectrum.meta",
    ".sdr-spectrum.bin",
    ".sdr-spectrum-index.jsonl",
)

_NATIVE_INPUT_SUFFIXES = (
    ".sigmf-meta",
    ".sigmf-index.jsonl",
    ".sigmf-gaps.jsonl",
)


def is_native_recording_uri(uri: str | Path) -> bool:
    """Return whether a path identifies a native capture family, not a legacy .sdrrec."""

    path = Path(uri)
    name = path.name
    while name.endswith(".part"):
        name = name[:-5]
    if name.endswith(_NATIVE_ARTIFACT_SUFFIXES):
        return True
    return Path(f"{path}.sigmf-meta").exists() or Path(f"{path}.sdr-spectrum.meta").exists()


class NativeSpectrumRecordingReader:
    """Final-manifest-only spectrum reader behind the common replay protocol."""

    def __init__(self, uri: str | Path) -> None:
        self.path = Path(uri)
        try:
            native_module = import_module("sdr_monitor._sdr_native")
            reader_type = getattr(native_module, "NativeSpectrumRecordingReader")
        except (ImportError, ModuleNotFoundError, OSError, AttributeError) as error:
            raise RuntimeError("native completed-capture reader is unavailable") from error
        self._reader: Any = reader_type(str(self.path))
        info = self._reader.info
        if not bool(info.spectrum_manifest_final):
            raise ValueError("partial native captures are scan-only and cannot be replayed")
        count = int(self._reader.frame_count)
        first_timestamp = int(self._reader.first_timestamp_ns) if count else 0
        last_timestamp = int(self._reader.last_timestamp_ns) if count else first_timestamp
        self.index = RecordingIndex(
            str(self.path),
            (),
            max(last_timestamp - first_timestamp, 0),
            int(self._reader.source_size_bytes),
            frame_count_hint=count,
            recording_format="native_spectrum_v2",
            native_iq_available=bool(info.iq_manifest_final),
            native_spectrum_available=True,
            control_gap_count=int(info.lifecycle_control_gaps),
            control_gap_duration_ns=int(info.lifecycle_control_gap_duration_ns),
        )

    def frame_count_for(self, kind: ReplayKind) -> int:
        if kind is ReplayKind.IQ:
            return 0
        return self.index.frame_count

    def entry_at(self, kind: ReplayKind, ordinal: int) -> ReplayIndexEntry:
        if kind is ReplayKind.IQ:
            raise ValueError("native I/Q reprocess is deferred to R09; no raw I/Q is exposed")
        item = self._reader.entry_at(int(ordinal))
        return ReplayIndexEntry(
            int(item.ordinal),
            int(item.offset),
            int(item.record_bytes),
            ReplayKind.SPECTRUM.value,
            as_frame_sequence(item.frame_sequence),
            as_timestamp_ns(item.timestamp_ns),
        )

    def read(self, entry: ReplayIndexEntry) -> SpectrumFrame:
        if entry.kind != ReplayKind.SPECTRUM.value:
            raise ValueError("native completed-capture reader contains published spectrum only")
        frame = self._reader.read_frame(int(entry.ordinal))
        return SpectrumFrame(
            as_frame_sequence(frame.frame_sequence),
            as_timestamp_ns(frame.timestamp_ns),
            frame.frequencies_hz,
            frame.values,
            str(frame.unit),
            as_source_id(frame.source_id),
            as_configuration_generation(frame.config_generation),
            str(frame.calibration_profile_id) or None,
            TimestampQuality.UNKNOWN,
            (),
        )

    def close(self) -> None:
        # The pybind reader owns no external mutable resource and its arrays
        # keep their own C++ lifetime.  Retain the common replay-reader API.
        return None


class NativeIqReprocessor:
    """Bounded, cancellable native reprocess without Python-visible raw I/Q."""

    def __init__(self, uri: str | Path, backend: str) -> None:
        self.path = Path(uri)
        native_module = _native_module()
        try:
            processor_type = getattr(native_module, "NativeIqRecordingReprocessor")
            selection_type = getattr(native_module, "DspBackendSelectionOptions")
            dsp_type = getattr(native_module, "DspConfig")
        except AttributeError as error:
            raise RuntimeError("native canonical I/Q reprocess is unavailable") from error
        requested = str(backend).strip().casefold()
        backend_enum = _native_reprocess_backend(native_module, requested)
        selection = selection_type(backend_enum, True, -1, 8)
        dsp = dsp_type(
            1024,
            512,
            native_module.WindowType.HANN,
            native_module.DetectorType.SAMPLE,
            native_module.SpectrumUnit.DBFS_BIN,
            native_module.PrecisionMode.ACCURATE_F32_F64_ACCUM,
            1,
            1,
            8.6,
            native_module.CalibrationStatus.UNCALIBRATED,
            "",
            5,
        )
        self.output_path = _native_reprocess_output_path(self.path)
        self._processor: Any = processor_type(
            str(self.path),
            str(self.output_path),
            dsp,
            selection,
            262_144,
        )
        self.requested_backend = requested

    def process(self, max_blocks: int = 8) -> bool:
        """Advance physically indexed input by at most ``max_blocks`` blocks."""

        return bool(self._processor.process(int(max_blocks)))

    def request_cancel(self) -> None:
        self._processor.request_cancel()

    @property
    def progress(self) -> Any:
        return self._processor.progress


def _native_module() -> Any:
    try:
        return import_module("sdr_monitor._sdr_native")
    except (ImportError, ModuleNotFoundError, OSError) as error:
        raise RuntimeError("native completed-capture reader is unavailable") from error


def _native_reprocess_backend(native_module: Any, requested: str) -> Any:
    if requested == "cpu":
        return native_module.ComputeBackendKind.CPU
    if requested == "cuda":
        return native_module.ComputeBackendKind.CUDA
    raise ValueError("native I/Q reprocess requires explicit cpu or cuda backend")


def _native_reprocess_output_path(uri: Path) -> Path:
    """Map any native I/Q artifact back to an independent reprocessed base."""

    value = str(uri)
    for suffix in _NATIVE_INPUT_SUFFIXES:
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    base = Path(value)
    return base.with_name(f"{base.name}.reprocessed")


__all__ = ["NativeIqReprocessor", "NativeSpectrumRecordingReader", "is_native_recording_uri"]

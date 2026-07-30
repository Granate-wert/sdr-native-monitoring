"""Unit-aware measurements over one native live :class:`SpectrumFrame`.

The adapter deliberately consumes exactly one frame.  This keeps interactive
measurements deterministic and prevents a result from silently mixing frames
from different calibration or gain configurations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

import numpy as np

from ..domain import SpectrumTrace
from ..models import AcquisitionMode, MeasurementQuality, MeasurementWarning, TraceMode
from ..power_measurements import (
    AclrResult,
    AclrService,
    SingleChannelPowerResult,
    SingleChannelPowerService,
    SpectrumFrame as MeasurementFrame,
    TemporalMode,
    carrier_to_noise,
    occupied_bandwidth,
)
from ..processing import NoiseFloorResult, noise_floor, peak_search_values
from ..time_gated_power import PowerSemantics
from .contracts import CalibrationStatus, QualityFlag, SpectrumFrame, SpectrumUnit


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class LivePeak:
    frequency_hz: float
    level_db: float
    index: int
    unit: SpectrumUnit


@dataclass(frozen=True, slots=True)
class LiveSnr:
    signal_dbm: float | None
    noise_dbm: float | None
    snr_db: float | None
    signal_band: tuple[float, float]
    noise_band: tuple[float, float]


@dataclass(frozen=True, slots=True)
class LiveMeasurementResult(Generic[T]):
    """A measurement value plus the exact native frame provenance."""

    kind: str
    value: T | None
    quality: MeasurementQuality
    warnings: tuple[MeasurementWarning, ...]
    uncertainty_db: float | None
    source_id: str
    frame_sequence: int
    config_generation: int
    timestamp_ns: int
    unit: SpectrumUnit
    calibration_status: CalibrationStatus


_QUALITY_RANK = {
    MeasurementQuality.EXACT: 0,
    MeasurementQuality.APPROXIMATE: 1,
    MeasurementQuality.LIMITED: 2,
    MeasurementQuality.UNSUPPORTED: 3,
    MeasurementQuality.UNKNOWN: 3,
    MeasurementQuality.INVALID: 4,
}


def _worst_quality(*values: MeasurementQuality) -> MeasurementQuality:
    return max(values or (MeasurementQuality.UNKNOWN,), key=_QUALITY_RANK.__getitem__)


def _warning(code: str, message: str, **context: float | int | str | bool) -> MeasurementWarning:
    return MeasurementWarning(code, message, context)


def _unique_warnings(values: tuple[MeasurementWarning, ...] | list[MeasurementWarning]) -> tuple[MeasurementWarning, ...]:
    result: list[MeasurementWarning] = []
    seen: set[tuple[str, str, tuple[tuple[str, object], ...]]] = set()
    for item in values:
        key = (item.code, item.message, tuple(sorted(item.context.items())))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


class LiveMeasurementAdapter:
    """Expose existing measurement math with live-frame safety semantics.

    ``interval_dropped_frames`` is supplied by a caller that owns a bounded
    interval.  The native frame counters are cumulative-before-frame counters
    and are reported separately, so the adapter never labels them as an
    interval count by inference.
    """

    def __init__(self, frame: SpectrumFrame, *, interval_dropped_frames: int = 0) -> None:
        if not isinstance(frame, SpectrumFrame):
            raise TypeError("LiveMeasurementAdapter requires native SpectrumFrame")
        if interval_dropped_frames < 0:
            raise ValueError("interval_dropped_frames must not be negative")
        self.frame = frame
        self.interval_dropped_frames = int(interval_dropped_frames)

    @property
    def frame_sequence(self) -> int:
        return int(self.frame.frame_sequence)

    @property
    def config_generation(self) -> int:
        return int(self.frame.config_generation)

    def _base_quality(self) -> tuple[MeasurementQuality, list[MeasurementWarning]]:
        frame = self.frame
        quality = MeasurementQuality.EXACT
        warnings: list[MeasurementWarning] = []
        if frame.calibration_status == CalibrationStatus.INVALID:
            quality = MeasurementQuality.INVALID
            warnings.append(_warning("invalid_calibration", "Калибровка кадра недействительна"))
        elif frame.calibration_status == CalibrationStatus.INTERPOLATED:
            quality = _worst_quality(quality, MeasurementQuality.APPROXIMATE)
            warnings.append(_warning("calibration_interpolated", "Использована интерполированная калибровка"))
        elif frame.calibration_status == CalibrationStatus.EXTRAPOLATED:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("calibration_extrapolated", "Калибровка экстраполирована за пределы профиля"))
        elif frame.calibration_status == CalibrationStatus.UNCALIBRATED:
            quality = _worst_quality(quality, MeasurementQuality.APPROXIMATE)
            warnings.append(_warning("uncalibrated", "Кадр не имеет калибровки; абсолютная мощность недоступна"))

        flags = frame.quality_flags
        if flags & QualityFlag.UNCALIBRATED and frame.calibration_status != CalibrationStatus.UNCALIBRATED:
            quality = _worst_quality(quality, MeasurementQuality.APPROXIMATE)
            warnings.append(_warning("uncalibrated_flag", "Кадр помечен как некалиброванный"))
        if flags & QualityFlag.CALIBRATION_INTERPOLATED and frame.calibration_status != CalibrationStatus.INTERPOLATED:
            quality = _worst_quality(quality, MeasurementQuality.APPROXIMATE)
            warnings.append(_warning("calibration_interpolated", "Кадр помечен как интерполированный"))
        if flags & QualityFlag.CALIBRATION_EXTRAPOLATED and frame.calibration_status != CalibrationStatus.EXTRAPOLATED:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("calibration_extrapolated", "Кадр помечен как экстраполированный"))
        if flags & QualityFlag.GAIN_MODE_AGC:
            quality = _worst_quality(quality, MeasurementQuality.APPROXIMATE)
            warnings.append(_warning("agc_active", "Измерение выполнено при автоматическом усилении"))
        dropped = {
            "samples_before": int(frame.dropped_samples_before),
            "iq_blocks_before": int(frame.dropped_iq_blocks_before),
            "fft_frames_before": int(frame.dropped_fft_frames_before),
        }
        if flags & (QualityFlag.IQ_DROPPED | QualityFlag.FFT_DROPPED) or any(dropped.values()):
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("dropped_frames", "До кадра обнаружены пропуски потока", **dropped))
        if self.interval_dropped_frames:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("dropped_frames_interval", "В измерительном интервале были пропуски кадров", count=self.interval_dropped_frames))
        if flags & QualityFlag.EDGE_BIN:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("edge_bin", "Измерение затрагивает граничный спектральный бин"))
        if flags & QualityFlag.ADC_OVERLOAD:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("adc_overload", "Обнаружена перегрузка ADC"))
        if flags & QualityFlag.SETTLING_INCOMPLETE:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("settling_incomplete", "Кадр получен до завершения settling"))
        uncertainty = float(frame.estimated_uncertainty_db)
        if np.isfinite(uncertainty) and uncertainty > 0.0:
            warnings.append(_warning("measurement_uncertainty", "Оценка неопределённости измерения доступна", uncertainty_db=uncertainty))
        return quality, warnings

    def _result(
        self,
        kind: str,
        value: T | None,
        quality: MeasurementQuality,
        warnings: tuple[MeasurementWarning, ...] | list[MeasurementWarning],
    ) -> LiveMeasurementResult[T]:
        base_quality, base_warnings = self._base_quality()
        uncertainty = float(self.frame.estimated_uncertainty_db)
        return LiveMeasurementResult(
            kind=kind,
            value=value,
            quality=_worst_quality(base_quality, quality),
            warnings=_unique_warnings(base_warnings + list(warnings)),
            uncertainty_db=uncertainty if np.isfinite(uncertainty) else None,
            source_id=self.frame.source.source_id,
            frame_sequence=int(self.frame.frame_sequence),
            config_generation=int(self.frame.config_generation),
            timestamp_ns=int(self.frame.timestamp_ns),
            unit=self.frame.unit,
            calibration_status=self.frame.calibration_status,
        )

    def _power_gate(self, kind: str) -> LiveMeasurementResult[object] | None:
        frame = self.frame
        if frame.unit not in (SpectrumUnit.DBM_BIN, SpectrumUnit.DBM_HZ):
            return self._result(
                kind,
                None,
                MeasurementQuality.UNSUPPORTED,
                [_warning("unsupported_unit", "Интегральное измерение требует dBm/bin или dBm/Hz", unit=frame.unit.value)],
            )
        if frame.calibration_status not in (
            CalibrationStatus.APPLIED,
            CalibrationStatus.INTERPOLATED,
            CalibrationStatus.EXTRAPOLATED,
        ):
            return self._result(
                kind,
                None,
                MeasurementQuality.UNSUPPORTED,
                [_warning("uncalibrated_power", "Абсолютное измерение мощности отклонено без применимой калибровки")],
            )
        return None

    def _measurement_frame(self) -> MeasurementFrame:
        frame = self.frame
        if frame.unit == SpectrumUnit.DBM_HZ:
            unit = "dBm/Hz"
            semantics = PowerSemantics.PSD_PER_HZ
        else:
            unit = "dBm"
            semantics = PowerSemantics.POWER_PER_BIN
        return MeasurementFrame(
            frequencies_hz=frame.frequencies_hz,
            values_db=frame.values,
            unit=unit,
            timestamp_s=float(frame.timestamp_ns) / 1.0e9,
            frame_index=int(frame.frame_sequence),
            source_id=frame.source.source_id,
            source_revision=str(frame.config_generation),
            acquisition_mode=AcquisitionMode.REAL_TIME,
            trace_mode=TraceMode.CLEAR_WRITE,
            detector=frame.detector.value,
            power_semantics=semantics,
            rbw_hz=float(frame.nominal_rbw_hz),
            enbw_hz=float(frame.enbw_hz),
            provenance=f"native:{frame.source.source_id}:frame:{frame.frame_sequence}:config:{frame.config_generation}",
        )

    def _trace_for_relative_measurements(self) -> SpectrumTrace:
        frame = self.frame
        values = np.asarray(frame.values, dtype=np.float64)
        if frame.unit == SpectrumUnit.DBM_HZ:
            effective = float(frame.enbw_hz or frame.nominal_rbw_hz or frame.fft_bin_width_hz)
            values = values + 10.0 * np.log10(effective)
        frequencies = np.asarray(frame.frequencies_hz, dtype=np.float64)
        step = float(np.median(np.diff(frequencies))) if frequencies.size > 1 else float(frame.fft_bin_width_hz)
        return SpectrumTrace(
            trace_id=f"{frame.source.source_id}:frame:{frame.frame_sequence}",
            name="Live Spectrum",
            start_frequency_hz=float(frequencies[0]),
            stop_frequency_hz=float(frequencies[-1]),
            frequency_step_hz=step,
            power_values=values,
            frequency_values=frequencies,
            axis_unit="Hz",
            unit="dBm",
            timestamp=float(frame.timestamp_ns) / 1.0e9,
            rbw_hz=float(frame.nominal_rbw_hz),
            detector=frame.detector.value,
            trace_mode="Live",
            source_stream=frame.source.source_id,
            metadata={
                "frame_sequence": int(frame.frame_sequence),
                "config_generation": int(frame.config_generation),
                "calibration_status": frame.calibration_status.value,
            },
        )

    @staticmethod
    def _value_quality(value: object) -> tuple[MeasurementQuality, list[MeasurementWarning]]:
        quality = getattr(value, "quality", MeasurementQuality.EXACT)
        warnings = list(getattr(value, "warnings", ()))
        if isinstance(value, AclrResult):
            bands = (value.main, *value.adjacent)
            quality = _worst_quality(*(getattr(item, "quality", MeasurementQuality.UNKNOWN) for item in bands))
            warnings = [warning for item in bands for warning in getattr(item, "warnings", ())]
        return quality, warnings

    def channel_power(self, start_hz: float, stop_hz: float) -> LiveMeasurementResult[SingleChannelPowerResult]:
        blocked = self._power_gate("Channel Power")
        if blocked is not None:
            return blocked  # type: ignore[return-value]
        value = SingleChannelPowerService().measure(self._measurement_frame(), start_hz, stop_hz)
        quality, warnings = self._value_quality(value.integrated)
        return self._result("Channel Power", value, quality, warnings)

    def acpr(
        self,
        center_hz: float,
        main_bandwidth_hz: float,
        offset_hz: float,
        *,
        adjacent_bandwidth_hz: float | None = None,
        adjacent_pairs: int = 1,
    ) -> LiveMeasurementResult[AclrResult]:
        blocked = self._power_gate("ACPR / ACLR")
        if blocked is not None:
            return blocked  # type: ignore[return-value]
        value = AclrService().measure(
            [self._measurement_frame()], center_hz, main_bandwidth_hz, offset_hz,
            adjacent_bandwidth_hz=adjacent_bandwidth_hz,
            adjacent_pairs=adjacent_pairs,
            temporal_mode=TemporalMode.CURRENT,
        )
        quality, warnings = self._value_quality(value)
        return self._result("ACPR / ACLR", value, quality, warnings)

    def occupied_bandwidth(
        self, fraction: float = 0.99, search_region: tuple[float, float] | None = None
    ) -> LiveMeasurementResult[object]:
        blocked = self._power_gate("Occupied Bandwidth")
        if blocked is not None:
            return blocked
        value = occupied_bandwidth(self._measurement_frame(), fraction, search_region)
        quality, warnings = self._value_quality(value)
        return self._result("Occupied Bandwidth", value, quality, warnings)

    def noise_floor(
        self,
        start_hz: float | None = None,
        stop_hz: float | None = None,
        *,
        percentile: float = 50.0,
        exclude_peaks_db: float = 8.0,
    ) -> LiveMeasurementResult[NoiseFloorResult]:
        blocked = self._power_gate("Noise Floor")
        if blocked is not None:
            return blocked  # type: ignore[return-value]
        value = noise_floor(
            self._trace_for_relative_measurements(), start_hz, stop_hz,
            percentile=percentile, exclude_peaks_db=exclude_peaks_db,
        )
        return self._result("Noise Floor", value, MeasurementQuality.EXACT, ())

    def snr(
        self,
        signal_band: tuple[float, float],
        noise_band: tuple[float, float] | None = None,
    ) -> LiveMeasurementResult[LiveSnr]:
        blocked = self._power_gate("SNR")
        if blocked is not None:
            return blocked  # type: ignore[return-value]
        frequencies = np.asarray(self.frame.frequencies_hz, dtype=np.float64)
        if noise_band is None and frequencies.size:
            signal_low, signal_high = sorted(map(float, signal_band))
            candidates = ((float(frequencies[0]), signal_low), (signal_high, float(frequencies[-1])))
            candidates = tuple(item for item in candidates if item[1] > item[0])
            if candidates:
                noise_band = max(candidates, key=lambda item: item[1] - item[0])
        if noise_band is None:
            return self._result("SNR", None, MeasurementQuality.INVALID, [_warning("noise_band_required", "Не удалось выбрать полосу шума вне сигнала")])
        value = carrier_to_noise(self._measurement_frame(), signal_band, noise_band)
        result = LiveSnr(value.carrier_power_dbm, value.noise_in_signal_band_dbm, value.cn_db, signal_band, noise_band)
        return self._result("SNR", result, value.quality, value.warnings)

    def peak(
        self,
        minimum_level_db: float = -np.inf,
        minimum_distance_hz: float = 0.0,
        limit: int = 20,
    ) -> LiveMeasurementResult[tuple[LivePeak, ...]]:
        values = peak_search_values(
            self.frame.frequencies_hz, self.frame.values,
            minimum_level_dbm=minimum_level_db,
            minimum_distance_hz=minimum_distance_hz,
            limit=limit,
        )
        peaks = tuple(LivePeak(frequency, level, index, self.frame.unit) for frequency, level, index in values)
        return self._result("Peak", peaks, MeasurementQuality.EXACT, ())

    peak_search = peak


__all__ = ["LiveMeasurementAdapter", "LiveMeasurementResult", "LivePeak", "LiveSnr"]

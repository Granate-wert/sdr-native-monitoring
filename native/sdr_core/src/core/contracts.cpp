#include "sdr_core/backend_info.hpp"
#include "sdr_core/capabilities.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/metrics.hpp"
#include "sdr_core/types.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <string>

namespace sdr_core {
namespace {

[[noreturn]] void invalid(const std::string& message) {
    throw ConfigurationError(message);
}

void finite(const double value, const std::string& name) {
    if (!std::isfinite(value)) {
        invalid(name + " must be finite");
    }
}

void positive(const double value, const std::string& name) {
    finite(value, name);
    if (value <= 0.0) {
        invalid(name + " must be positive");
    }
}

void schema(const std::uint32_t value) {
    if (value != contract_schema_version) {
        invalid("unsupported contract schema_version");
    }
}

void fft_geometry(const std::uint32_t fft_size, const std::uint32_t hop_size) {
    if (fft_size == 0U) {
        invalid("fft_size must be positive");
    }
    if (hop_size == 0U || hop_size > fft_size) {
        invalid("hop_size must be in [1, fft_size]");
    }
}

void dsp_fft_geometry(const std::uint32_t fft_size, const std::uint32_t hop_size) {
    // P05 CPU DSP contract: power-of-two FFT in [256, 262144].
    if (fft_size < 256U || fft_size > 262'144U || (fft_size & (fft_size - 1U)) != 0U) {
        invalid("fft_size must be a power of two in [256, 262144]");
    }
    if (hop_size == 0U || hop_size > fft_size) {
        invalid("hop_size must be in [1, fft_size]");
    }
}

[[nodiscard]] bool calibrated_unit(const SpectrumUnit unit) {
    return unit == SpectrumUnit::Dbm || unit == SpectrumUnit::DbmBin ||
           unit == SpectrumUnit::DbmHz;
}

[[nodiscard]] bool applied_calibration(const CalibrationStatus status) {
    return status == CalibrationStatus::Applied || status == CalibrationStatus::Interpolated ||
           status == CalibrationStatus::Extrapolated;
}

[[nodiscard]] std::size_t bytes_per_complex_sample(const SampleFormat format) {
    switch (format) {
    case SampleFormat::ComplexInt8Interleaved:
        return 2U;
    case SampleFormat::ComplexInt12InInt16Le:
    case SampleFormat::ComplexInt16Le:
        return 4U;
    case SampleFormat::ComplexFloat32Le:
        return 8U;
    }
    invalid("unknown SampleFormat");
}

constexpr std::uint32_t quality_mask = (1U << 16U) - 1U;

void quality(const QualityFlag flags) {
    if ((static_cast<std::uint32_t>(flags) & ~quality_mask) != 0U) {
        invalid("quality flags contain unknown bits");
    }
}

}  // namespace

std::string_view to_wire(const SourceType value) {
    switch (value) {
    case SourceType::DflFile:
        return "dfl_file";
    case SourceType::LiveIq:
        return "live_iq";
    case SourceType::RecordedIq:
        return "recorded_iq";
    case SourceType::LiveScalarSweep:
        return "live_scalar_sweep";
    case SourceType::RecordedSpectrum:
        return "recorded_spectrum";
    case SourceType::Synthetic:
        return "synthetic";
    }
    invalid("unknown SourceType");
}

std::string_view to_wire(const SpectrumUnit value) {
    switch (value) {
    case SpectrumUnit::DbfsBin:
        return "dBFS/bin";
    case SpectrumUnit::DbfsHz:
        return "dBFS/Hz";
    case SpectrumUnit::DbmBin:
        return "dBm/bin";
    case SpectrumUnit::DbmHz:
        return "dBm/Hz";
    case SpectrumUnit::Dbm:
        return "dBm";
    }
    invalid("unknown SpectrumUnit");
}

std::string_view to_wire(const SampleFormat value) {
    switch (value) {
    case SampleFormat::ComplexInt8Interleaved:
        return "ci8_interleaved";
    case SampleFormat::ComplexInt12InInt16Le:
        return "ci12_in_i16_le";
    case SampleFormat::ComplexInt16Le:
        return "ci16_le";
    case SampleFormat::ComplexFloat32Le:
        return "cf32_le";
    }
    invalid("unknown SampleFormat");
}

std::string_view to_wire(const WindowType value) {
    switch (value) {
    case WindowType::Rectangular:
        return "rectangular";
    case WindowType::Hann:
        return "hann";
    case WindowType::BlackmanHarris4Term:
        return "blackman_harris_4term";
    case WindowType::FlatTop:
        return "flat_top";
    case WindowType::Nuttall:
        return "nuttall";
    case WindowType::Kaiser:
        return "kaiser";
    }
    invalid("unknown WindowType");
}

std::string_view to_wire(const DetectorType value) {
    switch (value) {
    case DetectorType::Sample:
        return "sample";
    case DetectorType::Peak:
        return "peak";
    case DetectorType::NegativePeak:
        return "negative_peak";
    case DetectorType::Rms:
        return "rms";
    case DetectorType::AveragePower:
        return "average_power";
    }
    invalid("unknown DetectorType");
}

std::string_view to_wire(const GainMode value) {
    switch (value) {
    case GainMode::Manual:
        return "manual";
    case GainMode::SlowAttack:
        return "slow_attack";
    case GainMode::FastAttack:
        return "fast_attack";
    case GainMode::Hybrid:
        return "hybrid";
    }
    invalid("unknown GainMode");
}

std::string_view to_wire(const DeviceState value) {
    switch (value) {
    case DeviceState::Disconnected:
        return "disconnected";
    case DeviceState::Connecting:
        return "connecting";
    case DeviceState::Idle:
        return "idle";
    case DeviceState::Configuring:
        return "configuring";
    case DeviceState::Streaming:
        return "streaming";
    case DeviceState::Sweeping:
        return "sweeping";
    case DeviceState::Recording:
        return "recording";
    case DeviceState::Degraded:
        return "degraded";
    case DeviceState::Error:
        return "error";
    case DeviceState::Stopping:
        return "stopping";
    case DeviceState::ShuttingDown:
        return "shutting_down";
    }
    invalid("unknown DeviceState");
}

std::string_view to_wire(const CalibrationStatus value) {
    switch (value) {
    case CalibrationStatus::NotApplicable:
        return "not_applicable";
    case CalibrationStatus::Uncalibrated:
        return "uncalibrated";
    case CalibrationStatus::Applied:
        return "applied";
    case CalibrationStatus::Interpolated:
        return "interpolated";
    case CalibrationStatus::Extrapolated:
        return "extrapolated";
    case CalibrationStatus::Invalid:
        return "invalid";
    }
    invalid("unknown CalibrationStatus");
}

std::string_view to_wire(const BackendKind value) {
    switch (value) {
    case BackendKind::Cpu:
        return "cpu";
    case BackendKind::Cuda:
        return "cuda";
    }
    invalid("unknown BackendKind");
}

std::string_view to_wire(const ComputeBackendKind value) {
    switch (value) {
    case ComputeBackendKind::Auto:
        return "auto";
    case ComputeBackendKind::Cpu:
        return "cpu";
    case ComputeBackendKind::Cuda:
        return "cuda";
    case ComputeBackendKind::Hip:
        return "hip";
    }
    invalid("unknown ComputeBackendKind");
}

std::string_view to_wire(const BackendErrorCode value) {
    switch (value) {
    case BackendErrorCode::None:
        return "none";
    case BackendErrorCode::RuntimeNotFound:
        return "runtime_not_found";
    case BackendErrorCode::RuntimeIncompatible:
        return "runtime_incompatible";
    case BackendErrorCode::NoDevice:
        return "no_device";
    case BackendErrorCode::UnsupportedDevice:
        return "unsupported_device";
    case BackendErrorCode::AllocationFailed:
        return "allocation_failed";
    case BackendErrorCode::CopyFailed:
        return "copy_failed";
    case BackendErrorCode::KernelLaunchFailed:
        return "kernel_launch_failed";
    case BackendErrorCode::FftPlanFailed:
        return "fft_plan_failed";
    case BackendErrorCode::FftExecutionFailed:
        return "fft_execution_failed";
    case BackendErrorCode::DeviceLost:
        return "device_lost";
    case BackendErrorCode::TimeoutOrTdr:
        return "timeout_or_tdr";
    case BackendErrorCode::NumericalSelfTestFailed:
        return "numerical_self_test_failed";
    case BackendErrorCode::Unknown:
        return "unknown";
    }
    invalid("unknown BackendErrorCode");
}

std::string_view to_wire(const PrecisionMode value) {
    switch (value) {
    case PrecisionMode::ReferenceF64:
        return "reference_f64";
    case PrecisionMode::AccurateF32F64Accum:
        return "accurate_f32_f64_accum";
    case PrecisionMode::FastF32:
        return "fast_f32";
    }
    invalid("unknown PrecisionMode");
}

std::string_view to_wire(const PersistenceMode value) {
    switch (value) {
    case PersistenceMode::Disabled:
        return "disabled";
    case PersistenceMode::RollingExact:
        return "rolling_exact";
    case PersistenceMode::ExponentialDecay:
        return "exponential_decay";
    }
    invalid("unknown PersistenceMode");
}

std::string_view to_wire(const EngineState value) {
    switch (value) {
    case EngineState::Created:
        return "created";
    case EngineState::Configured:
        return "configured";
    case EngineState::Running:
        return "running";
    case EngineState::Stopping:
        return "stopping";
    case EngineState::Stopped:
        return "stopped";
    case EngineState::Error:
        return "error";
    }
    invalid("unknown EngineState");
}

std::string_view to_wire(const OverflowPolicy value) {
    switch (value) {
    case OverflowPolicy::Block:
        return "block";
    case OverflowPolicy::DropNewest:
        return "drop_newest";
    case OverflowPolicy::DropOldest:
        return "drop_oldest";
    case OverflowPolicy::LatestWins:
        return "latest_wins";
    }
    invalid("unknown OverflowPolicy");
}

std::string_view to_wire(const EventSeverity value) {
    switch (value) {
    case EventSeverity::Info:
        return "info";
    case EventSeverity::Warning:
        return "warning";
    case EventSeverity::Error:
        return "error";
    case EventSeverity::Critical:
        return "critical";
    }
    invalid("unknown EventSeverity");
}

void validate_unit_calibration(
    const SpectrumUnit unit,
    const CalibrationStatus status,
    const std::string& profile_id
) {
    static_cast<void>(to_wire(unit));
    static_cast<void>(to_wire(status));
    const bool calibrated = calibrated_unit(unit);
    const bool applied = applied_calibration(status);
    if (calibrated && (!applied || profile_id.empty())) {
        invalid("dBm units require an applicable calibration profile");
    }
    if (!calibrated && applied) {
        invalid("applied calibration is incompatible with dBFS units");
    }
}

void validate(const SourceDescriptor& value) {
    static_cast<void>(to_wire(value.source_type));
    schema(value.schema_version);
    if (value.source_id.empty() || value.display_name.empty()) {
        invalid("source_id and display_name must not be empty");
    }
    if (value.metadata_json.size() > 1024U) {
        invalid("source metadata entry count exceeds the low-rate contract bound");
    }
    for (const auto& [key, json] : value.metadata_json) {
        if (key.empty() || json.empty()) {
            invalid("source metadata keys and JSON values must not be empty");
        }
    }
}

void validate(const IqBlock& value) {
    positive(value.center_frequency_hz, "center_frequency_hz");
    positive(value.sample_rate_hz, "sample_rate_hz");
    quality(value.flags);
    if (value.sample_count == 0U || !value.samples) {
        invalid("IqBlock requires samples and positive sample_count");
    }
    const auto width = bytes_per_complex_sample(value.sample_format);
    const auto expected = static_cast<std::size_t>(value.sample_count) * width;
    if (value.samples->size() != expected) {
        invalid("IqBlock byte length disagrees with sample format/count");
    }
}

void validate(const SpectrumFrame& value) {
    validate(value.source);
    positive(value.center_frequency_hz, "center_frequency_hz");
    positive(value.sample_rate_hz, "sample_rate_hz");
    positive(value.analog_bandwidth_hz, "analog_bandwidth_hz");
    positive(value.fft_bin_width_hz, "fft_bin_width_hz");
    positive(value.enbw_hz, "enbw_hz");
    positive(value.nominal_rbw_hz, "nominal_rbw_hz");
    fft_geometry(value.fft_size, value.hop_size);
    static_cast<void>(to_wire(value.window));
    static_cast<void>(to_wire(value.detector));
    static_cast<void>(to_wire(value.precision_mode));
    validate_unit_calibration(
        value.unit,
        value.calibration_status,
        value.calibration_profile_id
    );
    quality(value.quality_flags);
    if (!value.frequencies_hz || !value.values ||
        value.frequencies_hz->size() != value.fft_size ||
        value.values->size() != value.fft_size) {
        invalid("SpectrumFrame arrays must match fft_size");
    }
    if (!std::all_of(value.frequencies_hz->begin(), value.frequencies_hz->end(), [](double item) {
            return std::isfinite(item);
        })) {
        invalid("SpectrumFrame frequencies must be finite");
    }
    if (std::any_of(value.values->begin(), value.values->end(), [](float item) {
            return std::isnan(item);
        })) {
        invalid("SpectrumFrame values must not contain NaN");
    }
    if (!std::isnan(value.estimated_uncertainty_db) &&
        (!std::isfinite(value.estimated_uncertainty_db) ||
         value.estimated_uncertainty_db < 0.0)) {
        invalid("estimated_uncertainty_db must be NaN or non-negative");
    }
}

void validate(const SweepSpectrumFrame& value) {
    if (value.completed_ns < value.started_ns) {
        invalid("sweep completion precedes start");
    }
    positive(value.requested_start_hz, "requested_start_hz");
    positive(value.requested_stop_hz, "requested_stop_hz");
    positive(value.actual_start_hz, "actual_start_hz");
    positive(value.actual_stop_hz, "actual_stop_hz");
    positive(value.nominal_rbw_hz, "nominal_rbw_hz");
    if (value.requested_stop_hz <= value.requested_start_hz ||
        value.actual_stop_hz <= value.actual_start_hz) {
        invalid("sweep stop frequencies must exceed start frequencies");
    }
    if (!value.frequencies_hz || !value.values || !value.quality_flags_per_bin ||
        value.frequencies_hz->size() != value.values->size() ||
        value.values->size() != value.quality_flags_per_bin->size()) {
        invalid("sweep arrays must have equal length");
    }
}

void validate(const DeviceConfig& value) {
    schema(value.schema_version);
    if (value.source_id.empty() || value.context_uri.empty()) {
        invalid("source_id and context_uri must not be empty");
    }
    positive(value.center_frequency_hz, "center_frequency_hz");
    positive(value.sample_rate_hz, "sample_rate_hz");
    positive(value.analog_bandwidth_hz, "analog_bandwidth_hz");
    finite(value.manual_gain_db, "manual_gain_db");
    static_cast<void>(to_wire(value.gain_mode));
    if (value.buffer_samples == 0U) {
        invalid("buffer_samples must be positive");
    }
}

void validate(const DspConfig& value) {
    schema(value.schema_version);
    dsp_fft_geometry(value.fft_size, value.hop_size);
    static_cast<void>(to_wire(value.window));
    static_cast<void>(to_wire(value.detector));
    static_cast<void>(to_wire(value.precision_mode));
    if (value.batch_size == 0U || value.averaging_frames == 0U) {
        invalid("batch_size and averaging_frames must be positive");
    }
    finite(value.kaiser_beta, "kaiser_beta");
    if (value.kaiser_beta < 0.0) {
        invalid("kaiser_beta must not be negative");
    }
    validate_unit_calibration(
        value.unit,
        value.calibration_status,
        value.calibration_profile_id
    );
}

void validate(const PersistenceConfig& value) {
    schema(value.schema_version);
    static_cast<void>(to_wire(value.mode));
    if (value.enabled != (value.mode != PersistenceMode::Disabled)) {
        invalid("persistence enabled and mode disagree");
    }
    if (value.window_frames == 0U || value.power_bins < 2U) {
        invalid("persistence window_frames/power_bins are invalid");
    }
    positive(value.half_life_seconds, "half_life_seconds");
    positive(value.snapshot_rate_hz, "snapshot_rate_hz");
    finite(value.power_min_db, "power_min_db");
    finite(value.power_max_db, "power_max_db");
    if (value.power_max_db <= value.power_min_db) {
        invalid("power_max_db must exceed power_min_db");
    }
}

void validate(const SweepConfig& value) {
    schema(value.schema_version);
    positive(value.start_frequency_hz, "start_frequency_hz");
    positive(value.stop_frequency_hz, "stop_frequency_hz");
    positive(value.sample_rate_hz, "sample_rate_hz");
    positive(value.analog_bandwidth_hz, "analog_bandwidth_hz");
    finite(value.overlap_hz, "overlap_hz");
    if (value.stop_frequency_hz <= value.start_frequency_hz) {
        invalid("stop_frequency_hz must exceed start_frequency_hz");
    }
    if (value.overlap_hz < 0.0 ||
        value.overlap_hz >= std::min(value.sample_rate_hz, value.analog_bandwidth_hz)) {
        invalid("overlap_hz is outside the usable width");
    }
    fft_geometry(value.fft_size, value.hop_size);
    if (value.dwell_frames == 0U) {
        invalid("dwell_frames must be positive");
    }
    finite(value.settling_time_seconds, "settling_time_seconds");
    if (value.settling_time_seconds < 0.0) {
        invalid("settling_time_seconds must not be negative");
    }
}

void validate(const RecordingConfig& value) {
    schema(value.schema_version);
    if (value.enabled &&
        (value.output_uri.empty() || (!value.record_iq && !value.record_spectrum))) {
        invalid("enabled recording requires output and at least one stream");
    }
    if (value.chunk_samples == 0U || value.queue_capacity == 0U) {
        invalid("recording chunk/queue bounds must be positive");
    }
}

bool NumericRange::contains(const double value) const noexcept {
    return std::isfinite(value) && value >= minimum && value <= maximum;
}

void validate(const NumericRange& value) {
    finite(value.minimum, "range.minimum");
    finite(value.maximum, "range.maximum");
    if (value.maximum < value.minimum) {
        invalid("range maximum must not be less than minimum");
    }
    if (value.step.has_value()) {
        positive(*value.step, "range.step");
    }
}

void validate(const DeviceCapabilities& value) {
    schema(value.schema_version);
    if (value.backend_id.empty() || value.device_id.empty() || value.model.empty()) {
        invalid("capability backend_id, device_id and model are required");
    }
    validate(value.tuning_range_hz);
    validate(value.gain_range_db);
    if (value.sample_rate_ranges_hz.empty() ||
        value.analog_bandwidth_ranges_hz.empty() ||
        value.gain_modes.empty() ||
        value.sample_formats.empty()) {
        invalid("capability ranges and enum sets must not be empty");
    }
    for (const auto& range : value.sample_rate_ranges_hz) {
        validate(range);
    }
    for (const auto& range : value.analog_bandwidth_ranges_hz) {
        validate(range);
    }
    for (const auto mode : value.gain_modes) {
        static_cast<void>(to_wire(mode));
    }
    for (const auto format : value.sample_formats) {
        static_cast<void>(to_wire(format));
    }
}

void validate(const EngineMetrics& value) {
    const double values[] = {
        value.analytical_fft_rate,
        value.cpu_processing_ms,
        value.gpu_processing_ms,
        value.h2d_ms,
        value.d2h_ms,
        value.end_to_end_latency_ms,
    };
    for (const auto item : values) {
        if (!std::isfinite(item) || item < 0.0) {
            invalid("engine metrics must be finite and non-negative");
        }
    }
}

void validate(const BackendInfo& value) {
    static_cast<void>(to_wire(value.kind));
    if (value.kind == ComputeBackendKind::Auto) {
        invalid("active backend info must be Cpu or Cuda");
    }
    if (value.backend_id.empty() || value.vendor.empty()) {
        invalid("backend info requires backend_id and vendor");
    }
}

void validate(const BackendAvailability& value) {
    if (!value.compiled &&
        (value.runtime_present || value.device_supported || value.self_test_passed)) {
        invalid("availability levels must not skip the compiled level");
    }
    if (!value.runtime_present && (value.device_supported || value.self_test_passed)) {
        invalid("availability levels must not skip the runtime level");
    }
    if (!value.reason_code.empty()) {
        // Reason codes use the BackendErrorCode wire vocabulary.
        bool known = false;
        for (const auto code : {
                 BackendErrorCode::RuntimeNotFound,
                 BackendErrorCode::RuntimeIncompatible,
                 BackendErrorCode::NoDevice,
                 BackendErrorCode::UnsupportedDevice,
                 BackendErrorCode::AllocationFailed,
                 BackendErrorCode::CopyFailed,
                 BackendErrorCode::KernelLaunchFailed,
                 BackendErrorCode::FftPlanFailed,
                 BackendErrorCode::FftExecutionFailed,
                 BackendErrorCode::DeviceLost,
                 BackendErrorCode::TimeoutOrTdr,
                 BackendErrorCode::NumericalSelfTestFailed,
                 BackendErrorCode::Unknown,
             }) {
            known = known || to_wire(code) == value.reason_code;
        }
        if (!known) {
            invalid("availability reason_code must be a BackendErrorCode wire value");
        }
    }
}

void validate(const DspBackendSelectionOptions& value) {
    static_cast<void>(to_wire(value.preference));
    if (value.plan_cache_capacity == 0U) {
        invalid("plan_cache_capacity must be positive");
    }
}

}  // namespace sdr_core

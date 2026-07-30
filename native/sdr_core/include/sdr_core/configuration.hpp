#pragma once

#include "sdr_core/types.hpp"

#include <cstdint>
#include <string>

namespace sdr_core {

struct DeviceConfig {
    std::string source_id;
    std::string context_uri;
    double center_frequency_hz{};
    double sample_rate_hz{};
    double analog_bandwidth_hz{};
    GainMode gain_mode{GainMode::Manual};
    double manual_gain_db{};
    std::uint32_t channel_index{};
    std::uint32_t buffer_samples{262144U};
    std::uint32_t schema_version{contract_schema_version};
};

struct DspConfig {
    std::uint32_t fft_size{};
    std::uint32_t hop_size{};
    WindowType window{WindowType::Hann};
    DetectorType detector{DetectorType::Sample};
    SpectrumUnit unit{SpectrumUnit::DbfsBin};
    PrecisionMode precision_mode{PrecisionMode::AccurateF32F64Accum};
    std::uint32_t batch_size{1U};
    std::uint32_t averaging_frames{1U};
    double kaiser_beta{8.6};
    CalibrationStatus calibration_status{CalibrationStatus::Uncalibrated};
    std::string calibration_profile_id;
    std::uint32_t schema_version{contract_schema_version};
};

struct PersistenceConfig {
    bool enabled{};
    PersistenceMode mode{PersistenceMode::Disabled};
    std::uint32_t window_frames{500U};
    double half_life_seconds{1.0};
    double power_min_db{-140.0};
    double power_max_db{20.0};
    std::uint32_t power_bins{256U};
    double snapshot_rate_hz{30.0};
    std::uint32_t schema_version{contract_schema_version};
};

struct SweepConfig {
    double start_frequency_hz{};
    double stop_frequency_hz{};
    double sample_rate_hz{};
    double analog_bandwidth_hz{};
    double overlap_hz{};
    std::uint32_t fft_size{};
    std::uint32_t hop_size{};
    std::uint32_t dwell_frames{1U};
    double settling_time_seconds{};
    std::uint32_t discard_blocks{};
    std::uint32_t schema_version{contract_schema_version};
};

struct RecordingConfig {
    bool enabled{};
    std::string output_uri;
    bool record_iq{};
    bool record_spectrum{};
    std::uint32_t chunk_samples{1048576U};
    std::uint32_t queue_capacity{8U};
    bool stop_on_overflow{true};
    std::uint32_t schema_version{contract_schema_version};
};

void validate(const DeviceConfig& value);
void validate(const DspConfig& value);
void validate(const PersistenceConfig& value);
void validate(const SweepConfig& value);
void validate(const RecordingConfig& value);

}  // namespace sdr_core

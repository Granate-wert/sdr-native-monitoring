#pragma once

#include "sdr_core/types.hpp"

#include <string>
#include <vector>

namespace sdr_core {

struct NumericRange {
    double minimum{};
    double maximum{};
    std::optional<double> step;

    [[nodiscard]] bool contains(double value) const noexcept;
};

struct DeviceCapabilities {
    std::string backend_id;
    std::string device_id;
    std::string serial;
    std::string model;
    std::string firmware;
    NumericRange tuning_range_hz;
    std::vector<NumericRange> sample_rate_ranges_hz;
    std::vector<NumericRange> analog_bandwidth_ranges_hz;
    NumericRange gain_range_db;
    std::vector<GainMode> gain_modes;
    std::vector<SampleFormat> sample_formats;
    bool supports_hardware_timestamps{};
    bool supports_fastlock{};
    bool supports_temperature{};
    bool supports_overflow_counter{};
    bool supports_continuous_iq{true};
    std::uint32_t schema_version{contract_schema_version};
};

void validate(const NumericRange& value);
void validate(const DeviceCapabilities& value);

}  // namespace sdr_core

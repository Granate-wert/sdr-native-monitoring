#pragma once

#include "sdr_core/capabilities.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace sdr_pluto {

struct RuntimeInfo {
    bool available{};
    std::string library_path;
    std::uint32_t major{};
    std::uint32_t minor{};
    std::string git_tag;
    std::vector<std::string> backends;
    std::string error;
};

struct ContextInfo {
    std::string uri;
    std::string description;
};

struct ContextProbe {
    std::string uri;
    std::string context_name;
    std::string description;
    std::uint32_t backend_major{};
    std::uint32_t backend_minor{};
    std::string backend_tag;
    std::string model;
    std::string serial;
    std::string firmware;
    std::vector<std::string> device_ids;
    std::string phy_device_id;
    std::string rx_stream_device_id;
};

struct SampleLayout {
    std::uint32_t storage_bits{};
    std::uint32_t significant_bits{};
    std::uint32_t shift{};
    bool is_signed{};
    bool is_big_endian{};
    std::uint32_t repeat{};
    std::ptrdiff_t stride_bytes{};
    sdr_core::SampleFormat output_format{sdr_core::SampleFormat::ComplexInt12InInt16Le};
};

struct AppliedConfig {
    sdr_core::DeviceConfig requested;
    double center_frequency_hz{};
    double sample_rate_hz{};
    double analog_bandwidth_hz{};
    sdr_core::GainMode gain_mode{sdr_core::GainMode::Manual};
    double manual_gain_db{};
    std::uint64_t config_generation{};
    SampleLayout sample_layout;
};

struct StreamMetrics {
    std::uint64_t blocks_received{};
    std::uint64_t samples_received{};
    std::uint64_t short_reads{};
    std::uint64_t refill_errors{};
    std::uint64_t output_pool_exhaustions{};
    std::uint64_t output_blocks_dropped{};
    std::uint64_t estimated_dropped_samples{};
};

[[nodiscard]] RuntimeInfo runtime_info();
[[nodiscard]] std::vector<ContextInfo> scan_contexts(const std::string& filter = "usb,ip");
[[nodiscard]] ContextProbe probe_context(const std::string& uri, std::uint32_t timeout_ms = 3000U);

class PlutoDevice final {
public:
    explicit PlutoDevice(std::string uri, std::uint32_t timeout_ms = 3000U);
    ~PlutoDevice();

    PlutoDevice(const PlutoDevice&) = delete;
    PlutoDevice& operator=(const PlutoDevice&) = delete;
    PlutoDevice(PlutoDevice&&) noexcept;
    PlutoDevice& operator=(PlutoDevice&&) noexcept;

    [[nodiscard]] bool connected() const noexcept;
    [[nodiscard]] std::string uri() const;
    [[nodiscard]] ContextProbe probe() const;
    [[nodiscard]] sdr_core::DeviceCapabilities capabilities() const;
    [[nodiscard]] AppliedConfig configure(const sdr_core::DeviceConfig& config);
    [[nodiscard]] AppliedConfig configure(
        const sdr_core::DeviceConfig& config,
        std::uint32_t output_pool_blocks
    );
    [[nodiscard]] AppliedConfig applied_config() const;

    void start_stream();
    [[nodiscard]] sdr_core::IqBlock refill();
    void cancel() noexcept;
    void stop_stream() noexcept;
    void disconnect() noexcept;

    [[nodiscard]] bool streaming() const noexcept;
    [[nodiscard]] StreamMetrics metrics() const noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_pluto
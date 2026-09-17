#pragma once

#include "sdr_core/capabilities.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
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
    // These are dynamic-export observations only.  They do not mean that a
    // receive-path option is supported by a particular context, nor that the
    // option has been applied to a device.
    bool supports_kernel_buffer_count{};
    bool supports_buffer_blocking_mode{};
    bool supports_buffer_poll_fd{};
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

// A read-only IIO topology observation.  It intentionally contains no buffer,
// no enabled state and no transport/rate claim.  A visible I/Q pair proves
// only the digital scan layout; RF-path routing/independence is separate E6
// evidence.
struct InputScanElement {
    std::string id;
    std::uint32_t device_channel_index{};
    std::uint32_t storage_bits{};
    std::uint32_t significant_bits{};
    std::uint32_t shift{};
    bool is_signed{};
    bool is_big_endian{};
    std::uint32_t repeat{};
};

struct ReceiverTopologyProbe {
    ContextProbe context;
    std::vector<std::string> phy_rx_channel_ids;
    std::vector<InputScanElement> input_scan_elements;
};

// E1 selection is intentionally Pluto/AD936x-specific, rather than a field
// on the portable sdr_core::DeviceConfig.  RX1/RX2 are names of the active
// IIO topology, not a generic SDR capability.  A selection is one physical
// stream ownership request; Both means one buffer containing both complex
// receiver chains, never two competing buffers or independently tuned radios.
enum class ReceiverSelection : std::uint8_t {
    Rx1,
    Rx2,
    Both,
};

// One successful refill of the one admitted IIO buffer.  `Both` emits two
// blocks that deliberately retain the same source sequence, sample index,
// timestamp and configuration generation: a transport/control gap is shared
// by both receiver chains.  Single-chain selections emit only their requested
// chain. This low-level device result is for native diagnostics/tests; it is
// not the normal Python/Qt per-sample publication contract.
struct ReceiverIqBlockSet {
    ReceiverSelection selection{ReceiverSelection::Rx1};
    std::optional<sdr_core::IqBlock> rx1;
    std::optional<sdr_core::IqBlock> rx2;
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
    // Cumulative host-wall time inside libiio's blocking refill call. This is
    // transport/driver observability, not a device overflow counter.
    std::uint64_t refill_wait_ns{};
    // Every attempted libiio refill, including an error/cancellation.  Together
    // with refill_wait_ns this gives a bounded-observation average for any
    // evidence interval; it never represents a device-side packet counter.
    std::uint64_t refill_calls{};
    // A refill taking longer than one or two nominal buffer periods calculated
    // from the applied sample rate. These are host-observed timing thresholds,
    // not a statement about RF continuity or device overflow.
    std::uint64_t refill_wait_over_nominal_period{};
    std::uint64_t refill_wait_over_two_nominal_periods{};
    // Cumulative host-wall time required to acquire output storage and turn a
    // received AD936x scan into the canonical CI12-in-i16-le IqBlock.  It
    // contains no retained payload and is intentionally separate from refill.
    std::uint64_t canonicalization_ns{};
    // Time between returning one successful IqBlock and entering the following
    // refill call. It attributes host-side work after the source adapter
    // separately from libiio's blocking wait. The first block of a stream has
    // no preceding interval, and errors never create a synthetic interval.
    std::uint64_t inter_refill_gap_ns{};
    std::uint64_t inter_refill_gap_count{};
    std::uint64_t short_reads{};
    std::uint64_t refill_errors{};
    std::uint64_t output_pool_exhaustions{};
    std::uint64_t output_blocks_dropped{};
    std::uint64_t estimated_dropped_samples{};
};

[[nodiscard]] RuntimeInfo runtime_info();
[[nodiscard]] std::vector<ContextInfo> scan_contexts(const std::string& filter = "usb,ip");
[[nodiscard]] ContextProbe probe_context(const std::string& uri, std::uint32_t timeout_ms = 3000U);
[[nodiscard]] ReceiverTopologyProbe probe_receiver_topology(
    const std::string& uri,
    std::uint32_t timeout_ms = 3000U
);

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
    [[nodiscard]] AppliedConfig configure(
        const sdr_core::DeviceConfig& config,
        ReceiverSelection selection,
        std::uint32_t output_pool_blocks
    );
    [[nodiscard]] AppliedConfig applied_config() const;
    [[nodiscard]] ReceiverSelection receiver_selection() const noexcept;

    void start_stream();
    [[nodiscard]] sdr_core::IqBlock refill();
    [[nodiscard]] ReceiverIqBlockSet refill_receivers();
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

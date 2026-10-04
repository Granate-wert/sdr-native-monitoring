#pragma once

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/types.hpp"

#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>

namespace sdr_rtlsdr {

// Vendor API callback data are interleaved *unsigned* 8-bit I/Q. The native
// owner converts them to signed CI8 in its bounded slot pool before CPU DSP.
using RtlBytesCallback = void (*)(std::span<const std::uint8_t>, void*) noexcept;

// One currently enumerated USB session, never a stable device or calibration
// identity. Same-string compatible replacement cannot be distinguished.
struct RtlSessionRoute {
    std::string manufacturer;
    std::string product;
    std::string serial;
    std::uint32_t tuner_type{};
    std::uint64_t selection_revision{};
};

struct RtlProfile {
    std::uint32_t center_hz{};
    std::uint32_t sample_rate_hz{};
    std::uint32_t fft_size{4096U};
    std::uint32_t hop_size{2048U};
    std::uint32_t slot_count{8U};
    std::uint32_t ready_capacity{6U};
    std::uint32_t dsp_output_capacity{10U};
    std::uint32_t presentation_capacity{4U};
    std::uint64_t configuration_generation{1U};
    std::string source_id{"native.rtl_sdr.live"};
    std::string expected_unique_serial;
    std::optional<RtlSessionRoute> session_route;
    bool official_unbundled_runtime{};  // set by the native official factory only
    sdr_core::DetectorType detector{sdr_core::DetectorType::Sample};
    // Absent = existing automatic default. Integer tenths of dB, exact table entry.
    std::optional<int> manual_tuner_gain_tenth_db;
    std::uint32_t analytical_event_capacity{};
};

void validate_rtl_profile(const RtlProfile& profile);
// Used only when an external SDK close/open transition leaves handle
// ownership ambiguous. It bars every subsequent RTL Start in this process.
void quarantine_rtl_process() noexcept;
[[nodiscard]] bool rtl_process_quarantined() noexcept;
[[nodiscard]] bool try_acquire_rtl_process_lease() noexcept;
void release_rtl_process_lease() noexcept;

// One injected, family-specific SDK owner. The official implementation is
// optional and deliberately unbundled; ordinary builds only compile mocks.
class RtlRuntimePort {
public:
    virtual ~RtlRuntimePort() = default;
    virtual int open_exact_unique_serial(const std::string& expected) noexcept = 0;
    virtual int open_selected_session_route(const RtlSessionRoute& expected) noexcept = 0;
    virtual int set_sample_rate(std::uint32_t value) noexcept = 0;
    virtual std::uint32_t get_sample_rate() noexcept = 0;
    virtual int set_center_frequency(std::uint32_t value) noexcept = 0;
    virtual std::uint32_t get_center_frequency() noexcept = 0;
    virtual int set_automatic_tuner_gain() noexcept = 0;
    // Optional protocol: unsupported injected ports fail closed, preserving auto.
    virtual int set_manual_tuner_gain(int) noexcept { return -103; }
    virtual std::optional<int> get_cached_tuner_gain() noexcept { return std::nullopt; }
    virtual int reset_buffer() noexcept = 0;
    virtual int verify_normal_tuner_mode() noexcept = 0;
    // Blocks on the caller's native RX thread until cancellation or failure.
    virtual int read_async(RtlBytesCallback callback, void* context,
                           std::uint32_t buffer_bytes) noexcept = 0;
    virtual int cancel_async() noexcept = 0;
    virtual int close() noexcept = 0;
};

struct RtlMetrics {
    std::uint64_t callbacks{};
    std::uint64_t malformed_callbacks{};
    std::uint64_t callbacks_after_stop{};
    std::uint64_t host_input_blocks_dropped{};
    std::uint64_t host_input_samples_dropped{};
    bool host_loss_cardinality_unknown{};
    std::uint64_t blocks_admitted{};
    std::uint64_t samples_admitted{};
    std::uint64_t worker_failures{};
    bool reader_returned{};
    // Valid only when reader_returned is true; zero beforehand is not success.
    int reader_return_status{};
    bool reader_returned_without_stop{};
    std::uint32_t ready_depth{};
    std::uint32_t ready_high_water{};
    std::uint32_t slots_in_use{};
    std::uint64_t presentation_frames_superseded{};
    sdr_core::DspBackendMetrics dsp{};
    // No hardware timestamp, USB overflow count or RF duty proof is implied.
};

struct RtlLatestFrame {
    // The common Python SpectrumFrame binding uses value ownership, as do
    // Pluto/HackRF latest-frame drains. Do not hand it a const shared_ptr.
    std::optional<sdr_core::SpectrumFrame> frame;
    std::uint32_t coalesced_frames{};
};

struct RtlStopResult {
    bool cancel_requested{};
    int cancel_status{};
    int first_cancel_error_status{};
    bool reader_joined{};
    bool dsp_joined{};
    bool close_called{};
    int close_status{};

    [[nodiscard]] bool complete() const noexcept {
        return reader_joined && dsp_joined && close_called && close_status == 0;
    }
};

struct RtlAcquisitionReadback {
    std::uint64_t session_epoch{};
    std::uint32_t actual_sample_rate_hz{};
    std::uint32_t actual_center_hz{};
    bool tuner_gain_readback_known{};  // no gain-mode getter is asserted
    // SDK cached setting, NOT independently measured RF gain or mode readback.
    std::optional<int> cached_tuner_gain_tenth_db;
};

// One RX thread and one CPU-DSP worker. The C ABI callback never calls Python,
// allocates an IQ buffer or performs an FFT. Stop must quiesce/join both before
// closing the vendor handle; a failed Stop retains this owner for explicit
// retry and bars a fresh Start.
class RtlRuntimeSession final {
public:
    [[nodiscard]] static std::unique_ptr<RtlRuntimeSession> start(
        std::unique_ptr<RtlRuntimePort> runtime, RtlProfile profile);
    ~RtlRuntimeSession();
    RtlRuntimeSession(const RtlRuntimeSession&) = delete;
    RtlRuntimeSession& operator=(const RtlRuntimeSession&) = delete;

    [[nodiscard]] RtlLatestFrame drain_latest_spectrum_frame();
    [[nodiscard]] RtlMetrics metrics() const;
    [[nodiscard]] sdr_core::AnalyticalReadyDrain drain_analytical_ready_events(std::size_t max_items);
    [[nodiscard]] RtlStopResult stop(std::chrono::milliseconds timeout) noexcept;
    [[nodiscard]] bool running() const noexcept;
    [[nodiscard]] bool cleanup_required() const noexcept;
    [[nodiscard]] RtlAcquisitionReadback readback() const noexcept;

private:
    struct Impl;
    explicit RtlRuntimeSession(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_rtlsdr

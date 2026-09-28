#pragma once

#include "sdr_hackrf/hackrf_rx_session.hpp"
#include "sdr_hackrf/hackrf_sweep_sequence.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>

namespace sdr_hackrf {

inline constexpr std::uint16_t hackrf_sweep_min_usb_api = 0x0104U;
inline constexpr std::uint32_t hackrf_sweep_max_ready_blocks = 256U;

struct HackrfSweepProfile {
    HackrfSweepSequencePlan sequence;
    double sample_rate_hz{20'000'000.0};
    std::uint32_t baseband_filter_hz{15'000'000U};
    std::uint32_t lna_gain_db{16U};
    std::uint32_t vga_gain_db{20U};
    std::uint32_t ready_capacity{64U};
    std::uint64_t config_generation{1U};
};

// The official implementation is the SAME single HackRF handle as the RTBW
// port, selected through a different explicit factory. These extra methods
// are never invoked by the existing fixed-centre RTBW session.
class HackrfSweepRuntimePort : public HackrfRxRuntimePort {
public:
    ~HackrfSweepRuntimePort() override = default;
    virtual int read_usb_api_version(std::uint16_t& version) noexcept = 0;
    virtual int initialize_sweep(const HackrfSweepSequencePlan& plan) noexcept = 0;
    virtual int start_rx_sweep(HackrfRxBytesCallback callback, void* context) noexcept = 0;
};

struct HackrfSweepQueuedBlock {
    // A complete, copied firmware block without the 10-byte header. A caller
    // may retain this value after try_pop() without borrowing callback/queue
    // storage. It is not yet an admitted RF subband or SpectrumFrame.
    std::array<std::uint8_t, hackrf_sweep_ci8_bytes> interleaved_ci8{};
    std::uint64_t reported_tuned_frequency_hz{};
    std::uint64_t scan_epoch{};
    std::uint64_t continuity_epoch{};
    std::uint32_t plan_index{};
    std::uint32_t range_index{};
    std::uint32_t step_index{};
    std::uint8_t interleave_phase{};
    std::uint32_t known_skipped_headers_before{};
    std::int64_t host_timestamp_ns{};
    std::uint64_t config_generation{};
    bool gap_before{};
    bool new_scan{};
};

struct HackrfSweepSessionMetrics {
    // Absent until Stop has proved callback quiescence. Never display an
    // all-zero live snapshot as evidence that headers/losses were clean.
    std::optional<HackrfSweepSequenceMetrics> sequence;
    std::uint64_t callbacks_seen{};
    std::uint64_t callback_bytes_seen{};
    std::uint64_t callback_gate_drops{};
    std::uint64_t invalid_timestamp_callbacks{};
    std::uint64_t blocks_queued{};
    std::uint64_t blocks_popped{};
    std::uint64_t blocks_abandoned{};
    std::uint64_t ready_full_drops{};
    std::uint32_t ready_depth{};
    std::uint32_t ready_high_water{};
    std::uint32_t callbacks_active{};
    bool accepting_callbacks{};
    // Not an RF/USB/device-overrun or probability-of-detection measurement.
    bool device_overrun_counter_available{};
};

struct HackrfSweepStopResult {
    bool stop_rx_called{};
    int stop_rx_status{};
    bool callbacks_quiescent{};
    std::uint64_t abandoned_blocks{};
    bool close_called{};
    int close_status{};
    int first_close_error{};
    bool exit_called{};
    int exit_status{};
    int first_exit_error{};

    // "complete" means the SDK handle/library and callback lifetime were
    // released. A failed vendor Stop followed by a successful close is a
    // completed but NOT clean shutdown; its first error remains visible.
    [[nodiscard]] bool complete() const noexcept {
        return stop_rx_called && callbacks_quiescent &&
               close_called && close_status == 0 && exit_called && exit_status == 0;
    }
    [[nodiscard]] bool clean() const noexcept {
        return complete() && stop_rx_status == 0 &&
               first_close_error == 0 && first_exit_error == 0;
    }
};

// Native source owner only: no DSP, Python admission, GUI or RF coverage is
// implied. One consumer calls try_pop; Stop and consumer polling are serialized
// internally. On an incomplete Stop the owner MUST remain alive for retry.
class HackrfSweepSession final {
public:
    [[nodiscard]] static std::unique_ptr<HackrfSweepSession> start(
        std::unique_ptr<HackrfSweepRuntimePort> runtime,
        HackrfSweepProfile profile
    );

    ~HackrfSweepSession();
    HackrfSweepSession(const HackrfSweepSession&) = delete;
    HackrfSweepSession& operator=(const HackrfSweepSession&) = delete;

    [[nodiscard]] bool try_pop(HackrfSweepQueuedBlock& output) noexcept;
    // The full gate snapshot is safe only after callback quiescence. While
    // Live, this returns queue/callback counters and no sequence snapshot.
    [[nodiscard]] HackrfSweepSessionMetrics metrics() const noexcept;
    [[nodiscard]] HackrfSweepStopResult stop(
        std::chrono::milliseconds callback_timeout
    ) noexcept;
    [[nodiscard]] bool running() const noexcept;
    [[nodiscard]] std::uint16_t observed_usb_api_version() const noexcept;
    [[nodiscard]] std::uint32_t transfer_bytes() const noexcept;

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
    void test_hold_active_callback(
        std::atomic<bool>& entered,
        const std::atomic<bool>& release
    ) noexcept;
#endif

private:
    struct Impl;
    explicit HackrfSweepSession(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_hackrf

#pragma once

#include "sdr_core/types.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <span>

namespace sdr_hackrf {

inline constexpr std::uint32_t hackrf_rx_max_slot_count = 256U;
inline constexpr std::uint32_t hackrf_rx_max_slot_bytes = 16U * 1024U * 1024U;

namespace detail {
struct HackrfRxIngressState;
}

struct HackrfRxIngressConfig {
    std::uint32_t slot_count{8U};
    // Exact byte length of one admitted libhackrf transfer. Short/oversized
    // callbacks are loss events rather than partially shaped IqBlock objects.
    std::uint32_t slot_bytes{262'144U};
    std::uint32_t ready_capacity{6U};
    double center_frequency_hz{};
    double sample_rate_hz{};
    std::uint64_t config_generation{};
};

struct HackrfRxBlockMetadata {
    std::uint64_t source_sequence{};
    std::uint64_t first_sample_index{};
    std::int64_t host_timestamp_ns{};
    std::uint32_t sample_count{};
    sdr_core::SampleFormat sample_format{
        sdr_core::SampleFormat::ComplexInt8Interleaved
    };
    sdr_core::QualityFlag quality_flags{sdr_core::QualityFlag::TimestampEstimated};
    std::uint64_t config_generation{};
};

enum class HackrfRxAdmissionResult : std::uint8_t {
    Admitted,
    Stopped,
    Malformed,
    Short,
    Oversized,
    LockContended,
    PoolExhausted,
    QueueFull,
};

enum class HackrfRxPopResult : std::uint8_t {
    Popped,
    Stopped,
};

struct HackrfRxIngressMetrics {
    std::uint32_t slot_capacity{};
    std::uint32_t slot_bytes{};
    std::uint32_t slots_in_use{};
    std::uint32_t slots_high_water{};
    std::uint32_t ready_capacity{};
    std::uint32_t ready_depth{};
    std::uint32_t ready_high_water{};
    std::uint32_t callbacks_active{};
    bool accepting_callbacks{};

    std::uint64_t callbacks_total{};
    std::uint64_t callback_bytes_total{};
    std::uint64_t blocks_admitted{};
    std::uint64_t bytes_admitted{};
    std::uint64_t samples_admitted{};
    std::uint64_t blocks_popped{};
    std::uint64_t leases_released{};

    std::uint64_t malformed_callbacks{};
    std::uint64_t short_callbacks{};
    std::uint64_t oversized_callbacks{};
    std::uint64_t callbacks_after_stop{};
    std::uint64_t lock_contention_drops{};
    std::uint64_t pool_exhaustion_drops{};
    std::uint64_t queue_full_drops{};
    std::uint64_t abandoned_blocks{};
    std::uint64_t dropped_bytes{};
    std::uint64_t dropped_samples{};
    std::uint64_t loss_events{};

    // HackRF One exposes no device/FPGA overflow counter through the bounded
    // R11-G contract. Host-clean counters must never be promoted to device
    // continuity evidence.
    bool device_overrun_counter_available{};
};

class HackrfRxLease final {
public:
    HackrfRxLease() noexcept = default;
    ~HackrfRxLease();

    HackrfRxLease(const HackrfRxLease&) = delete;
    HackrfRxLease& operator=(const HackrfRxLease&) = delete;
    HackrfRxLease(HackrfRxLease&& other) noexcept;
    HackrfRxLease& operator=(HackrfRxLease&& other) noexcept;

    [[nodiscard]] explicit operator bool() const noexcept;
    [[nodiscard]] std::span<const std::uint8_t> samples() const noexcept;
    [[nodiscard]] const HackrfRxBlockMetadata& metadata() const noexcept;
    // Transfers the slot into the common native IqBlock without copying raw
    // I/Q again. The slot returns to the ingress pool when the last IqBlock
    // sample owner releases it. This consumer-side ownership wrapper may
    // allocate; it is never called on the libhackrf callback thread.
    [[nodiscard]] sdr_core::IqBlock take_iq_block();
    void reset() noexcept;

private:
    friend class HackrfRxIngress;
    HackrfRxLease(
        std::shared_ptr<detail::HackrfRxIngressState> state,
        std::uint32_t slot_index,
        HackrfRxBlockMetadata metadata
    ) noexcept;

    std::shared_ptr<detail::HackrfRxIngressState> state_;
    std::uint32_t slot_index_{};
    HackrfRxBlockMetadata metadata_{};
};

// Callback-facing, allocation-free-after-construction CI8 ingress.
//
// The official libhackrf callback may call admit_callback(), but the ingress
// itself owns no SDK handle and exposes no TX/configuration operation. The
// callback never waits: mutex contention, pool exhaustion and a full ready
// ring are explicit host drops and make the next admitted block discontinuous.
class HackrfRxIngress final {
public:
    explicit HackrfRxIngress(HackrfRxIngressConfig config);
    ~HackrfRxIngress();

    HackrfRxIngress(const HackrfRxIngress&) = delete;
    HackrfRxIngress& operator=(const HackrfRxIngress&) = delete;

    [[nodiscard]] HackrfRxAdmissionResult admit_callback(
        std::span<const std::uint8_t> interleaved_ci8,
        std::int64_t host_timestamp_ns
    ) noexcept;
    // Consumer-only blocking pop. Pending leases are drained after
    // request_stop(); Stopped is returned only when callback admission is
    // closed, every active callback has returned and the ready ring is empty.
    [[nodiscard]] HackrfRxPopResult pop(HackrfRxLease& output) noexcept;
    [[nodiscard]] bool try_pop(HackrfRxLease& output) noexcept;

    void request_stop() noexcept;
    [[nodiscard]] bool wait_callbacks_quiescent(std::chrono::milliseconds timeout) noexcept;
    [[nodiscard]] std::uint64_t abandon_ready() noexcept;
    [[nodiscard]] HackrfRxIngressMetrics metrics() const noexcept;

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
    // Deterministic shutdown-race hook. It exercises the same active-callback
    // counter/quiescence notification as admit_callback(), but never exists in
    // a production build and never touches an SDR runtime.
    void test_hold_active_callback(
        std::atomic<bool>& entered,
        const std::atomic<bool>& release
    ) noexcept;
    // Deterministically occupies the callback single-writer gate so tests can
    // prove that a concurrent rejected callback marks the next later admitted
    // sequence, never the callback that was already in progress.
    void test_hold_callback_gate(
        std::atomic<bool>& entered,
        const std::atomic<bool>& release
    ) noexcept;
#endif

private:
    std::shared_ptr<detail::HackrfRxIngressState> state_;
};

// Minimal RX-only shutdown surface. A concrete libhackrf owner is a later
// package; no start/configure/TX symbol appears in this R11-G interface.
class HackrfRxShutdownPort {
public:
    virtual ~HackrfRxShutdownPort() = default;
    virtual int stop_rx() noexcept = 0;
    virtual int close_device() noexcept = 0;
    virtual int exit_library() noexcept = 0;
};

struct HackrfRxShutdownResult {
    int stop_rx_status{};
    bool callbacks_quiescent{};
    std::uint64_t abandoned_blocks{};
    bool close_called{};
    int close_status{};
    bool exit_called{};
    int exit_status{};

    [[nodiscard]] bool complete() const noexcept;
};

// Exact order: reject new callback admission -> stop RX -> wait for callback
// quiescence -> explicitly abandon pending copied blocks -> close -> library
// exit. If callbacks do not quiesce, close/exit are refused to prevent use-
// after-close and the owner remains available for an explicit recovery path.
[[nodiscard]] HackrfRxShutdownResult shutdown_hackrf_rx(
    HackrfRxIngress& ingress,
    HackrfRxShutdownPort& port,
    std::chrono::milliseconds callback_timeout
) noexcept;

}  // namespace sdr_hackrf

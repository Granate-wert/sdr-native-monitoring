#pragma once

#include "sdr_hackrf/hackrf_rx_ingress.hpp"

#include <chrono>
#include <cstdint>
#include <memory>
#include <span>

namespace sdr_hackrf {

class HackrfRuntimeDspSession;

struct HackrfRxProfile {
    double center_frequency_hz{100'000'000.0};
    double sample_rate_hz{10'000'000.0};
    std::uint32_t baseband_filter_hz{8'000'000U};
    std::uint32_t lna_gain_db{16U};
    std::uint32_t vga_gain_db{20U};
    bool rf_amplifier_enabled{};
    bool bias_tee_enabled{};
    std::uint32_t slot_count{32U};
    std::uint32_t ready_capacity{24U};
    std::uint64_t config_generation{1U};
};

// Pure profile validation shared by every composition path. It performs no
// library/device action, so a future factory can reject a malformed plan
// before it creates an official runtime port.
void validate_hackrf_rx_profile(const HackrfRxProfile& profile);

using HackrfRxBytesCallback = int (*)(
    std::span<const std::uint8_t> interleaved_ci8,
    std::int64_t host_timestamp_ns,
    void* context
) noexcept;

// R11-H private device-family boundary. A concrete implementation owns the
// official library/device and translates its SDK callback into byte spans.
// No SDK type, TX operation or raw identity crosses this interface.
class HackrfRxRuntimePort : public HackrfRxShutdownPort {
public:
    ~HackrfRxRuntimePort() override = default;

    virtual int initialize_library() noexcept = 0;
    virtual int open_exactly_one_hackrf_one() noexcept = 0;
    [[nodiscard]] virtual std::uint32_t transfer_buffer_size() const noexcept = 0;
    virtual int set_sample_rate(double sample_rate_hz) noexcept = 0;
    virtual int set_baseband_filter(std::uint32_t bandwidth_hz) noexcept = 0;
    virtual int set_center_frequency(std::uint64_t center_frequency_hz) noexcept = 0;
    virtual int set_rf_amplifier(bool enabled) noexcept = 0;
    virtual int set_bias_tee(bool enabled) noexcept = 0;
    virtual int set_lna_gain(std::uint32_t gain_db) noexcept = 0;
    virtual int set_vga_gain(std::uint32_t gain_db) noexcept = 0;
    virtual int start_rx(HackrfRxBytesCallback callback, void* context) noexcept = 0;
};

struct HackrfRxQuiesceResult {
    bool stop_rx_called{};
    int stop_rx_status{};
    bool callbacks_quiescent{};

    [[nodiscard]] bool complete() const noexcept {
        return stop_rx_called && stop_rx_status == 0 && callbacks_quiescent;
    }
};

struct HackrfRxFinalizeResult {
    bool drain_verified{};
    bool close_called{};
    int close_status{};
    bool exit_called{};
    int exit_status{};

    [[nodiscard]] bool complete() const noexcept {
        return drain_verified && close_called && close_status == 0 &&
               exit_called && exit_status == 0;
    }
};

class HackrfRxSession final {
public:
    [[nodiscard]] static std::unique_ptr<HackrfRxSession> start(
        std::unique_ptr<HackrfRxRuntimePort> runtime,
        HackrfRxProfile profile = {}
    );

    ~HackrfRxSession();
    HackrfRxSession(const HackrfRxSession&) = delete;
    HackrfRxSession& operator=(const HackrfRxSession&) = delete;

    [[nodiscard]] bool try_pop(HackrfRxLease& output) noexcept;
    [[nodiscard]] HackrfRxIngressMetrics metrics() const noexcept;
    [[nodiscard]] const HackrfRxProfile& profile() const noexcept;
    [[nodiscard]] std::uint32_t transfer_bytes() const noexcept;
    [[nodiscard]] bool running() const noexcept;

    // Phase 1 for a composed consumer: close admission, stop the producer and
    // prove callback quiescence. Pending ready leases are deliberately kept.
    [[nodiscard]] HackrfRxQuiesceResult quiesce(
        std::chrono::milliseconds callback_timeout
    ) noexcept;
    // Phase 3: allowed only after an external consumer has drained/joined and
    // the shared ingress has no ready, active or borrowed slot left.
    [[nodiscard]] HackrfRxFinalizeResult finalize_after_drain() noexcept;

    [[nodiscard]] HackrfRxShutdownResult stop(
        std::chrono::milliseconds callback_timeout
    ) noexcept;

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
    [[nodiscard]] std::shared_ptr<HackrfRxIngress> test_shared_ingress() const noexcept;
#endif

private:
    friend class HackrfRuntimeDspSession;
    HackrfRxSession(
        std::unique_ptr<HackrfRxRuntimePort> runtime,
        HackrfRxProfile profile
    );
    void initialize_and_start();
    void cleanup_unstarted() noexcept;
    [[nodiscard]] std::shared_ptr<HackrfRxIngress> shared_ingress() const noexcept;
    [[nodiscard]] static int callback_bridge(
        std::span<const std::uint8_t> interleaved_ci8,
        std::int64_t host_timestamp_ns,
        void* context
    ) noexcept;

    std::unique_ptr<HackrfRxRuntimePort> runtime_;
    HackrfRxProfile profile_;
    std::shared_ptr<HackrfRxIngress> ingress_;
    std::uint32_t transfer_bytes_{};
    bool library_initialized_{};
    bool device_open_{};
    bool running_{};
    bool stop_rx_succeeded_{};
    bool close_succeeded_{};
    bool exit_succeeded_{};
    HackrfRxQuiesceResult quiesce_result_{};
    HackrfRxFinalizeResult finalize_result_{};
    std::uint64_t shutdown_abandoned_blocks_{};
    HackrfRxShutdownResult shutdown_result_{};
};

}  // namespace sdr_hackrf

#include "sdr_hackrf/hackrf_rx_session.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <exception>
#include <string>
#include <utility>

namespace sdr_hackrf {
namespace {

constexpr std::array<std::uint32_t, 16> allowed_filter_bandwidths{
    1'750'000U, 2'500'000U, 3'500'000U, 5'000'000U,
    5'500'000U, 6'000'000U, 7'000'000U, 8'000'000U,
    9'000'000U, 10'000'000U, 12'000'000U, 14'000'000U,
    15'000'000U, 20'000'000U, 24'000'000U, 28'000'000U,
};

}  // namespace

void validate_hackrf_rx_profile(const HackrfRxProfile& profile) {
    if (!std::isfinite(profile.center_frequency_hz) ||
        profile.center_frequency_hz < 1'000'000.0 ||
        profile.center_frequency_hz > 6'000'000'000.0) {
        throw sdr_core::ConfigurationError("HackRF RX center frequency is invalid");
    }
    if (!std::isfinite(profile.sample_rate_hz) ||
        profile.sample_rate_hz < 2'000'000.0 ||
        profile.sample_rate_hz > 20'000'000.0) {
        throw sdr_core::ConfigurationError("HackRF RX sample rate is invalid");
    }
    if (std::find(
            allowed_filter_bandwidths.begin(),
            allowed_filter_bandwidths.end(),
            profile.baseband_filter_hz
        ) == allowed_filter_bandwidths.end() ||
        profile.baseband_filter_hz > profile.sample_rate_hz) {
        throw sdr_core::ConfigurationError("HackRF RX filter bandwidth is invalid");
    }
    if (profile.lna_gain_db > 40U || (profile.lna_gain_db % 8U) != 0U ||
        profile.vga_gain_db > 62U || (profile.vga_gain_db % 2U) != 0U) {
        throw sdr_core::ConfigurationError("HackRF RX gain step is invalid");
    }
    if (profile.rf_amplifier_enabled || profile.bias_tee_enabled) {
        throw sdr_core::ConfigurationError(
            "R11-H requires RF amplifier and bias tee to remain disabled"
        );
    }
    if (profile.slot_count == 0U || profile.slot_count > hackrf_rx_max_slot_count ||
        profile.ready_capacity == 0U ||
        profile.ready_capacity > profile.slot_count) {
        throw sdr_core::ConfigurationError("HackRF RX pool/ring bounds are invalid");
    }
}

namespace {

void require_success(const int status, const char* stage) {
    if (status != 0) {
        throw sdr_core::DeviceError(
            std::string("HackRF RX stage failed: ") + stage +
            " (status " + std::to_string(status) + ")"
        );
    }
}

}  // namespace

std::unique_ptr<HackrfRxSession> HackrfRxSession::start(
    std::unique_ptr<HackrfRxRuntimePort> runtime,
    const HackrfRxProfile profile
) {
    if (!runtime) {
        throw sdr_core::ConfigurationError("HackRF RX runtime is required");
    }
    validate_hackrf_rx_profile(profile);
    auto session = std::unique_ptr<HackrfRxSession>(
        new HackrfRxSession(std::move(runtime), profile)
    );
    session->initialize_and_start();
    return session;
}

HackrfRxSession::HackrfRxSession(
    std::unique_ptr<HackrfRxRuntimePort> runtime,
    const HackrfRxProfile profile
) : runtime_(std::move(runtime)), profile_(profile) {}

HackrfRxSession::~HackrfRxSession() {
    if (running_) {
        const auto result = stop(std::chrono::seconds(5));
        // Returning while the vendor callback still holds `this` would be a
        // use-after-free. A failed explicit stop must keep the owner alive;
        // destruction is therefore a fail-stop condition, never best-effort.
        if (!result.complete()) {
            std::terminate();
        }
    } else {
        cleanup_unstarted();
    }
}

void HackrfRxSession::initialize_and_start() {
    require_success(runtime_->initialize_library(), "library_init");
    library_initialized_ = true;
    require_success(runtime_->open_exactly_one_hackrf_one(), "open_exactly_one");
    device_open_ = true;

    transfer_bytes_ = runtime_->transfer_buffer_size();
    if (transfer_bytes_ == 0U || transfer_bytes_ > hackrf_rx_max_slot_bytes ||
        (transfer_bytes_ % 2U) != 0U) {
        throw sdr_core::DeviceError("HackRF RX transfer size failed bounded validation");
    }

    ingress_ = std::make_shared<HackrfRxIngress>(HackrfRxIngressConfig{
        .slot_count = profile_.slot_count,
        .slot_bytes = transfer_bytes_,
        .ready_capacity = profile_.ready_capacity,
        .center_frequency_hz = profile_.center_frequency_hz,
        .sample_rate_hz = profile_.sample_rate_hz,
        .config_generation = profile_.config_generation,
    });

    require_success(runtime_->set_sample_rate(profile_.sample_rate_hz), "sample_rate");
    require_success(
        runtime_->set_baseband_filter(profile_.baseband_filter_hz),
        "baseband_filter"
    );
    require_success(
        runtime_->set_center_frequency(
            static_cast<std::uint64_t>(profile_.center_frequency_hz)
        ),
        "center_frequency"
    );
    require_success(
        runtime_->set_rf_amplifier(profile_.rf_amplifier_enabled),
        "rf_amplifier"
    );
    require_success(runtime_->set_bias_tee(profile_.bias_tee_enabled), "bias_tee");
    require_success(runtime_->set_lna_gain(profile_.lna_gain_db), "lna_gain");
    require_success(runtime_->set_vga_gain(profile_.vga_gain_db), "vga_gain");
    require_success(runtime_->start_rx(&HackrfRxSession::callback_bridge, this), "start_rx");
    running_ = true;
}

void HackrfRxSession::cleanup_unstarted() noexcept {
    if (device_open_) {
        if (runtime_->close_device() == 0) {
            device_open_ = false;
        }
    }
    if (library_initialized_ && !device_open_) {
        if (runtime_->exit_library() == 0) {
            library_initialized_ = false;
        }
    }
}

int HackrfRxSession::callback_bridge(
    const std::span<const std::uint8_t> interleaved_ci8,
    const std::int64_t host_timestamp_ns,
    void* const context
) noexcept {
    if (context == nullptr) {
        return 1;
    }
    auto& session = *static_cast<HackrfRxSession*>(context);
    const auto result = session.ingress_->admit_callback(
        interleaved_ci8,
        host_timestamp_ns
    );
    return result == HackrfRxAdmissionResult::Stopped ? 1 : 0;
}

bool HackrfRxSession::try_pop(HackrfRxLease& output) noexcept {
    return ingress_ != nullptr && ingress_->try_pop(output);
}

std::shared_ptr<HackrfRxIngress> HackrfRxSession::shared_ingress() const noexcept {
    return ingress_;
}

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
std::shared_ptr<HackrfRxIngress> HackrfRxSession::test_shared_ingress() const noexcept {
    return shared_ingress();
}
#endif

HackrfRxIngressMetrics HackrfRxSession::metrics() const noexcept {
    return ingress_ != nullptr ? ingress_->metrics() : HackrfRxIngressMetrics{};
}

const HackrfRxProfile& HackrfRxSession::profile() const noexcept { return profile_; }

std::uint32_t HackrfRxSession::transfer_bytes() const noexcept { return transfer_bytes_; }

bool HackrfRxSession::running() const noexcept { return running_; }

HackrfRxQuiesceResult HackrfRxSession::quiesce(
    const std::chrono::milliseconds callback_timeout
) noexcept {
    if (!running_ || ingress_ == nullptr) {
        return quiesce_result_;
    }
    ingress_->request_stop();
    if (!stop_rx_succeeded_) {
        quiesce_result_.stop_rx_called = true;
        quiesce_result_.stop_rx_status = runtime_->stop_rx();
        stop_rx_succeeded_ = quiesce_result_.stop_rx_status == 0;
        if (!stop_rx_succeeded_) {
            return quiesce_result_;
        }
    }
    quiesce_result_.callbacks_quiescent =
        ingress_->wait_callbacks_quiescent(callback_timeout);
    return quiesce_result_;
}

HackrfRxFinalizeResult HackrfRxSession::finalize_after_drain() noexcept {
    if (finalize_result_.complete()) {
        return finalize_result_;
    }
    if (!quiesce_result_.complete() || ingress_ == nullptr) {
        return finalize_result_;
    }
    const auto ingress_metrics = ingress_->metrics();
    finalize_result_.drain_verified = ingress_metrics.ready_depth == 0U &&
                                      ingress_metrics.callbacks_active == 0U &&
                                      ingress_metrics.slots_in_use == 0U;
    if (!finalize_result_.drain_verified) {
        return finalize_result_;
    }
    if (!close_succeeded_) {
        finalize_result_.close_called = true;
        finalize_result_.close_status = runtime_->close_device();
        close_succeeded_ = finalize_result_.close_status == 0;
        if (!close_succeeded_) {
            return finalize_result_;
        }
        device_open_ = false;
    }
    if (!exit_succeeded_) {
        finalize_result_.exit_called = true;
        finalize_result_.exit_status = runtime_->exit_library();
        exit_succeeded_ = finalize_result_.exit_status == 0;
        if (!exit_succeeded_) {
            return finalize_result_;
        }
        library_initialized_ = false;
    }
    running_ = false;
    return finalize_result_;
}

HackrfRxShutdownResult HackrfRxSession::stop(
    const std::chrono::milliseconds callback_timeout
) noexcept {
    if (!running_) {
        return shutdown_result_;
    }
    const auto quiesced = quiesce(callback_timeout);
    shutdown_result_.stop_rx_status = quiesced.stop_rx_status;
    shutdown_result_.callbacks_quiescent = quiesced.callbacks_quiescent;
    if (!quiesced.complete()) {
        return shutdown_result_;
    }
    shutdown_abandoned_blocks_ += ingress_->abandon_ready();
    shutdown_result_.abandoned_blocks = shutdown_abandoned_blocks_;
    const auto finalized = finalize_after_drain();
    shutdown_result_.close_called = finalized.close_called;
    shutdown_result_.close_status = finalized.close_status;
    shutdown_result_.exit_called = finalized.exit_called;
    shutdown_result_.exit_status = finalized.exit_status;
    return shutdown_result_;
}

}  // namespace sdr_hackrf

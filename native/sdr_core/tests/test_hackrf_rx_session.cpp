#include "sdr_hackrf/hackrf_rx_session.hpp"

#include "sdr_core/errors.hpp"

#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

struct FakeState {
    std::vector<std::string> calls;
    std::vector<std::uint8_t> transfer = std::vector<std::uint8_t>(32U, 7U);
    int start_status{};
};

class FakeRuntime final : public sdr_hackrf::HackrfRxRuntimePort {
public:
    explicit FakeRuntime(std::shared_ptr<FakeState> state) : state_(std::move(state)) {}

    int initialize_library() noexcept override {
        state_->calls.emplace_back("init");
        return 0;
    }
    int open_exactly_one_hackrf_one() noexcept override {
        state_->calls.emplace_back("open_one");
        return 0;
    }
    std::uint32_t transfer_buffer_size() const noexcept override {
        state_->calls.emplace_back("buffer_size");
        return static_cast<std::uint32_t>(state_->transfer.size());
    }
    int set_sample_rate(const double value) noexcept override {
        state_->calls.emplace_back(value == 10'000'000.0 ? "sample_rate" : "bad_rate");
        return 0;
    }
    int set_baseband_filter(const std::uint32_t value) noexcept override {
        state_->calls.emplace_back(value == 8'000'000U ? "filter" : "bad_filter");
        return 0;
    }
    int set_center_frequency(const std::uint64_t value) noexcept override {
        state_->calls.emplace_back(value == 100'000'000U ? "frequency" : "bad_frequency");
        return 0;
    }
    int set_rf_amplifier(const bool enabled) noexcept override {
        state_->calls.emplace_back(enabled ? "amp_on" : "amp_off");
        return 0;
    }
    int set_bias_tee(const bool enabled) noexcept override {
        state_->calls.emplace_back(enabled ? "bias_on" : "bias_off");
        return 0;
    }
    int set_lna_gain(const std::uint32_t value) noexcept override {
        state_->calls.emplace_back(value == 16U ? "lna" : "bad_lna");
        return 0;
    }
    int set_vga_gain(const std::uint32_t value) noexcept override {
        state_->calls.emplace_back(value == 20U ? "vga" : "bad_vga");
        return 0;
    }
    int start_rx(
        const sdr_hackrf::HackrfRxBytesCallback callback,
        void* const context
    ) noexcept override {
        state_->calls.emplace_back("start_rx");
        if (state_->start_status != 0) {
            return state_->start_status;
        }
        static_cast<void>(callback(state_->transfer, 1'000, context));
        static_cast<void>(callback(state_->transfer, 2'000, context));
        return 0;
    }
    int stop_rx() noexcept override {
        state_->calls.emplace_back("stop_rx");
        return 0;
    }
    int close_device() noexcept override {
        state_->calls.emplace_back("close");
        return 0;
    }
    int exit_library() noexcept override {
        state_->calls.emplace_back("exit");
        return 0;
    }

private:
    std::shared_ptr<FakeState> state_;
};

void test_profile_is_applied_in_exact_order_and_data_reaches_ingress() {
    auto state = std::make_shared<FakeState>();
    auto session = sdr_hackrf::HackrfRxSession::start(
        std::make_unique<FakeRuntime>(state)
    );
    expect(session->running(), "session did not enter running state");
    expect(session->transfer_bytes() == 32U, "runtime transfer size was not retained");

    sdr_hackrf::HackrfRxLease first;
    sdr_hackrf::HackrfRxLease second;
    expect(session->try_pop(first), "first callback did not reach ingress");
    expect(session->try_pop(second), "second callback did not reach ingress");
    expect(first.metadata().source_sequence == 0U, "first sequence mismatch");
    expect(second.metadata().source_sequence == 1U, "second sequence mismatch");
    expect(second.metadata().first_sample_index == 16U, "sample cursor mismatch");
    first.reset();
    second.reset();

    const auto result = session->stop(std::chrono::milliseconds(100));
    expect(result.complete(), "clean fake shutdown was not complete");
    expect(!session->running(), "session stayed running after stop");
    expect(
        state->calls == std::vector<std::string>({
            "init", "open_one", "buffer_size", "sample_rate", "filter",
            "frequency", "amp_off", "bias_off", "lna", "vga", "start_rx",
            "stop_rx", "close", "exit"
        }),
        "R11-H configure/shutdown order changed"
    );
}

void test_invalid_profile_fails_before_runtime_access() {
    auto state = std::make_shared<FakeState>();
    auto profile = sdr_hackrf::HackrfRxProfile{};
    profile.bias_tee_enabled = true;
    bool rejected = false;
    try {
        const auto session = sdr_hackrf::HackrfRxSession::start(
            std::make_unique<FakeRuntime>(state),
            profile
        );
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "unsafe R11-H profile was accepted");
    expect(state->calls.empty(), "runtime was touched before profile validation");
}

void test_failed_start_closes_device_and_exits_library() {
    auto state = std::make_shared<FakeState>();
    state->start_status = -71;
    bool rejected = false;
    try {
        const auto session = sdr_hackrf::HackrfRxSession::start(
            std::make_unique<FakeRuntime>(state)
        );
    } catch (const sdr_core::DeviceError&) {
        rejected = true;
    }
    expect(rejected, "failed vendor start was not propagated");
    expect(state->calls.size() >= 3U, "failed start call history is incomplete");
    expect(
        state->calls[state->calls.size() - 2U] == "close" &&
            state->calls.back() == "exit",
        "failed start did not close and exit"
    );
}

}  // namespace

int main() {
    try {
        test_profile_is_applied_in_exact_order_and_data_reaches_ingress();
        test_invalid_profile_fails_before_runtime_access();
        test_failed_start_closes_device_and_exits_library();
        std::cout << "R11-H HackRF RX session OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

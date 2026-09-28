#include "sdr_hackrf/hackrf_sweep_session.hpp"

#include "sdr_core/errors.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

using sdr_hackrf::hackrf_sweep_block_bytes;
using sdr_hackrf::HackrfRxBytesCallback;
using sdr_hackrf::HackrfSweepProfile;
using sdr_hackrf::HackrfSweepQueuedBlock;
using sdr_hackrf::HackrfSweepRuntimePort;
using sdr_hackrf::HackrfSweepSession;

std::vector<std::uint8_t> make_transfer(
    const std::vector<std::uint64_t>& frequencies
) {
    std::vector<std::uint8_t> result(frequencies.size() * hackrf_sweep_block_bytes);
    for (std::size_t index = 0U; index < frequencies.size(); ++index) {
        const auto base = index * hackrf_sweep_block_bytes;
        result[base] = 0x7fU;
        result[base + 1U] = 0x7fU;
        for (std::size_t byte = 0U; byte < 8U; ++byte) {
            result[base + 2U + byte] = static_cast<std::uint8_t>(
                frequencies[index] >> (8U * byte)
            );
        }
        result[base + 10U] = static_cast<std::uint8_t>(index + 17U);
    }
    return result;
}

HackrfSweepProfile profile() {
    HackrfSweepProfile result;
    result.sequence.ranges = {{100U, 120U}};
    result.sequence.step_width_hz = 20'000'000U;
    result.sequence.offset_hz = 7'500'000U;
    result.sequence.style = sdr_hackrf::HackrfSweepStyle::Interleaved;
    result.sequence.max_transfer_blocks = 16U;
    result.ready_capacity = 2U;
    result.config_generation = 31U;
    return result;
}

struct FakeState {
    std::vector<std::string> calls;
    std::uint16_t usb_api{0x0104U};
    std::uint32_t transfer_bytes{262'144U};
    int start_status{};
    int first_stop_status{};
    int first_close_status{};
    std::uint32_t stop_calls{};
    std::uint32_t close_calls{};
    HackrfRxBytesCallback callback{};
    void* callback_context{};
    std::vector<std::uint8_t> inline_transfer;

    int emit(const std::vector<std::uint8_t>& bytes,
             const std::int64_t timestamp_ns = 10'000) const noexcept {
        return callback(bytes, timestamp_ns, callback_context);
    }
};

class FakeRuntime final : public HackrfSweepRuntimePort {
public:
    explicit FakeRuntime(std::shared_ptr<FakeState> state)
        : state_(std::move(state)) {}

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
        return state_->transfer_bytes;
    }
    int read_usb_api_version(std::uint16_t& version) noexcept override {
        state_->calls.emplace_back("usb_api");
        version = state_->usb_api;
        return 0;
    }
    int set_sample_rate(const double value) noexcept override {
        state_->calls.emplace_back(value == 20'000'000.0 ? "rate" : "bad_rate");
        return 0;
    }
    int set_baseband_filter(const std::uint32_t value) noexcept override {
        state_->calls.emplace_back(value == 15'000'000U ? "filter" : "bad_filter");
        return 0;
    }
    int set_center_frequency(std::uint64_t) noexcept override {
        state_->calls.emplace_back("FORBIDDEN_FIXED_FREQUENCY");
        return -1;
    }
    int set_rf_amplifier(const bool enabled) noexcept override {
        state_->calls.emplace_back(enabled ? "FORBIDDEN_AMP_ON" : "amp_off");
        return enabled ? -1 : 0;
    }
    int set_bias_tee(const bool enabled) noexcept override {
        state_->calls.emplace_back(enabled ? "FORBIDDEN_BIAS_ON" : "bias_off");
        return enabled ? -1 : 0;
    }
    int set_lna_gain(const std::uint32_t value) noexcept override {
        state_->calls.emplace_back(value == 16U ? "lna" : "bad_lna");
        return 0;
    }
    int set_vga_gain(const std::uint32_t value) noexcept override {
        state_->calls.emplace_back(value == 20U ? "vga" : "bad_vga");
        return 0;
    }
    int initialize_sweep(const sdr_hackrf::HackrfSweepSequencePlan& plan) noexcept override {
        state_->calls.emplace_back(
            plan.ranges.size() == 1U && plan.step_width_hz == 20'000'000U &&
                    plan.offset_hz == 7'500'000U
                ? "init_sweep_one_block_per_tune"
                : "bad_sweep_plan"
        );
        return 0;
    }
    int start_rx(HackrfRxBytesCallback, void*) noexcept override {
        state_->calls.emplace_back("FORBIDDEN_FIXED_START");
        return -1;
    }
    int start_rx_sweep(const HackrfRxBytesCallback callback,
                       void* const context) noexcept override {
        state_->calls.emplace_back("start_sweep");
        if (state_->start_status != 0) {
            return state_->start_status;
        }
        state_->callback = callback;
        state_->callback_context = context;
        if (!state_->inline_transfer.empty()) {
            static_cast<void>(state_->emit(state_->inline_transfer));
        }
        return 0;
    }
    int stop_rx() noexcept override {
        state_->calls.emplace_back("stop");
        ++state_->stop_calls;
        // The pinned SDK cannot retry Stop after its first cancellation has
        // consumed transfers_setup, even when the later RF-off command fails.
        return state_->first_stop_status;
    }
    int close_device() noexcept override {
        state_->calls.emplace_back("close");
        ++state_->close_calls;
        return state_->close_calls == 1U ? state_->first_close_status : 0;
    }
    int exit_library() noexcept override {
        state_->calls.emplace_back("exit");
        return 0;
    }

private:
    std::shared_ptr<FakeState> state_;
};

void test_exact_sdk_order_and_inline_callback() {
    auto state = std::make_shared<FakeState>();
    state->inline_transfer = make_transfer({100'000'000U, 105'000'000U});
    auto session = HackrfSweepSession::start(
        std::make_unique<FakeRuntime>(state), profile()
    );
    expect(session->running(), "sweep source did not start");
    expect(session->observed_usb_api_version() == 0x0104U,
           "observed USB API was not retained");
    expect(session->transfer_bytes() == 262'144U,
           "transfer geometry was not retained");
    HackrfSweepQueuedBlock first;
    HackrfSweepQueuedBlock second;
    expect(session->try_pop(first) && session->try_pop(second),
           "inline SDK callback did not reach the bounded queue");
    expect(first.reported_tuned_frequency_hz == 100'000'000U &&
               first.new_scan && first.scan_epoch == 1U &&
               first.interleaved_ci8[0] == 17U &&
               first.config_generation == 31U,
           "first block lost sequence/sample/generation provenance");
    expect(second.reported_tuned_frequency_hz == 105'000'000U &&
               second.interleave_phase == 1U && !second.gap_before &&
               second.interleaved_ci8[0] == 18U,
           "second block was not independently attributed");
    expect(!session->try_pop(first), "empty queue yielded a stale block");
    expect(session->stop(std::chrono::milliseconds(100)).complete(),
           "clean Sweep Stop did not close the owner");
    expect(!session->running(), "Stop left Sweep running");
    expect(state->calls == std::vector<std::string>({
        "init", "open_one", "usb_api", "buffer_size", "rate", "filter",
        "amp_off", "bias_off", "lna", "vga", "init_sweep_one_block_per_tune",
        "start_sweep", "stop", "close", "exit"
    }), "Sweep used a fixed-centre or incorrect SDK lifecycle");
    const auto metrics = session->metrics();
    expect(metrics.sequence && metrics.sequence->blocks_admitted == 2U &&
               metrics.blocks_queued == 2U && metrics.blocks_popped == 2U &&
               metrics.ready_depth == 0U,
           "Stop metrics did not retain admission/queue accounting");
}

void test_unsafe_requests_refuse_before_sdk() {
    auto state = std::make_shared<FakeState>();
    auto invalid = profile();
    invalid.sequence.ranges.clear();
    bool refused = false;
    try {
        static_cast<void>(HackrfSweepSession::start(
            std::make_unique<FakeRuntime>(state), invalid));
    } catch (const sdr_core::ConfigurationError&) {
        refused = true;
    }
    expect(refused && state->calls.empty(), "empty Sweep plan touched the SDK");

    invalid = profile();
    invalid.ready_capacity = 257U;
    refused = false;
    try {
        static_cast<void>(HackrfSweepSession::start(
            std::make_unique<FakeRuntime>(state), invalid));
    } catch (const sdr_core::ConfigurationError&) {
        refused = true;
    }
    expect(refused && state->calls.empty(), "oversized Sweep ring touched the SDK");
}

void test_old_firmware_or_wrong_transfer_refuses_before_rf_settings() {
    for (const bool old_api : {true, false}) {
        auto state = std::make_shared<FakeState>();
        if (old_api) {
            state->usb_api = 0x0103U;
        } else {
            state->transfer_bytes = 262'145U;
        }
        bool refused = false;
        try {
            static_cast<void>(HackrfSweepSession::start(
                std::make_unique<FakeRuntime>(state), profile()));
        } catch (const sdr_core::DeviceError&) {
            refused = true;
        }
        expect(refused, "old API or malformed transfer was admitted");
        expect(state->calls.back() == "exit" &&
                   state->calls[state->calls.size() - 2U] == "close",
               "pre-RF refusal did not close its exact device");
        for (const auto& call : state->calls) {
            expect(call != "rate" && call != "init_sweep_one_block_per_tune" &&
                       call != "start_sweep",
                   "pre-RF refusal configured/started the device");
        }
    }
}

void test_queue_loss_marks_next_scan_without_fake_iq_continuity() {
    auto state = std::make_shared<FakeState>();
    auto requested = profile();
    requested.ready_capacity = 1U;
    auto session = HackrfSweepSession::start(
        std::make_unique<FakeRuntime>(state), requested
    );
    const auto pair = make_transfer({100'000'000U, 105'000'000U});
    expect(state->emit(pair) == 0, "unexpected callback cancellation");
    HackrfSweepQueuedBlock first;
    expect(session->try_pop(first), "first block was not queued");
    expect(first.reported_tuned_frequency_hz == 100'000'000U,
           "ring overwrote the queued first block");
    expect(state->emit(make_transfer({100'000'000U})) == 0,
           "next Sweep origin was not processed");
    HackrfSweepQueuedBlock after_gap;
    expect(session->try_pop(after_gap), "next origin was not queued");
    expect(after_gap.new_scan && after_gap.scan_epoch == 2U &&
               after_gap.gap_before && after_gap.continuity_epoch == 2U,
           "queue-full loss was hidden at the next admitted scan");
    expect(session->stop(std::chrono::milliseconds(100)).complete(),
           "queue pressure prevented clean Stop");
    const auto metrics = session->metrics();
    expect(metrics.ready_full_drops == 1U &&
               metrics.sequence && metrics.sequence->downstream_drops == 1U &&
               metrics.sequence->blocks_admitted == 3U &&
               metrics.blocks_queued == 2U,
           "queue-full and header counts were conflated");
}

void test_invalid_callback_marks_gap_and_stop_abandons_ready() {
    auto state = std::make_shared<FakeState>();
    auto session = HackrfSweepSession::start(
        std::make_unique<FakeRuntime>(state), profile()
    );
    expect(state->emit(make_transfer({100'000'000U}), 0) == 0,
           "invalid host timestamp unexpectedly stopped RX");
    expect(state->emit(std::vector<std::uint8_t>{1U, 2U, 3U}) == 0,
           "malformed callback unexpectedly stopped RX");
    expect(state->emit(make_transfer({100'000'000U})) == 0,
           "origin after malformed callback was refused");
    const auto before_stop = session->metrics();
    expect(before_stop.ready_depth == 1U && !before_stop.sequence,
           "Live metrics invented a non-thread-safe sequence snapshot");
    const auto stopped = session->stop(std::chrono::milliseconds(100));
    expect(stopped.complete() && stopped.abandoned_blocks == 1U,
           "Stop did not abandon its unconsumed copied block");
    HackrfSweepQueuedBlock stale;
    expect(!session->try_pop(stale),
           "Stop retained a stale ready block");
    const auto metrics = session->metrics();
    expect(metrics.sequence && metrics.sequence->transfers_rejected == 1U &&
               metrics.sequence->downstream_drops == 1U &&
               metrics.sequence->blocks_admitted == 1U &&
               metrics.sequence->continuity_epoch == 1U &&
               metrics.blocks_abandoned == 1U &&
               metrics.invalid_timestamp_callbacks == 1U,
           "malformed callback/gap/abandon accounting changed");
}

void test_live_depth_snapshot_never_wraps_during_consumer_pop() {
    auto state = std::make_shared<FakeState>();
    auto requested = profile();
    requested.ready_capacity = 2U;
    auto session = HackrfSweepSession::start(
        std::make_unique<FakeRuntime>(state), requested
    );
    const auto pair = make_transfer({100'000'000U, 105'000'000U});
    std::atomic<bool> done{};
    std::thread producer([&] {
        for (std::size_t index = 0U; index < 1'000U; ++index) {
            static_cast<void>(state->emit(pair));
        }
        done.store(true, std::memory_order_release);
    });
    HackrfSweepQueuedBlock output;
    bool bad_depth = false;
    while (!done.load(std::memory_order_acquire)) {
        static_cast<void>(session->try_pop(output));
        bad_depth = bad_depth ||
                    session->metrics().ready_depth > requested.ready_capacity;
    }
    producer.join();
    expect(!bad_depth && session->metrics().ready_depth <= requested.ready_capacity,
           "final live queue depth exceeded capacity");
    expect(session->stop(std::chrono::milliseconds(100)).complete(),
           "metrics concurrency test did not release the source");
}

void test_stop_failure_and_callback_timeout_keep_owner_for_retry() {
    {
        auto state = std::make_shared<FakeState>();
        state->first_stop_status = -71;
        auto session = HackrfSweepSession::start(
            std::make_unique<FakeRuntime>(state), profile()
        );
        const auto released = session->stop(std::chrono::milliseconds(100));
        expect(released.complete() && !released.clean() &&
                   released.stop_rx_status == -71,
               "failed vendor Stop was masked or prevented release");
        expect(!session->running() && state->calls.back() == "exit" &&
                   state->stop_calls == 1U && state->close_calls == 1U,
               "failed Stop retried the consumed SDK transfer state");
    }
    {
        auto state = std::make_shared<FakeState>();
        state->first_stop_status = -71;
        state->first_close_status = -1001;
        auto session = HackrfSweepSession::start(
            std::make_unique<FakeRuntime>(state), profile()
        );
        const auto first = session->stop(std::chrono::milliseconds(100));
        expect(!first.complete() && first.first_close_error == -1001 &&
                   session->running(),
               "consumed-close error was treated as a clean release");
        const auto recovered = session->stop(std::chrono::milliseconds(100));
        expect(recovered.complete() && !recovered.clean() &&
                   recovered.stop_rx_status == -71 &&
                   recovered.first_close_error == -1001 &&
                   state->stop_calls == 1U && state->close_calls == 2U,
               "consumed close retry lost its first error or repeated Stop");
    }
    {
        auto state = std::make_shared<FakeState>();
        auto session = HackrfSweepSession::start(
            std::make_unique<FakeRuntime>(state), profile()
        );
        std::atomic<bool> entered{};
        std::atomic<bool> release{};
        std::thread callback([&] {
            session->test_hold_active_callback(entered, release);
        });
        while (!entered.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
        const auto timed_out = session->stop(std::chrono::milliseconds(1));
        expect(!timed_out.complete() && !timed_out.callbacks_quiescent &&
                   state->calls.back() == "stop" && session->running(),
               "callback timeout closed or destroyed an active owner");
        release.store(true, std::memory_order_release);
        callback.join();
        expect(session->stop(std::chrono::milliseconds(100)).complete(),
               "callback quiescence retry did not close");
        expect(state->stop_calls == 1U,
               "successful vendor Stop was repeated after timeout");
    }
}

void test_failed_start_closes_and_exits() {
    auto state = std::make_shared<FakeState>();
    state->start_status = -77;
    bool refused = false;
    try {
        static_cast<void>(HackrfSweepSession::start(
            std::make_unique<FakeRuntime>(state), profile()));
    } catch (const sdr_core::DeviceError&) {
        refused = true;
    }
    expect(refused && state->calls.back() == "exit" &&
               state->calls[state->calls.size() - 2U] == "close",
           "failed Sweep Start leaked the exact handle");
}

}  // namespace

int main() {
    try {
        test_exact_sdk_order_and_inline_callback();
        test_unsafe_requests_refuse_before_sdk();
        test_old_firmware_or_wrong_transfer_refuses_before_rf_settings();
        test_queue_loss_marks_next_scan_without_fake_iq_continuity();
        test_invalid_callback_marks_gap_and_stop_abandons_ready();
        test_live_depth_snapshot_never_wraps_during_consumer_pop();
        test_stop_failure_and_callback_timeout_keep_owner_for_retry();
        test_failed_start_closes_and_exits();
        std::cout << "HackRF Sweep source owner OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

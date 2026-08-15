#include "sdr_hackrf/hackrf_runtime_dsp_session.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

struct FakeState {
    std::vector<std::string> calls;
    std::vector<std::uint8_t> transfer = std::vector<std::uint8_t>(512U, 7U);
    sdr_hackrf::HackrfRxBytesCallback callback{};
    void* callback_context{};
    std::uint32_t start_blocks{};
    std::vector<int> stop_statuses{0};
    std::vector<int> close_statuses{0};
    std::vector<int> exit_statuses{0};
    std::size_t stop_index{};
    std::size_t close_index{};
    std::size_t exit_index{};
    std::function<bool()> close_guard;
    bool close_observed_guard{};

    [[nodiscard]] int status_at(const std::vector<int>& values, std::size_t& index) {
        const auto selected = index < values.size() ? index : values.size() - 1U;
        ++index;
        return values[selected];
    }

    int emit(const std::int64_t timestamp_ns) {
        if (callback == nullptr) {
            throw std::runtime_error("fake callback was not registered");
        }
        return callback(transfer, timestamp_ns, callback_context);
    }
};

class FakeRuntime final : public sdr_hackrf::HackrfRxRuntimePort {
public:
    explicit FakeRuntime(std::shared_ptr<FakeState> state) : state_(std::move(state)) {}

    int initialize_library() noexcept override {
        state_->calls.emplace_back("init");
        return 0;
    }
    int open_exactly_one_hackrf_one() noexcept override {
        state_->calls.emplace_back("open");
        return 0;
    }
    std::uint32_t transfer_buffer_size() const noexcept override {
        state_->calls.emplace_back("buffer_size");
        return static_cast<std::uint32_t>(state_->transfer.size());
    }
    int set_sample_rate(double) noexcept override {
        state_->calls.emplace_back("sample_rate");
        return 0;
    }
    int set_baseband_filter(std::uint32_t) noexcept override {
        state_->calls.emplace_back("filter");
        return 0;
    }
    int set_center_frequency(std::uint64_t) noexcept override {
        state_->calls.emplace_back("frequency");
        return 0;
    }
    int set_rf_amplifier(bool) noexcept override {
        state_->calls.emplace_back("amp_off");
        return 0;
    }
    int set_bias_tee(bool) noexcept override {
        state_->calls.emplace_back("bias_off");
        return 0;
    }
    int set_lna_gain(std::uint32_t) noexcept override {
        state_->calls.emplace_back("lna");
        return 0;
    }
    int set_vga_gain(std::uint32_t) noexcept override {
        state_->calls.emplace_back("vga");
        return 0;
    }
    int start_rx(
        const sdr_hackrf::HackrfRxBytesCallback callback,
        void* const context
    ) noexcept override {
        state_->calls.emplace_back("start_rx");
        state_->callback = callback;
        state_->callback_context = context;
        for (std::uint32_t index = 0U; index < state_->start_blocks; ++index) {
            if (callback(state_->transfer, 1'000 + index, context) != 0) {
                return -91;
            }
        }
        return 0;
    }
    int stop_rx() noexcept override {
        state_->calls.emplace_back("stop_rx");
        return state_->status_at(state_->stop_statuses, state_->stop_index);
    }
    int close_device() noexcept override {
        state_->calls.emplace_back("close");
        state_->close_observed_guard = !state_->close_guard || state_->close_guard();
        return state_->status_at(state_->close_statuses, state_->close_index);
    }
    int exit_library() noexcept override {
        state_->calls.emplace_back("exit");
        return state_->status_at(state_->exit_statuses, state_->exit_index);
    }

private:
    std::shared_ptr<FakeState> state_;
};

sdr_core::SourceDescriptor source() {
    sdr_core::SourceDescriptor value;
    value.source_type = sdr_core::SourceType::LiveIq;
    value.source_id = "hackrf-fake-runtime-dsp-0";
    value.display_name = "HackRF fake runtime DSP";
    value.backend_id = "native.libhackrf.rx.v1";
    return value;
}

sdr_hackrf::HackrfRuntimeDspSessionConfig config() {
    sdr_hackrf::HackrfRuntimeDspSessionConfig value;
    value.rx.slot_count = 8U;
    value.rx.ready_capacity = 8U;
    value.rx.config_generation = 17U;
    value.processing.dsp.dsp.fft_size = 256U;
    value.processing.dsp.dsp.hop_size = 256U;
    value.processing.dsp.dsp.window = sdr_core::WindowType::Rectangular;
    value.processing.dsp.dsp.detector = sdr_core::DetectorType::Sample;
    value.processing.dsp.dsp.unit = sdr_core::SpectrumUnit::DbfsBin;
    value.processing.dsp.dsp.precision_mode = sdr_core::PrecisionMode::ReferenceF64;
    value.processing.dsp.source = source();
    value.processing.dsp.dsp_output_capacity = 16U;
    value.processing.dsp.presentation_capacity = 8U;
    return value;
}

template <typename Predicate>
bool wait_until(Predicate&& predicate, const std::chrono::milliseconds timeout = 1s) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) {
            return true;
        }
        std::this_thread::sleep_for(1ms);
    }
    return predicate();
}

void test_composed_frames_and_exact_shutdown_order() {
    auto state = std::make_shared<FakeState>();
    state->start_blocks = 4U;
    auto session = sdr_hackrf::HackrfRuntimeDspSession::start(
        std::make_unique<FakeRuntime>(state),
        config()
    );
    expect(wait_until([&] {
        return session->metrics().processing.worker_blocks_processed == 4U;
    }), "composed worker did not drain all startup blocks");
    expect(session->poll_spectrum_frames().size() == 4U, "composed frames are missing");

    state->close_guard = [&] {
        const auto metrics = session->metrics();
        return metrics.processing.worker_joined && metrics.source.ready_depth == 0U &&
               metrics.source.callbacks_active == 0U &&
               metrics.source.slots_in_use == 0U;
    };
    const auto stopped = session->stop(1s);
    expect(stopped.complete(), "composed shutdown did not complete");
    expect(state->close_observed_guard, "close ran before worker drain/join");
    const auto metrics = session->metrics();
    expect(!metrics.lifecycle_open, "composed source lifecycle stayed open");
    expect(metrics.processing.worker_joined, "composed worker was not joined");
    expect(metrics.source.abandoned_blocks == 0U, "composition abandoned ready data");
    expect(
        state->calls == std::vector<std::string>({
            "init", "open", "buffer_size", "sample_rate", "filter",
            "frequency", "amp_off", "bias_off", "lna", "vga", "start_rx",
            "stop_rx", "close", "exit"
        }),
        "runtime/DSP shutdown order changed"
    );
}

void test_composed_stop_drains_pending_ready_blocks_before_close() {
    auto state = std::make_shared<FakeState>();
    state->start_blocks = 8U;
    auto session = sdr_hackrf::HackrfRuntimeDspSession::start(
        std::make_unique<FakeRuntime>(state),
        config()
    );
    state->close_guard = [&] {
        const auto metrics = session->metrics();
        return metrics.processing.worker_joined &&
               metrics.processing.worker_blocks_processed == 8U &&
               metrics.source.ready_depth == 0U &&
               metrics.source.slots_in_use == 0U;
    };

    const auto stopped = session->stop(1s);
    expect(stopped.complete(), "composed pending-block shutdown did not complete");
    expect(state->close_observed_guard, "close preceded pending-block drain");
    const auto metrics = session->metrics();
    expect(metrics.processing.worker_blocks_processed == 8U,
           "composed stop did not process every admitted block");
    expect(metrics.source.abandoned_blocks == 0U,
           "composed stop abandoned an admitted block");
    expect(session->poll_spectrum_frames().size() == 8U,
           "drained pending blocks did not produce canonical frames");
}

void test_quiescence_timeout_forbids_finalize_then_explicit_retry_completes() {
    auto state = std::make_shared<FakeState>();
    auto source_session = sdr_hackrf::HackrfRxSession::start(
        std::make_unique<FakeRuntime>(state)
    );
    auto shared_ingress = source_session->test_shared_ingress();
    std::atomic<bool> entered{};
    std::atomic<bool> release{};
    std::thread held([&] {
        shared_ingress->test_hold_active_callback(entered, release);
    });
    expect(wait_until([&] { return entered.load(std::memory_order_acquire); }),
           "held callback did not enter");

    const auto incomplete = source_session->quiesce(2ms);
    expect(!incomplete.complete() && !incomplete.callbacks_quiescent,
           "held callback did not time out quiescence");
    expect(!source_session->finalize_after_drain().complete(),
           "finalize advanced before callback quiescence");
    expect(state->calls.back() == "stop_rx", "close/exit ran after timeout");

    release.store(true, std::memory_order_release);
    held.join();
    const auto complete = source_session->quiesce(1s);
    expect(complete.complete(), "explicit quiescence retry did not complete");
    expect(source_session->finalize_after_drain().complete(),
           "finalize failed after callback release");
    expect(std::count(state->calls.begin(), state->calls.end(), "stop_rx") == 1,
           "successful stop_rx was repeated during callback retry");
}

void test_borrowed_slot_forbids_close_until_lease_release() {
    auto state = std::make_shared<FakeState>();
    state->start_blocks = 1U;
    auto source_session = sdr_hackrf::HackrfRxSession::start(
        std::make_unique<FakeRuntime>(state)
    );
    auto shared_ingress = source_session->test_shared_ingress();
    sdr_hackrf::HackrfRxLease held;
    expect(shared_ingress->try_pop(held), "startup lease was not available");
    expect(source_session->quiesce(1s).complete(), "source did not quiesce");
    expect(!source_session->finalize_after_drain().complete(),
           "close advanced while a consumer still owned a slot");
    expect(std::find(state->calls.begin(), state->calls.end(), "close") ==
               state->calls.end(),
           "device close ran with a borrowed I/Q slot");
    held.reset();
    expect(source_session->finalize_after_drain().complete(),
           "finalize did not resume after lease release");
}

void test_stop_failure_and_close_failure_retry_only_incomplete_phase() {
    auto state = std::make_shared<FakeState>();
    state->stop_statuses = {-5, 0};
    state->close_statuses = {-7, 0};
    auto session = sdr_hackrf::HackrfRuntimeDspSession::start(
        std::make_unique<FakeRuntime>(state),
        config()
    );

    const auto stop_failed = session->stop(5ms);
    expect(!stop_failed.complete(), "failed stop_rx was hidden");
    expect(std::find(state->calls.begin(), state->calls.end(), "close") ==
               state->calls.end(),
           "close ran after stop_rx failure");

    const auto close_failed = session->stop(1s);
    expect(!close_failed.complete(), "failed close was hidden");
    expect(std::find(state->calls.begin(), state->calls.end(), "exit") ==
               state->calls.end(),
           "exit ran after close failure");
    expect(session->metrics().processing.worker_joined,
           "worker was not joined before failed close");

    const auto complete = session->stop(1s);
    expect(complete.complete(), "explicit close retry did not complete");
    expect(std::count(state->calls.begin(), state->calls.end(), "stop_rx") == 2,
           "stop_rx retry count mismatch");
    expect(std::count(state->calls.begin(), state->calls.end(), "close") == 2,
           "close retry count mismatch");
    expect(std::count(state->calls.begin(), state->calls.end(), "exit") == 1,
           "exit should run exactly once after close succeeds");
}

void test_exit_failure_retries_exit_without_reclosing() {
    auto state = std::make_shared<FakeState>();
    state->exit_statuses = {-11, 0};
    auto session = sdr_hackrf::HackrfRuntimeDspSession::start(
        std::make_unique<FakeRuntime>(state),
        config()
    );
    expect(!session->stop(1s).complete(), "failed exit was hidden");
    expect(session->stop(1s).complete(), "explicit exit retry did not complete");
    expect(std::count(state->calls.begin(), state->calls.end(), "close") == 1,
           "successful close was repeated during exit retry");
    expect(std::count(state->calls.begin(), state->calls.end(), "exit") == 2,
           "exit retry count mismatch");
}

void test_invalid_processing_config_fails_before_runtime_access() {
    auto state = std::make_shared<FakeState>();
    auto invalid = config();
    invalid.processing.dsp.source.uri = "usb:forbidden";
    bool rejected = false;
    try {
        auto session = sdr_hackrf::HackrfRuntimeDspSession::start(
            std::make_unique<FakeRuntime>(state),
            invalid
        );
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "invalid processing config was accepted");
    expect(state->calls.empty(), "runtime was touched before DSP validation");
}

}  // namespace

int main() {
    try {
        test_composed_frames_and_exact_shutdown_order();
        test_composed_stop_drains_pending_ready_blocks_before_close();
        test_quiescence_timeout_forbids_finalize_then_explicit_retry_completes();
        test_borrowed_slot_forbids_close_until_lease_release();
        test_stop_failure_and_close_failure_retry_only_incomplete_phase();
        test_exit_failure_retries_exit_without_reclosing();
        test_invalid_processing_config_fails_before_runtime_access();
        std::cout << "R11-K HackRF runtime/DSP composition OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

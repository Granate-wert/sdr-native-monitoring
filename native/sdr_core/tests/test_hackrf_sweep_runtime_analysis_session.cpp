#include "sdr_hackrf/hackrf_sweep_runtime_analysis_session.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

using sdr_hackrf::HackrfRxBytesCallback;
using sdr_hackrf::HackrfSweepRuntimeAnalysisConfig;
using sdr_hackrf::HackrfSweepRuntimeAnalysisSession;
using sdr_hackrf::HackrfSweepRuntimePort;
using sdr_hackrf::HackrfSweepPublication;

std::optional<sdr_core::SweepProgressFrame> progress_from(
    HackrfSweepPublication publication
) {
    if (auto* frame = std::get_if<sdr_core::SweepProgressFrame>(&publication)) {
        return std::move(*frame);
    }
    return std::nullopt;
}

std::optional<sdr_core::SweepLineFrame> line_from(
    HackrfSweepPublication publication
) {
    if (auto* frame = std::get_if<sdr_core::SweepLineFrame>(&publication)) {
        return std::move(*frame);
    }
    return std::nullopt;
}

struct FakeState {
    std::vector<std::string> calls;
    HackrfRxBytesCallback callback{};
    void* context{};
    int first_close_status{};
    std::uint32_t close_calls{};
    std::uint32_t stop_calls{};

    void emit(const std::vector<std::uint8_t>& bytes,
              const std::int64_t timestamp_ns = 10'000) const {
        expect(callback != nullptr && context != nullptr,
               "Sweep callback was not installed");
        expect(callback(bytes, timestamp_ns, context) == 0,
               "fake Sweep transfer was rejected");
    }
};

class FakeRuntime final : public HackrfSweepRuntimePort {
public:
    explicit FakeRuntime(std::shared_ptr<FakeState> state) : state_(std::move(state)) {}

    int initialize_library() noexcept override { state_->calls.emplace_back("init"); return 0; }
    int open_exactly_one_hackrf_one() noexcept override { state_->calls.emplace_back("open"); return 0; }
    std::uint32_t transfer_buffer_size() const noexcept override { return 262'144U; }
    int read_usb_api_version(std::uint16_t& version) noexcept override {
        version = 0x0109U;
        return 0;
    }
    int set_sample_rate(double) noexcept override { return 0; }
    int set_baseband_filter(std::uint32_t) noexcept override { return 0; }
    int set_center_frequency(std::uint64_t) noexcept override {
        state_->calls.emplace_back("FORBIDDEN_FIXED_FREQ");
        return -1;
    }
    int set_rf_amplifier(const bool enabled) noexcept override {
        state_->calls.emplace_back(enabled ? "FORBIDDEN_AMP" : "amp_off");
        return enabled ? -1 : 0;
    }
    int set_bias_tee(const bool enabled) noexcept override {
        state_->calls.emplace_back(enabled ? "FORBIDDEN_BIAS" : "bias_off");
        return enabled ? -1 : 0;
    }
    int set_lna_gain(std::uint32_t) noexcept override { return 0; }
    int set_vga_gain(std::uint32_t) noexcept override { return 0; }
    int initialize_sweep(const sdr_hackrf::HackrfSweepSequencePlan&) noexcept override {
        state_->calls.emplace_back("init_sweep");
        return 0;
    }
    int start_rx(HackrfRxBytesCallback, void*) noexcept override {
        state_->calls.emplace_back("FORBIDDEN_FIXED_RX");
        return -1;
    }
    int start_rx_sweep(const HackrfRxBytesCallback callback,
                       void* const context) noexcept override {
        state_->calls.emplace_back("start_sweep");
        state_->callback = callback;
        state_->context = context;
        return 0;
    }
    int stop_rx() noexcept override { ++state_->stop_calls; return 0; }
    int close_device() noexcept override {
        ++state_->close_calls;
        return state_->close_calls == 1U ? state_->first_close_status : 0;
    }
    int exit_library() noexcept override { state_->calls.emplace_back("exit"); return 0; }

private:
    std::shared_ptr<FakeState> state_;
};

HackrfSweepRuntimeAnalysisConfig config() {
    HackrfSweepRuntimeAnalysisConfig result;
    auto& analysis = result.analysis;
    analysis.acquisition.sequence.ranges = {{2400U, 2480U}};
    analysis.acquisition.sequence.step_width_hz = 20'000'000U;
    analysis.acquisition.sequence.offset_hz = 7'500'000U;
    analysis.acquisition.sequence.style = sdr_hackrf::HackrfSweepStyle::Interleaved;
    analysis.acquisition.sequence.max_transfer_blocks = 16U;
    analysis.acquisition.ready_capacity = 16U;
    analysis.acquisition.config_generation = 9U;
    analysis.source.source_type = sdr_core::SourceType::LiveIq;
    analysis.source.source_id = "hackrf:runtime-test";
    analysis.source.display_name = "HackRF runtime test";
    analysis.source.backend_id = "native.libhackrf.sweep.v1";
    analysis.acquisition_epoch = 7U;
    analysis.fft_size = 4096U;
    result.preview_rate_hz = 50U;
    return result;
}

std::vector<std::uint8_t> transfer(const std::uint32_t first,
                                   const std::uint32_t count) {
    std::vector<std::uint8_t> result(
        static_cast<std::size_t>(count) * sdr_hackrf::hackrf_sweep_block_bytes
    );
    for (std::uint32_t offset = 0U; offset < count; ++offset) {
        const auto index = first + offset;
        const auto base = static_cast<std::size_t>(offset) *
                          sdr_hackrf::hackrf_sweep_block_bytes;
        const std::uint64_t frequency = 2'400'000'000ULL +
            (index / 2U) * 20'000'000ULL + (index % 2U) * 5'000'000ULL;
        result[base] = result[base + 1U] = 0x7fU;
        for (std::size_t byte = 0U; byte < 8U; ++byte) {
            result[base + 2U + byte] = static_cast<std::uint8_t>(
                frequency >> (8U * byte)
            );
        }
        for (std::size_t sample = 0U;
             sample < sdr_hackrf::hackrf_sweep_ci8_bytes / 2U;
             ++sample) {
            const int value = sample % 4U == 0U ? 80 : sample % 4U == 2U ? -80 : 0;
            result[base + 10U + 2U * sample] = static_cast<std::uint8_t>(value);
            result[base + 11U + 2U * sample] = 0U;
        }
    }
    return result;
}

template <typename Predicate>
bool eventually(Predicate&& condition) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (std::chrono::steady_clock::now() < deadline) {
        if (condition()) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return condition();
}

void test_progress_complete_and_owned_stop() {
    auto state = std::make_shared<FakeState>();
    auto requested = config();
    requested.analysis.layer_event_capacity = 128U;
    auto control = HackrfSweepRuntimeAnalysisSession::start(
        std::make_unique<FakeRuntime>(state), requested
    );
    const auto initial = control->drain_sweep_layer_ready_events(0U);
    expect(initial.summary.created == 0U, "Start fabricated Sweep creation evidence");
    state->emit(transfer(0U, 1U));
    expect(eventually([&] {
        return control->metrics().analysis.accepted_blocks == 1U;
    }), "native worker did not consume the first Sweep block");
    std::optional<sdr_core::SweepProgressFrame> progress;
    expect(eventually([&] {
        progress = progress_from(control->poll_next_publication());
        return progress.has_value();
    }), "no progressive native preview was published");
    expect(progress->source.source_id == "hackrf:runtime-test" &&
               progress->epoch == 7U && progress->acquired_segments.size() == 2U &&
               progress->pending_segment_indices.size() == 14U,
           "native preview lost source/epoch/partial segment provenance");
    expect(progress->layer_ready &&
               progress->layer_ready->kind == sdr_core::LayerReadyKind::SweepProgress &&
               progress->layer_ready->producer_instance_id == initial.summary.producer_instance_id,
           "Sweep progress has no original owner creation receipt");

    state->emit(transfer(1U, 7U), 20'000);
    expect(eventually([&] {
        // Analysis completion and host publication have separate locks. The
        // numerical counter is not a publication fence: wait for the actual
        // terminal slot too, without changing the existing 3-second deadline.
        const auto observed = control->metrics();
        return observed.analysis.lines.completed_lines == 1U && observed.terminal_pending;
    }), "native worker did not publish the completed numerical Sweep line");
    const auto line = line_from(control->poll_next_publication());
    expect(line && line->state == sdr_core::SweepLineState::Complete &&
               line->source.source_id == "hackrf:runtime-test" &&
               line->analysis_bins_per_usable_window == 1024U &&
               line->physical_fft_size == 4096U &&
               line->acquired_segments.size() == 16U,
           "terminal native line lost physical geometry or owner provenance");
    expect(line->layer_ready &&
               line->layer_ready->kind == sdr_core::LayerReadyKind::SweepTerminal &&
               line->layer_ready->producer_instance_id == progress->layer_ready->producer_instance_id &&
               line->layer_ready->creation_sequence > progress->layer_ready->creation_sequence,
           "Sweep terminal lost the configured owner journal");
    expect(std::holds_alternative<std::monostate>(control->poll_next_publication()),
           "terminal Sweep line left a stale partial preview");
    const auto stopped = control->stop(std::chrono::seconds(1));
    expect(stopped.clean(), "Sweep source/worker did not Stop and join cleanly");
    const auto metrics = control->metrics();
    expect(!metrics.lifecycle_open && metrics.worker_exited && metrics.worker_joined &&
               !metrics.worker_failed && metrics.worker_blocks_processed == 8U &&
               metrics.analysis.dsp.fft_frames_computed == 8U &&
               metrics.analysis.iq_payload_samples_accepted == 8U * 8187U &&
               metrics.analysis.dsp.samples_processed == 8U * 4096U &&
               metrics.source.callbacks_active == 0U &&
               metrics.source.ready_depth == 0U &&
               state->stop_calls == 1U && state->close_calls == 1U,
           "Stop metrics or same-handle release are incomplete");
    expect(std::none_of(state->calls.begin(), state->calls.end(), [](const auto& value) {
        return value.starts_with("FORBIDDEN");
    }), "Sweep started fixed-centre RX or enabled amp/bias");
    const auto journal = control->drain_sweep_layer_ready_events(0U);
    expect(journal.summary.created >= 2U && journal.summary.events_lost == 0U &&
               journal.summary.created == journal.summary.events_drained &&
               std::find(journal.creations.begin(), journal.creations.end(), *progress->layer_ready) != journal.creations.end() &&
               std::find(journal.creations.begin(), journal.creations.end(), *line->layer_ready) != journal.creations.end(),
           "Stop or publication regenerated/lost original Sweep creations");
    const auto after = control->drain_sweep_layer_ready_events(0U);
    expect(after.creations.empty() && after.summary.created == journal.summary.created,
           "rereading stopped Sweep evidence created or replayed frames");
}

void test_stop_flushes_partial_line_and_clears_preview() {
    auto state = std::make_shared<FakeState>();
    auto control = HackrfSweepRuntimeAnalysisSession::start(
        std::make_unique<FakeRuntime>(state), config()
    );
    state->emit(transfer(0U, 1U));
    expect(eventually([&] { return control->metrics().analysis.accepted_blocks == 1U; }),
           "partial block did not reach analysis");
    const auto stopped = control->stop(std::chrono::seconds(1));
    expect(stopped.complete(), "partial Sweep Stop did not release owner");
    const auto partial_metrics = control->metrics();
    expect(partial_metrics.analysis.iq_payload_samples_accepted == 8187U &&
               partial_metrics.analysis.dsp.samples_processed == 4096U &&
               partial_metrics.analysis.dsp.fft_frames_computed == 1U,
           "partial terminal flush lost the accepted payload/analytical FFT counters");
    const auto gap = line_from(control->poll_next_publication());
    expect(gap && gap->state == sdr_core::SweepLineState::Gap &&
               gap->line_sequence == 1U && !gap->missing_segment_indices.empty() &&
               std::holds_alternative<std::monostate>(control->poll_next_publication()),
           "Stop silently discarded a partial line or revived a stale preview");
}

void test_stop_drains_queued_complete_scan() {
    auto state = std::make_shared<FakeState>();
    auto control = HackrfSweepRuntimeAnalysisSession::start(
        std::make_unique<FakeRuntime>(state), config()
    );
    state->emit(transfer(0U, 8U));
    const auto stopped = control->stop(std::chrono::seconds(1));
    const auto line = line_from(control->poll_next_publication());
    const auto observed = control->metrics();
    expect(stopped.clean() && line &&
               line->state == sdr_core::SweepLineState::Complete &&
               line->acquired_segments.size() == 16U &&
               observed.worker_blocks_processed == 8U &&
               observed.analysis.iq_payload_samples_accepted == 8U * 8187U &&
               observed.analysis.dsp.fft_frames_computed == 8U &&
               observed.source.blocks_abandoned == 0U &&
               observed.source.ready_depth == 0U,
           "immediate Stop dropped copied blocks from a complete Sweep scan");
}

void test_ordered_terminal_then_next_scan_preview() {
    auto state = std::make_shared<FakeState>();
    auto control = HackrfSweepRuntimeAnalysisSession::start(
        std::make_unique<FakeRuntime>(state), config()
    );
    state->emit(transfer(0U, 8U));
    expect(eventually([&] { return control->metrics().terminal_pending; }),
           "first scan did not publish a terminal line");
    state->emit(transfer(0U, 1U), 30'000);
    expect(eventually([&] {
        const auto observed = control->metrics();
        return observed.terminal_pending && observed.progress_pending &&
               observed.analysis.accepted_blocks == 9U;
    }), "second scan did not publish a preview alongside the older terminal");
    const auto first = line_from(control->poll_next_publication());
    const auto second = progress_from(control->poll_next_publication());
    expect(first && second && first->line_sequence == 1U &&
               second->line_sequence == 2U,
           "ordered publication let a newer preview overtake an older terminal");
    expect(control->stop(std::chrono::seconds(1)).complete(),
           "ordered publication test did not release source owner");
}

void test_failed_close_retains_same_owner_for_explicit_retry() {
    auto state = std::make_shared<FakeState>();
    state->first_close_status = -7;
    auto control = HackrfSweepRuntimeAnalysisSession::start(
        std::make_unique<FakeRuntime>(state), config()
    );
    const auto first = control->stop(std::chrono::milliseconds(100));
    expect(!first.complete() && first.source.first_close_error == -7 &&
               state->stop_calls == 1U && state->close_calls == 1U &&
               !control->metrics().worker_joined,
           "failed close falsely released the same Sweep owner");
    const auto retry = control->stop(std::chrono::seconds(1));
    expect(retry.complete() && !retry.clean() &&
               retry.source.first_close_error == -7 &&
               state->stop_calls == 1U && state->close_calls == 2U,
           "explicit same-owner close retry lost the first error or retried SDK Stop");
}

void test_invalid_geometry_refuses_before_sdk() {
    for (const std::uint32_t case_id : {0U, 1U, 2U, 3U}) {
        auto state = std::make_shared<FakeState>();
        auto invalid = config();
        if (case_id == 0U) {
            invalid.preview_rate_hz = 0U;
        } else if (case_id == 1U) {
            invalid.analysis.fft_size = 512U;
        } else if (case_id == 2U) {
            invalid.analysis.layer_event_capacity = 4097U;
        } else {
            invalid.analysis.acquisition.sequence.ranges = {{100U, 3500U}};
            invalid.analysis.layer_event_capacity = 4096U;
        }
        bool refused = false;
        try {
            static_cast<void>(HackrfSweepRuntimeAnalysisSession::start(
                std::make_unique<FakeRuntime>(state), std::move(invalid)
            ));
        } catch (const sdr_core::ConfigurationError&) {
            refused = true;
        }
        expect(refused && state->calls.empty(),
               "invalid native Sweep analysis touched the SDK");
    }
}

void test_sweep_journal_overflow_partial_stop_and_new_identity() {
    auto state = std::make_shared<FakeState>();
    auto requested = config();
    requested.analysis.layer_event_capacity = 1U;
    auto control = HackrfSweepRuntimeAnalysisSession::start(
        std::make_unique<FakeRuntime>(state), requested);
    const auto identity = control->drain_sweep_layer_ready_events(0U).summary.producer_instance_id;
    state->emit(transfer(0U, 1U));
    expect(eventually([&] { return control->metrics().progress_pending; }),
           "overflow setup missing real Sweep preview");
    const auto progress = progress_from(control->poll_next_publication());
    expect(progress && progress->layer_ready, "overflow preview missing creation receipt");
    expect(control->stop(std::chrono::seconds(1)).complete(), "partial journal Stop incomplete");
    const auto terminal = line_from(control->poll_next_publication());
    expect(terminal && terminal->state == sdr_core::SweepLineState::Gap && terminal->layer_ready &&
               terminal->layer_ready->producer_instance_id == identity &&
               terminal->layer_ready->creation_sequence > progress->layer_ready->creation_sequence,
           "partial Stop did not create terminal evidence through the same journal");
    const auto events = control->drain_sweep_layer_ready_events(1U);
    expect(events.creations.size() == 1U && events.creations.front() == *progress->layer_ready &&
               events.summary.created >= 2U && events.summary.events_lost > 0U &&
               events.summary.created == events.summary.events_drained + events.summary.events_lost &&
               events.summary.events_pending == 0U,
           "full Sweep journal blocked production or hid creation loss");
    auto next_state = std::make_shared<FakeState>();
    auto next = HackrfSweepRuntimeAnalysisSession::start(std::make_unique<FakeRuntime>(next_state), requested);
    const auto fresh = next->drain_sweep_layer_ready_events(0U);
    expect(fresh.summary.created == 0U && fresh.summary.producer_instance_id != identity,
           "new Sweep runtime reused the old producer identity");
    expect(next->stop(std::chrono::seconds(1)).complete(), "fresh Sweep runtime did not close");
    auto disabled = HackrfSweepRuntimeAnalysisSession::start(
        std::make_unique<FakeRuntime>(std::make_shared<FakeState>()), config());
    bool refused = false;
    try { static_cast<void>(disabled->drain_sweep_layer_ready_events(0U)); }
    catch (const sdr_core::ConfigurationError&) { refused = true; }
    expect(refused && disabled->stop(std::chrono::seconds(1)).complete(),
           "default-off journal fabricated empty valid evidence");
    // Pure analysis admission establishes the baseline geometry fits the old
    // component budget; capacity4096 above must fail BEFORE the SDK is touched.
    auto near_budget = config().analysis;
    near_budget.acquisition.sequence.ranges = {{100U, 3500U}};
    sdr_hackrf::HackrfSweepAnalysis admitted(near_budget);
    expect(admitted.metrics().accepted_blocks == 0U, "budget preflight fabricated acquisition");
}

}  // namespace

int main() {
    try {
        test_progress_complete_and_owned_stop();
        test_stop_flushes_partial_line_and_clears_preview();
        test_stop_drains_queued_complete_scan();
        test_ordered_terminal_then_next_scan_preview();
        test_failed_close_retains_same_owner_for_explicit_retry();
        test_invalid_geometry_refuses_before_sdk();
        test_sweep_journal_overflow_partial_stop_and_new_identity();
        std::cout << "HackRF Sweep runtime analysis ownership OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

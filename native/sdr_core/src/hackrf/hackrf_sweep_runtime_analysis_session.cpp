#include "sdr_hackrf/hackrf_sweep_runtime_analysis_session.hpp"

#include "sdr_core/errors.hpp"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <exception>
#include <mutex>
#include <thread>
#include <utility>

namespace sdr_hackrf {
namespace {

constexpr std::uint32_t minimum_preview_rate_hz = 1U;
constexpr std::uint32_t maximum_preview_rate_hz = 100U;
constexpr auto empty_poll_wait = std::chrono::microseconds(200);

}  // namespace

struct HackrfSweepRuntimeAnalysisSession::Impl final {
    Impl(std::unique_ptr<HackrfSweepSession> owned_source,
         std::unique_ptr<HackrfSweepAnalysis> owned_analysis,
         const std::uint32_t requested_preview_rate_hz)
        : source(std::move(owned_source)),
          analysis(std::move(owned_analysis)),
          preview_interval(std::chrono::nanoseconds(
              1'000'000'000ULL / requested_preview_rate_hz)) {}

    void publish_terminal(sdr_core::SweepLineFrame line) {
        std::lock_guard lock(publication_mutex);
        if (latest_terminal) {
            ++terminal_superseded;
        }
        if (latest_progress &&
            latest_progress->line_sequence <= line.line_sequence) {
            latest_progress.reset();
            ++progress_cleared_by_terminal;
        }
        latest_terminal = std::move(line);
    }

    void publish_progress(sdr_core::SweepProgressFrame frame) {
        std::lock_guard lock(publication_mutex);
        if (latest_terminal &&
            frame.line_sequence <= latest_terminal->line_sequence) {
            // A delayed preview must never revive a terminal line.
            return;
        }
        if (latest_progress) {
            ++progress_superseded;
        }
        latest_progress = std::move(frame);
    }

    void run() noexcept {
        auto next_preview = std::chrono::steady_clock::now() + preview_interval;
        bool progress_dirty = false;
        try {
            HackrfSweepQueuedBlock block;
            for (;;) {
                if (source->try_pop(block)) {
                    std::vector<sdr_core::SweepLineFrame> emitted;
                    {
                        std::lock_guard lock(analysis_mutex);
                        emitted = analysis->admit(block);
                    }
                    worker_blocks_processed.fetch_add(1U, std::memory_order_relaxed);
                    progress_dirty = true;
                    for (auto& line : emitted) {
                        publish_terminal(std::move(line));
                    }
                } else if (stop_requested.load(std::memory_order_acquire)) {
                    // Callback admission has ended. Drain every copied block
                    // before analysis.finish() classifies an incomplete line.
                    break;
                } else {
                    std::this_thread::sleep_for(empty_poll_wait);
                }
                const auto now = std::chrono::steady_clock::now();
                if (progress_dirty && now >= next_preview) {
                    std::optional<sdr_core::SweepProgressFrame> progress;
                    {
                        std::lock_guard lock(analysis_mutex);
                        progress = analysis->preview();
                    }
                    if (progress) {
                        publish_progress(std::move(*progress));
                    }
                    progress_dirty = false;
                    next_preview = now + preview_interval;
                }
            }
            std::vector<sdr_core::SweepLineFrame> final;
            {
                std::lock_guard lock(analysis_mutex);
                final = analysis->finish();
            }
            for (auto& line : final) {
                publish_terminal(std::move(line));
            }
        } catch (...) {
            // The source is still owned, and only an explicit Stop may close
            // the same SDK handle. Do not publish a possibly inconsistent
            // terminal line after a processing failure.
            worker_failed.store(true, std::memory_order_release);
        }
        worker_exited.store(true, std::memory_order_release);
        worker_exit_cv.notify_all();
    }

    std::unique_ptr<HackrfSweepSession> source;
    std::unique_ptr<HackrfSweepAnalysis> analysis;
    std::chrono::nanoseconds preview_interval;
    std::thread worker;
    mutable std::mutex lifecycle_mutex;
    mutable std::mutex analysis_mutex;
    mutable std::mutex publication_mutex;
    std::mutex worker_exit_mutex;
    std::condition_variable worker_exit_cv;
    std::atomic<bool> stop_requested{};
    std::atomic<bool> worker_exited{};
    std::atomic<bool> worker_joined{};
    std::atomic<bool> worker_failed{};
    std::atomic<std::uint64_t> worker_blocks_processed{};
    std::optional<sdr_core::SweepProgressFrame> latest_progress;
    std::optional<sdr_core::SweepLineFrame> latest_terminal;
    std::uint64_t progress_superseded{};
    std::uint64_t progress_cleared_by_terminal{};
    std::uint64_t terminal_superseded{};
    HackrfSweepRuntimeAnalysisStopResult stop_result{};
};

std::unique_ptr<HackrfSweepRuntimeAnalysisSession>
HackrfSweepRuntimeAnalysisSession::start(
    std::unique_ptr<HackrfSweepRuntimePort> runtime,
    HackrfSweepRuntimeAnalysisConfig config
) {
    if (config.preview_rate_hz < minimum_preview_rate_hz ||
        config.preview_rate_hz > maximum_preview_rate_hz) {
        throw sdr_core::ConfigurationError("HackRF Sweep preview cadence is invalid");
    }
    // All numerical geometry/allocation admission precedes SDK init/RX.
    auto analysis = std::make_unique<HackrfSweepAnalysis>(config.analysis);
    auto source = HackrfSweepSession::start(
        std::move(runtime), config.analysis.acquisition
    );
    auto impl = std::make_unique<Impl>(
        std::move(source), std::move(analysis), config.preview_rate_hz
    );
    auto session = std::unique_ptr<HackrfSweepRuntimeAnalysisSession>(
        new HackrfSweepRuntimeAnalysisSession(std::move(impl))
    );
    session->impl_->worker = std::thread([owned = session->impl_.get()] {
        owned->run();
    });
    return session;
}

HackrfSweepRuntimeAnalysisSession::HackrfSweepRuntimeAnalysisSession(
    std::unique_ptr<Impl> impl
) noexcept : impl_(std::move(impl)) {}

HackrfSweepRuntimeAnalysisSession::~HackrfSweepRuntimeAnalysisSession() {
    if (impl_ &&
        (impl_->source->running() || impl_->worker.joinable())) {
        if (!stop(std::chrono::seconds(5)).complete()) {
            // A still-live SDK callback/worker may reference Impl.
            std::terminate();
        }
    }
}

HackrfSweepPublication
HackrfSweepRuntimeAnalysisSession::poll_next_publication() {
    std::lock_guard lock(impl_->publication_mutex);
    if (impl_->latest_terminal &&
        (!impl_->latest_progress ||
         impl_->latest_terminal->line_sequence <=
             impl_->latest_progress->line_sequence)) {
        auto frame = std::move(*impl_->latest_terminal);
        impl_->latest_terminal.reset();
        return frame;
    }
    if (impl_->latest_progress) {
        auto frame = std::move(*impl_->latest_progress);
        impl_->latest_progress.reset();
        return frame;
    }
    return std::monostate{};
}

sdr_core::LayerReadyDrain HackrfSweepRuntimeAnalysisSession::drain_sweep_layer_ready_events(std::size_t max_items) {
    // The analysis object/journal lifetime is immutable for this session.
    // Only its journal mutex is taken, never the worker's analysis/SDK locks.
    return impl_->analysis->drain_sweep_layer_ready_events(max_items);
}

HackrfSweepRuntimeAnalysisMetrics
HackrfSweepRuntimeAnalysisSession::metrics() const {
    HackrfSweepRuntimeAnalysisMetrics result;
    result.lifecycle_open = impl_->source->running();
    result.worker_exited = impl_->worker_exited.load(std::memory_order_acquire);
    result.worker_joined = impl_->worker_joined.load(std::memory_order_acquire);
    result.worker_failed = impl_->worker_failed.load(std::memory_order_acquire);
    result.worker_blocks_processed =
        impl_->worker_blocks_processed.load(std::memory_order_relaxed);
    result.source = impl_->source->metrics();
    {
        std::lock_guard lock(impl_->analysis_mutex);
        result.analysis = impl_->analysis->metrics();
    }
    {
        std::lock_guard lock(impl_->publication_mutex);
        result.progress_superseded = impl_->progress_superseded;
        result.progress_cleared_by_terminal = impl_->progress_cleared_by_terminal;
        result.terminal_superseded = impl_->terminal_superseded;
        result.progress_pending = impl_->latest_progress.has_value();
        result.terminal_pending = impl_->latest_terminal.has_value();
    }
    return result;
}

HackrfSweepRuntimeAnalysisStopResult
HackrfSweepRuntimeAnalysisSession::stop(
    const std::chrono::milliseconds callback_timeout
) noexcept {
    std::lock_guard lifecycle_lock(impl_->lifecycle_mutex);
    if (impl_->stop_result.complete()) {
        return impl_->stop_result;
    }
    impl_->stop_result.source = impl_->source->stop(callback_timeout, true);
    if (!impl_->stop_result.source.complete()) {
        return impl_->stop_result;
    }
    impl_->stop_requested.store(true, std::memory_order_release);
    if (impl_->worker.joinable()) {
        std::unique_lock wait_lock(impl_->worker_exit_mutex);
        if (!impl_->worker_exit_cv.wait_for(wait_lock, callback_timeout, [this] {
                return impl_->worker_exited.load(std::memory_order_acquire);
            })) {
            return impl_->stop_result;
        }
        wait_lock.unlock();
        impl_->worker.join();
    }
    impl_->worker_joined.store(true, std::memory_order_release);
    impl_->stop_result.worker_joined = true;
    impl_->stop_result.worker_failed =
        impl_->worker_failed.load(std::memory_order_acquire);
    // An abnormal worker exit can leave copied blocks behind. Account them
    // only after the sole consumer has joined, then refresh the source result.
    static_cast<void>(impl_->source->discard_ready_after_stop());
    impl_->stop_result.source = impl_->source->stop(callback_timeout, true);
    return impl_->stop_result;
}

}  // namespace sdr_hackrf

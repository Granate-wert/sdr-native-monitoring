#include "sdr_core/engine.hpp"

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <exception>
#include <string>
#include <utility>

namespace sdr_core {

namespace {

[[noreturn]] void invalid(const std::string& message) {
    throw ConfigurationError(message);
}

void positive(const double value, const char* name) {
    if (!std::isfinite(value) || value <= 0.0) {
        invalid(std::string(name) + " must be finite and positive");
    }
}

void positive(const std::uint32_t value, const char* name) {
    if (value == 0U) {
        invalid(std::string(name) + " must be positive");
    }
}

// Deterministic transport payload: xorshift-derived ComplexFloat32Le values
// in [-1, 1). This is not a signal generator; it gives every block
// reproducible, seed-derived and always finite contents so the P05 DSP
// stage accepts the stream (non-finite input is rejected by contract).
void fill_block(std::vector<std::uint8_t>& block, std::uint64_t state) noexcept {
    if (state == 0U) {
        state = 0x9E3779B97F4A7C15ULL;
    }
    constexpr double to_unit = 1.0 / 9007199254740992.0;  // 2^53
    for (std::size_t offset = 0; offset + 4U <= block.size(); offset += 4U) {
        state ^= state << 13U;
        state ^= state >> 7U;
        state ^= state << 17U;
        const double unit = static_cast<double>(state >> 11U) * to_unit;
        const float value = static_cast<float>(2.0 * unit - 1.0);
        std::memcpy(block.data() + offset, &value, sizeof(value));
    }
}

}  // namespace

std::int64_t host_monotonic_ns() noexcept {
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}

void validate(const EngineConfig& value) {
    if (value.schema_version != contract_schema_version) {
        invalid("unsupported contract schema_version");
    }
    static_cast<void>(to_wire(value.acquisition_overflow));
    static_cast<void>(to_wire(value.dsp_overflow));
    static_cast<void>(to_wire(value.recorder_overflow));
    static_cast<void>(to_wire(value.scenario));
    positive(value.acquisition_queue_capacity, "acquisition_queue_capacity");
    positive(value.dsp_queue_capacity, "dsp_queue_capacity");
    positive(value.snapshot_queue_capacity, "snapshot_queue_capacity");
    positive(value.event_queue_capacity, "event_queue_capacity");
    positive(value.recorder_queue_capacity, "recorder_queue_capacity");
    positive(value.pool_block_count, "pool_block_count");
    positive(value.block_size_samples, "block_size_samples");
    if (value.block_size_samples > UINT32_MAX / 8U) {
        invalid("block_size_samples overflows the ComplexFloat32Le block size");
    }
    positive(value.snapshot_interval_blocks, "snapshot_interval_blocks");
    positive(value.sample_rate_hz, "sample_rate_hz");
    positive(value.center_frequency_hz, "center_frequency_hz");
    positive(value.spectrum_queue_capacity, "spectrum_queue_capacity");
    validate(value.dsp);
    if (value.recorder_stop_on_overflow && value.recorder_overflow != OverflowPolicy::Block) {
        invalid("recorder_stop_on_overflow requires the Block overflow policy");
    }
}

SyntheticEngine::SyntheticEngine() = default;

SyntheticEngine::~SyntheticEngine() noexcept {
    initiate_shutdown();
    for (auto* thread :
         {&producer_thread_, &mover_thread_, &consumer_thread_, &recorder_thread_}) {
        if (thread->joinable()) {
            thread->join();
        }
    }
}

void SyntheticEngine::configure(const EngineConfig& config) {
    std::lock_guard lock(lifecycle_mutex_);
    const auto current = state_.load(std::memory_order_acquire);
    if (current != EngineState::Created && current != EngineState::Configured &&
        current != EngineState::Stopped) {
        throw ConfigurationError("configure requires CREATED, CONFIGURED or STOPPED state");
    }
    validate(config);

    // Build the new generation first; commit only when every component
    // exists, so a bad_alloc cannot leave a half-updated engine.
    auto acquisition = std::make_unique<BoundedQueue<IqBlock>>(
        config.acquisition_queue_capacity,
        config.acquisition_overflow
    );
    auto dsp = std::make_unique<BoundedQueue<IqBlock>>(
        config.dsp_queue_capacity,
        config.dsp_overflow
    );
    auto recorder = std::make_unique<BoundedQueue<IqBlock>>(
        config.recorder_queue_capacity,
        config.recorder_overflow
    );
    auto snapshot = std::make_unique<BoundedQueue<EngineMetrics>>(
        config.snapshot_queue_capacity,
        OverflowPolicy::LatestWins
    );
    auto spectrum = std::make_unique<BoundedQueue<SpectrumFrame>>(
        config.spectrum_queue_capacity,
        OverflowPolicy::LatestWins
    );
    auto events = std::make_unique<BoundedQueue<DiagnosticEvent>>(
        config.event_queue_capacity,
        OverflowPolicy::DropNewest
    );
    auto pool = std::make_unique<BufferPool>(
        config.pool_block_count,
        config.block_size_samples * 8U  // ComplexFloat32Le
    );
    auto stop = make_stop_token();

    // Commit. Pending events/snapshots of the previous generation are
    // discarded here by design: a generation change starts a new diagnostic
    // stream (ADR lifecycle/generation semantics).
    config_ = config;
    configured_ = true;
    ++config_generation_;
    stop_ = std::move(stop);
    counters_.reset();
    events_lost_.store(0U, std::memory_order_relaxed);
    event_sequence_.store(0U, std::memory_order_relaxed);
    events_lost_reported_ = 0U;

    acquisition_queue_ = std::move(acquisition);
    dsp_queue_ = std::move(dsp);
    recorder_queue_ = std::move(recorder);
    snapshot_queue_ = std::move(snapshot);
    spectrum_queue_ = std::move(spectrum);
    event_queue_ = std::move(events);
    pool_ = std::move(pool);

    state_.store(EngineState::Configured, std::memory_order_release);
    emit_event(EventSeverity::Info, "engine_configured", "engine configuration applied");
}

void SyntheticEngine::start() {
    std::lock_guard lock(lifecycle_mutex_);
    if (state_.load(std::memory_order_acquire) != EngineState::Configured) {
        throw ConfigurationError("start requires CONFIGURED state");
    }
    // Publish RUNNING before spawning: worker auto-stop and failure paths are
    // live immediately and must not be overwritten by a later store.
    state_.store(EngineState::Running, std::memory_order_release);
    try {
        consumer_thread_ = std::thread([this] { consumer_run(); });
        mover_thread_ = std::thread([this] { mover_run(); });
        if (config_.recorder_enabled) {
            recorder_thread_ = std::thread([this] { recorder_run(); });
        }
        producer_thread_ = std::thread([this] { producer_run(); });
    } catch (...) {
        initiate_shutdown();
        for (auto* thread :
             {&producer_thread_, &mover_thread_, &consumer_thread_, &recorder_thread_}) {
            if (thread->joinable()) {
                thread->join();
            }
        }
        state_.store(EngineState::Error, std::memory_order_release);
        throw;
    }
    emit_event(EventSeverity::Info, "engine_started", "engine transport started");
}

void SyntheticEngine::request_stop() {
    std::lock_guard lock(lifecycle_mutex_);
    // CAS: a concurrent worker failure must not be overwritten with STOPPING.
    auto expected = EngineState::Running;
    if (state_.compare_exchange_strong(
            expected,
            EngineState::Stopping,
            std::memory_order_acq_rel
        )) {
        initiate_shutdown();
        return;
    }
    if (expected == EngineState::Stopping || expected == EngineState::Error) {
        return;  // stop already in progress or terminal failure; idempotent
    }
    throw ConfigurationError("request_stop requires RUNNING state");
}

void SyntheticEngine::join() {
    // The mutex is held across the join loop: workers never take
    // lifecycle_mutex_, so this cannot deadlock, and concurrent join() calls
    // are serialized instead of racing on the same std::thread objects.
    std::lock_guard lock(lifecycle_mutex_);
    const auto current = state_.load(std::memory_order_acquire);
    if (current == EngineState::Stopped) {
        return;  // already joined
    }
    if (current != EngineState::Stopping && current != EngineState::Error) {
        throw ConfigurationError("join requires request_stop() first");
    }
    for (auto* thread :
         {&producer_thread_, &mover_thread_, &consumer_thread_, &recorder_thread_}) {
        if (thread->joinable()) {
            thread->join();
        }
    }
    account_abandoned();
    state_.store(EngineState::Stopped, std::memory_order_release);
    emit_event(EventSeverity::Info, "engine_stopped", "engine transport stopped");
}

void SyntheticEngine::stop() {
    request_stop();
    join();
}

EngineState SyntheticEngine::state() const noexcept {
    return state_.load(std::memory_order_acquire);
}

std::uint64_t SyntheticEngine::config_generation() const noexcept {
    std::lock_guard lock(lifecycle_mutex_);
    return config_generation_;
}

EngineConfig SyntheticEngine::config() const {
    std::lock_guard lock(lifecycle_mutex_);
    return config_;
}

EngineMetrics SyntheticEngine::metrics() const {
    std::lock_guard lock(lifecycle_mutex_);
    return assemble_metrics();
}

std::vector<DiagnosticEvent> SyntheticEngine::poll_events(const std::size_t max_items) {
    std::lock_guard lock(lifecycle_mutex_);
    std::vector<DiagnosticEvent> result;
    if (!event_queue_) {
        return result;
    }
    DiagnosticEvent event;
    while (max_items == 0U || result.size() < max_items) {
        if (!event_queue_->try_pop(event)) {
            break;
        }
        result.push_back(std::move(event));
    }
    const auto lost = events_lost_.load(std::memory_order_relaxed);
    if (lost > events_lost_reported_ && (max_items == 0U || result.size() < max_items)) {
        events_lost_reported_ = lost;
        DiagnosticEvent overflow;
        overflow.severity = EventSeverity::Warning;
        overflow.code = "events_lost";
        overflow.message =
            "diagnostic events lost to event queue overflow: " + std::to_string(lost);
        overflow.timestamp_ns = host_monotonic_ns();
        overflow.sequence = event_sequence_.fetch_add(1U, std::memory_order_relaxed) + 1U;
        result.push_back(std::move(overflow));
    }
    return result;
}

std::vector<EngineMetrics> SyntheticEngine::poll_snapshots(const std::size_t max_items) {
    std::lock_guard lock(lifecycle_mutex_);
    std::vector<EngineMetrics> result;
    if (!snapshot_queue_) {
        return result;
    }
    EngineMetrics snapshot;
    while (max_items == 0U || result.size() < max_items) {
        if (!snapshot_queue_->try_pop(snapshot)) {
            break;
        }
        result.push_back(snapshot);
    }
    return result;
}

std::vector<SpectrumFrame> SyntheticEngine::poll_spectrum_frames(const std::size_t max_items) {
    std::lock_guard lock(lifecycle_mutex_);
    std::vector<SpectrumFrame> result;
    if (!spectrum_queue_) {
        return result;
    }
    SpectrumFrame frame;
    while (max_items == 0U || result.size() < max_items) {
        if (!spectrum_queue_->try_pop(frame)) {
            break;
        }
        result.push_back(std::move(frame));
    }
    return result;
}

QueueStats SyntheticEngine::queue_stats(const QueueId id) const {
    std::lock_guard lock(lifecycle_mutex_);
    switch (id) {
    case QueueId::Acquisition:
        return acquisition_queue_ ? acquisition_queue_->stats() : QueueStats{};
    case QueueId::Dsp:
        return dsp_queue_ ? dsp_queue_->stats() : QueueStats{};
    case QueueId::Recorder:
        return recorder_queue_ ? recorder_queue_->stats() : QueueStats{};
    case QueueId::Snapshot:
        return snapshot_queue_ ? snapshot_queue_->stats() : QueueStats{};
    case QueueId::Spectrum:
        return spectrum_queue_ ? spectrum_queue_->stats() : QueueStats{};
    case QueueId::Event:
        return event_queue_ ? event_queue_->stats() : QueueStats{};
    }
    return QueueStats{};
}

PoolStats SyntheticEngine::pool_stats() const {
    std::lock_guard lock(lifecycle_mutex_);
    return pool_ ? pool_->stats() : PoolStats{};
}

void SyntheticEngine::initiate_shutdown() noexcept {
    try {
        stop_->request_stop();
        if (acquisition_queue_) {
            acquisition_queue_->request_stop();
        }
        if (dsp_queue_) {
            dsp_queue_->request_stop();
        }
        if (recorder_queue_) {
            recorder_queue_->request_stop();
        }
        if (snapshot_queue_) {
            snapshot_queue_->request_stop();
        }
        // Spectrum publication is non-blocking. Keep this output boundary
        // open until consumer_run() flushes its final partial FFT batch.
        // configure() replaces the queue only after every worker is joined.
        if (pool_) {
            pool_->request_stop();
        }
        auto expected = EngineState::Running;
        state_.compare_exchange_strong(expected, EngineState::Stopping, std::memory_order_acq_rel);
    } catch (...) {
        // Shutdown must never throw, not even from the destructor.
    }
}

void SyntheticEngine::mark_error() noexcept {
    // Only RUNNING or STOPPING can transition to ERROR; a terminal state
    // already published (or a completed stop) is never overwritten.
    auto expected = state_.load(std::memory_order_acquire);
    while (expected == EngineState::Running || expected == EngineState::Stopping) {
        if (state_.compare_exchange_weak(
                expected,
                EngineState::Error,
                std::memory_order_acq_rel
            )) {
            return;
        }
    }
}

void SyntheticEngine::emit_event(
    const EventSeverity severity,
    std::string code,
    std::string message
) noexcept {
    try {
        if (!event_queue_) {
            return;
        }
        DiagnosticEvent event;
        event.severity = severity;
        event.code = std::move(code);
        event.message = std::move(message);
        event.timestamp_ns = host_monotonic_ns();
        event.sequence = event_sequence_.fetch_add(1U, std::memory_order_relaxed) + 1U;
        if (event_queue_->try_push(std::move(event)) != PushResult::Pushed) {
            events_lost_.fetch_add(1U, std::memory_order_relaxed);
        }
    } catch (...) {
        // Event reporting is best-effort and must never break a worker.
    }
}

void SyntheticEngine::producer_run() noexcept {
    try {
        SyntheticSourceConfig source_config;
        source_config.scenario = config_.scenario;
        source_config.seed = config_.seed;
        source_config.sample_count = config_.block_size_samples;
        source_config.sample_rate_hz = config_.sample_rate_hz;
        source_config.center_frequency_hz = config_.center_frequency_hz;
        const SyntheticSourceSkeleton source(source_config);
        const auto generation = config_generation_;
        const auto pacing = config_.blocks_per_second == 0U
                                ? std::chrono::nanoseconds(0)
                                : std::chrono::nanoseconds(1'000'000'000ULL / config_.blocks_per_second);

        std::uint64_t sequence = 0U;
        std::uint64_t overflow_drops = 0U;
        while (!stop_->stop_requested()) {
            if (config_.max_blocks != 0U && sequence >= config_.max_blocks) {
                break;
            }
            if (pacing.count() > 0 && sequence != 0U && stop_->wait_for(pacing)) {
                break;
            }
            auto block = pool_->acquire();
            if (!block) {
                break;  // pool stopped
            }
            fill_block(*block, source.block_seed(sequence));

            IqBlock iq;
            iq.source_sequence = sequence;
            iq.first_sample_index = sequence * config_.block_size_samples;
            iq.timestamp_ns = host_monotonic_ns();
            iq.center_frequency_hz = config_.center_frequency_hz;
            iq.sample_rate_hz = config_.sample_rate_hz;
            iq.sample_format = SampleFormat::ComplexFloat32Le;
            iq.sample_count = config_.block_size_samples;
            iq.flags = QualityFlag::None;
            iq.samples = std::move(block);
            iq.config_generation = generation;

            counters_.iq_blocks_received.fetch_add(1U, std::memory_order_relaxed);
            counters_.iq_samples_received.fetch_add(
                config_.block_size_samples,
                std::memory_order_relaxed
            );

            const auto result = acquisition_queue_->push(std::move(iq));
            if (result == PushResult::Stopped) {
                // In-flight block at shutdown: never entered a queue, but it
                // was counted as received, so the loss is accounted exactly.
                counters_.iq_blocks_dropped.fetch_add(1U, std::memory_order_relaxed);
                counters_.iq_samples_dropped.fetch_add(
                    config_.block_size_samples,
                    std::memory_order_relaxed
                );
                break;
            }
            if (result == PushResult::Dropped || result == PushResult::Evicted) {
                counters_.iq_blocks_dropped.fetch_add(1U, std::memory_order_relaxed);
                counters_.iq_samples_dropped.fetch_add(
                    config_.block_size_samples,
                    std::memory_order_relaxed
                );
                // Overflow events are throttled: first drop and every 65536th.
                // This keeps the bounded event queue free for lifecycle and
                // critical records even under permanent saturation.
                ++overflow_drops;
                if (overflow_drops == 1U || overflow_drops % 65536U == 0U) {
                    emit_event(
                        EventSeverity::Warning,
                        "acquisition_overflow",
                        "acquisition queue overflow: I/Q blocks are being dropped"
                    );
                }
            }
            ++sequence;
        }
        // Natural completion (max_blocks): drain the transport before
        // shutdown so the produced tail is fully processed instead of being
        // abandoned in-flight. A block in-flight inside the mover at this
        // point is still possible; it is accounted as dropped, never hidden.
        // User-requested stops skip the drain.
        if (!stop_->stop_requested() && config_.max_blocks != 0U &&
            sequence >= config_.max_blocks) {
            while (acquisition_queue_->depth() != 0U || dsp_queue_->depth() != 0U ||
                   (config_.recorder_enabled && recorder_queue_->depth() != 0U)) {
                if (stop_->wait_for(std::chrono::milliseconds(1))) {
                    break;
                }
            }
        }
        emit_event(EventSeverity::Info, "producer_completed", "synthetic producer finished");
        initiate_shutdown();
    } catch (const std::exception& error) {
        emit_event(EventSeverity::Critical, "producer_failure", error.what());
        mark_error();
        initiate_shutdown();
    } catch (...) {
        emit_event(EventSeverity::Critical, "producer_failure", "unknown producer failure");
        mark_error();
        initiate_shutdown();
    }
}

void SyntheticEngine::mover_run() noexcept {
    try {
        std::uint64_t recorder_drops = 0U;
        std::uint64_t dsp_drops = 0U;
        while (true) {
            IqBlock block;
            if (acquisition_queue_->pop(block) == PopResult::Stopped) {
                break;
            }
            if (config_.recorder_enabled) {
                // BLOCK without stop-on-overflow means actual recorder
                // backpressure. A non-blocking probe here used to reject
                // thousands of blocks without incrementing recorder.dropped.
                const auto recorded =
                    config_.recorder_overflow == OverflowPolicy::Block &&
                            !config_.recorder_stop_on_overflow
                        ? recorder_queue_->push(block)
                        : recorder_queue_->try_push(block);
                if (recorded == PushResult::Stopped) {
                    // In-flight main-path block at shutdown.
                    counters_.iq_blocks_dropped.fetch_add(1U, std::memory_order_relaxed);
                    counters_.iq_samples_dropped.fetch_add(
                        config_.block_size_samples,
                        std::memory_order_relaxed
                    );
                    break;
                }
                if (recorded == PushResult::Full || recorded == PushResult::Dropped ||
                    recorded == PushResult::Evicted) {
                    if (recorded == PushResult::Full && config_.recorder_stop_on_overflow) {
                        emit_event(
                            EventSeverity::Error,
                            "recorder_overflow",
                            "recorder queue overflow with stop_on_overflow: stopping engine"
                        );
                        initiate_shutdown();
                        break;
                    }
                    // Recorder-stream loss: the analytical block still flows
                    // to the DSP queue, so this is NOT counted in
                    // iq_blocks_dropped. It stays observable through the
                    // recorder queue stats and throttled events.
                    ++recorder_drops;
                    if (recorder_drops == 1U || recorder_drops % 65536U == 0U) {
                        emit_event(
                            EventSeverity::Warning,
                            "recorder_overflow",
                            "recorder queue overflow: recorder-stream blocks are being dropped"
                        );
                    }
                }
            }
            const auto result = dsp_queue_->push(std::move(block));
            if (result == PushResult::Stopped) {
                // In-flight block at shutdown.
                counters_.iq_blocks_dropped.fetch_add(1U, std::memory_order_relaxed);
                counters_.iq_samples_dropped.fetch_add(
                    config_.block_size_samples,
                    std::memory_order_relaxed
                );
                break;
            }
            if (result == PushResult::Dropped || result == PushResult::Evicted) {
                counters_.iq_blocks_dropped.fetch_add(1U, std::memory_order_relaxed);
                counters_.iq_samples_dropped.fetch_add(
                    config_.block_size_samples,
                    std::memory_order_relaxed
                );
                ++dsp_drops;
                if (dsp_drops == 1U || dsp_drops % 65536U == 0U) {
                    emit_event(
                        EventSeverity::Warning,
                        "dsp_overflow",
                        "DSP queue overflow: I/Q blocks are being dropped"
                    );
                }
            }
        }
    } catch (const std::exception& error) {
        emit_event(EventSeverity::Critical, "mover_failure", error.what());
        mark_error();
        initiate_shutdown();
    } catch (...) {
        emit_event(EventSeverity::Critical, "mover_failure", "unknown mover failure");
        mark_error();
        initiate_shutdown();
    }
}

void SyntheticEngine::consumer_run() noexcept {
    try {
        // P05: the consumer runs the CPU DSP stage (unpack -> overlap ->
        // window -> FFT -> power/PSD -> detector -> SpectrumFrame) on every
        // I/Q block, independent of the GUI snapshot rate.
        CpuDspOptions dsp_options;
        dsp_options.dc_removal =
            config_.dc_removal_block_mean ? DcRemovalMode::BlockMean : DcRemovalMode::Off;
        // One configured FFT batch must fit even when the public latest-wins
        // queue is intentionally tiny. Public queue loss is annotated at the
        // exact publication boundary below.
        dsp_options.output_capacity = std::max(
            config_.spectrum_queue_capacity,
            config_.dsp.batch_size
        );
        {
            SyntheticSourceConfig source_config;
            source_config.scenario = config_.scenario;
            source_config.seed = config_.seed;
            source_config.sample_count = config_.block_size_samples;
            source_config.sample_rate_hz = config_.sample_rate_hz;
            source_config.center_frequency_hz = config_.center_frequency_hz;
            dsp_options.source = SyntheticSourceSkeleton(source_config).descriptor();
        }
        auto backend = make_cpu_dsp_backend(std::move(dsp_options));
        backend->configure(config_.dsp);
        const auto run_started = std::chrono::steady_clock::now();

        std::uint64_t processed = 0U;
        std::uint64_t spectrum_rejected_drops = 0U;
        const auto publish_frames =
            [this, &spectrum_rejected_drops](
                std::vector<SpectrumFrame> frames,
                const DspBackendMetrics& backend_metrics
            ) {
                for (auto& frame : frames) {
                    frame.dropped_samples_before =
                        counters_.iq_samples_dropped.load(std::memory_order_relaxed);
                    frame.dropped_iq_blocks_before =
                        counters_.iq_blocks_dropped.load(std::memory_order_relaxed);
                    const auto backend_drops = backend_metrics.fft_frames_dropped;
                    const auto rejected_before = spectrum_rejected_drops;
                    const auto pushed = spectrum_queue_->try_push_prepared(
                        std::move(frame),
                        [backend_drops, rejected_before](
                            SpectrumFrame& accepted,
                            const std::uint64_t queue_drops
                        ) noexcept {
                            const auto total_drops =
                                backend_drops + queue_drops + rejected_before;
                            accepted.dropped_fft_frames_before = total_drops;
                            if (total_drops != 0U) {
                                accepted.quality_flags =
                                    accepted.quality_flags | QualityFlag::FftDropped;
                            }
                        }
                    );
                    if (pushed == PushResult::Full || pushed == PushResult::Dropped ||
                        pushed == PushResult::Stopped) {
                        ++spectrum_rejected_drops;
                    }
                }
            };
        const auto update_fft_counters =
            [this, &spectrum_rejected_drops](const DspBackendMetrics& backend_metrics) {
                counters_.fft_frames_computed.store(
                    backend_metrics.fft_frames_computed,
                    std::memory_order_relaxed
                );
                counters_.fft_frames_dropped.store(
                    backend_metrics.fft_frames_dropped +
                        spectrum_queue_->stats().dropped + spectrum_rejected_drops,
                    std::memory_order_relaxed
                );
            };
        while (true) {
            IqBlock block;
            if (dsp_queue_->pop(block) == PopResult::Stopped) {
                break;
            }
            const auto started = std::chrono::steady_clock::now();
            backend->push_iq(block);
            ++processed;
            // Do not flush a partial FFT batch on every I/Q block. This is
            // what makes batch_size effective across block boundaries.
            auto ready = backend->poll_spectrum(0U, false);
            const auto backend_metrics = backend->metrics();
            publish_frames(std::move(ready), backend_metrics);
            update_fft_counters(backend_metrics);
            const auto elapsed = std::chrono::steady_clock::now() - started;
            const double elapsed_ms = std::chrono::duration<double, std::milli>(elapsed).count();
            double current = counters_.cpu_processing_ms.load(std::memory_order_relaxed);
            while (!counters_.cpu_processing_ms.compare_exchange_weak(
                current,
                current + elapsed_ms,
                std::memory_order_relaxed
            )) {
            }
            const double run_seconds =
                std::chrono::duration<double>(started - run_started).count();
            if (run_seconds > 0.0) {
                counters_.analytical_fft_rate.store(
                    static_cast<double>(backend_metrics.fft_frames_computed) / run_seconds,
                    std::memory_order_relaxed
                );
            }
            if (processed % config_.snapshot_interval_blocks == 0U) {
                const auto snapshot = assemble_metrics();
                const auto result = snapshot_queue_->try_push(snapshot);
                if (result == PushResult::Pushed || result == PushResult::Evicted) {
                    counters_.spectrum_snapshots_emitted.fetch_add(
                        1U,
                        std::memory_order_relaxed
                    );
                }
            }
        }
        // Shutdown boundary: flush and publish the final partial FFT batch.
        // The spectrum queue deliberately remains open until this completes.
        auto final_frames = backend->poll_spectrum(0U, true);
        const auto final_backend_metrics = backend->metrics();
        publish_frames(std::move(final_frames), final_backend_metrics);
        update_fft_counters(final_backend_metrics);
    } catch (const std::exception& error) {
        emit_event(EventSeverity::Critical, "consumer_failure", error.what());
        mark_error();
        initiate_shutdown();
    } catch (...) {
        emit_event(EventSeverity::Critical, "consumer_failure", "unknown consumer failure");
        mark_error();
        initiate_shutdown();
    }
}

void SyntheticEngine::recorder_run() noexcept {
    try {
        while (true) {
            IqBlock block;
            if (recorder_queue_->pop(block) == PopResult::Stopped) {
                break;
            }
            // P04 accounts recorder transport only; file writing is P14.
        }
    } catch (const std::exception& error) {
        emit_event(EventSeverity::Critical, "recorder_failure", error.what());
        mark_error();
        initiate_shutdown();
    } catch (...) {
        emit_event(EventSeverity::Critical, "recorder_failure", "unknown recorder failure");
        mark_error();
        initiate_shutdown();
    }
}

EngineMetrics SyntheticEngine::assemble_metrics() const {
    EngineMetrics result = counters_.snapshot();
    if (acquisition_queue_) {
        result.acquisition_queue_depth = acquisition_queue_->depth();
    }
    if (dsp_queue_) {
        result.dsp_queue_depth = dsp_queue_->depth();
    }
    if (recorder_queue_) {
        result.recorder_queue_depth = recorder_queue_->depth();
    }
    return result;
}

void SyntheticEngine::account_abandoned() noexcept {
    try {
        // Main-path abandoned blocks are I/Q loss and are counted exactly.
        // Recorder-stream abandoned blocks are observable through recorder
        // queue stats; their analytical copies still flow on the main path.
        std::uint64_t abandoned = 0U;
        if (acquisition_queue_) {
            abandoned += acquisition_queue_->abandon();
        }
        if (dsp_queue_) {
            abandoned += dsp_queue_->abandon();
        }
        if (abandoned != 0U) {
            counters_.iq_blocks_dropped.fetch_add(abandoned, std::memory_order_relaxed);
            counters_.iq_samples_dropped.fetch_add(
                abandoned * config_.block_size_samples,
                std::memory_order_relaxed
            );
            emit_event(
                EventSeverity::Warning,
                "queue_abandoned",
                "shutdown abandoned queued I/Q blocks: " + std::to_string(abandoned)
            );
        }
        if (recorder_queue_) {
            const auto recorder_abandoned = recorder_queue_->abandon();
            if (recorder_abandoned != 0U) {
                emit_event(
                    EventSeverity::Warning,
                    "recorder_abandoned",
                    "shutdown abandoned recorder-stream blocks: " +
                        std::to_string(recorder_abandoned)
                );
            }
        }
    } catch (...) {
        // Accounting during shutdown must never throw.
    }
}

}  // namespace sdr_core

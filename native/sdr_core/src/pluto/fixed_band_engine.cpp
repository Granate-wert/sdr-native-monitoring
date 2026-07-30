#include "sdr_pluto/fixed_band_engine.hpp"

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/stop_token.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <exception>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <thread>
#include <utility>

namespace sdr_pluto {

namespace {

[[noreturn]] void invalid(const std::string& message) {
    throw sdr_core::ConfigurationError(message);
}

[[nodiscard]] std::int64_t system_time_ns() noexcept {
    const auto now = std::chrono::system_clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}

[[nodiscard]] std::uint32_t dsp_output_capacity(const FixedBandConfig& config) {
    const auto frames_per_block =
        static_cast<std::uint64_t>(config.device.buffer_samples) /
        static_cast<std::uint64_t>(config.dsp.hop_size);
    const auto required = frames_per_block +
                          static_cast<std::uint64_t>(config.dsp.batch_size) + 2U;
    return static_cast<std::uint32_t>(std::min<std::uint64_t>(
        std::numeric_limits<std::uint32_t>::max(),
        std::max<std::uint64_t>(required, config.spectrum_queue_capacity)
    ));
}

}  // namespace

void validate(const FixedBandConfig& value) {
    if (value.schema_version != sdr_core::contract_schema_version) {
        invalid("unsupported fixed-band schema_version");
    }
    sdr_core::validate(value.device);
    sdr_core::validate(value.dsp);
    sdr_core::validate(value.persistence);
    if (value.acquisition_queue_capacity >
        std::numeric_limits<std::uint32_t>::max() - 3U) {
        invalid("acquisition_queue_capacity exceeds Pluto pool bound" );
    }
    if (value.acquisition_queue_capacity == 0U) {
        invalid("acquisition_queue_capacity must be positive");
    }
    if (value.spectrum_queue_capacity == 0U) {
        invalid("spectrum_queue_capacity must be positive");
    }
    if (value.event_queue_capacity == 0U) {
        invalid("event_queue_capacity must be positive");
    }
    if (!std::isfinite(value.snapshot_rate_hz) || value.snapshot_rate_hz <= 0.0) {
        invalid("snapshot_rate_hz must be finite and positive");
    }
    static_cast<void>(sdr_core::to_wire(value.acquisition_overflow));
    static_cast<void>(sdr_core::to_wire(value.backend));
    if (value.dsp.unit == sdr_core::SpectrumUnit::Dbm ||
        value.dsp.unit == sdr_core::SpectrumUnit::DbmBin ||
        value.dsp.unit == sdr_core::SpectrumUnit::DbmHz) {
        invalid("fixed-band P07 supports only uncalibrated dBFS units");
    }
}

class FixedBandEngine::Impl final {
public:
    Impl(std::string uri, const std::uint32_t timeout_ms)
        : device_(std::move(uri), timeout_ms) {}

    ~Impl() noexcept {
        shutdown_noexcept();
        disconnect();
    }

    AppliedConfig configure(const FixedBandConfig& config) {
        std::lock_guard lock(lifecycle_mutex_);
        if (disconnect_requested_.load(std::memory_order_acquire)) {
            throw sdr_core::ConfigurationError(
                "fixed-band engine is terminally disconnected"
            );
        }
        const auto current = state_.load(std::memory_order_acquire);
        if (current != sdr_core::EngineState::Created &&
            current != sdr_core::EngineState::Configured &&
            current != sdr_core::EngineState::Stopped) {
            throw sdr_core::ConfigurationError(
                "fixed-band configure requires CREATED, CONFIGURED or STOPPED state"
            );
        }
        validate(config);

        auto acquisition = std::make_unique<sdr_core::BoundedQueue<sdr_core::IqBlock>>(
            config.acquisition_queue_capacity,
            config.acquisition_overflow
        );
        auto spectrum =
            std::make_unique<sdr_core::BoundedQueue<sdr_core::SpectrumFrame>>(
                config.spectrum_queue_capacity,
                sdr_core::OverflowPolicy::LatestWins
            );
        auto events =
            std::make_unique<sdr_core::BoundedQueue<sdr_core::DiagnosticEvent>>(
                config.event_queue_capacity,
                sdr_core::OverflowPolicy::DropNewest
            );
        auto persistence = std::make_unique<sdr_core::PersistenceAccumulator>(
            config.persistence
        );
        auto persistence_queue =
            std::make_unique<sdr_core::BoundedQueue<sdr_core::PersistenceSnapshot>>(
                2U, sdr_core::OverflowPolicy::LatestWins
            );

        const auto probe = device_.probe();
        sdr_core::CpuDspOptions options;
        options.output_capacity = dsp_output_capacity(config);
        options.dc_removal = config.dc_removal_block_mean
                                 ? sdr_core::DcRemovalMode::BlockMean
                                 : sdr_core::DcRemovalMode::Off;
        options.source = {
            .source_type = sdr_core::SourceType::LiveIq,
            .source_id = config.device.source_id,
            .display_name = probe.model.empty() ? "PlutoSDR / AD936x" : probe.model,
            .uri = config.device.context_uri,
            .device_serial = probe.serial,
            .backend_id = "pluto-libiio",
            .schema_version = sdr_core::contract_schema_version,
            .metadata_json = {},
        };
        sdr_core::validate(options.source);
        // P08: the DSP stage is selected through the vendor-neutral factory
        // (CPU / CUDA with runtime failover per configuration).
        sdr_core::DspBackendSelectionOptions selection;
        selection.preference = config.backend;
        selection.allow_runtime_fallback = config.allow_runtime_fallback;
        auto backend = sdr_core::make_dsp_backend(selection, std::move(options));
        backend->configure(config.dsp);

        // PlutoDevice guarantees transactional hardware configure/readback.
        const auto applied = device_.configure(
            config.device,
            std::max(8U, config.acquisition_queue_capacity + 3U)
        );

        config_ = config;
        applied_ = applied;
        backend_ = std::move(backend);
        acquisition_queue_ = std::move(acquisition);
        spectrum_queue_ = std::move(spectrum);
        event_queue_ = std::move(events);
        persistence_ = std::move(persistence);
        persistence_queue_ = std::move(persistence_queue);
        stop_ = sdr_core::make_stop_token();
        counters_.reset();
        transient_blocks_discarded_.store(0U, std::memory_order_relaxed);
        transient_samples_discarded_.store(0U, std::memory_order_relaxed);
        snapshots_superseded_.store(0U, std::memory_order_relaxed);
        persistence_snapshots_superseded_.store(0U, std::memory_order_relaxed);
        shutdown_blocks_discarded_.store(0U, std::memory_order_relaxed);
        shutdown_samples_discarded_.store(0U, std::memory_order_relaxed);
        expected_cancellations_.store(0U, std::memory_order_relaxed);
        has_error_.store(false, std::memory_order_relaxed);
        events_lost_.store(0U, std::memory_order_relaxed);
        {
            std::lock_guard priority_lock(priority_event_mutex_);
            pending_error_event_.reset();
        }
        event_sequence_.store(0U, std::memory_order_relaxed);
        events_lost_reported_ = 0U;
        config_generation_ = applied.config_generation;
        configured_ = true;
        state_.store(sdr_core::EngineState::Configured, std::memory_order_release);
        emit_event(
            sdr_core::EventSeverity::Info,
            "fixed_band_configured",
            "Pluto and CPU DSP configuration applied"
        );
        return applied_;
    }

    AppliedConfig reconfigure(const FixedBandConfig& config) {
        const bool resume = state() == sdr_core::EngineState::Running;
        if (resume) {
            stop();
        }
        const auto applied = configure(config);
        if (resume) {
            start();
        }
        return applied;
    }

    void start() {
        std::lock_guard lock(lifecycle_mutex_);
        if (disconnect_requested_.load(std::memory_order_acquire)) {
            throw sdr_core::ConfigurationError(
                "fixed-band engine is terminally disconnected"
            );
        }
        if (state_.load(std::memory_order_acquire) !=
            sdr_core::EngineState::Configured) {
            throw sdr_core::ConfigurationError(
                "fixed-band start requires CONFIGURED state"
            );
        }
        transient_remaining_.store(
            config_.discard_blocks_after_start,
            std::memory_order_relaxed
        );
        device_.start_stream();
        state_.store(sdr_core::EngineState::Running, std::memory_order_release);
        try {
            dsp_thread_ = std::thread([this] { dsp_run(); });
            acquisition_thread_ = std::thread([this] { acquisition_run(); });
        } catch (...) {
            initiate_shutdown();
            if (acquisition_thread_.joinable()) {
                acquisition_thread_.join();
            }
            if (dsp_thread_.joinable()) {
                dsp_thread_.join();
            }
            device_.stop_stream();
            mark_error();
            throw;
        }
        emit_event(
            sdr_core::EventSeverity::Info,
            "fixed_band_started",
            "fixed-band acquisition and CPU DSP started"
        );
    }

    void request_stop() {
        std::lock_guard lock(lifecycle_mutex_);
        auto expected = sdr_core::EngineState::Running;
        if (state_.compare_exchange_strong(
                expected,
                sdr_core::EngineState::Stopping,
                std::memory_order_acq_rel
            )) {
            initiate_shutdown();
            return;
        }
        if (expected == sdr_core::EngineState::Stopping ||
            expected == sdr_core::EngineState::Error) {
            return;
        }
        throw sdr_core::ConfigurationError(
            "fixed-band request_stop requires RUNNING state"
        );
    }

    void join() {
        std::lock_guard lock(lifecycle_mutex_);
        const auto current = state_.load(std::memory_order_acquire);
        if (current == sdr_core::EngineState::Stopped) {
            return;
        }
        if (current != sdr_core::EngineState::Stopping &&
            current != sdr_core::EngineState::Error) {
            throw sdr_core::ConfigurationError(
                "fixed-band join requires request_stop() first"
            );
        }
        if (acquisition_thread_.joinable()) {
            acquisition_thread_.join();
        }
        if (dsp_thread_.joinable()) {
            dsp_thread_.join();
        }
        device_.stop_stream();
        account_abandoned();
        state_.store(sdr_core::EngineState::Stopped, std::memory_order_release);
        emit_event(
            sdr_core::EventSeverity::Info,
            "fixed_band_stopped",
            "fixed-band engine stopped"
        );
    }

    void stop() {
        request_stop();
        join();
    }

    void disconnect() noexcept {
        disconnect_requested_.store(true, std::memory_order_release);
        shutdown_noexcept();
        {
            std::lock_guard lock(lifecycle_mutex_);
            auto expected = sdr_core::EngineState::Running;
            if (state_.compare_exchange_strong(
                    expected,
                    sdr_core::EngineState::Stopping,
                    std::memory_order_acq_rel
                )) {
                initiate_shutdown();
            }
        }
        shutdown_noexcept();
        std::lock_guard lock(lifecycle_mutex_);
        device_.disconnect();
        configured_ = false;
        config_ = {};
        applied_ = {};
        backend_.reset();
        acquisition_queue_.reset();
        spectrum_queue_.reset();
        persistence_queue_.reset();
        persistence_.reset();
        state_.store(sdr_core::EngineState::Stopped, std::memory_order_release);
    }

    [[nodiscard]] bool connected() const noexcept {
        return device_.connected();
    }

    [[nodiscard]] bool streaming() const noexcept {
        return device_.streaming();
    }

    [[nodiscard]] sdr_core::EngineState state() const noexcept {
        return state_.load(std::memory_order_acquire);
    }

    [[nodiscard]] std::uint64_t config_generation() const noexcept {
        std::lock_guard lock(lifecycle_mutex_);
        return config_generation_;
    }

    [[nodiscard]] FixedBandConfig config() const {
        std::lock_guard lock(lifecycle_mutex_);
        if (!configured_) {
            throw sdr_core::ConfigurationError("fixed-band engine is not configured");
        }
        return config_;
    }

    [[nodiscard]] AppliedConfig applied_config() const {
        std::lock_guard lock(lifecycle_mutex_);
        if (!configured_) {
            throw sdr_core::ConfigurationError("fixed-band engine is not configured");
        }
        return applied_;
    }

    [[nodiscard]] FixedBandMetrics metrics() const {
        std::lock_guard lock(lifecycle_mutex_);
        FixedBandMetrics result;
        result.state = state_.load(std::memory_order_acquire);
        result.has_error = has_error_.load(std::memory_order_relaxed);
        result.engine = assemble_engine_metrics();
        result.device = device_.metrics();
        if (acquisition_queue_) {
            result.acquisition_queue = acquisition_queue_->stats();
        }
        if (spectrum_queue_) {
            result.spectrum_queue = spectrum_queue_->stats();
        }
        if (persistence_queue_) {
            result.persistence_queue = persistence_queue_->stats();
        }
        result.transient_blocks_discarded =
            transient_blocks_discarded_.load(std::memory_order_relaxed);
        result.transient_samples_discarded =
            transient_samples_discarded_.load(std::memory_order_relaxed);
        result.spectrum_snapshots_superseded =
            snapshots_superseded_.load(std::memory_order_relaxed);
        result.persistence_snapshots_superseded =
            persistence_snapshots_superseded_.load(std::memory_order_relaxed);
        result.shutdown_blocks_discarded =
            shutdown_blocks_discarded_.load(std::memory_order_relaxed);
        result.shutdown_samples_discarded =
            shutdown_samples_discarded_.load(std::memory_order_relaxed);
        result.expected_cancellations =
            expected_cancellations_.load(std::memory_order_relaxed);
        result.diagnostic_events_lost =
            events_lost_.load(std::memory_order_relaxed);
        if (backend_) {
            const auto dsp_metrics = backend_->metrics();
            result.requested_backend = dsp_metrics.requested_preference;
            result.active_backend = dsp_metrics.active_backend;
            result.backend_self_test_passed = dsp_metrics.backend_self_test_passed;
            result.backend_fallback_count = dsp_metrics.backend_fallback_count;
            result.backend_switch_count = dsp_metrics.backend_switch_count;
            result.last_backend_error = dsp_metrics.last_backend_error;
        } else {
            result.requested_backend = config_.backend;
            result.active_backend = config_.backend;
        }
        return result;
    }

    [[nodiscard]] std::vector<sdr_core::SpectrumFrame> poll_spectrum_frames(
        const std::size_t max_items
    ) {
        std::lock_guard lock(lifecycle_mutex_);
        std::vector<sdr_core::SpectrumFrame> result;
        if (!spectrum_queue_) {
            return result;
        }
        sdr_core::SpectrumFrame frame;
        while (max_items == 0U || result.size() < max_items) {
            if (!spectrum_queue_->try_pop(frame)) {
                break;
            }
            result.push_back(std::move(frame));
        }
        return result;
    }

    [[nodiscard]] std::vector<sdr_core::PersistenceSnapshot> poll_persistence_snapshots(
        const std::size_t max_items
    ) {
        std::lock_guard lock(lifecycle_mutex_);
        std::vector<sdr_core::PersistenceSnapshot> result;
        if (!persistence_queue_) {
            return result;
        }
        sdr_core::PersistenceSnapshot snapshot;
        while (max_items == 0U || result.size() < max_items) {
            if (!persistence_queue_->try_pop(snapshot)) {
                break;
            }
            result.push_back(std::move(snapshot));
        }
        return result;
    }

    [[nodiscard]] std::vector<sdr_core::DiagnosticEvent> poll_events(
        const std::size_t max_items
    ) {
        std::lock_guard lock(lifecycle_mutex_);
        std::vector<sdr_core::DiagnosticEvent> result;
        if (!event_queue_) {
            return result;
        }
        sdr_core::DiagnosticEvent event;
        while (max_items == 0U || result.size() < max_items) {
            if (!event_queue_->try_pop(event)) {
                break;
            }
            result.push_back(std::move(event));
        }
        {
            std::lock_guard priority_lock(priority_event_mutex_);
            if (pending_error_event_.has_value()) {
                const auto sequence = pending_error_event_->sequence;
                const bool already_present = std::any_of(
                    result.begin(),
                    result.end(),
                    [sequence](const sdr_core::DiagnosticEvent& item) {
                        return item.sequence == sequence;
                    }
                );
                if (already_present) {
                    pending_error_event_.reset();
                } else if (max_items == 0U || result.size() < max_items) {
                    result.push_back(std::move(*pending_error_event_));
                    pending_error_event_.reset();
                }
            }
        }
        const auto lost = events_lost_.load(std::memory_order_relaxed);
        if (lost > events_lost_reported_ &&
            (max_items == 0U || result.size() < max_items)) {
            events_lost_reported_ = lost;
            result.push_back({
                .severity = sdr_core::EventSeverity::Warning,
                .code = "events_lost",
                .message = "diagnostic events lost to queue overflow: " +
                           std::to_string(lost),
                .timestamp_ns = sdr_core::host_monotonic_ns(),
                .sequence =
                    event_sequence_.fetch_add(1U, std::memory_order_relaxed) + 1U,
            });
        }
        return result;
    }

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
    void emit_diagnostic_for_test(
        const sdr_core::EventSeverity severity,
        std::string code,
        std::string message
    ) {
        emit_event(severity, std::move(code), std::move(message));
    }
#endif
private:
    void shutdown_noexcept() noexcept {
        try {
            const auto current = state_.load(std::memory_order_acquire);
            if (current == sdr_core::EngineState::Running) {
                request_stop();
            }
            const auto after_request = state_.load(std::memory_order_acquire);
            if (after_request == sdr_core::EngineState::Stopping ||
                after_request == sdr_core::EngineState::Error) {
                join();
            }
        } catch (...) {
            initiate_shutdown();
            if (acquisition_thread_.joinable()) {
                acquisition_thread_.join();
            }
            if (dsp_thread_.joinable()) {
                dsp_thread_.join();
            }
            device_.stop_stream();
        }
    }

    void initiate_shutdown() noexcept {
        try {
            stop_->request_stop();
            device_.cancel();
            if (acquisition_queue_) {
                acquisition_queue_->request_stop();
            }
        } catch (...) {
        }
    }

    void mark_error() noexcept {
        has_error_.store(true, std::memory_order_relaxed);
        auto expected = state_.load(std::memory_order_acquire);
        while (expected == sdr_core::EngineState::Running ||
               expected == sdr_core::EngineState::Stopping) {
            if (state_.compare_exchange_weak(
                    expected,
                    sdr_core::EngineState::Error,
                    std::memory_order_acq_rel
                )) {
                return;
            }
        }
    }

    void emit_event(
        const sdr_core::EventSeverity severity,
        std::string code,
        std::string message
    ) noexcept {
        try {
            const bool preserve =
                severity == sdr_core::EventSeverity::Error ||
                severity == sdr_core::EventSeverity::Critical;
            if (preserve) {
                has_error_.store(true, std::memory_order_relaxed);
            }
            if (!event_queue_) {
                return;
            }
            sdr_core::DiagnosticEvent event{
                .severity = severity,
                .code = std::move(code),
                .message = std::move(message),
                .timestamp_ns = sdr_core::host_monotonic_ns(),
                .sequence =
                    event_sequence_.fetch_add(1U, std::memory_order_relaxed) + 1U,
            };
            const auto pushed = event_queue_->try_push(event);
            if (pushed == sdr_core::PushResult::Pushed) {
                return;
            }
            if (!preserve) {
                events_lost_.fetch_add(1U, std::memory_order_relaxed);
                return;
            }
            std::lock_guard priority_lock(priority_event_mutex_);
            if (pending_error_event_.has_value()) {
                events_lost_.fetch_add(1U, std::memory_order_relaxed);
            }
            pending_error_event_ = std::move(event);
        } catch (...) {
        }
    }

    void acquisition_run() noexcept {
        std::uint64_t overflow_drops = 0U;
        try {
            while (!stop_->stop_requested()) {
                sdr_core::IqBlock block;
                try {
                    block = device_.refill();
                } catch (const std::exception& error) {
                    if (stop_->stop_requested()) {
                        expected_cancellations_.fetch_add(
                            1U,
                            std::memory_order_relaxed
                        );
                        break;
                    }
                    emit_event(
                        sdr_core::EventSeverity::Critical,
                        "acquisition_failure",
                        error.what()
                    );
                    mark_error();
                    initiate_shutdown();
                    return;
                }
                counters_.iq_blocks_received.fetch_add(1U, std::memory_order_relaxed);
                counters_.iq_samples_received.fetch_add(
                    block.sample_count,
                    std::memory_order_relaxed
                );
                auto transient = transient_remaining_.load(std::memory_order_relaxed);
                if (transient != 0U) {
                    transient_remaining_.fetch_sub(1U, std::memory_order_relaxed);
                    transient_blocks_discarded_.fetch_add(
                        1U,
                        std::memory_order_relaxed
                    );
                    transient_samples_discarded_.fetch_add(
                        block.sample_count,
                        std::memory_order_relaxed
                    );
                    continue;
                }
                const auto sample_count = block.sample_count;
                std::uint64_t discarded_samples = sample_count;
                const auto pushed = acquisition_queue_->push_with_eviction(
                    std::move(block),
                    [&discarded_samples](const sdr_core::IqBlock& evicted) noexcept {
                        discarded_samples = evicted.sample_count;
                    }
                );
                if (pushed == sdr_core::PushResult::Stopped) {
                    counters_.iq_blocks_dropped.fetch_add(1U, std::memory_order_relaxed);
                    counters_.iq_samples_dropped.fetch_add(
                        sample_count,
                        std::memory_order_relaxed
                    );
                    shutdown_blocks_discarded_.fetch_add(
                        1U,
                        std::memory_order_relaxed
                    );
                    shutdown_samples_discarded_.fetch_add(
                        sample_count,
                        std::memory_order_relaxed
                    );
                    break;
                }

                if (pushed == sdr_core::PushResult::Dropped ||
                    pushed == sdr_core::PushResult::Evicted) {
                    counters_.iq_blocks_dropped.fetch_add(1U, std::memory_order_relaxed);
                    counters_.iq_samples_dropped.fetch_add(
                        discarded_samples,
                        std::memory_order_relaxed
                    );
                    ++overflow_drops;
                    if (overflow_drops == 1U ||
                        overflow_drops % 65536U == 0U) {
                        emit_event(
                            sdr_core::EventSeverity::Warning,
                            "acquisition_overflow",
                            "fixed-band acquisition queue overflow"
                        );
                    }
                }
            }
        } catch (const std::exception& error) {
            emit_event(
                sdr_core::EventSeverity::Critical,
                "acquisition_failure",
                error.what()
            );
            mark_error();
            initiate_shutdown();
        } catch (...) {
            emit_event(
                sdr_core::EventSeverity::Critical,
                "acquisition_failure",
                "unknown acquisition failure"
            );
            mark_error();
            initiate_shutdown();
        }
    }

    void dsp_run() noexcept {
        try {
            const auto started_at = std::chrono::steady_clock::now();
            const auto snapshot_period = std::chrono::duration<double>(
                1.0 / config_.snapshot_rate_hz
            );
            auto next_snapshot = started_at;
            std::optional<sdr_core::SpectrumFrame> latest;
            while (true) {
                sdr_core::IqBlock block;
                if (acquisition_queue_->pop(block) == sdr_core::PopResult::Stopped) {
                    break;
                }
                const auto processing_started = std::chrono::steady_clock::now();
                backend_->push_iq(block);
                auto ready = backend_->poll_spectrum(0U, false);
                const auto backend_metrics = backend_->metrics();
                for (auto& frame : ready) {
                    annotate_frame(frame, backend_metrics);
                    publish_persistence(frame);
                    latest = std::move(frame);
                }
                update_dsp_metrics(backend_metrics, started_at, processing_started);
                const auto now = std::chrono::steady_clock::now();
                if (latest.has_value() && now >= next_snapshot) {
                    publish(std::move(*latest));
                    latest.reset();
                    next_snapshot = now + std::chrono::duration_cast<
                        std::chrono::steady_clock::duration
                    >(snapshot_period);
                }
            }
            auto final_frames = backend_->poll_spectrum(0U, true);
            const auto final_metrics = backend_->metrics();
            for (auto& frame : final_frames) {
                annotate_frame(frame, final_metrics);
                publish_persistence(frame);
                latest = std::move(frame);
            }
            update_dsp_metrics(
                final_metrics,
                started_at,
                std::chrono::steady_clock::now()
            );
            if (latest.has_value()) {
                publish(std::move(*latest));
            }
        } catch (const std::exception& error) {
            emit_event(
                sdr_core::EventSeverity::Critical,
                "dsp_failure",
                error.what()
            );
            mark_error();
            initiate_shutdown();
        } catch (...) {
            emit_event(
                sdr_core::EventSeverity::Critical,
                "dsp_failure",
                "unknown DSP failure"
            );
            mark_error();
            initiate_shutdown();
        }
    }

    void annotate_frame(
        sdr_core::SpectrumFrame& frame,
        const sdr_core::DspBackendMetrics& backend_metrics
    ) const noexcept {
        frame.analog_bandwidth_hz = applied_.analog_bandwidth_hz;
        frame.dropped_samples_before =
            counters_.iq_samples_dropped.load(std::memory_order_relaxed);
        frame.dropped_iq_blocks_before =
            counters_.iq_blocks_dropped.load(std::memory_order_relaxed);
        frame.dropped_fft_frames_before = backend_metrics.fft_frames_dropped;
        if (backend_metrics.fft_frames_dropped != 0U) {
            frame.quality_flags =
                frame.quality_flags | sdr_core::QualityFlag::FftDropped;
        }
        if (frame.dropped_iq_blocks_before != 0U) {
            frame.quality_flags =
                frame.quality_flags | sdr_core::QualityFlag::IqDropped;
        }
    }

    void update_dsp_metrics(
        const sdr_core::DspBackendMetrics& backend_metrics,
        const std::chrono::steady_clock::time_point started_at,
        const std::chrono::steady_clock::time_point processing_started
    ) noexcept {
        counters_.fft_frames_computed.store(
            backend_metrics.fft_frames_computed,
            std::memory_order_relaxed
        );
        counters_.fft_frames_dropped.store(
            backend_metrics.fft_frames_dropped,
            std::memory_order_relaxed
        );
        const auto now = std::chrono::steady_clock::now();
        const double elapsed_ms =
            std::chrono::duration<double, std::milli>(now - processing_started).count();
        if (backend_metrics.active_backend == sdr_core::ComputeBackendKind::Cpu) {
            double current = counters_.cpu_processing_ms.load(std::memory_order_relaxed);
            while (!counters_.cpu_processing_ms.compare_exchange_weak(
                current,
                current + elapsed_ms,
                std::memory_order_relaxed
            )) {
            }
        } else {
            // Backend stage counters are cumulative; expose them without
            // charging CUDA work to the CPU wall-time metric.
            counters_.gpu_processing_ms.store(
                static_cast<double>(backend_metrics.gpu_processing_ns) / 1.0e6,
                std::memory_order_relaxed
            );
            counters_.h2d_ms.store(
                static_cast<double>(backend_metrics.h2d_ns) / 1.0e6,
                std::memory_order_relaxed
            );
            counters_.d2h_ms.store(
                static_cast<double>(backend_metrics.d2h_ns) / 1.0e6,
                std::memory_order_relaxed
            );
        }
        const double run_seconds =
            std::chrono::duration<double>(now - started_at).count();
        if (run_seconds > 0.0) {
            counters_.analytical_fft_rate.store(
                static_cast<double>(backend_metrics.fft_frames_computed) /
                    run_seconds,
                std::memory_order_relaxed
            );
        }
    }

    void publish_persistence(const sdr_core::SpectrumFrame& frame) noexcept {
        if (!persistence_ || !persistence_queue_) {
            return;
        }
        try {
            auto snapshot = persistence_->update(frame);
            counters_.persistence_updates.store(
                persistence_->processed_frames(), std::memory_order_relaxed
            );
            if (!snapshot.has_value()) {
                return;
            }
            const auto result = persistence_queue_->try_push(std::move(*snapshot));
            if (result == sdr_core::PushResult::Evicted) {
                persistence_snapshots_superseded_.fetch_add(
                    1U, std::memory_order_relaxed
                );
            }
        } catch (...) {
            emit_event(
                sdr_core::EventSeverity::Error,
                "persistence_failure",
                "failed to update native persistence"
            );
        }
    }

    void publish(sdr_core::SpectrumFrame frame) noexcept {
        try {
            const double latency_ms = std::max(
                0.0,
                static_cast<double>(system_time_ns() - frame.timestamp_ns) / 1.0e6
            );
            end_to_end_latency_ms_.store(latency_ms, std::memory_order_relaxed);
            const auto result = spectrum_queue_->try_push(std::move(frame));
            if (result == sdr_core::PushResult::Pushed ||
                result == sdr_core::PushResult::Evicted) {
                counters_.spectrum_snapshots_emitted.fetch_add(
                    1U,
                    std::memory_order_relaxed
                );
                if (result == sdr_core::PushResult::Evicted) {
                    snapshots_superseded_.fetch_add(1U, std::memory_order_relaxed);
                }
            }
        } catch (...) {
            emit_event(
                sdr_core::EventSeverity::Error,
                "snapshot_publication_failure",
                "failed to publish fixed-band spectrum snapshot"
            );
        }
    }

    [[nodiscard]] sdr_core::EngineMetrics assemble_engine_metrics() const {
        auto result = counters_.snapshot();
        if (acquisition_queue_) {
            result.acquisition_queue_depth = acquisition_queue_->depth();
        }
        result.end_to_end_latency_ms =
            end_to_end_latency_ms_.load(std::memory_order_relaxed);
        return result;
    }

    void account_abandoned() noexcept {
        try {
            if (!acquisition_queue_) {
                return;
            }
            std::uint64_t abandoned_samples = 0U;
            const auto abandoned = acquisition_queue_->abandon_with(
                [&abandoned_samples](const sdr_core::IqBlock& block) noexcept {
                    abandoned_samples += block.sample_count;
                }
            );
            if (abandoned == 0U) {
                return;
            }
            counters_.iq_blocks_dropped.fetch_add(abandoned, std::memory_order_relaxed);
            counters_.iq_samples_dropped.fetch_add(
                abandoned_samples,
                std::memory_order_relaxed
            );
            emit_event(
                sdr_core::EventSeverity::Warning,
                "queue_abandoned",
                "shutdown abandoned queued I/Q blocks: " +
                    std::to_string(abandoned)
            );
        } catch (...) {
        }
    }

    mutable std::mutex lifecycle_mutex_;
    PlutoDevice device_;
    std::atomic<sdr_core::EngineState> state_{sdr_core::EngineState::Created};
    std::atomic<bool> disconnect_requested_{false};
    std::atomic<bool> has_error_{false};
    FixedBandConfig config_{};
    AppliedConfig applied_{};
    std::uint64_t config_generation_{};
    bool configured_{};
    sdr_core::StopToken stop_{sdr_core::make_stop_token()};
    std::unique_ptr<sdr_core::DspBackend> backend_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::IqBlock>> acquisition_queue_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::SpectrumFrame>> spectrum_queue_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::DiagnosticEvent>> event_queue_;
    std::unique_ptr<sdr_core::PersistenceAccumulator> persistence_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::PersistenceSnapshot>> persistence_queue_;
    sdr_core::EngineMetricsCounters counters_{};
    std::atomic<std::uint32_t> transient_remaining_{};
    std::atomic<std::uint64_t> transient_blocks_discarded_{};
    std::atomic<std::uint64_t> transient_samples_discarded_{};
    std::atomic<std::uint64_t> snapshots_superseded_{};
    std::atomic<std::uint64_t> persistence_snapshots_superseded_{};
    std::atomic<std::uint64_t> shutdown_blocks_discarded_{};
    std::atomic<std::uint64_t> shutdown_samples_discarded_{};
    std::atomic<std::uint64_t> expected_cancellations_{};
    std::atomic<std::uint64_t> events_lost_{};
    std::atomic<std::uint64_t> event_sequence_{};
    mutable std::mutex priority_event_mutex_;
    std::optional<sdr_core::DiagnosticEvent> pending_error_event_;
    std::atomic<double> end_to_end_latency_ms_{};
    std::uint64_t events_lost_reported_{};
    std::thread acquisition_thread_;
    std::thread dsp_thread_;
};

FixedBandEngine::FixedBandEngine(std::string uri, const std::uint32_t timeout_ms)
    : impl_(std::make_unique<Impl>(std::move(uri), timeout_ms)) {}
FixedBandEngine::~FixedBandEngine() noexcept = default;
AppliedConfig FixedBandEngine::configure(const FixedBandConfig& config) {
    return impl_->configure(config);
}
AppliedConfig FixedBandEngine::reconfigure(const FixedBandConfig& config) {
    return impl_->reconfigure(config);
}
void FixedBandEngine::start() { impl_->start(); }
void FixedBandEngine::request_stop() { impl_->request_stop(); }
void FixedBandEngine::join() { impl_->join(); }
void FixedBandEngine::stop() { impl_->stop(); }
void FixedBandEngine::disconnect() noexcept { impl_->disconnect(); }
bool FixedBandEngine::connected() const noexcept { return impl_->connected(); }
bool FixedBandEngine::streaming() const noexcept { return impl_->streaming(); }
sdr_core::EngineState FixedBandEngine::state() const noexcept { return impl_->state(); }
std::uint64_t FixedBandEngine::config_generation() const noexcept {
    return impl_->config_generation();
}
FixedBandConfig FixedBandEngine::config() const { return impl_->config(); }
AppliedConfig FixedBandEngine::applied_config() const {
    return impl_->applied_config();
}
FixedBandMetrics FixedBandEngine::metrics() const { return impl_->metrics(); }
std::vector<sdr_core::SpectrumFrame> FixedBandEngine::poll_spectrum_frames(
    const std::size_t max_items
) {
    return impl_->poll_spectrum_frames(max_items);
}
std::vector<sdr_core::PersistenceSnapshot> FixedBandEngine::poll_persistence_snapshots(
    const std::size_t max_items
) {
    return impl_->poll_persistence_snapshots(max_items);
}
std::vector<sdr_core::DiagnosticEvent> FixedBandEngine::poll_events(
    const std::size_t max_items
) {
    return impl_->poll_events(max_items);
}
#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
void FixedBandEngine::emit_diagnostic_for_test(
    const sdr_core::EventSeverity severity,
    std::string code,
    std::string message
) {
    impl_->emit_diagnostic_for_test(
        severity,
        std::move(code),
        std::move(message)
    );
}
#endif
}  // namespace sdr_pluto

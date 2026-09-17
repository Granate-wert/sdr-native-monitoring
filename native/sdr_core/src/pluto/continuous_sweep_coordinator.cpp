#include "sdr_pluto/continuous_sweep_coordinator.hpp"

#include "sdr_core/errors.hpp"
#include "sdr_core/sweep_line_assembler.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <thread>
#include <utility>

namespace sdr_pluto {
namespace {

[[noreturn]] void invalid(const char* message) {
    throw sdr_core::ConfigurationError(message);
}

[[nodiscard]] std::int64_t monotonic_now_ns() noexcept {
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}

[[nodiscard]] sdr_core::SourceDescriptor planned_source(
    const ContinuousSweepCoordinatorConfig& config
) {
    const auto& device = config.segments.front().fixed_band.device;
    return {
        .source_type = sdr_core::SourceType::LiveIq,
        .source_id = device.source_id,
        .display_name = "AD936x native continuous sweep",
        .uri = device.context_uri,
        .device_serial = {},
        .backend_id = "pluto-libiio",
        .schema_version = sdr_core::contract_schema_version,
        .metadata_json = {},
    };
}

[[nodiscard]] sdr_core::SweepLineDefinition planned_definition(
    const ContinuousSweepCoordinatorConfig& config
) {
    std::vector<sdr_core::SweepLineSegmentDefinition> segments;
    segments.reserve(config.segments.size());
    for (std::size_t index = 0U; index < config.segments.size(); ++index) {
        const auto& segment = config.segments[index];
        segments.push_back({
            .segment_index = static_cast<std::uint32_t>(index),
            .config_generation = 0U,
            .usable_start_hz = segment.usable_start_hz,
            .usable_stop_hz = segment.usable_stop_hz,
        });
    }
    const auto& dsp = config.segments.front().fixed_band.dsp;
    const auto physical_spacing_hz = config.segments.front().fixed_band.device.sample_rate_hz /
        static_cast<double>(dsp.fft_size);
    const auto declared_usable_window_hz = config.usable_window_hz > 0.0
        ? config.usable_window_hz
        : config.display_stop_hz - config.display_start_hz;
    const auto line_spacing_hz = config.analysis_bins_per_usable_window == 0U
        ? physical_spacing_hz
        : declared_usable_window_hz /
            static_cast<double>(config.analysis_bins_per_usable_window);
    return {
        .source = planned_source(config),
        .epoch = config.epoch,
        .start_frequency_hz = config.display_start_hz,
        .stop_frequency_hz = config.display_stop_hz,
        .target_spacing_hz = line_spacing_hz,
        .analysis_window_hz = config.analysis_bins_per_usable_window == 0U
            ? 0.0 : declared_usable_window_hz,
        .analysis_bins_per_usable_window = config.analysis_bins_per_usable_window,
        .physical_fft_bin_width_hz = config.analysis_bins_per_usable_window == 0U
            ? 0.0 : physical_spacing_hz,
        .physical_fft_size = config.analysis_bins_per_usable_window == 0U
            ? 0U : dsp.fft_size,
        .unit = dsp.unit,
        .max_inflight_lines = 1U,
        .segments = std::move(segments),
    };
}

[[nodiscard]] bool same_geometry(
    const FixedBandConfig& left,
    const FixedBandConfig& right
) noexcept {
    return left.device.context_uri == right.device.context_uri &&
        left.device.source_id == right.device.source_id &&
        left.device.sample_rate_hz == right.device.sample_rate_hz &&
        left.device.analog_bandwidth_hz == right.device.analog_bandwidth_hz &&
        left.device.gain_mode == right.device.gain_mode &&
        left.device.manual_gain_db == right.device.manual_gain_db &&
        left.device.channel_index == right.device.channel_index &&
        left.dsp.fft_size == right.dsp.fft_size &&
        left.dsp.hop_size == right.dsp.hop_size &&
        left.dsp.window == right.dsp.window &&
        left.dsp.kaiser_beta == right.dsp.kaiser_beta &&
        left.dsp.detector == right.dsp.detector &&
        left.dsp.unit == right.dsp.unit &&
        left.dsp.precision_mode == right.dsp.precision_mode &&
        left.dsp.averaging_frames == right.dsp.averaging_frames &&
        left.dsp.calibration_status == right.dsp.calibration_status &&
        left.dsp.calibration_profile_id == right.dsp.calibration_profile_id &&
        left.backend == right.backend &&
        left.allow_runtime_fallback == right.allow_runtime_fallback &&
        left.dc_removal_block_mean == right.dc_removal_block_mean;
}

[[nodiscard]] bool covers_usable_range(
    const sdr_core::SpectrumFrame& frame,
    const ContinuousSweepSegmentConfig& segment
) noexcept {
    if (!frame.frequencies_hz || frame.frequencies_hz->empty()) {
        return false;
    }
    constexpr double tolerance_hz = 0.5;
    return frame.frequencies_hz->front() <= segment.usable_start_hz + tolerance_hz &&
        frame.frequencies_hz->back() >= segment.usable_stop_hz - tolerance_hz;
}

[[nodiscard]] sdr_core::SweepLineDefinition applied_definition(
    const ContinuousSweepCoordinatorConfig& config,
    const std::vector<sdr_core::SpectrumFrame>& frames
) {
    if (frames.empty() || frames.size() > config.segments.size()) {
        invalid("continuous sweep definition requires a nonempty acquired prefix");
    }
    const auto spacing = frames.front().fft_bin_width_hz;
    const auto declared_usable_window_hz = config.usable_window_hz > 0.0
        ? config.usable_window_hz
        : config.display_stop_hz - config.display_start_hz;
    const auto line_spacing_hz = config.analysis_bins_per_usable_window == 0U
        ? spacing
        : declared_usable_window_hz /
            static_cast<double>(config.analysis_bins_per_usable_window);
    std::vector<sdr_core::SweepLineSegmentDefinition> segments;
    segments.reserve(frames.size());
    for (std::size_t index = 0U; index < frames.size(); ++index) {
        const auto& frame = frames[index];
        if (frame.source.source_type != frames.front().source.source_type ||
            frame.source.source_id != frames.front().source.source_id ||
            frame.unit != frames.front().unit ||
            std::abs(frame.fft_bin_width_hz - spacing) > 1e-6 ||
            frame.config_generation == 0U) {
            invalid("continuous sweep segment readback geometry/provenance differs");
        }
        segments.push_back({
            .segment_index = static_cast<std::uint32_t>(index),
            .config_generation = frame.config_generation,
            .usable_start_hz = config.segments[index].usable_start_hz,
            .usable_stop_hz = config.segments[index].usable_stop_hz,
        });
    }
    for (std::size_t index = frames.size(); index < config.segments.size(); ++index) {
        segments.push_back({
            .segment_index = static_cast<std::uint32_t>(index),
            .config_generation = 0U,
            .usable_start_hz = config.segments[index].usable_start_hz,
            .usable_stop_hz = config.segments[index].usable_stop_hz,
        });
    }
    return {
        .source = frames.front().source,
        .epoch = config.epoch,
        .start_frequency_hz = config.display_start_hz,
        .stop_frequency_hz = config.display_stop_hz,
        .target_spacing_hz = line_spacing_hz,
        .analysis_window_hz = config.analysis_bins_per_usable_window == 0U
            ? 0.0 : declared_usable_window_hz,
        .analysis_bins_per_usable_window = config.analysis_bins_per_usable_window,
        .physical_fft_bin_width_hz = config.analysis_bins_per_usable_window == 0U
            ? 0.0 : spacing,
        .physical_fft_size = config.analysis_bins_per_usable_window == 0U
            ? 0U : frames.front().fft_size,
        .unit = frames.front().unit,
        .max_inflight_lines = 1U,
        .segments = std::move(segments),
    };
}

}  // namespace

void validate(const ContinuousSweepCoordinatorConfig& value) {
    if (!std::isfinite(value.display_start_hz) || !std::isfinite(value.display_stop_hz) ||
        value.display_start_hz <= 0.0 || value.display_stop_hz <= value.display_start_hz) {
        invalid("continuous sweep coordinator requires a finite increasing display span");
    }
    if (!std::isfinite(value.usable_window_hz) || value.usable_window_hz < 0.0) {
        invalid("continuous sweep coordinator usable window must be finite and non-negative");
    }
    const auto declared_usable_window_hz = value.usable_window_hz > 0.0
        ? value.usable_window_hz : value.display_stop_hz - value.display_start_hz;
    if (value.output_queue_capacity == 0U || value.output_queue_capacity > 64U ||
        value.segment_frame_timeout_ms == 0U || value.segment_frame_timeout_ms > 60'000U) {
        invalid("continuous sweep coordinator queue/timeout bound is invalid");
    }
    if (value.analysis_bins_per_usable_window != 0U &&
        (value.analysis_bins_per_usable_window < 256U ||
         value.analysis_bins_per_usable_window > 262'144U ||
         (value.analysis_bins_per_usable_window &
          (value.analysis_bins_per_usable_window - 1U)) != 0U)) {
        invalid("continuous sweep coordinator analysis bins per usable window must be a power of two in [256, 262144]");
    }
    if (value.analysis_bins_per_usable_window != 0U &&
        value.usable_window_hz <= 0.0) {
        invalid("continuous sweep coordinator analysis bins require an explicit usable window");
    }
    if (value.line_snapshot_rate_hz != 0.0 &&
        (!std::isfinite(value.line_snapshot_rate_hz) ||
         value.line_snapshot_rate_hz < 1.0 || value.line_snapshot_rate_hz > 2'000.0)) {
        invalid("continuous sweep coordinator line snapshot rate must be zero or in [1, 2000] Hz");
    }
    if (value.segments.empty() || value.segments.size() > 64U) {
        invalid("continuous sweep coordinator segment count must be in [1, 64]");
    }
    const auto& first = value.segments.front().fixed_band;
    validate(first);
    if (value.analysis_bins_per_usable_window != 0U) {
        const auto physical_spacing_hz = first.device.sample_rate_hz /
            static_cast<double>(first.dsp.fft_size);
        const auto analysis_spacing_hz = declared_usable_window_hz /
            static_cast<double>(value.analysis_bins_per_usable_window);
        if (analysis_spacing_hz + 1e-9 < physical_spacing_hz) {
            invalid("continuous sweep analysis grid requires a denser physical FFT transform");
        }
    }
    if (first.recording.enabled || first.recorder_enabled || first.sweep_statistics_sink ||
        (first.continuous_sweep_line.has_value() && first.continuous_sweep_line->enabled)) {
        invalid("continuous sweep coordinator forbids recording and nested line publication");
    }
    double previous_stop = value.display_start_hz;
    for (std::size_t index = 0U; index < value.segments.size(); ++index) {
        const auto& segment = value.segments[index];
        validate(segment.fixed_band);
        if (segment.fixed_band.recording.enabled || segment.fixed_band.recorder_enabled ||
            segment.fixed_band.sweep_statistics_sink ||
            (segment.fixed_band.continuous_sweep_line.has_value() &&
             segment.fixed_band.continuous_sweep_line->enabled) ||
            !same_geometry(first, segment.fixed_band) ||
            !std::isfinite(segment.usable_start_hz) || !std::isfinite(segment.usable_stop_hz) ||
            segment.usable_start_hz < value.display_start_hz ||
            segment.usable_stop_hz > value.display_stop_hz ||
            segment.usable_stop_hz <= segment.usable_start_hz ||
            segment.usable_stop_hz - segment.usable_start_hz > declared_usable_window_hz ||
            (index != 0U && segment.usable_start_hz > previous_stop)) {
            invalid("continuous sweep segments must be compatible, ordered and overlap/cover the display span");
        }
        previous_stop = segment.usable_stop_hz;
    }
    if (value.segments.front().usable_start_hz > value.display_start_hz ||
        previous_stop < value.display_stop_hz) {
        invalid("continuous sweep segments do not cover the display span");
    }
    static_cast<void>(planned_definition(value));
    if (value.statistics && (!std::isfinite(value.statistics_snapshot_rate_hz) ||
        value.statistics_snapshot_rate_hz < 1.0 || value.statistics_snapshot_rate_hz > 60.0)) {
        invalid("Sweep statistics snapshot rate must be in [1, 60] Hz");
    }
}

class ContinuousSweepCoordinator::Impl final {
public:
    Impl(std::string uri, const std::uint32_t timeout_ms)
        : uri_(std::move(uri)), engine_(uri_, timeout_ms) {}

    ~Impl() noexcept {
        shutdown_noexcept();
        engine_.disconnect();
    }

    void configure(ContinuousSweepCoordinatorConfig config) {
        std::lock_guard lock(lifecycle_mutex_);
        const auto current = state_.load(std::memory_order_acquire);
        if (current != sdr_core::EngineState::Created &&
            current != sdr_core::EngineState::Configured &&
            current != sdr_core::EngineState::Stopped) {
            invalid("continuous sweep coordinator configure requires CREATED, CONFIGURED or STOPPED state");
        }
        // A naturally completed worker may still be joinable. Reap it before
        // accepting another configured epoch; assigning a new std::thread to
        // a joinable object would terminate the process.
        if (worker_.joinable()) {
            worker_.join();
        }
        validate(config);
        if (config.segments.front().fixed_band.device.context_uri != uri_) {
            invalid("continuous sweep coordinator URI differs from its fixed-band segments");
        }
        std::shared_ptr<sdr_core::SweepStatisticsPublisher> statistics;
        if (config.statistics) {
            // Queue copies may share snapshots, but reserve the worst case
            // before any Apply/RX. Include drained native relay/output batches,
            // queued items, latest/current preview and a constructing snapshot.
            std::size_t slots = static_cast<std::size_t>(config.output_queue_capacity) * 2 + 4;
            if (config.segments.size() == 1) {
                const auto& fixed = config.segments.front().fixed_band;
                const auto burst = static_cast<std::size_t>(fixed.device.buffer_samples) /
                    fixed.dsp.hop_size + fixed.dsp.batch_size + 2;
                slots += 2 * std::max({burst, static_cast<std::size_t>(fixed.spectrum_queue_capacity),
                                     static_cast<std::size_t>(config.output_queue_capacity)});
            }
            const sdr_core::ContinuousSweepLineAssembler grid(planned_definition(config));
            statistics = std::make_shared<sdr_core::SweepStatisticsPublisher>(
                *config.statistics, grid.definition().source, config.epoch, grid.definition().unit,
                grid.frequencies(), config.statistics_snapshot_rate_hz, slots);
        }
        config_ = std::move(config);
        statistics_ = std::move(statistics);
        output_ = std::make_unique<sdr_core::BoundedQueue<sdr_core::SweepLineFrame>>(
            config_.output_queue_capacity, sdr_core::OverflowPolicy::LatestWins
        );
        // A preview belongs to one configured epoch, never to the next run.
        // Reset only after validation and after the old worker has been reaped.
        {
            std::lock_guard progress_lock(progress_mutex_);
            progress_.reset();
        }
        completed_lines_.store(0U, std::memory_order_relaxed);
        gapped_lines_.store(0U, std::memory_order_relaxed);
        line_relay_snapshots_superseded_.store(0U, std::memory_order_relaxed);
        line_relay_queue_capacity_.store(0U, std::memory_order_relaxed);
        line_relay_queue_high_water_.store(0U, std::memory_order_relaxed);
        output_snapshots_superseded_.store(0U, std::memory_order_relaxed);
        segment_reconfigurations_.store(0U, std::memory_order_relaxed);
        segment_frame_timeouts_.store(0U, std::memory_order_relaxed);
        terminal_control_gaps_.store(0U, std::memory_order_relaxed);
        expected_stop_requests_.store(0U, std::memory_order_relaxed);
        expected_cancellations_.store(0U, std::memory_order_relaxed);
        last_accounted_metrics_.reset();
        last_accounted_generation_ = 0U;
        device_iq_samples_.store(0U, std::memory_order_relaxed);
        device_iq_blocks_.store(0U, std::memory_order_relaxed);
        analytical_fft_frames_.store(0U, std::memory_order_relaxed);
        source_short_reads_.store(0U, std::memory_order_relaxed);
        source_refill_errors_.store(0U, std::memory_order_relaxed);
        source_output_pool_exhaustions_.store(0U, std::memory_order_relaxed);
        source_estimated_dropped_samples_.store(0U, std::memory_order_relaxed);
        acquisition_queue_blocks_dropped_.store(0U, std::memory_order_relaxed);
        acquisition_queue_samples_dropped_.store(0U, std::memory_order_relaxed);
        source_sequence_discontinuities_.store(0U, std::memory_order_relaxed);
        source_sample_index_discontinuities_.store(0U, std::memory_order_relaxed);
        source_timestamp_regressions_.store(0U, std::memory_order_relaxed);
        source_estimated_timestamp_blocks_.store(0U, std::memory_order_relaxed);
        hardware_overflow_counter_available_.store(false, std::memory_order_relaxed);
        fft_frames_dropped_.store(0U, std::memory_order_relaxed);
        acquisition_queue_high_water_.store(0U, std::memory_order_relaxed);
        spectrum_queue_high_water_.store(0U, std::memory_order_relaxed);
        {
            std::lock_guard segment_metrics_lock(segment_frame_metrics_mutex_);
            completed_current_generation_fft_frames_.assign(config_.segments.size(), 0U);
            completed_line_analysis_geometry_available_ = false;
            completed_line_analysis_window_hz_ = 0.0;
            completed_line_analysis_bins_per_usable_window_ = 0U;
            completed_line_physical_fft_bin_width_hz_ = 0.0;
            completed_line_physical_fft_size_ = 0U;
            completed_line_analysis_geometry_mismatches_ = 0U;
        }
        {
            std::lock_guard applied_lock(applied_segments_mutex_);
            current_applied_segments_.clear();
            completed_applied_segments_.clear();
            current_applied_segments_.reserve(config_.segments.size());
            completed_applied_segments_.reserve(config_.segments.size());
        }
        has_error_.store(false, std::memory_order_relaxed);
        stop_requested_.store(false, std::memory_order_release);
        state_.store(sdr_core::EngineState::Configured, std::memory_order_release);
    }

    void start() {
        std::lock_guard lock(lifecycle_mutex_);
        if (state_.load(std::memory_order_acquire) != sdr_core::EngineState::Configured ||
            !output_) {
            invalid("continuous sweep coordinator start requires CONFIGURED state");
        }
        {
            std::lock_guard progress_lock(progress_mutex_);
            progress_.reset();
        }
        stop_requested_.store(false, std::memory_order_release);
        state_.store(sdr_core::EngineState::Running, std::memory_order_release);
        worker_ = std::thread([this] { run(); });
    }

    void request_stop() {
        auto expected = sdr_core::EngineState::Running;
        if (state_.compare_exchange_strong(
                expected, sdr_core::EngineState::Stopping, std::memory_order_acq_rel
            )) {
            expected_stop_requests_.fetch_add(1U, std::memory_order_relaxed);
            stop_requested_.store(true, std::memory_order_release);
            return;
        }
        if (expected == sdr_core::EngineState::Stopping || expected == sdr_core::EngineState::Stopped ||
            expected == sdr_core::EngineState::Error) {
            return;
        }
        invalid("continuous sweep coordinator request_stop requires RUNNING state");
    }

    void join() {
        if (worker_.joinable()) {
            worker_.join();
        }
    }

    void stop() {
        request_stop();
        join();
    }

    [[nodiscard]] sdr_core::EngineState state() const noexcept {
        return state_.load(std::memory_order_acquire);
    }

    [[nodiscard]] ContinuousSweepCoordinatorMetrics metrics() const {
        ContinuousSweepCoordinatorMetrics result;
        result.state = state();
        result.has_error = has_error_.load(std::memory_order_relaxed);
        if (output_) {
            result.output_queue = output_->stats();
        }
        result.completed_lines = completed_lines_.load(std::memory_order_relaxed);
        result.gapped_lines = gapped_lines_.load(std::memory_order_relaxed);
        result.line_relay_snapshots_superseded =
            line_relay_snapshots_superseded_.load(std::memory_order_relaxed);
        result.line_relay_queue_capacity =
            line_relay_queue_capacity_.load(std::memory_order_relaxed);
        result.line_relay_queue_high_water =
            line_relay_queue_high_water_.load(std::memory_order_relaxed);
        result.output_snapshots_superseded = output_snapshots_superseded_.load(std::memory_order_relaxed);
        result.segment_reconfigurations = segment_reconfigurations_.load(std::memory_order_relaxed);
        result.segment_frame_timeouts = segment_frame_timeouts_.load(std::memory_order_relaxed);
        result.terminal_control_gaps = terminal_control_gaps_.load(std::memory_order_relaxed);
        result.expected_cancellations = expected_cancellations_.load(std::memory_order_relaxed);
        result.device_iq_samples = device_iq_samples_.load(std::memory_order_relaxed);
        result.device_iq_blocks = device_iq_blocks_.load(std::memory_order_relaxed);
        result.analytical_fft_frames = analytical_fft_frames_.load(std::memory_order_relaxed);
        {
            std::lock_guard segment_metrics_lock(segment_frame_metrics_mutex_);
            result.completed_current_generation_fft_frames = completed_current_generation_fft_frames_;
            result.completed_line_analysis_geometry_available = completed_line_analysis_geometry_available_;
            result.completed_line_analysis_window_hz = completed_line_analysis_window_hz_;
            result.completed_line_analysis_bins_per_usable_window =
                completed_line_analysis_bins_per_usable_window_;
            result.completed_line_physical_fft_bin_width_hz =
                completed_line_physical_fft_bin_width_hz_;
            result.completed_line_physical_fft_size = completed_line_physical_fft_size_;
            result.completed_line_analysis_geometry_mismatches =
                completed_line_analysis_geometry_mismatches_;
        }
        result.source_short_reads = source_short_reads_.load(std::memory_order_relaxed);
        result.source_refill_errors = source_refill_errors_.load(std::memory_order_relaxed);
        result.source_output_pool_exhaustions = source_output_pool_exhaustions_.load(std::memory_order_relaxed);
        result.source_estimated_dropped_samples = source_estimated_dropped_samples_.load(std::memory_order_relaxed);
        result.acquisition_queue_blocks_dropped = acquisition_queue_blocks_dropped_.load(std::memory_order_relaxed);
        result.acquisition_queue_samples_dropped = acquisition_queue_samples_dropped_.load(std::memory_order_relaxed);
        result.source_sequence_discontinuities = source_sequence_discontinuities_.load(std::memory_order_relaxed);
        result.source_sample_index_discontinuities = source_sample_index_discontinuities_.load(std::memory_order_relaxed);
        result.source_timestamp_regressions = source_timestamp_regressions_.load(std::memory_order_relaxed);
        result.source_estimated_timestamp_blocks = source_estimated_timestamp_blocks_.load(std::memory_order_relaxed);
        result.hardware_overflow_counter_available = hardware_overflow_counter_available_.load(std::memory_order_relaxed);
        result.fft_frames_dropped = fft_frames_dropped_.load(std::memory_order_relaxed);
        result.acquisition_queue_high_water = acquisition_queue_high_water_.load(std::memory_order_relaxed);
        result.spectrum_queue_high_water = spectrum_queue_high_water_.load(std::memory_order_relaxed);
        return result;
    }

    [[nodiscard]] std::vector<AppliedConfig> applied_segments() const {
        std::lock_guard lock(applied_segments_mutex_);
        return completed_applied_segments_;
    }

    [[nodiscard]] std::vector<sdr_core::SweepLineFrame> poll_lines(const std::size_t max_items) {
        std::vector<sdr_core::SweepLineFrame> result;
        if (!output_) {
            return result;
        }
        sdr_core::SweepLineFrame line;
        while ((max_items == 0U || result.size() < max_items) && output_->try_pop(line)) {
            result.push_back(std::move(line));
        }
        return result;
    }

    [[nodiscard]] std::optional<sdr_core::SweepProgressFrame> poll_progress() {
        std::lock_guard lock(progress_mutex_);
        auto result = std::move(progress_);
        progress_.reset();
        return result;
    }

    [[nodiscard]] std::size_t discard_lines(const std::size_t max_items) {
        if (!output_) {
            return 0U;
        }
        std::size_t discarded = 0U;
        sdr_core::SweepLineFrame line;
        while ((max_items == 0U || discarded < max_items) && output_->try_pop(line)) {
            ++discarded;
        }
        return discarded;
    }

    void disconnect() noexcept {
        shutdown_noexcept();
        engine_.disconnect();
    }

private:
    void publish(sdr_core::SweepLineFrame line) {
        if (statistics_ && config_.segments.size() > 1) {
            statistics_->consume(line, monotonic_now_ns(), line.state == sdr_core::SweepLineState::Gap);
        }
        {
            std::lock_guard lock(progress_mutex_);
            if (progress_ && progress_->epoch == line.epoch &&
                progress_->line_sequence <= line.line_sequence) progress_.reset();
        }
        if (!output_) {
            return;
        }
        const auto pushed = output_->try_push(std::move(line));
        if (pushed == sdr_core::PushResult::Evicted) {
            output_snapshots_superseded_.fetch_add(1U, std::memory_order_relaxed);
        }
    }

    void publish_terminal_gap(
        const std::uint64_t sequence,
        const sdr_core::SweepLineGapReason reason
    ) noexcept {
        try {
            sdr_core::ContinuousSweepLineAssembler assembler(planned_definition(config_));
            auto gap_sequence = sequence;
            if (statistics_ && config_.segments.size() == 1) {
                // Single-window DSP can be ahead of the coordinator's last
                // drained line. Stop must join that owner before this handoff.
                gap_sequence = std::max(gap_sequence, statistics_->newest_sequence() + 1U);
            }
            auto gap = assembler.emit_gap(gap_sequence, monotonic_now_ns(), reason);
            if (statistics_ && config_.segments.size() == 1) {
                statistics_->consume(gap, monotonic_now_ns(), true);
            }
            publish(std::move(gap));
            gapped_lines_.fetch_add(1U, std::memory_order_relaxed);
            terminal_control_gaps_.fetch_add(1U, std::memory_order_relaxed);
        } catch (...) {
            has_error_.store(true, std::memory_order_relaxed);
        }
    }

    [[nodiscard]] std::optional<sdr_core::SpectrumFrame> wait_for_current_frame(
        const std::uint64_t generation
    ) {
        const auto deadline = std::chrono::steady_clock::now() +
            std::chrono::milliseconds(config_.segment_frame_timeout_ms);
        while (!stop_requested_.load(std::memory_order_acquire) &&
               std::chrono::steady_clock::now() < deadline) {
            auto frames = engine_.poll_spectrum_frames(1U);
            if (!frames.empty() && frames.front().config_generation == generation) {
                return std::move(frames.front());
            }
            // A one-millisecond idle sleep would itself cap a continuous
            // single-window line stream at roughly 1 kLPS, regardless of
            // native DSP throughput.  This is only the coordinator's native
            // reduced-frame wait; it neither polls Python nor changes the
            // bounded UI render cadence.
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
        return std::nullopt;
    }

    void apply_segment(const FixedBandConfig& config) {
        const auto current = engine_.state();
        if (current == sdr_core::EngineState::Running) {
            engine_.stop();
        } else if (current != sdr_core::EngineState::Created && current != sdr_core::EngineState::Stopped &&
                   current != sdr_core::EngineState::Configured) {
            invalid("fixed-band engine entered an unusable state during continuous sweep");
        }
        if (stop_requested_.load(std::memory_order_acquire)) return;
        // Do not use the convenience reconfigure() here: it automatically
        // resumes RX even if the coordinator accepted Stop during readback.
        static_cast<void>(engine_.configure(config));
        segment_reconfigurations_.fetch_add(1U, std::memory_order_relaxed);
        if (!stop_requested_.load(std::memory_order_acquire)) engine_.start();
    }

    [[nodiscard]] FixedBandConfig single_window_fixed_config() const {
        FixedBandConfig result = config_.segments.front().fixed_band;
        // This is an internal coordinator-owned line path, not a second
        // public producer.  It converts each native post-DSP SpectrumFrame
        // into the already bounded reduced line while the RF configuration is
        // stable, so a 4k/16k acquisition block cannot artificially define
        // the visible scan cadence.
        result.continuous_sweep_line = ContinuousSweepLineConfig{
            .enabled = true,
            .epoch = config_.epoch,
            .display_start_hz = config_.display_start_hz,
            .display_stop_hz = config_.display_stop_hz,
            .usable_window_hz = config_.usable_window_hz > 0.0
                ? config_.usable_window_hz
                : config_.display_stop_hz - config_.display_start_hz,
            .output_queue_capacity = config_.output_queue_capacity,
            .analysis_bins_per_usable_window = config_.analysis_bins_per_usable_window,
            .line_snapshot_rate_hz = config_.line_snapshot_rate_hz > 0.0
                ? config_.line_snapshot_rate_hz
                : result.snapshot_rate_hz,
        };
        result.sweep_statistics_sink = statistics_;
        return result;
    }

    void run_single_window() {
        static_cast<void>(engine_.configure(single_window_fixed_config()));
        segment_reconfigurations_.fetch_add(1U, std::memory_order_relaxed);
        const auto applied = engine_.applied_config();
        {
            std::lock_guard applied_lock(applied_segments_mutex_);
            current_applied_segments_ = {applied};
        }
        if (!stop_requested_.load(std::memory_order_acquire)) engine_.start();

        std::uint64_t next_line_sequence = 1U;
        while (!stop_requested_.load(std::memory_order_acquire)) {
            auto lines = engine_.poll_sweep_line_frames(0U);
            account_accepted_segment_metrics(applied.config_generation);
            if (lines.empty()) {
                const auto engine_metrics = engine_.metrics();
                if (engine_metrics.has_error ||
                    engine_metrics.state == sdr_core::EngineState::Error) {
                    throw std::runtime_error("fixed-band single-window line producer failed");
                }
                std::this_thread::sleep_for(std::chrono::microseconds(100));
                continue;
            }
            for (auto& line : lines) {
                if (stop_requested_.load(std::memory_order_acquire)) {
                    break;
                }
                if (line.state != sdr_core::SweepLineState::Complete ||
                    line.segment_generations.size() != 1U ||
                    line.segment_generations.front().config_generation != applied.config_generation ||
                    line.start_frequency_hz != config_.display_start_hz ||
                    line.stop_frequency_hz != config_.display_stop_hz) {
                    throw std::runtime_error("fixed-band single-window line provenance/geometry differs");
                }
                record_completed_current_generation_fft_frame(0U);
                {
                    std::lock_guard applied_lock(applied_segments_mutex_);
                    completed_applied_segments_ = current_applied_segments_;
                }
                next_line_sequence = std::max(next_line_sequence, line.line_sequence + 1U);
                record_completed_line_analysis_geometry(line);
                publish(std::move(line));
                completed_lines_.fetch_add(1U, std::memory_order_relaxed);
            }
        }
        if (engine_.state() == sdr_core::EngineState::Running) {
            engine_.stop();
        }
        publish_terminal_gap(next_line_sequence, sdr_core::SweepLineGapReason::Cancellation);
        if (expected_stop_requests_.load(std::memory_order_relaxed) != 0U) {
            expected_cancellations_.fetch_add(1U, std::memory_order_relaxed);
        }
        state_.store(sdr_core::EngineState::Stopped, std::memory_order_release);
    }

    static void update_max(std::atomic<std::uint32_t>& target, const std::uint32_t value) noexcept {
        auto previous = target.load(std::memory_order_relaxed);
        while (previous < value && !target.compare_exchange_weak(
            previous, value, std::memory_order_relaxed, std::memory_order_relaxed
        )) {
        }
    }

    [[nodiscard]] static std::uint64_t counter_delta(
        const std::uint64_t current,
        const std::uint64_t previous
    ) noexcept {
        // A counter regression is never reinterpreted as a giant unsigned
        // delta. The native engine resets only on a new configuration
        // generation, which is handled explicitly by the caller.
        return current >= previous ? current - previous : 0U;
    }

    // A reconfigure resets FixedBandEngine counters, while a single-window
    // line intentionally keeps one engine running. Snapshot deltas support
    // both paths without double-counting a continuous one-window stream.
    void account_accepted_segment_metrics(const std::uint64_t generation) noexcept {
        const auto metrics = engine_.metrics();
        const auto previous = last_accounted_metrics_.has_value() &&
            last_accounted_generation_ == generation
            ? &*last_accounted_metrics_ : nullptr;
        const auto delta = [previous](const std::uint64_t value, const std::uint64_t prior) noexcept {
            return previous == nullptr ? value : counter_delta(value, prior);
        };
        device_iq_samples_.fetch_add(
            delta(metrics.device.samples_received, previous ? previous->device.samples_received : 0U),
            std::memory_order_relaxed
        );
        device_iq_blocks_.fetch_add(
            delta(metrics.engine.iq_blocks_received, previous ? previous->engine.iq_blocks_received : 0U),
            std::memory_order_relaxed
        );
        analytical_fft_frames_.fetch_add(
            delta(metrics.engine.fft_frames_computed, previous ? previous->engine.fft_frames_computed : 0U),
            std::memory_order_relaxed
        );
        line_relay_snapshots_superseded_.fetch_add(
            delta(
                metrics.sweep_line_snapshots_superseded,
                previous ? previous->sweep_line_snapshots_superseded : 0U
            ),
            std::memory_order_relaxed
        );
        line_relay_queue_capacity_.store(
            metrics.sweep_line_queue.capacity, std::memory_order_relaxed
        );
        update_max(line_relay_queue_high_water_, metrics.sweep_line_queue.high_water);
        source_short_reads_.fetch_add(
            delta(metrics.device.short_reads, previous ? previous->device.short_reads : 0U),
            std::memory_order_relaxed
        );
        source_refill_errors_.fetch_add(
            delta(metrics.device.refill_errors, previous ? previous->device.refill_errors : 0U),
            std::memory_order_relaxed
        );
        source_output_pool_exhaustions_.fetch_add(
            delta(
                metrics.device.output_pool_exhaustions,
                previous ? previous->device.output_pool_exhaustions : 0U
            ),
            std::memory_order_relaxed
        );
        source_estimated_dropped_samples_.fetch_add(
            delta(
                metrics.device.estimated_dropped_samples,
                previous ? previous->device.estimated_dropped_samples : 0U
            ),
            std::memory_order_relaxed
        );
        acquisition_queue_blocks_dropped_.fetch_add(
            delta(
                metrics.acquisition_queue_blocks_dropped,
                previous ? previous->acquisition_queue_blocks_dropped : 0U
            ),
            std::memory_order_relaxed
        );
        acquisition_queue_samples_dropped_.fetch_add(
            delta(
                metrics.acquisition_queue_samples_dropped,
                previous ? previous->acquisition_queue_samples_dropped : 0U
            ),
            std::memory_order_relaxed
        );
        source_sequence_discontinuities_.fetch_add(
            delta(metrics.source_sequence_discontinuities, previous ? previous->source_sequence_discontinuities : 0U),
            std::memory_order_relaxed
        );
        source_sample_index_discontinuities_.fetch_add(
            delta(metrics.source_sample_index_discontinuities, previous ? previous->source_sample_index_discontinuities : 0U),
            std::memory_order_relaxed
        );
        source_timestamp_regressions_.fetch_add(
            delta(metrics.source_timestamp_regressions, previous ? previous->source_timestamp_regressions : 0U),
            std::memory_order_relaxed
        );
        source_estimated_timestamp_blocks_.fetch_add(
            delta(metrics.source_estimated_timestamp_blocks, previous ? previous->source_estimated_timestamp_blocks : 0U),
            std::memory_order_relaxed
        );
        hardware_overflow_counter_available_.store(
            metrics.hardware_overflow_counter_available,
            std::memory_order_relaxed
        );
        fft_frames_dropped_.fetch_add(
            delta(
                metrics.engine.fft_frames_dropped,
                previous ? previous->engine.fft_frames_dropped : 0U
            ),
            std::memory_order_relaxed
        );
        update_max(acquisition_queue_high_water_, metrics.acquisition_queue.high_water);
        update_max(spectrum_queue_high_water_, metrics.spectrum_queue.high_water);
        last_accounted_metrics_ = metrics;
        last_accounted_generation_ = generation;
    }

    void record_completed_current_generation_fft_frame(const std::size_t segment_index) noexcept {
        std::lock_guard segment_metrics_lock(segment_frame_metrics_mutex_);
        if (segment_index >= completed_current_generation_fft_frames_.size()) {
            has_error_.store(true, std::memory_order_relaxed);
            return;
        }
        ++completed_current_generation_fft_frames_[segment_index];
    }

    void record_completed_line_analysis_geometry(const sdr_core::SweepLineFrame& line) noexcept {
        std::lock_guard segment_metrics_lock(segment_frame_metrics_mutex_);
        const auto valid = line.analysis_window_hz > 0.0 &&
            line.analysis_bins_per_usable_window != 0U &&
            line.physical_fft_bin_width_hz > 0.0 && line.physical_fft_size != 0U;
        if (!valid) {
            ++completed_line_analysis_geometry_mismatches_;
            return;
        }
        if (!completed_line_analysis_geometry_available_) {
            completed_line_analysis_geometry_available_ = true;
            completed_line_analysis_window_hz_ = line.analysis_window_hz;
            completed_line_analysis_bins_per_usable_window_ = line.analysis_bins_per_usable_window;
            completed_line_physical_fft_bin_width_hz_ = line.physical_fft_bin_width_hz;
            completed_line_physical_fft_size_ = line.physical_fft_size;
            return;
        }
        if (std::abs(completed_line_analysis_window_hz_ - line.analysis_window_hz) > 1e-6 ||
            completed_line_analysis_bins_per_usable_window_ != line.analysis_bins_per_usable_window ||
            std::abs(completed_line_physical_fft_bin_width_hz_ - line.physical_fft_bin_width_hz) > 1e-6 ||
            completed_line_physical_fft_size_ != line.physical_fft_size) {
            ++completed_line_analysis_geometry_mismatches_;
        }
    }

    void run() noexcept {
        std::uint64_t line_sequence = 1U;
        try {
            if (stop_requested_.load(std::memory_order_acquire)) {
                publish_terminal_gap(line_sequence, sdr_core::SweepLineGapReason::Cancellation);
                expected_cancellations_.fetch_add(1U, std::memory_order_relaxed);
                state_.store(sdr_core::EngineState::Stopped, std::memory_order_release);
                return;
            }
            if (config_.segments.size() == 1U) {
                run_single_window();
                return;
            }
            bool fatal_failure = false;
            std::optional<AppliedConfig> single_window_applied;
            while (!stop_requested_.load(std::memory_order_acquire)) {
                std::vector<sdr_core::SpectrumFrame> frames;
                frames.reserve(config_.segments.size());
                auto last_preview_at = std::chrono::steady_clock::time_point::min();
                std::unique_ptr<sdr_core::ContinuousSweepLineAssembler> assembler;
                std::vector<sdr_core::SweepLineFrame> lines;
                {
                    std::lock_guard applied_lock(applied_segments_mutex_);
                    current_applied_segments_.clear();
                }
                auto gap_reason = sdr_core::SweepLineGapReason::MissingSegment;
                bool terminal = false;
                for (std::size_t segment_index = 0U; segment_index < config_.segments.size(); ++segment_index) {
                    const auto& segment = config_.segments[segment_index];
                    if (stop_requested_.load(std::memory_order_acquire)) {
                        gap_reason = sdr_core::SweepLineGapReason::Cancellation;
                        terminal = true;
                        break;
                    }
                    AppliedConfig applied;
                    if (config_.segments.size() == 1U && single_window_applied.has_value()) {
                        // A display span wholly contained in one usable window
                        // is continuous RTBW-style acquisition. Retuning every
                        // completed line would manufacture the very latency the
                        // max-rate comparator is meant to measure.
                        applied = *single_window_applied;
                    } else {
                        try {
                            apply_segment(segment.fixed_band);
                        } catch (...) {
                            gap_reason = engine_.connected()
                                ? sdr_core::SweepLineGapReason::Reconfigure
                                : sdr_core::SweepLineGapReason::Disconnect;
                            terminal = true;
                            break;
                        }
                        if (stop_requested_.load(std::memory_order_acquire)) {
                            gap_reason = sdr_core::SweepLineGapReason::Cancellation;
                            terminal = true;
                            break;
                        }
                        applied = engine_.applied_config();
                        if (config_.segments.size() == 1U) {
                            single_window_applied = applied;
                        }
                        {
                            std::lock_guard applied_lock(applied_segments_mutex_);
                            if (current_applied_segments_.size() <= segment_index) {
                                current_applied_segments_.resize(segment_index + 1U);
                            }
                            current_applied_segments_[segment_index] = applied;
                        }
                    }
                    const auto frame = wait_for_current_frame(applied.config_generation);
                    if (stop_requested_.load(std::memory_order_acquire)) {
                        gap_reason = sdr_core::SweepLineGapReason::Cancellation;
                        terminal = true;
                        break;
                    }
                    if (!frame.has_value()) {
                        segment_frame_timeouts_.fetch_add(1U, std::memory_order_relaxed);
                        terminal = true;
                        break;
                    }
                    if (!covers_usable_range(*frame, segment)) {
                        gap_reason = sdr_core::SweepLineGapReason::Reconfigure;
                        terminal = true;
                        break;
                    }
                    record_completed_current_generation_fft_frame(segment_index);
                    account_accepted_segment_metrics(applied.config_generation);
                    frames.push_back(std::move(*frame));
                    if (!assembler) {
                        assembler = std::make_unique<sdr_core::ContinuousSweepLineAssembler>(
                            applied_definition(config_, frames));
                    } else {
                        const auto& first = frames.front();
                        const auto& current = frames.back();
                        if (current.source.source_id != first.source.source_id ||
                            current.source.source_type != first.source.source_type ||
                            current.unit != first.unit ||
                            std::abs(current.fft_bin_width_hz - first.fft_bin_width_hz) > 1e-6 ||
                            current.fft_size != first.fft_size || current.config_generation == 0) {
                            gap_reason = sdr_core::SweepLineGapReason::Reconfigure;
                            terminal = true;
                            break;
                        }
                        assembler->bind_segment_generation(
                            static_cast<std::uint32_t>(segment_index), current.config_generation);
                    }
                    auto emitted = assembler->admit(line_sequence, frames.back().timestamp_ns,
                        {.segment_index = static_cast<std::uint32_t>(segment_index),
                         .spectrum = frames.back()});
                    lines.insert(lines.end(), std::make_move_iterator(emitted.begin()),
                                 std::make_move_iterator(emitted.end()));
                    const auto preview_now = std::chrono::steady_clock::now();
                    const bool publish_preview = segment_index == 0 ||
                        preview_now - last_preview_at >= std::chrono::milliseconds(33);
                    if (lines.empty() && (statistics_ || publish_preview)) {
                        auto preview = assembler->preview(line_sequence);
                        if (statistics_ && preview) statistics_->consume(*preview, monotonic_now_ns());
                        if (publish_preview) {
                            std::lock_guard lock(progress_mutex_);
                            progress_ = std::move(preview);
                            last_preview_at = preview_now;
                        }
                    }
                }
                if (terminal) {
                    if (assembler && assembler->metrics().pending_lines != 0) {
                        for (auto& partial : assembler->flush(gap_reason)) {
                            publish(std::move(partial));
                            gapped_lines_.fetch_add(1U, std::memory_order_relaxed);
                            terminal_control_gaps_.fetch_add(1U, std::memory_order_relaxed);
                        }
                        ++line_sequence;
                    } else {
                        publish_terminal_gap(line_sequence++, gap_reason);
                    }
                    if (gap_reason == sdr_core::SweepLineGapReason::Cancellation) {
                        if (expected_stop_requests_.load(std::memory_order_relaxed) != 0U) {
                            expected_cancellations_.fetch_add(1U, std::memory_order_relaxed);
                        }
                        break;
                    }
                    // A failed control transition or lost device must latch;
                    // looping a reconfigure failure would be a hidden
                    // unbounded retry, not a continuous sweep.
                    if (gap_reason == sdr_core::SweepLineGapReason::Reconfigure ||
                        gap_reason == sdr_core::SweepLineGapReason::Disconnect) {
                        has_error_.store(true, std::memory_order_relaxed);
                        fatal_failure = true;
                        break;
                    }
                    continue;
                }
                if (lines.size() != 1U || lines.front().state != sdr_core::SweepLineState::Complete) {
                    throw std::runtime_error("continuous sweep coordinator did not finalise one complete line");
                }
                {
                    std::lock_guard applied_lock(applied_segments_mutex_);
                    completed_applied_segments_ = current_applied_segments_;
                }
                record_completed_line_analysis_geometry(lines.front());
                publish(std::move(lines.front()));
                completed_lines_.fetch_add(1U, std::memory_order_relaxed);
                ++line_sequence;
            }
            if (engine_.state() == sdr_core::EngineState::Running) {
                engine_.stop();
            }
            // Stop may arrive after a complete pass, before the next outer
            // iteration. It still owns exactly one explicit epoch boundary.
            if (stop_requested_.load(std::memory_order_acquire) &&
                expected_cancellations_.load(std::memory_order_relaxed) == 0U && !fatal_failure) {
                publish_terminal_gap(line_sequence, sdr_core::SweepLineGapReason::Cancellation);
                expected_cancellations_.fetch_add(1U, std::memory_order_relaxed);
            }
            state_.store(
                fatal_failure ? sdr_core::EngineState::Error : sdr_core::EngineState::Stopped,
                std::memory_order_release
            );
        } catch (...) {
            has_error_.store(true, std::memory_order_relaxed);
            try {
                if (engine_.state() == sdr_core::EngineState::Running) {
                    engine_.stop();
                } else if (engine_.state() == sdr_core::EngineState::Stopping ||
                           engine_.state() == sdr_core::EngineState::Error) {
                    engine_.join();
                }
            } catch (...) {
            }
            // Stop/join the one-window DSP statistics writer before emitting
            // the coordinator-owned terminal gap into the same accumulator.
            publish_terminal_gap(
                line_sequence,
                engine_.connected() ? sdr_core::SweepLineGapReason::Reconfigure : sdr_core::SweepLineGapReason::Disconnect
            );
            state_.store(sdr_core::EngineState::Error, std::memory_order_release);
        }
    }

    void shutdown_noexcept() noexcept {
        try {
            const auto current = state();
            if (current == sdr_core::EngineState::Running || current == sdr_core::EngineState::Stopping) {
                stop_requested_.store(true, std::memory_order_release);
            }
            if (worker_.joinable()) {
                worker_.join();
            }
            if (engine_.state() == sdr_core::EngineState::Running) {
                engine_.stop();
            }
        } catch (...) {
        }
    }

    std::string uri_;
    FixedBandEngine engine_;
    ContinuousSweepCoordinatorConfig config_;
    std::shared_ptr<sdr_core::SweepStatisticsPublisher> statistics_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::SweepLineFrame>> output_;
    std::mutex progress_mutex_;
    std::optional<sdr_core::SweepProgressFrame> progress_;
    std::atomic<sdr_core::EngineState> state_{sdr_core::EngineState::Created};
    std::atomic<bool> stop_requested_{};
    std::atomic<bool> has_error_{};
    std::atomic<std::uint64_t> completed_lines_{};
    std::atomic<std::uint64_t> gapped_lines_{};
    std::atomic<std::uint64_t> line_relay_snapshots_superseded_{};
    std::atomic<std::uint32_t> line_relay_queue_capacity_{};
    std::atomic<std::uint32_t> line_relay_queue_high_water_{};
    std::atomic<std::uint64_t> output_snapshots_superseded_{};
    std::atomic<std::uint64_t> segment_reconfigurations_{};
    std::atomic<std::uint64_t> segment_frame_timeouts_{};
    std::atomic<std::uint64_t> terminal_control_gaps_{};
    std::atomic<std::uint64_t> expected_stop_requests_{};
    std::atomic<std::uint64_t> expected_cancellations_{};
    std::optional<FixedBandMetrics> last_accounted_metrics_;
    std::uint64_t last_accounted_generation_{};
    std::atomic<std::uint64_t> device_iq_samples_{};
    std::atomic<std::uint64_t> device_iq_blocks_{};
    std::atomic<std::uint64_t> analytical_fft_frames_{};
    mutable std::mutex segment_frame_metrics_mutex_;
    std::vector<std::uint64_t> completed_current_generation_fft_frames_;
    bool completed_line_analysis_geometry_available_{};
    double completed_line_analysis_window_hz_{};
    std::uint32_t completed_line_analysis_bins_per_usable_window_{};
    double completed_line_physical_fft_bin_width_hz_{};
    std::uint32_t completed_line_physical_fft_size_{};
    std::uint64_t completed_line_analysis_geometry_mismatches_{};
    std::atomic<std::uint64_t> source_short_reads_{};
    std::atomic<std::uint64_t> source_refill_errors_{};
    std::atomic<std::uint64_t> source_output_pool_exhaustions_{};
    std::atomic<std::uint64_t> source_estimated_dropped_samples_{};
    std::atomic<std::uint64_t> acquisition_queue_blocks_dropped_{};
    std::atomic<std::uint64_t> acquisition_queue_samples_dropped_{};
    std::atomic<std::uint64_t> source_sequence_discontinuities_{};
    std::atomic<std::uint64_t> source_sample_index_discontinuities_{};
    std::atomic<std::uint64_t> source_timestamp_regressions_{};
    std::atomic<std::uint64_t> source_estimated_timestamp_blocks_{};
    std::atomic<bool> hardware_overflow_counter_available_{};
    std::atomic<std::uint64_t> fft_frames_dropped_{};
    std::atomic<std::uint32_t> acquisition_queue_high_water_{};
    std::atomic<std::uint32_t> spectrum_queue_high_water_{};
    mutable std::mutex applied_segments_mutex_;
    std::vector<AppliedConfig> current_applied_segments_;
    std::vector<AppliedConfig> completed_applied_segments_;
    std::mutex lifecycle_mutex_;
    std::thread worker_;
};

ContinuousSweepCoordinator::ContinuousSweepCoordinator(std::string uri, const std::uint32_t timeout_ms)
    : impl_(std::make_unique<Impl>(std::move(uri), timeout_ms)) {}

ContinuousSweepCoordinator::~ContinuousSweepCoordinator() noexcept = default;
void ContinuousSweepCoordinator::configure(ContinuousSweepCoordinatorConfig config) { impl_->configure(std::move(config)); }
void ContinuousSweepCoordinator::start() { impl_->start(); }
void ContinuousSweepCoordinator::request_stop() { impl_->request_stop(); }
void ContinuousSweepCoordinator::join() { impl_->join(); }
void ContinuousSweepCoordinator::stop() { impl_->stop(); }
void ContinuousSweepCoordinator::disconnect() noexcept { impl_->disconnect(); }
sdr_core::EngineState ContinuousSweepCoordinator::state() const noexcept { return impl_->state(); }
ContinuousSweepCoordinatorMetrics ContinuousSweepCoordinator::metrics() const { return impl_->metrics(); }
std::vector<AppliedConfig> ContinuousSweepCoordinator::applied_segments() const {
    return impl_->applied_segments();
}
std::vector<sdr_core::SweepLineFrame> ContinuousSweepCoordinator::poll_lines(const std::size_t max_items) {
    return impl_->poll_lines(max_items);
}

std::optional<sdr_core::SweepProgressFrame> ContinuousSweepCoordinator::poll_progress() {
    return impl_->poll_progress();
}
std::size_t ContinuousSweepCoordinator::discard_lines(const std::size_t max_items) {
    return impl_->discard_lines(max_items);
}

}  // namespace sdr_pluto

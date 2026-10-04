#include "sdr_pluto/fixed_band_engine.hpp"

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/recording_writer.hpp"
#include "sdr_core/stop_token.hpp"
#include "sdr_core/sweep_line_assembler.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <climits>
#include <cmath>
#include <exception>
#include <filesystem>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <thread>
#include <utility>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace sdr_pluto {

namespace {

#ifndef SDR_CORE_PROFILING_ENABLED
#define SDR_CORE_PROFILING_ENABLED 0
#endif

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

// A libiio refill can yield many ready, overlapping SpectrumFrames.  The
// native continuous-line relay retains this finite post-DSP burst until the
// coordinator drains it.  It is intentionally distinct from the coordinator's
// small final latest-wins UI/presentation queue.
[[nodiscard]] std::uint32_t continuous_sweep_line_relay_capacity(
    const FixedBandConfig& config
) {
    return std::max(
        config.continuous_sweep_line->output_queue_capacity,
        dsp_output_capacity(config)
    );
}

#if SDR_CORE_PROFILING_ENABLED
void add_profile_elapsed_ms(
    std::atomic<double>& counter,
    const std::chrono::steady_clock::time_point started
) noexcept {
    const double elapsed_ms =
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    double current = counter.load(std::memory_order_relaxed);
    while (!counter.compare_exchange_weak(
        current,
        current + elapsed_ms,
        std::memory_order_relaxed
    )) {
    }
}
#endif

constexpr std::uint64_t mebibyte = 1024U * 1024U;
constexpr std::uint64_t max_iq_pool_bytes = 128U * mebibyte;
constexpr std::uint64_t max_dsp_working_bytes = 128U * mebibyte;
constexpr std::uint64_t max_spectrum_backlog_bytes = 128U * mebibyte;
constexpr std::uint64_t max_sweep_line_backlog_bytes = 128U * mebibyte;
constexpr std::uint64_t max_persistence_bytes = 256U * mebibyte;
constexpr std::uint64_t max_total_bytes = 512U * mebibyte;

[[nodiscard]] std::uint64_t checked_multiply(
    const std::uint64_t left,
    const std::uint64_t right,
    const char* label
) {
    if (left != 0U && right > std::numeric_limits<std::uint64_t>::max() / left) {
        invalid(std::string(label) + " exceeds resource accounting range");
    }
    return left * right;
}

[[nodiscard]] std::uint64_t checked_add(
    const std::uint64_t left,
    const std::uint64_t right,
    const char* label
) {
    if (right > std::numeric_limits<std::uint64_t>::max() - left) {
        invalid(std::string(label) + " exceeds resource accounting range");
    }
    return left + right;
}

[[nodiscard]] std::uint64_t sweep_line_bin_count(const FixedBandConfig& config) {
    const auto& profile = *config.continuous_sweep_line;
    const auto span_hz = profile.display_stop_hz - profile.display_start_hz;
    const auto requested_spacing_hz = profile.analysis_bins_per_usable_window == 0U
        ? config.device.sample_rate_hz / static_cast<double>(config.dsp.fft_size)
        : profile.usable_window_hz /
            static_cast<double>(profile.analysis_bins_per_usable_window);
    if (!std::isfinite(span_hz) || !std::isfinite(requested_spacing_hz) ||
        span_hz <= 0.0 || requested_spacing_hz <= 0.0) {
        invalid("continuous sweep line preflight geometry is invalid");
    }
    const auto count = profile.analysis_bins_per_usable_window == 0U
        ? std::floor(span_hz / requested_spacing_hz) + 1.0
        : std::ceil(span_hz / requested_spacing_hz - 1e-12);
    if (!std::isfinite(count) || count < 2.0 ||
        count > static_cast<double>(std::numeric_limits<std::uint32_t>::max())) {
        invalid("continuous sweep line preflight bin count is invalid");
    }
    return static_cast<std::uint64_t>(count);
}

[[nodiscard]] bool native_iq_recording_enabled(const FixedBandConfig& config) {
    return config.recording.enabled && config.recording.record_iq;
}

[[nodiscard]] bool native_spectrum_recording_enabled(const FixedBandConfig& config) {
    return config.recording.enabled && config.recording.record_spectrum;
}

[[nodiscard]] bool recorder_enabled(const FixedBandConfig& config) {
    return config.recorder_enabled || native_iq_recording_enabled(config);
}

[[nodiscard]] std::uint32_t recorder_queue_capacity(
    const FixedBandConfig& config
) {
    return native_iq_recording_enabled(config)
               ? config.recording.queue_capacity
               : config.recorder_queue_capacity;
}

struct LiveResourceBudget {
    std::uint64_t iq{}, dsp{}, spectrum{}, persistence{}, sweep{}, total{};
};
[[nodiscard]] LiveResourceBudget validate_resource_budget(const FixedBandConfig& config) {
    const auto retained_iq_blocks = checked_add(
        static_cast<std::uint64_t>(config.acquisition_queue_capacity) + 3U,
        recorder_enabled(config)
            ? static_cast<std::uint64_t>(recorder_queue_capacity(config))
            : 0U,
        "I/Q pool"
    );
    const auto iq_pool_bytes = checked_multiply(
        checked_multiply(config.device.buffer_samples, 4U, "I/Q pool"),
        std::max<std::uint64_t>(8U, retained_iq_blocks),
        "I/Q pool"
    );
    const auto dsp_working_bytes = checked_multiply(
        config.dsp.fft_size,
        116U + 48U * static_cast<std::uint64_t>(config.dsp.batch_size),
        "DSP working set"
    );
    auto spectrum_backlog_bytes = checked_multiply(
        checked_multiply(dsp_output_capacity(config), config.dsp.fft_size, "spectrum backlog"),
        16U,
        "spectrum backlog"
    );
    spectrum_backlog_bytes = checked_add(spectrum_backlog_bytes,
        sdr_core::analytical_ready_reserved_bytes(config.analytical_event_capacity),
        "analytical journal and drain");
    spectrum_backlog_bytes = checked_add(spectrum_backlog_bytes,
        checked_multiply(checked_add(
                             2U * static_cast<std::uint64_t>(dsp_output_capacity(config)),
                             static_cast<std::uint64_t>(config.spectrum_queue_capacity) + 1U +
                                 (native_spectrum_recording_enabled(config)
                                      ? config.recording.queue_capacity : 0U),
                             "analytical receipt slots"),
                         sizeof(std::optional<sdr_core::AnalyticalReadyRef>),
                         "analytical receipt backlog"), "spectrum backlog");
    if (native_spectrum_recording_enabled(config)) {
        spectrum_backlog_bytes = checked_add(
            spectrum_backlog_bytes,
            checked_multiply(
                checked_multiply(
                    config.recording.queue_capacity,
                    config.dsp.fft_size,
                    "spectrum recorder backlog"
                ),
                16U,
                "spectrum recorder backlog"
            ),
            "spectrum backlog"
        );
    }
    std::uint64_t persistence_state_bytes = 0U;
    std::uint64_t persistence_snapshot_bytes = 0U;
    if (config.persistence.enabled) {
        const auto cells = checked_multiply(
            config.dsp.fft_size,
            config.persistence.power_bins,
            "persistence"
        );
        // The native histogram is float32.  Rolling exact keeps its bounded
        // uint32 row history separately; exponential mode keeps only a
        // scalar epoch/normalization state and never materializes a second
        // full grid per analytical FFT.
        persistence_state_bytes = checked_multiply(cells, 4U, "persistence density");
        if (config.persistence.mode == sdr_core::PersistenceMode::RollingExact) {
            persistence_state_bytes = checked_add(
                persistence_state_bytes,
                checked_multiply(
                    checked_multiply(
                        config.persistence.window_frames,
                        config.dsp.fft_size,
                        "persistence rolling window"
                    ),
                    4U,
                    "persistence rolling window"
                ),
                "persistence state"
            );
        }
        // A constructing snapshot plus the two-element latest-wins queue
        // retain three float32 grids.  Frequency axes are shared, not copied,
        // but each retained snapshot may keep a distinct source axis alive.
        persistence_snapshot_bytes = checked_add(
            checked_multiply(cells, 12U, "persistence snapshots"),
            checked_multiply(config.dsp.fft_size, 24U, "persistence snapshot axes"),
            "persistence snapshots"
        );
    }
    const auto persistence_bytes = checked_add(
        persistence_state_bytes,
        persistence_snapshot_bytes,
        "persistence"
    );
    std::uint64_t sweep_line_bytes = 0U;
    if (config.continuous_sweep_line.has_value() &&
        config.continuous_sweep_line->enabled) {
        // frequency f64 + value f32 + quality u32 + source segment i32.
        // Budget the bounded relay independently from the final UI queue.
        sweep_line_bytes = checked_multiply(
            checked_multiply(
                sweep_line_bin_count(config),
                20U,
                "continuous sweep line"
            ),
            static_cast<std::uint64_t>(
                continuous_sweep_line_relay_capacity(config) + 2U
            ),
            "continuous sweep line"
        );
    }
    auto total_bytes = checked_add(iq_pool_bytes, dsp_working_bytes, "live-engine memory");
    total_bytes = checked_add(total_bytes, spectrum_backlog_bytes, "live-engine memory");
    total_bytes = checked_add(total_bytes, persistence_bytes, "live-engine memory");
    total_bytes = checked_add(total_bytes, sweep_line_bytes, "live-engine memory");
    if (config.sweep_statistics_sink) {
        total_bytes = checked_add(total_bytes, config.sweep_statistics_sink->payload_bytes(),
                                  "native Sweep statistics");
    }
    if (iq_pool_bytes > max_iq_pool_bytes ||
        dsp_working_bytes > max_dsp_working_bytes ||
        spectrum_backlog_bytes > max_spectrum_backlog_bytes ||
        sweep_line_bytes > max_sweep_line_backlog_bytes ||
        persistence_bytes > max_persistence_bytes ||
        total_bytes > max_total_bytes) {
        invalid("fixed-band configuration exceeds bounded live-engine memory budget");
    }
    return {iq_pool_bytes, dsp_working_bytes, spectrum_backlog_bytes,
            persistence_bytes, sweep_line_bytes, total_bytes};
}

}  // namespace

void validate(const ContinuousSweepLineConfig& value) {
    if (!value.enabled) {
        return;
    }
    if (!std::isfinite(value.display_start_hz) ||
        !std::isfinite(value.display_stop_hz) ||
        !std::isfinite(value.usable_window_hz) ||
        value.display_start_hz <= 0.0 ||
        value.display_stop_hz <= value.display_start_hz ||
        value.usable_window_hz <= 0.0) {
        invalid("continuous sweep profile requires a finite increasing display span and usable window");
    }
    if (value.display_stop_hz - value.display_start_hz > value.usable_window_hz) {
        invalid("continuous sweep display span exceeds its declared usable window");
    }
    if (value.output_queue_capacity == 0U || value.output_queue_capacity > 64U) {
        invalid("continuous sweep output queue capacity must be in [1, 64]");
    }
    if (value.analysis_bins_per_usable_window != 0U &&
        (value.analysis_bins_per_usable_window < 256U ||
         value.analysis_bins_per_usable_window > 262'144U ||
         (value.analysis_bins_per_usable_window &
          (value.analysis_bins_per_usable_window - 1U)) != 0U)) {
        invalid("continuous sweep analysis bins per usable window must be a power of two in [256, 262144]");
    }
    if (!std::isfinite(value.line_snapshot_rate_hz) ||
        value.line_snapshot_rate_hz < 1.0 || value.line_snapshot_rate_hz > 2000.0) {
        invalid("continuous sweep line snapshot rate must be in [1, 2000] Hz");
    }
}

void validate(const FixedBandConfig& value) {
    if (value.receiver_selection != ReceiverSelection::Rx1 &&
        value.receiver_selection != ReceiverSelection::Rx2) {
        invalid("single-producer fixed-band engine requires RX1 or RX2; BOTH needs a paired group owner");
    }
    if (value.schema_version != sdr_core::contract_schema_version) {
        invalid("unsupported fixed-band schema_version");
    }
    sdr_core::validate(value.device);
    sdr_core::validate(value.dsp);
    sdr_core::validate(value.persistence);
    sdr_core::validate(value.recording);
    if (value.sweep_statistics_sink && (!value.continuous_sweep_line ||
        !value.continuous_sweep_line->enabled)) {
        invalid("native Sweep statistics sink requires the coordinator-owned line path");
    }
    if (value.continuous_sweep_line.has_value()) {
        validate(*value.continuous_sweep_line);
        if (value.continuous_sweep_line->enabled && value.recording.record_iq) {
            invalid("continuous sweep profile cannot enable native raw I/Q recording");
        }
    }
    if (value.acquisition_queue_capacity >
        std::numeric_limits<std::uint32_t>::max() - 3U) {
        invalid("acquisition_queue_capacity exceeds Pluto pool bound" );
    }
    if (value.acquisition_queue_capacity == 0U) {
        invalid("acquisition_queue_capacity must be positive");
    }
    if (value.recorder_queue_capacity == 0U) {
        invalid("recorder_queue_capacity must be positive");
    }
    if (recorder_enabled(value) &&
        value.recorder_overflow == sdr_core::OverflowPolicy::Block) {
        invalid("fixed-band recorder must use a non-blocking overflow policy");
    }
    if (value.recording.enabled && value.recording.stop_on_overflow) {
        invalid("fixed-band native recording cannot stop DSP on recorder overflow");
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
    static_cast<void>(sdr_core::to_wire(value.recorder_overflow));
    static_cast<void>(sdr_core::to_wire(value.backend));
    if (value.dsp.unit == sdr_core::SpectrumUnit::Dbm ||
        value.dsp.unit == sdr_core::SpectrumUnit::DbmBin ||
        value.dsp.unit == sdr_core::SpectrumUnit::DbmHz) {
        invalid("fixed-band P07 supports only uncalibrated dBFS units");
    }
    static_cast<void>(validate_resource_budget(value));
}


namespace {
[[nodiscard]] sdr_core::SourceDescriptor producer_source(
    const FixedBandConfig& config, const ContextProbe& probe, const bool paired
) {
    sdr_core::SourceDescriptor source{
        .source_type = sdr_core::SourceType::LiveIq,
        .source_id = config.device.source_id,
        .display_name = probe.model.empty() ? "PlutoSDR / AD936x" : probe.model,
        .uri = config.device.context_uri, .device_serial = probe.serial,
        .backend_id = "pluto-libiio", .schema_version = sdr_core::contract_schema_version,
        .metadata_json = {},
    };
    if (paired || config.receiver_selection == ReceiverSelection::Rx2) {
        source.metadata_json["receiver_selection"] =
            config.receiver_selection == ReceiverSelection::Rx1 ? "\"RX1\"" : "\"RX2\"";
    }
    sdr_core::validate(source);
    return source;
}
[[nodiscard]] sdr_core::DualRxDspConfig paired_dsp_config(
    const PairedFixedBandConfig& config, const ContextProbe& probe
) {
    return {
        .primary = {producer_source(config.primary, probe, true), config.primary.dsp},
        .secondary = {producer_source(config.secondary, probe, true), config.secondary.dsp},
        .dc_removal_block_mean = config.primary.dc_removal_block_mean,
        .output_queue_capacity = 1U, // Owner consumes every pair before its final queue.
        .max_input_samples_per_push = config.primary.device.buffer_samples,
        .backend = {.preference = config.primary.backend,
                    .allow_runtime_fallback = config.primary.allow_runtime_fallback},
        .analytical_event_capacity = config.primary.analytical_event_capacity,
    };
}
} // namespace

void validate(const PairedFixedBandConfig& value) {
    validate(value.primary);
    validate(value.secondary);
    const auto& p = value.primary;
    const auto& q = value.secondary;
    if (p.receiver_selection != ReceiverSelection::Rx1 ||
        q.receiver_selection != ReceiverSelection::Rx2 ||
        p.device.source_id == q.device.source_id) {
        invalid("paired engine requires distinct producer IDs in RX1/RX2 order");
    }
    if (value.output_queue_capacity == 0U || value.output_queue_capacity > 64U) {
        invalid("paired spectrum queue capacity must be in [1, 64]");
    }
    if (p.device.context_uri != q.device.context_uri ||
        p.device.center_frequency_hz != q.device.center_frequency_hz ||
        p.device.sample_rate_hz != q.device.sample_rate_hz ||
        p.device.analog_bandwidth_hz != q.device.analog_bandwidth_hz ||
        p.device.gain_mode != q.device.gain_mode ||
        p.device.manual_gain_db != q.device.manual_gain_db ||
        p.device.channel_index != q.device.channel_index ||
        p.device.buffer_samples != q.device.buffer_samples ||
        p.backend != q.backend || p.allow_runtime_fallback != q.allow_runtime_fallback ||
        p.dc_removal_block_mean != q.dc_removal_block_mean ||
        p.acquisition_queue_capacity != q.acquisition_queue_capacity ||
        p.acquisition_overflow != q.acquisition_overflow ||
        p.snapshot_rate_hz != q.snapshot_rate_hz ||
        p.discard_blocks_after_start != q.discard_blocks_after_start ||
        p.analytical_event_capacity != q.analytical_event_capacity) {
        invalid("paired engine requires one common RF/gain/acquisition/cadence policy");
    }
    const auto dsp = paired_dsp_config(value, {});
    sdr_core::validate(dsp);
    const auto a = validate_resource_budget(p);
    const auto b = validate_resource_budget(q);
    const auto sum = [](std::uint64_t x, std::uint64_t y) {
        return checked_add(x, y, "paired engine memory");
    };
    // Reserve paired final snapshots, a constructing pair and terminal pair.
    // Include the bridge input/scratch reservation and one common IIO scan.
    const auto pair_snapshots = checked_multiply(
        checked_multiply(p.dsp.fft_size, 32U, "paired snapshots"),
        value.output_queue_capacity + 2U, "paired snapshots");
    const auto scratch = checked_multiply(p.device.buffer_samples, 24U, "paired input scratch");
    const auto sink_bytes = value.analytical_sink ? value.analytical_sink->payload_bytes() : 0U;
    const auto iq = sum(sum(a.iq, b.iq), scratch);
    const auto spectrum = sum(sum(a.spectrum, b.spectrum), pair_snapshots);
    if (iq > max_iq_pool_bytes || sum(a.dsp, b.dsp) > max_dsp_working_bytes ||
        spectrum > max_spectrum_backlog_bytes ||
        sum(a.persistence, b.persistence) > max_persistence_bytes ||
        sum(sum(a.sweep, b.sweep), sink_bytes) > max_sweep_line_backlog_bytes ||
        sum(sum(a.total, b.total), sum(sum(pair_snapshots, scratch), sink_bytes)) > max_total_bytes) {
        invalid("paired configuration exceeds aggregate live-engine memory budget");
    }
    if (p.recording.enabled && q.recording.enabled) {
        // Separate native artifacts are not an atomic multi-channel ledger.
        // Reject aliases using the SAME writer normalization, before RF writes.
        auto left = sdr_core::native_recording_base_path(p.recording.output_uri);
        auto right = sdr_core::native_recording_base_path(q.recording.output_uri);
        left = std::filesystem::weakly_canonical(std::filesystem::absolute(left));
        right = std::filesystem::weakly_canonical(std::filesystem::absolute(right));
#if defined(_WIN32)
        const auto& l = left.native();
        const auto& r = right.native();
        if (l.size() > INT_MAX || r.size() > INT_MAX) {
            invalid("paired recording path exceeds Windows comparison range");
        }
        // Windows ordinal casing is independent of the process CRT locale.
        // Conservative case-insensitive refusal also protects case-sensitive folders.
        const auto comparison = CompareStringOrdinal(l.data(), static_cast<int>(l.size()),
            r.data(), static_cast<int>(r.size()), TRUE);
        if (comparison == 0) invalid("paired recording path comparison failed");
        if (comparison == CSTR_EQUAL) invalid("paired recordings require distinct canonical base paths");
#else
        if (left == right) invalid("paired recordings require distinct canonical base paths");
#endif
    }
}

// Per-producer native consumers; no device, acquisition thread or control lease.
struct FixedBandChannelState {
    FixedBandConfig config_{};
    std::unique_ptr<sdr_core::DspBackend> backend_;
    std::unique_ptr<sdr_core::SegmentedIqRecordingWriter> recording_writer_;
    std::unique_ptr<sdr_core::SpectrumFrameRecordingWriter> spectrum_recording_writer_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::IqBlock>> recorder_queue_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::SpectrumFrame>> spectrum_queue_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::SweepLineFrame>> sweep_line_queue_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::SpectrumFrame>> spectrum_recorder_queue_;
    std::unique_ptr<sdr_core::PersistenceAccumulator> persistence_;
    std::unique_ptr<sdr_core::ContinuousSweepLineAssembler> sweep_line_assembler_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::PersistenceSnapshot>> persistence_queue_;
    sdr_core::EngineMetricsCounters counters_{};
    std::atomic<std::uint64_t> snapshots_superseded_{};
    std::atomic<std::uint64_t> sweep_line_snapshots_superseded_{};
    std::atomic<std::uint64_t> completed_sweep_lines_{};
    std::atomic<std::uint64_t> gapped_sweep_lines_{};
    std::atomic<std::uint64_t> sweep_line_capacity_evicted_{};
    std::atomic<std::uint64_t> persistence_snapshots_superseded_{};
    std::atomic<std::uint64_t> recorder_queue_blocks_dropped_{};
    std::atomic<std::uint64_t> recorder_queue_samples_dropped_{};
    std::atomic<std::uint64_t> recorder_shutdown_blocks_discarded_{};
    std::atomic<std::uint64_t> recorder_shutdown_samples_discarded_{};
    std::atomic<std::uint64_t> recorder_queue_overflow_notifications_{};
    std::atomic<std::uint64_t> recorder_writer_blocks_written_{};
    std::atomic<std::uint64_t> recorder_writer_samples_written_{};
    std::atomic<std::uint64_t> recorder_writer_bytes_written_{};
    std::atomic<std::uint64_t> recorder_writer_blocks_unavailable_{};
    std::atomic<std::uint64_t> recorder_writer_samples_unavailable_{};
    std::atomic<bool> recorder_writer_failed_{false};
    std::atomic<std::uint64_t> spectrum_recorder_frames_dropped_{};
    std::atomic<std::uint64_t> spectrum_recorder_frames_unavailable_{};
    std::atomic<std::uint64_t> spectrum_recorder_shutdown_frames_discarded_{};
    std::atomic<std::uint64_t> spectrum_recorder_overflow_notifications_{};
    std::atomic<std::uint64_t> spectrum_writer_frames_written_{};
    std::atomic<std::uint64_t> spectrum_writer_bytes_written_{};
    std::atomic<bool> spectrum_writer_failed_{false};
    std::atomic<double> end_to_end_latency_ms_{};
    std::thread recorder_thread_;
    std::thread spectrum_recorder_thread_;
    mutable std::mutex backend_metrics_mutex_;
    sdr_core::DspBackendMetrics backend_metrics_cache_;
};

struct FixedBandIqEnvelope {
    sdr_core::IqBlock primary;
    std::optional<sdr_core::IqBlock> secondary;
};

class FixedBandEngine::Impl final : private FixedBandChannelState {
public:
    Impl(std::string uri, const std::uint32_t timeout_ms, std::optional<std::string> expected_serial,
         std::optional<ExpectedUsbConnection> expected_usb_connection)
        : device_(std::move(uri), timeout_ms, std::move(expected_serial), std::move(expected_usb_connection)) {}

    ~Impl() noexcept {
        shutdown_noexcept();
        disconnect();
    }

    AppliedConfig configure(const FixedBandConfig& config) {
        return configure_internal(config, nullptr);
    }

    AppliedConfig configure_paired(const PairedFixedBandConfig& config) {
        validate(config);
        return configure_internal(config.primary, &config);
    }

    AppliedConfig configure_internal(const FixedBandConfig& config, const PairedFixedBandConfig* pair) {
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
        if (recorder_queue_ && recorder_queue_->depth() != 0U) {
            throw sdr_core::ConfigurationError(
                "fixed-band recorder staging queue must be drained before reconfigure"
            );
        }

        if (secondary_ && secondary_->recorder_queue_ && secondary_->recorder_queue_->depth() != 0U) {
            invalid("secondary recorder staging queue must be drained before reconfigure");
        }
        if (pair) validate(*pair); else validate(config);
        const auto probe = device_.probe(); // Same owned context, no second opener.
        auto primary = prepare_channel(config, probe, pair != nullptr);
        auto secondary = pair ? prepare_channel(pair->secondary, probe, true) : nullptr;
        auto acquisition = std::make_unique<sdr_core::BoundedQueue<FixedBandIqEnvelope>>(
            config.acquisition_queue_capacity, config.acquisition_overflow);
        auto events = std::make_unique<sdr_core::BoundedQueue<sdr_core::DiagnosticEvent>>(
            config.event_queue_capacity, sdr_core::OverflowPolicy::DropNewest);
        std::unique_ptr<sdr_core::DualRxDspPublisher> paired_dsp;
        std::unique_ptr<sdr_core::BoundedQueue<sdr_core::DualRxSpectrumFrame>> paired_output;
        if (pair) {
            paired_dsp = std::make_unique<sdr_core::DualRxDspPublisher>();
            paired_dsp->configure(paired_dsp_config(*pair, probe)); // All DSP admission before RF.
            paired_output = std::make_unique<sdr_core::BoundedQueue<sdr_core::DualRxSpectrumFrame>>(
                pair->output_queue_capacity, sdr_core::OverflowPolicy::LatestWins);
            const auto metrics = paired_dsp->metrics();
            primary->backend_metrics_cache_ = metrics.primary;
            secondary->backend_metrics_cache_ = metrics.secondary;
        }
        const auto retain = [](const FixedBandConfig& c) {
            return static_cast<std::uint64_t>(c.acquisition_queue_capacity) + 3U +
                (recorder_enabled(c) ? recorder_queue_capacity(c) : 0U);
        };
        const auto pool_blocks = std::max<std::uint64_t>(
            8U, pair ? checked_add(retain(config), retain(pair->secondary), "paired pool") : retain(config));
        if (pool_blocks > std::numeric_limits<std::uint32_t>::max()) invalid("paired pool bound overflow");
        const auto applied = device_.configure(config.device,
            pair ? ReceiverSelection::Both : config.receiver_selection,
            static_cast<std::uint32_t>(pool_blocks));
        try {
            prepare_sweep_line(*primary, applied, probe, pair != nullptr);
            if (secondary) prepare_sweep_line(*secondary, applied, probe, true);
            start_channel_writers(*primary);
            if (secondary) start_channel_writers(*secondary);
        } catch (...) {
            abort_channel_writers(*primary, "configure_writer_start_failure");
            if (secondary) abort_channel_writers(*secondary, "configure_writer_start_failure");
            throw;
        }
        install_primary(std::move(primary));
        secondary_ = std::move(secondary);
        paired_dsp_ = std::move(paired_dsp);
        paired_spectrum_queue_ = std::move(paired_output);
        paired_analytical_sink_ = pair ? pair->analytical_sink : nullptr;
        {
            std::lock_guard cache_lock(paired_metrics_mutex_);
            paired_dsp_metrics_cache_ = paired_dsp_ ? paired_dsp_->metrics() : sdr_core::DualRxDspMetrics{};
        }
        applied_ = applied;
        acquisition_queue_ = std::move(acquisition);
        event_queue_ = std::move(events);
        stop_ = sdr_core::make_stop_token();
        counters_.reset();
        end_to_end_latency_ms_.store(0., std::memory_order_relaxed);
        paired_snapshots_emitted_.store(0U);
        paired_snapshots_superseded_.store(0U);
        paired_processing_ms_.store(0.);
        paired_snapshots_abandoned_.store(0U);
        history_queue_snapshots_abandoned_.store(0U);
        transient_blocks_discarded_.store(0U, std::memory_order_relaxed);
        transient_samples_discarded_.store(0U, std::memory_order_relaxed);
        snapshots_superseded_.store(0U, std::memory_order_relaxed);
        sweep_line_snapshots_superseded_.store(0U, std::memory_order_relaxed);
        completed_sweep_lines_.store(0U, std::memory_order_relaxed);
        gapped_sweep_lines_.store(0U, std::memory_order_relaxed);
        sweep_line_capacity_evicted_.store(0U, std::memory_order_relaxed);
        persistence_snapshots_superseded_.store(0U, std::memory_order_relaxed);
        acquisition_queue_blocks_dropped_.store(0U, std::memory_order_relaxed);
        acquisition_queue_samples_dropped_.store(0U, std::memory_order_relaxed);
        source_sequence_discontinuities_.store(0U, std::memory_order_relaxed);
        source_sample_index_discontinuities_.store(0U, std::memory_order_relaxed);
        source_timestamp_regressions_.store(0U, std::memory_order_relaxed);
        source_estimated_timestamp_blocks_.store(0U, std::memory_order_relaxed);
        recorder_queue_blocks_dropped_.store(0U, std::memory_order_relaxed);
        recorder_queue_samples_dropped_.store(0U, std::memory_order_relaxed);
        recorder_shutdown_blocks_discarded_.store(0U, std::memory_order_relaxed);
        recorder_shutdown_samples_discarded_.store(0U, std::memory_order_relaxed);
        recorder_queue_overflow_notifications_.store(0U, std::memory_order_relaxed);
        recorder_writer_blocks_written_.store(0U, std::memory_order_relaxed);
        recorder_writer_samples_written_.store(0U, std::memory_order_relaxed);
        recorder_writer_bytes_written_.store(0U, std::memory_order_relaxed);
        recorder_writer_blocks_unavailable_.store(0U, std::memory_order_relaxed);
        recorder_writer_samples_unavailable_.store(0U, std::memory_order_relaxed);
        recorder_writer_failed_.store(false, std::memory_order_relaxed);
        spectrum_recorder_frames_dropped_.store(0U, std::memory_order_relaxed);
        spectrum_recorder_frames_unavailable_.store(0U, std::memory_order_relaxed);
        spectrum_recorder_shutdown_frames_discarded_.store(0U, std::memory_order_relaxed);
        spectrum_recorder_overflow_notifications_.store(0U, std::memory_order_relaxed);
        spectrum_writer_frames_written_.store(0U, std::memory_order_relaxed);
        spectrum_writer_bytes_written_.store(0U, std::memory_order_relaxed);
        spectrum_writer_failed_.store(false, std::memory_order_relaxed);
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
        // Invalid selection/budget/host intent must not stop a valid run.
        validate(config);
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
        static_cast<void>(start_guarded(nullptr));
    }

    bool start_guarded(const std::shared_ptr<StartAdmissionGate>& gate) {
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
        if (gate && !gate->claim()) return false;
        device_.start_stream();
        if (gate && gate->cancelled()) {
            device_.stop_stream();
            state_.store(sdr_core::EngineState::Stopped, std::memory_order_release);
            return false;
        }
        state_.store(sdr_core::EngineState::Running, std::memory_order_release);
        try {
            start_channel_threads(*this);
            if (secondary_) start_channel_threads(*secondary_);
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
            join_channel_threads(*this);
            if (secondary_) join_channel_threads(*secondary_);
            abort_channel_writers(*this, "engine_start_failure");
            if (secondary_) abort_channel_writers(*secondary_, "engine_start_failure");
            device_.stop_stream();
            mark_error();
            throw;
        }
        emit_event(
            sdr_core::EventSeverity::Info,
            "fixed_band_started",
            "fixed-band acquisition and CPU DSP started"
        );
        return true;
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
        finalize_channel(*this);
        if (secondary_) finalize_channel(*secondary_);
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
        if (secondary_) {
            account_recorder_abandoned(*secondary_);
            account_spectrum_recorder_abandoned(*secondary_);
            abort_channel_writers(*secondary_, "disconnect");
        }
        secondary_.reset();
        paired_dsp_.reset();
        paired_spectrum_queue_.reset();
        paired_analytical_sink_.reset();
        backend_.reset();
        acquisition_queue_.reset();
        account_recorder_abandoned(*this);
        recorder_queue_.reset();
        if (recording_writer_) {
            recording_writer_->abort("disconnect");
        }
        recording_writer_.reset();
        account_spectrum_recorder_abandoned(*this);
        spectrum_recorder_queue_.reset();
        if (spectrum_recording_writer_) {
            spectrum_recording_writer_->abort("disconnect");
        }
        spectrum_recording_writer_.reset();
        spectrum_queue_.reset();
        sweep_line_queue_.reset();
        sweep_line_assembler_.reset();
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
        return channel_metrics(*this);
    }

    [[nodiscard]] sdr_core::AnalyticalReadyDrain drain_analytical_ready_events(
        ReceiverSelection receiver, std::size_t max_items) {
        std::lock_guard lock(lifecycle_mutex_);
        if (!configured_) invalid("analytical journal requires a configured owner");
        if (receiver != ReceiverSelection::Rx1 && receiver != ReceiverSelection::Rx2)
            invalid("analytical journal requires one exact receiver");
        if (secondary_) return paired_dsp_->drain_analytical_ready_events(
            receiver == ReceiverSelection::Rx1, max_items);
        if (receiver != config_.receiver_selection)
            invalid("analytical journal receiver is not admitted by this owner");
        return backend_->drain_analytical_ready(max_items);
    }

    [[nodiscard]] PairedFixedBandMetrics paired_metrics() const {
        std::lock_guard lock(lifecycle_mutex_);
        if (!secondary_) invalid("paired metrics require a paired configuration");
        PairedFixedBandMetrics result;
        result.primary = channel_metrics(*this);
        result.secondary = channel_metrics(*secondary_);
        {
            std::lock_guard cache_lock(paired_metrics_mutex_);
            result.dsp = paired_dsp_metrics_cache_;
        }
        result.paired_spectrum_queue = paired_spectrum_queue_->stats();
        result.paired_snapshots_emitted = paired_snapshots_emitted_.load();
        result.paired_snapshots_superseded = paired_snapshots_superseded_.load();
        result.paired_processing_ms = paired_processing_ms_.load();
        result.paired_snapshots_abandoned = paired_snapshots_abandoned_.load();
        result.history_queue_snapshots_abandoned = history_queue_snapshots_abandoned_.load();
        return result;
    }

    [[nodiscard]] FixedBandMetrics channel_metrics(const FixedBandChannelState& channel) const {
        FixedBandMetrics result;
        result.receiver_selection = channel.config_.receiver_selection;
        result.state = state_.load(std::memory_order_acquire);
        result.has_error = has_error_.load(std::memory_order_relaxed);
        result.engine = assemble_channel_engine_metrics(channel);
        result.device = device_.metrics();
        // Common acquisition counters describe ONE stream epoch, repeated in the
        // channel views for compatibility; they must not be summed as two streams.
        result.engine.iq_blocks_received = counters_.iq_blocks_received.load();
        result.engine.iq_samples_received = counters_.iq_samples_received.load();
        result.engine.iq_blocks_dropped = counters_.iq_blocks_dropped.load();
        result.engine.iq_samples_dropped = counters_.iq_samples_dropped.load();
        if (acquisition_queue_) {
            result.acquisition_queue = acquisition_queue_->stats();
        }
        if (channel.recorder_queue_) {
            result.recorder_queue = channel.recorder_queue_->stats();
        }
        if (channel.spectrum_queue_) {
            result.spectrum_queue = channel.spectrum_queue_->stats();
        }
        if (channel.sweep_line_queue_) {
            result.sweep_line_queue = channel.sweep_line_queue_->stats();
        }
        if (channel.spectrum_recorder_queue_) {
            result.spectrum_recorder_queue = channel.spectrum_recorder_queue_->stats();
        }
        if (channel.persistence_queue_) {
            result.persistence_queue = channel.persistence_queue_->stats();
        }
        result.acquisition_queue_blocks_dropped =
            acquisition_queue_blocks_dropped_.load(std::memory_order_relaxed);
        result.acquisition_queue_samples_dropped =
            acquisition_queue_samples_dropped_.load(std::memory_order_relaxed);
        result.source_sequence_discontinuities =
            source_sequence_discontinuities_.load(std::memory_order_relaxed);
        result.source_sample_index_discontinuities =
            source_sample_index_discontinuities_.load(std::memory_order_relaxed);
        result.source_timestamp_regressions =
            source_timestamp_regressions_.load(std::memory_order_relaxed);
        result.source_estimated_timestamp_blocks =
            source_estimated_timestamp_blocks_.load(std::memory_order_relaxed);
        // The current libiio/AD936x adapter has no device/FPGA overflow
        // counter. Keep this explicit instead of inferring zero overflow from
        // host sequence or timestamp checks.
        result.hardware_overflow_counter_available = false;
        result.recorder_queue_blocks_dropped =
            channel.recorder_queue_blocks_dropped_.load(std::memory_order_relaxed);
        result.recorder_queue_samples_dropped =
            channel.recorder_queue_samples_dropped_.load(std::memory_order_relaxed);
        result.recorder_shutdown_blocks_discarded =
            channel.recorder_shutdown_blocks_discarded_.load(std::memory_order_relaxed);
        result.recorder_shutdown_samples_discarded =
            channel.recorder_shutdown_samples_discarded_.load(std::memory_order_relaxed);
        result.recorder_writer_blocks_written =
            channel.recorder_writer_blocks_written_.load(std::memory_order_relaxed);
        result.recorder_writer_samples_written =
            channel.recorder_writer_samples_written_.load(std::memory_order_relaxed);
        result.recorder_writer_bytes_written =
            channel.recorder_writer_bytes_written_.load(std::memory_order_relaxed);
        result.recorder_writer_blocks_unavailable =
            channel.recorder_writer_blocks_unavailable_.load(std::memory_order_relaxed);
        result.recorder_writer_samples_unavailable =
            channel.recorder_writer_samples_unavailable_.load(std::memory_order_relaxed);
        result.recorder_writer_failed =
            channel.recorder_writer_failed_.load(std::memory_order_relaxed);
        result.spectrum_recorder_frames_dropped =
            channel.spectrum_recorder_frames_dropped_.load(std::memory_order_relaxed);
        result.spectrum_recorder_frames_unavailable =
            channel.spectrum_recorder_frames_unavailable_.load(std::memory_order_relaxed);
        result.spectrum_recorder_shutdown_frames_discarded =
            channel.spectrum_recorder_shutdown_frames_discarded_.load(std::memory_order_relaxed);
        result.spectrum_writer_frames_written =
            channel.spectrum_writer_frames_written_.load(std::memory_order_relaxed);
        result.spectrum_writer_bytes_written =
            channel.spectrum_writer_bytes_written_.load(std::memory_order_relaxed);
        result.spectrum_writer_failed =
            channel.spectrum_writer_failed_.load(std::memory_order_relaxed);
        result.transient_blocks_discarded =
            transient_blocks_discarded_.load(std::memory_order_relaxed);
        result.transient_samples_discarded =
            transient_samples_discarded_.load(std::memory_order_relaxed);
        result.spectrum_snapshots_superseded =
            channel.snapshots_superseded_.load(std::memory_order_relaxed);
        result.sweep_line_snapshots_superseded =
            channel.sweep_line_snapshots_superseded_.load(std::memory_order_relaxed);
        result.completed_sweep_lines =
            channel.completed_sweep_lines_.load(std::memory_order_relaxed);
        result.gapped_sweep_lines =
            channel.gapped_sweep_lines_.load(std::memory_order_relaxed);
        result.sweep_line_capacity_evicted =
            channel.sweep_line_capacity_evicted_.load(std::memory_order_relaxed);
        result.persistence_snapshots_superseded =
            channel.persistence_snapshots_superseded_.load(std::memory_order_relaxed);
        result.shutdown_blocks_discarded =
            shutdown_blocks_discarded_.load(std::memory_order_relaxed);
        result.shutdown_samples_discarded =
            shutdown_samples_discarded_.load(std::memory_order_relaxed);
        result.expected_cancellations =
            expected_cancellations_.load(std::memory_order_relaxed);
        result.diagnostic_events_lost =
            events_lost_.load(std::memory_order_relaxed);
        if (configured_) {
            std::lock_guard metrics_lock(channel.backend_metrics_mutex_);
            const auto& dsp_metrics = channel.backend_metrics_cache_;
            result.requested_backend = dsp_metrics.requested_preference;
            result.active_backend = dsp_metrics.active_backend;
            result.backend_self_test_passed = dsp_metrics.backend_self_test_passed;
            result.backend_fallback_count = dsp_metrics.backend_fallback_count;
            result.backend_switch_count = dsp_metrics.backend_switch_count;
            result.last_backend_error = dsp_metrics.last_backend_error;
        } else {
            result.requested_backend = channel.config_.backend;
            result.active_backend = channel.config_.backend;
        }
        return result;
    }


    [[nodiscard]] FixedBandChannelState& receiver_channel(ReceiverSelection receiver) {
        if (receiver == ReceiverSelection::Both) invalid("per-channel consumer requires RX1 or RX2");
        if (receiver == config_.receiver_selection) return *this;
        if (secondary_ && receiver == ReceiverSelection::Rx2) return *secondary_;
        invalid("receiver is not admitted by this engine");
    }

    template<class T>
    [[nodiscard]] std::vector<T> poll_channel_queue(
        std::unique_ptr<sdr_core::BoundedQueue<T>>& queue, std::size_t max_items
    ) {
        std::vector<T> result;
        if (!queue) return result;
        T item;
        while (max_items == 0U || result.size() < max_items) {
            if (!queue->try_pop(item)) break;
            result.push_back(std::move(item));
        }
        return result;
    }

    [[nodiscard]] std::vector<sdr_core::DualRxSpectrumFrame> poll_paired_spectrum_frames(std::size_t max_items) {
        std::lock_guard lock(lifecycle_mutex_);
        if (!secondary_) invalid("paired Spectrum read requires a paired engine");
        return poll_channel_queue(paired_spectrum_queue_, max_items);
    }

    [[nodiscard]] sdr_core::LatestDualRxSpectrumFrameDrain drain_latest_paired_spectrum_frame() {
        std::lock_guard lock(lifecycle_mutex_);
        if (!secondary_) invalid("paired Spectrum drain requires a paired engine");
        sdr_core::LatestDualRxSpectrumFrameDrain result;
        sdr_core::DualRxSpectrumFrame frame;
        while (paired_spectrum_queue_->try_pop(frame)) {
            if (result.frame) ++result.coalesced_frames;
            result.frame = std::move(frame);
        }
        return result;
    }

    [[nodiscard]] std::vector<sdr_core::PersistenceSnapshot> poll_receiver_persistence_snapshots(
        ReceiverSelection receiver, std::size_t max_items) {
        std::lock_guard lock(lifecycle_mutex_);
        return poll_channel_queue(receiver_channel(receiver).persistence_queue_, max_items);
    }
    [[nodiscard]] std::vector<sdr_core::SweepLineFrame> poll_receiver_sweep_line_frames(
        ReceiverSelection receiver, std::size_t max_items) {
        std::lock_guard lock(lifecycle_mutex_);
        return poll_channel_queue(receiver_channel(receiver).sweep_line_queue_, max_items);
    }
    [[nodiscard]] std::vector<sdr_core::IqBlock> poll_receiver_recorded_iq_blocks(
        ReceiverSelection receiver, std::size_t max_items) {
        std::lock_guard lock(lifecycle_mutex_);
        return poll_channel_queue(receiver_channel(receiver).recorder_queue_, max_items);
    }

    [[nodiscard]] std::vector<sdr_core::SpectrumFrame> poll_spectrum_frames(
        const std::size_t max_items
    ) {
        std::lock_guard lock(lifecycle_mutex_);
        if (secondary_) invalid("paired engine requires an explicit paired or receiver-scoped read");
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

    [[nodiscard]] std::vector<sdr_core::SweepLineFrame> poll_sweep_line_frames(
        const std::size_t max_items
    ) {
        std::lock_guard lock(lifecycle_mutex_);
        if (secondary_) invalid("paired engine requires an explicit paired or receiver-scoped read");
        std::vector<sdr_core::SweepLineFrame> result;
        if (!sweep_line_queue_) {
            return result;
        }
        sdr_core::SweepLineFrame frame;
        while (max_items == 0U || result.size() < max_items) {
            if (!sweep_line_queue_->try_pop(frame)) {
                break;
            }
            result.push_back(std::move(frame));
        }
        return result;
    }

    [[nodiscard]] LatestSpectrumFrameDrain drain_latest_spectrum_frame() {
        std::lock_guard lock(lifecycle_mutex_);
        if (secondary_) invalid("paired engine requires an explicit paired or receiver-scoped read");
        LatestSpectrumFrameDrain result;
        if (!spectrum_queue_) {
            return result;
        }
        sdr_core::SpectrumFrame frame;
        while (spectrum_queue_->try_pop(frame)) {
            if (result.frame.has_value()) {
                ++result.coalesced_frames;
            }
            result.frame = std::move(frame);
        }
        return result;
    }

    [[nodiscard]] std::vector<sdr_core::IqBlock> poll_recorded_iq_blocks(
        const std::size_t max_items
    ) {
        std::lock_guard lock(lifecycle_mutex_);
        if (secondary_) invalid("paired engine requires an explicit paired or receiver-scoped read");
        std::vector<sdr_core::IqBlock> result;
        if (!recorder_queue_) {
            return result;
        }
        sdr_core::IqBlock block;
        while (max_items == 0U || result.size() < max_items) {
            if (!recorder_queue_->try_pop(block)) {
                break;
            }
            result.push_back(std::move(block));
        }
        return result;
    }

    [[nodiscard]] std::vector<sdr_core::PersistenceSnapshot> poll_persistence_snapshots(
        const std::size_t max_items
    ) {
        std::lock_guard lock(lifecycle_mutex_);
        if (secondary_) invalid("paired engine requires an explicit paired or receiver-scoped read");
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

    void set_dsp_delay_for_test(const std::uint32_t milliseconds) noexcept {
        dsp_delay_for_test_ms_.store(milliseconds, std::memory_order_relaxed);
    }
#endif
private:
    void finalize_channel(FixedBandChannelState& channel) {
        if (channel.recorder_thread_.joinable()) {
            channel.recorder_thread_.join();
        }
        if (channel.spectrum_recorder_thread_.joinable()) {
            channel.spectrum_recorder_thread_.join();
        }
        if (channel.recording_writer_ &&
            channel.recorder_writer_failed_.load(std::memory_order_relaxed)) {
            account_recorder_abandoned(channel);
        }
        if (channel.recording_writer_ &&
            !channel.recorder_writer_failed_.load(std::memory_order_relaxed)) {
            try {
                channel.recording_writer_->set_recorder_queue_loss(
                    channel.recorder_queue_blocks_dropped_.load(std::memory_order_relaxed),
                    channel.recorder_queue_samples_dropped_.load(std::memory_order_relaxed)
                );
                channel.recording_writer_->finalize();
            } catch (const std::exception& error) {
                channel.recorder_writer_failed_.store(true, std::memory_order_relaxed);
                channel.recording_writer_->abort("finalize_failure");
                emit_event(
                    sdr_core::EventSeverity::Warning,
                    "recorder_finalize_failure",
                    error.what()
                );
            } catch (...) {
                channel.recorder_writer_failed_.store(true, std::memory_order_relaxed);
                channel.recording_writer_->abort("finalize_failure");
                emit_event(
                    sdr_core::EventSeverity::Warning,
                    "recorder_finalize_failure",
                    "native recorder finalization failed"
                );
            }
        }
        if (channel.spectrum_recording_writer_ &&
            channel.spectrum_writer_failed_.load(std::memory_order_relaxed)) {
            account_spectrum_recorder_abandoned(channel);
        }
        if (channel.spectrum_recording_writer_ &&
            !channel.spectrum_writer_failed_.load(std::memory_order_relaxed)) {
            try {
                channel.spectrum_recording_writer_->set_recorder_queue_loss(
                    channel.spectrum_recorder_frames_dropped_.load(std::memory_order_relaxed)
                );
                channel.spectrum_recording_writer_->finalize();
            } catch (const std::exception& error) {
                channel.spectrum_writer_failed_.store(true, std::memory_order_relaxed);
                channel.spectrum_recording_writer_->abort("finalize_failure");
                emit_event(
                    sdr_core::EventSeverity::Warning,
                    "spectrum_recorder_finalize_failure",
                    error.what()
                );
            } catch (...) {
                channel.spectrum_writer_failed_.store(true, std::memory_order_relaxed);
                channel.spectrum_recording_writer_->abort("finalize_failure");
                emit_event(
                    sdr_core::EventSeverity::Warning,
                    "spectrum_recorder_finalize_failure",
                    "native spectrum recorder finalization failed"
                );
            }
        }
    }


    [[nodiscard]] std::unique_ptr<FixedBandChannelState> prepare_channel(
        const FixedBandConfig& config, const ContextProbe& probe, bool paired
    ) {
        auto channel = std::make_unique<FixedBandChannelState>();
        std::unique_ptr<sdr_core::BoundedQueue<sdr_core::IqBlock>> recorder;
        if (recorder_enabled(config)) {
            recorder = std::make_unique<sdr_core::BoundedQueue<sdr_core::IqBlock>>(
                recorder_queue_capacity(config),
                config.recorder_overflow
            );
        }
        auto spectrum =
            std::make_unique<sdr_core::BoundedQueue<sdr_core::SpectrumFrame>>(
                config.spectrum_queue_capacity,
                sdr_core::OverflowPolicy::LatestWins
            );
        std::unique_ptr<sdr_core::BoundedQueue<sdr_core::SweepLineFrame>> sweep_lines;
        if (config.continuous_sweep_line.has_value() &&
            config.continuous_sweep_line->enabled) {
            sweep_lines = std::make_unique<
                sdr_core::BoundedQueue<sdr_core::SweepLineFrame>
            >(
                continuous_sweep_line_relay_capacity(config),
                sdr_core::OverflowPolicy::LatestWins
            );
        }
        std::unique_ptr<sdr_core::BoundedQueue<sdr_core::SpectrumFrame>>
            spectrum_recorder;
        if (native_spectrum_recording_enabled(config)) {
            spectrum_recorder =
                std::make_unique<sdr_core::BoundedQueue<sdr_core::SpectrumFrame>>(
                    config.recording.queue_capacity,
                    sdr_core::OverflowPolicy::DropNewest
                );
        }

        auto persistence = std::make_unique<sdr_core::PersistenceAccumulator>(config.persistence);
        auto persistence_queue =
            std::make_unique<sdr_core::BoundedQueue<sdr_core::PersistenceSnapshot>>(
                2U, sdr_core::OverflowPolicy::LatestWins);
        sdr_core::CpuDspOptions options;
        options.output_capacity = dsp_output_capacity(config);
        options.analytical_event_capacity = config.analytical_event_capacity;
        options.dc_removal = config.dc_removal_block_mean
                                 ? sdr_core::DcRemovalMode::BlockMean
                                 : sdr_core::DcRemovalMode::Off;
        options.source = producer_source(config, probe, paired);
        std::unique_ptr<sdr_core::SegmentedIqRecordingWriter> recording_writer;
        if (native_iq_recording_enabled(config)) {
            recording_writer = std::make_unique<sdr_core::SegmentedIqRecordingWriter>(
                config.recording,
                options.source
            );
        }
        std::unique_ptr<sdr_core::SpectrumFrameRecordingWriter> spectrum_recording_writer;
        if (native_spectrum_recording_enabled(config)) {
            spectrum_recording_writer =
                std::make_unique<sdr_core::SpectrumFrameRecordingWriter>(
                    config.recording,
                    options.source
                );
        }
        // P08: the DSP stage is selected through the vendor-neutral factory
        // (CPU / CUDA with runtime failover per configuration).
        sdr_core::DspBackendSelectionOptions selection;
        selection.preference = config.backend;
        selection.allow_runtime_fallback = config.allow_runtime_fallback;
        std::unique_ptr<sdr_core::DspBackend> backend;
        if (!paired) {
            backend = sdr_core::make_dsp_backend(selection, std::move(options));
            backend->configure(config.dsp);
        }


        channel->config_ = config;
        if (backend) channel->backend_metrics_cache_ = backend->metrics();
        channel->backend_ = std::move(backend);
        channel->recorder_queue_ = std::move(recorder);
        channel->recording_writer_ = std::move(recording_writer);
        channel->spectrum_queue_ = std::move(spectrum);
        channel->sweep_line_queue_ = std::move(sweep_lines);
        channel->spectrum_recorder_queue_ = std::move(spectrum_recorder);
        channel->spectrum_recording_writer_ = std::move(spectrum_recording_writer);
        channel->persistence_ = std::move(persistence);
        channel->persistence_queue_ = std::move(persistence_queue);

        return channel;
    }

    void prepare_sweep_line(FixedBandChannelState& channel, const AppliedConfig& applied,
                            const ContextProbe& probe, bool paired) {
        const auto& config = channel.config_;
        const auto sweep_source = producer_source(config, probe, paired);
        if (config.continuous_sweep_line.has_value() &&
            config.continuous_sweep_line->enabled) {
            const auto& profile = *config.continuous_sweep_line;
            const auto display_span_hz = profile.display_stop_hz - profile.display_start_hz;
            const auto display_center_hz =
                profile.display_start_hz + display_span_hz / 2.0;
            const auto required_half_window_hz = std::abs(
                display_center_hz - applied.center_frequency_hz
            ) + display_span_hz / 2.0;
            if (applied.sample_rate_hz < profile.usable_window_hz ||
                applied.analog_bandwidth_hz < profile.usable_window_hz ||
                required_half_window_hz > profile.usable_window_hz / 2.0) {
                throw sdr_core::ConfigurationError(
                    "continuous sweep applied readback cannot satisfy declared usable window"
                );
            }
            const auto physical_spacing_hz = applied.sample_rate_hz /
                static_cast<double>(config.dsp.fft_size);
            const auto line_spacing_hz = profile.analysis_bins_per_usable_window == 0U
                ? physical_spacing_hz
                : profile.usable_window_hz /
                    static_cast<double>(profile.analysis_bins_per_usable_window);
            if (line_spacing_hz + 1e-9 < physical_spacing_hz) {
                throw sdr_core::ConfigurationError(
                    "continuous sweep analysis grid requires a denser physical FFT transform"
                );
            }
            channel.sweep_line_assembler_ = std::make_unique<sdr_core::ContinuousSweepLineAssembler>(
                sdr_core::SweepLineDefinition{
                    .source = sweep_source,
                    .epoch = profile.epoch,
                    .start_frequency_hz = profile.display_start_hz,
                    .stop_frequency_hz = profile.display_stop_hz,
                    .target_spacing_hz = line_spacing_hz,
                    .analysis_window_hz = profile.analysis_bins_per_usable_window == 0U
                        ? 0.0 : profile.usable_window_hz,
                    .analysis_bins_per_usable_window = profile.analysis_bins_per_usable_window,
                    .physical_fft_bin_width_hz = profile.analysis_bins_per_usable_window == 0U
                        ? 0.0 : physical_spacing_hz,
                    .physical_fft_size = profile.analysis_bins_per_usable_window == 0U
                        ? 0U : config.dsp.fft_size,
                    .unit = config.dsp.unit,
                    .max_inflight_lines = 1U,
                    .segments = {
                        {
                            .segment_index = 0U,
                            .config_generation = applied.config_generation,
                            .usable_start_hz = profile.display_start_hz,
                            .usable_stop_hz = profile.display_stop_hz,
                        },
                    },
                }
            );
        }

    }

    void start_channel_writers(FixedBandChannelState& channel) {
        if (channel.recording_writer_) channel.recording_writer_->start();
        if (channel.spectrum_recording_writer_) channel.spectrum_recording_writer_->start();
    }

    void abort_channel_writers(FixedBandChannelState& channel, const char* reason) noexcept {
        if (channel.recording_writer_) channel.recording_writer_->abort(reason);
        if (channel.spectrum_recording_writer_) channel.spectrum_recording_writer_->abort(reason);
    }

    void install_primary(std::unique_ptr<FixedBandChannelState> channel) {
        config_ = std::move(channel->config_);
        backend_ = std::move(channel->backend_);
        recording_writer_ = std::move(channel->recording_writer_);
        spectrum_recording_writer_ = std::move(channel->spectrum_recording_writer_);
        recorder_queue_ = std::move(channel->recorder_queue_);
        spectrum_queue_ = std::move(channel->spectrum_queue_);
        sweep_line_queue_ = std::move(channel->sweep_line_queue_);
        spectrum_recorder_queue_ = std::move(channel->spectrum_recorder_queue_);
        persistence_ = std::move(channel->persistence_);
        sweep_line_assembler_ = std::move(channel->sweep_line_assembler_);
        persistence_queue_ = std::move(channel->persistence_queue_);
        std::lock_guard metrics_lock(backend_metrics_mutex_);
        backend_metrics_cache_ = channel->backend_metrics_cache_;
    }

    void start_channel_threads(FixedBandChannelState& channel) {
        if (channel.recording_writer_) {
            channel.recorder_thread_ = std::thread([this, &channel] { recorder_run(channel); });
        }
        if (channel.spectrum_recording_writer_) {
            channel.spectrum_recorder_thread_ =
                std::thread([this, &channel] { spectrum_recorder_run(channel); });
        }
    }

    void join_channel_threads(FixedBandChannelState& channel) {
        if (channel.recorder_thread_.joinable()) channel.recorder_thread_.join();
        if (channel.spectrum_recorder_thread_.joinable()) channel.spectrum_recorder_thread_.join();
    }

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
            join_channel_threads(*this);
            if (secondary_) join_channel_threads(*secondary_);
            abort_channel_writers(*this, "shutdown_exception");
            if (secondary_) abort_channel_writers(*secondary_, "shutdown_exception");
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
            if (recorder_queue_) {
                recorder_queue_->request_stop();
            }
            if (secondary_) {
                if (secondary_->recorder_queue_) secondary_->recorder_queue_->request_stop();
                if (secondary_->spectrum_recorder_queue_) secondary_->spectrum_recorder_queue_->request_stop();
            }
            if (spectrum_recorder_queue_) {
                spectrum_recorder_queue_->request_stop();
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
        std::optional<std::uint64_t> previous_source_sequence;
        std::optional<std::uint64_t> expected_first_sample_index;
        std::optional<std::int64_t> previous_timestamp_ns;
        try {
            while (!stop_->stop_requested()) {
                FixedBandIqEnvelope envelope;
                auto& block = envelope.primary;
                try {
                    if (secondary_) {
                        auto receivers = device_.refill_receivers();
                        if (receivers.selection != ReceiverSelection::Both ||
                            !receivers.rx1 || !receivers.rx2) {
                            throw sdr_core::SdrNativeError("paired engine received incomplete receiver epoch");
                        }
                        block = std::move(*receivers.rx1);
                        envelope.secondary = std::move(*receivers.rx2);
                    } else if (config_.receiver_selection == ReceiverSelection::Rx1) {
                        block = device_.refill();
                    } else {
                        auto receivers = device_.refill_receivers();
                        if (receivers.selection != ReceiverSelection::Rx2 ||
                            !receivers.rx2 || receivers.rx1) {
                            throw sdr_core::SdrNativeError("RX2 engine received a different receiver selection");
                        }
                        block = std::move(*receivers.rx2);
                    }
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
                if (previous_source_sequence.has_value() &&
                    block.source_sequence != *previous_source_sequence + 1U) {
                    source_sequence_discontinuities_.fetch_add(1U, std::memory_order_relaxed);
                }
                if (expected_first_sample_index.has_value() &&
                    block.first_sample_index != *expected_first_sample_index) {
                    source_sample_index_discontinuities_.fetch_add(1U, std::memory_order_relaxed);
                }
                if (previous_timestamp_ns.has_value() &&
                    block.timestamp_ns < *previous_timestamp_ns) {
                    source_timestamp_regressions_.fetch_add(1U, std::memory_order_relaxed);
                }
                if (sdr_core::has_flag(block.flags, sdr_core::QualityFlag::TimestampEstimated)) {
                    source_estimated_timestamp_blocks_.fetch_add(1U, std::memory_order_relaxed);
                }
                previous_source_sequence = block.source_sequence;
                expected_first_sample_index = block.first_sample_index + block.sample_count;
                previous_timestamp_ns = block.timestamp_ns;
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
                    std::move(envelope),
                    [&discarded_samples](const FixedBandIqEnvelope& evicted) noexcept {
                        discarded_samples = evicted.primary.sample_count;
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
                    pushed == sdr_core::PushResult::Evicted ||
                    pushed == sdr_core::PushResult::Full) {
                    counters_.iq_blocks_dropped.fetch_add(1U, std::memory_order_relaxed);
                    counters_.iq_samples_dropped.fetch_add(
                        discarded_samples,
                        std::memory_order_relaxed
                    );
                    acquisition_queue_blocks_dropped_.fetch_add(
                        1U,
                        std::memory_order_relaxed
                    );
                    acquisition_queue_samples_dropped_.fetch_add(
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


    void consume_analytical(FixedBandChannelState& channel, sdr_core::SpectrumFrame& frame,
                    const sdr_core::DspBackendMetrics& backend_metrics,
                    std::chrono::steady_clock::time_point started_at,
                    std::uint64_t& emitted_line_slots) {
        const auto line_profile = channel.config_.continuous_sweep_line.has_value() &&
            channel.config_.continuous_sweep_line->enabled
        ? &*channel.config_.continuous_sweep_line : nullptr;
        annotate_frame(channel, frame, backend_metrics);
        publish_persistence(channel, frame);
        if (line_profile != nullptr) {
            const auto elapsed_ns = std::max<std::int64_t>(
                0LL,
                std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now() - started_at
                ).count()
            );
            const auto allowed_line_slots = static_cast<std::uint64_t>(
                std::floor(
                    static_cast<long double>(elapsed_ns) *
                    static_cast<long double>(line_profile->line_snapshot_rate_hz) /
                    1'000'000'000.0L
                )
            );
            if (emitted_line_slots < allowed_line_slots) {
                publish_sweep_line(channel, frame);
                ++emitted_line_slots;
            }
        }

    }

    void publish_pair(sdr_core::DualRxSpectrumFrame frame) {
        const double latency_ms = std::max(
            0.0, static_cast<double>(system_time_ns() - frame.timestamp_ns) / 1.0e6);
        end_to_end_latency_ms_.store(latency_ms, std::memory_order_relaxed);
        secondary_->end_to_end_latency_ms_.store(latency_ms, std::memory_order_relaxed);
        // Record native selected snapshots BEFORE the final pair latest-wins queue.
        // The recording cadence is unchanged; this is not an all-FFT recorder.
        tee_spectrum_frame(*this, frame.primary);
        tee_spectrum_frame(*secondary_, frame.secondary);
        const auto result = paired_spectrum_queue_->try_push(std::move(frame));
        if (result == sdr_core::PushResult::Pushed || result == sdr_core::PushResult::Evicted) {
            paired_snapshots_emitted_.fetch_add(1U);
            counters_.spectrum_snapshots_emitted.fetch_add(1U);
            secondary_->counters_.spectrum_snapshots_emitted.fetch_add(1U);
        }
        if (result == sdr_core::PushResult::Evicted) paired_snapshots_superseded_.fetch_add(1U);
    }

    void cache_paired_metrics(std::chrono::steady_clock::time_point started_at) {
        const auto metrics = paired_dsp_->metrics();
        const auto now = std::chrono::steady_clock::now();
        update_dsp_metrics(*this, metrics.primary, started_at, now, false);
        update_dsp_metrics(*secondary_, metrics.secondary, started_at, now, false);
        std::lock_guard cache_lock(paired_metrics_mutex_);
        paired_dsp_metrics_cache_ = metrics;
    }

    void paired_dsp_run() noexcept {
        // Keep the native sink alive until the sole worker terminates. No sink
        // invocation escapes this worker or survives a joined reconfiguration.
        const auto sink = paired_analytical_sink_;
        try {
            if (sink) sink->begin(applied_);
            const auto started_at = std::chrono::steady_clock::now();
            const auto period = std::max<std::int64_t>(1LL, static_cast<std::int64_t>(
                std::llround(1'000'000'000. / config_.snapshot_rate_hz)));
            std::int64_t next_timestamp{};
            bool deadline_initialized = false;
            std::uint64_t primary_line_slots{}, secondary_line_slots{};
            std::optional<sdr_core::DualRxSpectrumFrame> terminal_pair;
            paired_dsp_->set_shared_gap_consumer([&] {
                terminal_pair.reset();
                deadline_initialized = false;
                paired_snapshots_abandoned_.fetch_add(paired_spectrum_queue_->abandon());
                if (sink) sink->shared_gap();
                for (auto* channel : {static_cast<FixedBandChannelState*>(this), secondary_.get()}) {
                    channel->persistence_->reset();
                    if (channel->persistence_queue_)
                        history_queue_snapshots_abandoned_.fetch_add(channel->persistence_queue_->abandon());
                    if (channel->sweep_line_queue_)
                        history_queue_snapshots_abandoned_.fetch_add(channel->sweep_line_queue_->abandon());
                }
            });
            paired_dsp_->set_analytical_consumer(
                [&](sdr_core::DualRxSpectrumFrame& pair,
                    const sdr_core::DspBackendMetrics& primary_metrics,
                    const sdr_core::DspBackendMetrics& secondary_metrics) {
                    counters_.fft_frames_computed.store(primary_metrics.fft_frames_computed);
                    secondary_->counters_.fft_frames_computed.store(secondary_metrics.fft_frames_computed);
                    consume_analytical(*this, pair.primary, primary_metrics, started_at, primary_line_slots);
                    consume_analytical(*secondary_, pair.secondary, secondary_metrics,
                                       started_at, secondary_line_slots);
                    // Every validated pair, including the terminal partial batch,
                    // reaches the Sweep owner AFTER per-chain consumers and
                    // BEFORE either cadence selection or final render coalescing.
                    if (sink) sink->consume(pair);
                    if (!deadline_initialized || pair.timestamp_ns >= next_timestamp) {
                        const auto scheduled = deadline_initialized
                            ? next_timestamp + period : pair.timestamp_ns + period;
                        next_timestamp = scheduled > pair.timestamp_ns ? scheduled : pair.timestamp_ns + period;
                        deadline_initialized = true;
                        terminal_pair.reset();
                        publish_pair(std::move(pair));
                    } else {
                        terminal_pair = std::move(pair);
                    }
                    return false;
                });
            while (true) {
                FixedBandIqEnvelope envelope;
                if (acquisition_queue_->pop(envelope) == sdr_core::PopResult::Stopped) break;
                if (!envelope.secondary) throw sdr_core::SdrNativeError("paired acquisition epoch lost peer");
                tee_recorder_block(*this, envelope.primary);
                tee_recorder_block(*secondary_, *envelope.secondary);
                const auto processing_started = std::chrono::steady_clock::now();
                paired_dsp_->push(envelope.primary, *envelope.secondary);
#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
                const auto delay = dsp_delay_for_test_ms_.load();
                if (delay) std::this_thread::sleep_for(std::chrono::milliseconds(delay));
#endif
                cache_paired_metrics(started_at);
                paired_processing_ms_.fetch_add(std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - processing_started).count());
            }
            const auto flush_started = std::chrono::steady_clock::now();
            paired_dsp_->flush(); // Both partial FFT batches through the SAME consumers.
            cache_paired_metrics(started_at);
            if (terminal_pair) publish_pair(std::move(*terminal_pair));
            paired_processing_ms_.fetch_add(std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - flush_started).count());
            paired_dsp_->set_shared_gap_consumer({});
            paired_dsp_->set_analytical_consumer({}); // Never retain worker-stack captures.
            if (sink) sink->finish(has_error_.load(std::memory_order_acquire));
        } catch (const std::exception& error) {
            paired_dsp_->set_shared_gap_consumer({});
            paired_dsp_->set_analytical_consumer({});
            if (sink) sink->finish(true);
            emit_event(sdr_core::EventSeverity::Critical, "dsp_failure", error.what());
            mark_error();
            initiate_shutdown();
        } catch (...) {
            paired_dsp_->set_shared_gap_consumer({});
            paired_dsp_->set_analytical_consumer({});
            if (sink) sink->finish(true);
            emit_event(sdr_core::EventSeverity::Critical, "dsp_failure", "unknown paired DSP failure");
            mark_error();
            initiate_shutdown();
        }
    }

    void dsp_run() noexcept {
        if (secondary_) {
            paired_dsp_run();
            return;
        }
        try {
            const auto started_at = std::chrono::steady_clock::now();
            // A device refill can contain many completed overlapping FFTs.
            // Pacing with steady-clock time *after* the whole refill used to
            // publish at most one SpectrumFrame per I/Q block.  That made the
            // observable Spectrum rate depend on IIO buffer geometry even
            // when the configured snapshot rate was higher.  Frames carry a
            // canonical sample-offset timestamp, so rate-limit this bounded
            // presentation boundary in the same timeline as the analysis.
            // The DSP still computes every admitted FFT; intermediate output
            // is deliberately reduced only here, after persistence/sweep
            // consumers have received every frame.
            const auto snapshot_period_ns = std::max<std::int64_t>(
                1LL,
                static_cast<std::int64_t>(std::llround(1'000'000'000.0 / config_.snapshot_rate_hz))
            );
            std::int64_t next_snapshot_timestamp_ns{};
            bool snapshot_deadline_initialized{false};
            // Sweep-line LPS is a host wall-clock throughput metric.  It
            // must not be paced by the nominal ADC timestamp: a transport
            // that returns a 5-ms nominal I/Q refill every 40 ms would then
            // turn a healthy post-DSP burst into an artificial ~250-LPS
            // result.  Timestamps remain untouched in every SpectrumFrame
            // and SweepLineFrame for RF provenance and discontinuity
            // reporting; only this bounded selection quota uses elapsed host
            // time.  It never creates a line without a distinct admitted
            // SpectrumFrame and it never retains a backlog.
            const auto line_started_at = std::chrono::steady_clock::now();
            std::uint64_t emitted_line_slots{};
            std::optional<sdr_core::SpectrumFrame> latest_unpublished;
            auto consider_for_publication = [
                this,
                &next_snapshot_timestamp_ns,
                &snapshot_deadline_initialized,
                snapshot_period_ns,
                &latest_unpublished,
                line_started_at,
                &emitted_line_slots
            ](sdr_core::SpectrumFrame frame, const sdr_core::DspBackendMetrics& backend_metrics) mutable {
                consume_analytical(*this, frame, backend_metrics, line_started_at, emitted_line_slots);
                if (!snapshot_deadline_initialized ||
                    frame.timestamp_ns >= next_snapshot_timestamp_ns) {
                    // Retain the requested cadence phase across quantised FFT
                    // timestamps.  Resetting to frame.timestamp + period on
                    // every publish rounds every interval upward (notably
                    // FFT4096: four frames every time instead of a bounded
                    // 3/4-frame distribution).  After a genuine large input
                    // gap we intentionally reset rather than emit a catch-up
                    // burst or build a backlog.
                    const auto scheduled_next = snapshot_deadline_initialized
                        ? next_snapshot_timestamp_ns + snapshot_period_ns
                        : frame.timestamp_ns + snapshot_period_ns;
                    next_snapshot_timestamp_ns = scheduled_next > frame.timestamp_ns
                        ? scheduled_next
                        : frame.timestamp_ns + snapshot_period_ns;
                    snapshot_deadline_initialized = true;
                    latest_unpublished.reset();
                    publish(*this, std::move(frame));
                    return;
                }
                latest_unpublished = std::move(frame);
            };
            while (true) {
                FixedBandIqEnvelope envelope;
                if (acquisition_queue_->pop(envelope) == sdr_core::PopResult::Stopped) {
                    break;
                }
                // The recorder receives the same immutable native I/Q block
                // before the DSP backend.  Its non-blocking bounded queue
                // may lose recording data, but can never backpressure or
                // suppress an analytical block.
                const auto& block = envelope.primary;
                tee_recorder_block(*this, block);
                const auto processing_started = std::chrono::steady_clock::now();
                backend_->push_iq(block);
#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
                const auto dsp_delay = dsp_delay_for_test_ms_.load(std::memory_order_relaxed);
                if (dsp_delay != 0U) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(dsp_delay));
                }
#endif
                auto ready = backend_->poll_spectrum(0U, false);
                const auto backend_metrics = backend_->metrics();
                // Commit analytical progress before exposing any frame from
                // this batch to a concurrent sweep consumer. Timing accounting
                // below still includes publication; it must not be the first
                // visibility point for the number of already-produced FFTs.
                counters_.fft_frames_computed.store(
                    backend_metrics.fft_frames_computed, std::memory_order_relaxed
                );
                for (auto& frame : ready) {
                    consider_for_publication(std::move(frame), backend_metrics);
                }
                update_dsp_metrics(*this, backend_metrics, started_at, processing_started);
            }
            auto final_frames = backend_->poll_spectrum(0U, true);
            const auto final_metrics = backend_->metrics();
            counters_.fft_frames_computed.store(
                final_metrics.fft_frames_computed, std::memory_order_relaxed
            );
            for (auto& frame : final_frames) {
                consider_for_publication(std::move(frame), final_metrics);
            }
            update_dsp_metrics(
                *this, final_metrics,
                started_at,
                std::chrono::steady_clock::now()
            );
            // Preserve the existing terminal latest-frame contract without
            // creating a producer-rate backlog while running.
            if (latest_unpublished.has_value()) {
                publish(*this, std::move(*latest_unpublished));
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

    void recorder_run(FixedBandChannelState& channel) noexcept {
        try {
            while (true) {
                sdr_core::IqBlock block;
                if (channel.recorder_queue_->pop(block) == sdr_core::PopResult::Stopped) {
                    return;
                }
                channel.recording_writer_->write_block(block);
                channel.recorder_writer_blocks_written_.fetch_add(
                    1U,
                    std::memory_order_relaxed
                );
                channel.recorder_writer_samples_written_.fetch_add(
                    block.sample_count,
                    std::memory_order_relaxed
                );
                channel.recorder_writer_bytes_written_.fetch_add(
                    block.samples->size(),
                    std::memory_order_relaxed
                );
            }
        } catch (const std::exception& error) {
            channel.recorder_writer_failed_.store(true, std::memory_order_relaxed);
            if (channel.recording_writer_) {
                channel.recording_writer_->abort("writer_failure");
            }
            if (channel.recorder_queue_) {
                channel.recorder_queue_->request_stop();
            }
            emit_event(
                sdr_core::EventSeverity::Warning,
                "recorder_write_failure",
                error.what()
            );
        } catch (...) {
            channel.recorder_writer_failed_.store(true, std::memory_order_relaxed);
            if (channel.recording_writer_) {
                channel.recording_writer_->abort("writer_failure");
            }
            if (channel.recorder_queue_) {
                channel.recorder_queue_->request_stop();
            }
            emit_event(
                sdr_core::EventSeverity::Warning,
                "recorder_write_failure",
                "native recorder write failed"
            );
        }
    }

    void spectrum_recorder_run(FixedBandChannelState& channel) noexcept {
        try {
            while (true) {
                sdr_core::SpectrumFrame frame;
                if (channel.spectrum_recorder_queue_->pop(frame) == sdr_core::PopResult::Stopped) {
                    return;
                }
                channel.spectrum_recording_writer_->write_frame(frame);
                const auto writer_metrics = channel.spectrum_recording_writer_->metrics();
                channel.spectrum_writer_frames_written_.store(
                    writer_metrics.written_frames,
                    std::memory_order_relaxed
                );
                channel.spectrum_writer_bytes_written_.store(
                    writer_metrics.written_bytes,
                    std::memory_order_relaxed
                );
            }
        } catch (const std::exception& error) {
            channel.spectrum_writer_failed_.store(true, std::memory_order_relaxed);
            if (channel.spectrum_recording_writer_) {
                channel.spectrum_recording_writer_->abort("writer_failure");
            }
            if (channel.spectrum_recorder_queue_) {
                channel.spectrum_recorder_queue_->request_stop();
            }
            emit_event(
                sdr_core::EventSeverity::Warning,
                "spectrum_recorder_write_failure",
                error.what()
            );
        } catch (...) {
            channel.spectrum_writer_failed_.store(true, std::memory_order_relaxed);
            if (channel.spectrum_recording_writer_) {
                channel.spectrum_recording_writer_->abort("writer_failure");
            }
            if (channel.spectrum_recorder_queue_) {
                channel.spectrum_recorder_queue_->request_stop();
            }
            emit_event(
                sdr_core::EventSeverity::Warning,
                "spectrum_recorder_write_failure",
                "native spectrum recorder write failed"
            );
        }
    }

    void tee_recorder_block(FixedBandChannelState& channel, const sdr_core::IqBlock& block) noexcept {
        if (!recorder_enabled(channel.config_) || !channel.recorder_queue_) {
            return;
        }
        try {
            std::uint64_t discarded_samples = block.sample_count;
            const auto recorded = channel.recorder_queue_->push_with_eviction(
                block,
                [&discarded_samples](const sdr_core::IqBlock& evicted) noexcept {
                    discarded_samples = evicted.sample_count;
                }
            );
            if (recorded == sdr_core::PushResult::Pushed) {
                return;
            }
            if (recorded == sdr_core::PushResult::Stopped) {
                if (channel.recorder_writer_failed_.load(std::memory_order_relaxed)) {
                    channel.recorder_writer_blocks_unavailable_.fetch_add(
                        1U,
                        std::memory_order_relaxed
                    );
                    channel.recorder_writer_samples_unavailable_.fetch_add(
                        block.sample_count,
                        std::memory_order_relaxed
                    );
                } else {
                    channel.recorder_shutdown_blocks_discarded_.fetch_add(
                        1U,
                        std::memory_order_relaxed
                    );
                    channel.recorder_shutdown_samples_discarded_.fetch_add(
                        block.sample_count,
                        std::memory_order_relaxed
                    );
                }
                return;
            }
            // DropNewest rejects the incoming block; DropOldest/LatestWins
            // evict a retained block.  In both cases precisely one recording
            // block is lost and the DSP path still consumes `block` below.
            channel.recorder_queue_blocks_dropped_.fetch_add(1U, std::memory_order_relaxed);
            channel.recorder_queue_samples_dropped_.fetch_add(
                discarded_samples,
                std::memory_order_relaxed
            );
            const auto drops = channel.recorder_queue_overflow_notifications_.fetch_add(
                1U,
                std::memory_order_relaxed
            ) + 1U;
            if (drops == 1U || drops % 65536U == 0U) {
                emit_event(
                    sdr_core::EventSeverity::Warning,
                    "recorder_overflow",
                    "fixed-band recorder queue overflow; DSP continues"
                );
            }
        } catch (...) {
            // A recorder staging failure must not transform into a DSP
            // failure.  The next R08 phase replaces this staging drain with a
            // native binary writer and an explicit writer-failure contract.
            channel.recorder_queue_blocks_dropped_.fetch_add(1U, std::memory_order_relaxed);
            channel.recorder_queue_samples_dropped_.fetch_add(
                block.sample_count,
                std::memory_order_relaxed
            );
        }
    }

    void tee_spectrum_frame(FixedBandChannelState& channel, const sdr_core::SpectrumFrame& frame) noexcept {
        if (!channel.spectrum_recording_writer_ || !channel.spectrum_recorder_queue_) {
            return;
        }
        try {
            const auto recorded = channel.spectrum_recorder_queue_->try_push(frame);
            if (recorded == sdr_core::PushResult::Pushed) {
                return;
            }
            if (recorded == sdr_core::PushResult::Stopped) {
                if (channel.spectrum_writer_failed_.load(std::memory_order_relaxed)) {
                    channel.spectrum_recorder_frames_unavailable_.fetch_add(
                        1U,
                        std::memory_order_relaxed
                    );
                } else {
                    channel.spectrum_recorder_shutdown_frames_discarded_.fetch_add(
                        1U,
                        std::memory_order_relaxed
                    );
                }
                return;
            }
            channel.spectrum_recorder_frames_dropped_.fetch_add(1U, std::memory_order_relaxed);
            const auto drops = channel.spectrum_recorder_overflow_notifications_.fetch_add(
                1U,
                std::memory_order_relaxed
            ) + 1U;
            if (drops == 1U || drops % 65536U == 0U) {
                emit_event(
                    sdr_core::EventSeverity::Warning,
                    "spectrum_recorder_overflow",
                    "fixed-band spectrum recorder queue overflow; DSP continues"
                );
            }
        } catch (...) {
            channel.spectrum_recorder_frames_dropped_.fetch_add(1U, std::memory_order_relaxed);
        }
    }

    void annotate_frame(const FixedBandChannelState& channel,
        sdr_core::SpectrumFrame& frame,
        const sdr_core::DspBackendMetrics& backend_metrics
    ) const noexcept {
        frame.analog_bandwidth_hz = applied_.analog_bandwidth_hz;
        frame.dropped_samples_before =
            channel.counters_.iq_samples_dropped.load(std::memory_order_relaxed);
        frame.dropped_iq_blocks_before =
            channel.counters_.iq_blocks_dropped.load(std::memory_order_relaxed);
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

    void update_dsp_metrics(FixedBandChannelState& channel,
        const sdr_core::DspBackendMetrics& backend_metrics,
        const std::chrono::steady_clock::time_point started_at,
        const std::chrono::steady_clock::time_point processing_started,
        const bool attribute_wall_time = true
    ) {
        {
            std::lock_guard cache_lock(channel.backend_metrics_mutex_);
            channel.backend_metrics_cache_ = backend_metrics;
        }
        channel.counters_.fft_frames_computed.store(
            backend_metrics.fft_frames_computed,
            std::memory_order_relaxed
        );
        channel.counters_.fft_frames_dropped.store(
            backend_metrics.fft_frames_dropped,
            std::memory_order_relaxed
        );
        channel.counters_.stage_timing_mask.fetch_or(
            backend_metrics.stage_timing_mask,
            std::memory_order_relaxed
        );
        channel.counters_.input_unpack_ms.store(
            static_cast<double>(backend_metrics.input_unpack_ns) / 1.0e6,
            std::memory_order_relaxed
        );
        channel.counters_.window_ms.store(
            static_cast<double>(backend_metrics.window_ns) / 1.0e6,
            std::memory_order_relaxed
        );
        channel.counters_.fft_ms.store(
            static_cast<double>(backend_metrics.fft_ns) / 1.0e6,
            std::memory_order_relaxed
        );
        channel.counters_.detector_ms.store(
            static_cast<double>(backend_metrics.detector_ns) / 1.0e6,
            std::memory_order_relaxed
        );
        const auto now = std::chrono::steady_clock::now();
        const double elapsed_ms =
            std::chrono::duration<double, std::milli>(now - processing_started).count();
        if (attribute_wall_time && backend_metrics.active_backend == sdr_core::ComputeBackendKind::Cpu) {
            double current = channel.counters_.cpu_processing_ms.load(std::memory_order_relaxed);
            while (!channel.counters_.cpu_processing_ms.compare_exchange_weak(
                current,
                current + elapsed_ms,
                std::memory_order_relaxed
            )) {
            }
        } else {
            // Backend stage counters are cumulative; expose them without
            // charging CUDA work to the CPU wall-time metric.
            channel.counters_.gpu_processing_ms.store(
                static_cast<double>(backend_metrics.gpu_processing_ns) / 1.0e6,
                std::memory_order_relaxed
            );
            channel.counters_.h2d_ms.store(
                static_cast<double>(backend_metrics.h2d_ns) / 1.0e6,
                std::memory_order_relaxed
            );
            channel.counters_.d2h_ms.store(
                static_cast<double>(backend_metrics.d2h_ns) / 1.0e6,
                std::memory_order_relaxed
            );
        }
        const double run_seconds =
            std::chrono::duration<double>(now - started_at).count();
        if (run_seconds > 0.0) {
            channel.counters_.analytical_fft_rate.store(
                static_cast<double>(backend_metrics.fft_frames_computed) /
                    run_seconds,
                std::memory_order_relaxed
            );
        }
    }

    void publish_persistence(FixedBandChannelState& channel, const sdr_core::SpectrumFrame& frame) noexcept {
        if (!channel.persistence_ || !channel.persistence_queue_) {
            return;
        }
        try {
#if SDR_CORE_PROFILING_ENABLED
            const auto persistence_started = std::chrono::steady_clock::now();
#endif
            auto snapshot = channel.persistence_->update(frame);
            channel.counters_.persistence_updates.store(
                channel.persistence_->processed_frames(), std::memory_order_relaxed
            );
#if SDR_CORE_PROFILING_ENABLED
            add_profile_elapsed_ms(
                channel.counters_.persistence_processing_ms,
                persistence_started
            );
            channel.counters_.stage_timing_mask.fetch_or(
                sdr_core::stage_timing_mask(sdr_core::DspStageTimingFlag::Persistence),
                std::memory_order_relaxed
            );
#endif
            if (!snapshot.has_value()) {
                return;
            }
            const auto result = channel.persistence_queue_->try_push(std::move(*snapshot));
            if (result == sdr_core::PushResult::Evicted) {
                channel.persistence_snapshots_superseded_.fetch_add(
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

    void publish(FixedBandChannelState& channel, sdr_core::SpectrumFrame frame) noexcept {
        try {
#if SDR_CORE_PROFILING_ENABLED
            const auto publication_started = std::chrono::steady_clock::now();
#endif
            const double latency_ms = std::max(
                0.0,
                static_cast<double>(system_time_ns() - frame.timestamp_ns) / 1.0e6
            );
            channel.end_to_end_latency_ms_.store(latency_ms, std::memory_order_relaxed);
            std::optional<sdr_core::SpectrumFrame> spectrum_recording_frame;
            if (channel.spectrum_recording_writer_ && channel.spectrum_recorder_queue_) {
                spectrum_recording_frame = frame;
            }
            const auto result = channel.spectrum_queue_->try_push(std::move(frame));
            if (result == sdr_core::PushResult::Pushed ||
                result == sdr_core::PushResult::Evicted) {
                channel.counters_.spectrum_snapshots_emitted.fetch_add(
                    1U,
                    std::memory_order_relaxed
                );
                if (result == sdr_core::PushResult::Evicted) {
                    channel.snapshots_superseded_.fetch_add(1U, std::memory_order_relaxed);
                }
                if (spectrum_recording_frame.has_value()) {
                    tee_spectrum_frame(channel, *spectrum_recording_frame);
                }
            }
#if SDR_CORE_PROFILING_ENABLED
            add_profile_elapsed_ms(
                channel.counters_.publication_processing_ms,
                publication_started
            );
            channel.counters_.stage_timing_mask.fetch_or(
                sdr_core::stage_timing_mask(sdr_core::DspStageTimingFlag::Publication),
                std::memory_order_relaxed
            );
#endif
        } catch (...) {
            emit_event(
                sdr_core::EventSeverity::Error,
                "snapshot_publication_failure",
                "failed to publish fixed-band spectrum snapshot"
            );
        }
    }

    [[nodiscard]] sdr_core::EngineMetrics assemble_channel_engine_metrics(const FixedBandChannelState& channel) const {
        auto result = channel.counters_.snapshot();
        if (acquisition_queue_) {
            result.acquisition_queue_depth = acquisition_queue_->depth();
        }
        result.end_to_end_latency_ms =
            channel.end_to_end_latency_ms_.load(std::memory_order_relaxed);
        return result;
    }

    void account_abandoned() noexcept {
        try {
            if (!acquisition_queue_) {
                return;
            }
            std::uint64_t abandoned_samples = 0U;
            const auto abandoned = acquisition_queue_->abandon_with(
                [&abandoned_samples](const FixedBandIqEnvelope& block) noexcept {
                    abandoned_samples += block.primary.sample_count;
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

    void publish_sweep_line(FixedBandChannelState& channel, const sdr_core::SpectrumFrame& frame) noexcept {
        if (!channel.sweep_line_assembler_ || !channel.sweep_line_queue_) {
            return;
        }
        try {
            auto lines = channel.sweep_line_assembler_->admit(
                frame.frame_sequence,
                frame.timestamp_ns,
                sdr_core::SweepLineSegmentFrame{
                    .segment_index = 0U,
                    .spectrum = frame,
                }
            );
            const auto assembly_metrics = channel.sweep_line_assembler_->metrics();
            channel.completed_sweep_lines_.store(
                assembly_metrics.completed_lines, std::memory_order_relaxed
            );
            channel.gapped_sweep_lines_.store(
                assembly_metrics.gapped_lines, std::memory_order_relaxed
            );
            channel.sweep_line_capacity_evicted_.store(
                assembly_metrics.capacity_evicted_lines, std::memory_order_relaxed
            );
            for (auto& line : lines) {
                if (channel.config_.sweep_statistics_sink) {
                    const auto now = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count();
                    channel.config_.sweep_statistics_sink->consume(line, now);
                }
                const auto pushed = channel.sweep_line_queue_->try_push(std::move(line));
                if (pushed == sdr_core::PushResult::Evicted) {
                    channel.sweep_line_snapshots_superseded_.fetch_add(
                        1U, std::memory_order_relaxed
                    );
                }
            }
        } catch (const std::exception& error) {
            emit_event(
                sdr_core::EventSeverity::Critical,
                "continuous_sweep_line_failure",
                error.what()
            );
            mark_error();
            initiate_shutdown();
        } catch (...) {
            emit_event(
                sdr_core::EventSeverity::Critical,
                "continuous_sweep_line_failure",
                "failed to publish continuous sweep line"
            );
            mark_error();
            initiate_shutdown();
        }
    }

    void account_recorder_abandoned(FixedBandChannelState& channel) noexcept {
        try {
            if (!channel.recorder_queue_) {
                return;
            }
            std::uint64_t abandoned_samples = 0U;
            const auto abandoned = channel.recorder_queue_->abandon_with(
                [&abandoned_samples](const sdr_core::IqBlock& block) noexcept {
                    abandoned_samples += block.sample_count;
                }
            );
            if (abandoned == 0U) {
                return;
            }
            const bool writer_failed =
                channel.recorder_writer_failed_.load(std::memory_order_relaxed);
            if (writer_failed) {
                channel.recorder_writer_blocks_unavailable_.fetch_add(
                    abandoned,
                    std::memory_order_relaxed
                );
                channel.recorder_writer_samples_unavailable_.fetch_add(
                    abandoned_samples,
                    std::memory_order_relaxed
                );
            } else {
                channel.recorder_shutdown_blocks_discarded_.fetch_add(
                    abandoned,
                    std::memory_order_relaxed
                );
                channel.recorder_shutdown_samples_discarded_.fetch_add(
                    abandoned_samples,
                    std::memory_order_relaxed
                );
            }
            emit_event(
                sdr_core::EventSeverity::Warning,
                "recorder_queue_abandoned",
                (writer_failed
                     ? "recorder writer failure abandoned queued I/Q blocks: "
                     : "disconnect abandoned queued recorder I/Q blocks: ") +
                    std::to_string(abandoned)
            );
        } catch (...) {
        }
    }

    void account_spectrum_recorder_abandoned(FixedBandChannelState& channel) noexcept {
        try {
            if (!channel.spectrum_recorder_queue_) {
                return;
            }
            const auto abandoned = channel.spectrum_recorder_queue_->abandon();
            if (abandoned == 0U) {
                return;
            }
            if (channel.spectrum_writer_failed_.load(std::memory_order_relaxed)) {
                channel.spectrum_recorder_frames_unavailable_.fetch_add(
                    abandoned,
                    std::memory_order_relaxed
                );
            } else {
                channel.spectrum_recorder_shutdown_frames_discarded_.fetch_add(
                    abandoned,
                    std::memory_order_relaxed
                );
            }
            emit_event(
                sdr_core::EventSeverity::Warning,
                "spectrum_recorder_queue_abandoned",
                "disconnect abandoned queued spectrum recording frames: " +
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
    AppliedConfig applied_{};
    std::uint64_t config_generation_{};
    bool configured_{};
    sdr_core::StopToken stop_{sdr_core::make_stop_token()};
    std::unique_ptr<sdr_core::BoundedQueue<FixedBandIqEnvelope>> acquisition_queue_;
    std::unique_ptr<FixedBandChannelState> secondary_;
    std::unique_ptr<sdr_core::DualRxDspPublisher> paired_dsp_;
    std::shared_ptr<PairedSpectrumAnalyticalSink> paired_analytical_sink_;
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::DualRxSpectrumFrame>> paired_spectrum_queue_;
    mutable std::mutex paired_metrics_mutex_;
    sdr_core::DualRxDspMetrics paired_dsp_metrics_cache_;
    std::atomic<std::uint64_t> paired_snapshots_emitted_{}, paired_snapshots_superseded_{};
    std::atomic<double> paired_processing_ms_{};
    std::atomic<std::uint64_t> paired_snapshots_abandoned_{}, history_queue_snapshots_abandoned_{};
    std::unique_ptr<sdr_core::BoundedQueue<sdr_core::DiagnosticEvent>> event_queue_;
    std::atomic<std::uint32_t> transient_remaining_{};
    std::atomic<std::uint64_t> transient_blocks_discarded_{};
    std::atomic<std::uint64_t> transient_samples_discarded_{};
    std::atomic<std::uint64_t> acquisition_queue_blocks_dropped_{};
    std::atomic<std::uint64_t> acquisition_queue_samples_dropped_{};
    std::atomic<std::uint64_t> source_sequence_discontinuities_{};
    std::atomic<std::uint64_t> source_sample_index_discontinuities_{};
    std::atomic<std::uint64_t> source_timestamp_regressions_{};
    std::atomic<std::uint64_t> source_estimated_timestamp_blocks_{};
    std::atomic<std::uint64_t> shutdown_blocks_discarded_{};
    std::atomic<std::uint64_t> shutdown_samples_discarded_{};
    std::atomic<std::uint64_t> expected_cancellations_{};
    std::atomic<std::uint64_t> events_lost_{};
    std::atomic<std::uint64_t> event_sequence_{};
#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
    std::atomic<std::uint32_t> dsp_delay_for_test_ms_{};
#endif
    mutable std::mutex priority_event_mutex_;
    std::optional<sdr_core::DiagnosticEvent> pending_error_event_;
    std::uint64_t events_lost_reported_{};
    std::thread acquisition_thread_;
    std::thread dsp_thread_;
};

FixedBandEngine::FixedBandEngine(std::string uri, const std::uint32_t timeout_ms, std::optional<std::string> expected_serial,
                                 std::optional<ExpectedUsbConnection> expected_usb_connection)
    : impl_(std::make_unique<Impl>(std::move(uri), timeout_ms, std::move(expected_serial), std::move(expected_usb_connection))) {}
FixedBandEngine::~FixedBandEngine() noexcept = default;
AppliedConfig FixedBandEngine::configure(const FixedBandConfig& config) {
    return impl_->configure(config);
}

AppliedConfig FixedBandEngine::configure_paired(const PairedFixedBandConfig& config) {
    return impl_->configure_paired(config);
}
PairedFixedBandMetrics FixedBandEngine::paired_metrics() const {
    return impl_->paired_metrics();
}
std::vector<sdr_core::DualRxSpectrumFrame> FixedBandEngine::poll_paired_spectrum_frames(std::size_t max_items) {
    return impl_->poll_paired_spectrum_frames(max_items);
}
sdr_core::LatestDualRxSpectrumFrameDrain FixedBandEngine::drain_latest_paired_spectrum_frame() {
    return impl_->drain_latest_paired_spectrum_frame();
}
std::vector<sdr_core::PersistenceSnapshot> FixedBandEngine::poll_receiver_persistence_snapshots(
    ReceiverSelection receiver, std::size_t max_items) {
    return impl_->poll_receiver_persistence_snapshots(receiver, max_items);
}
std::vector<sdr_core::SweepLineFrame> FixedBandEngine::poll_receiver_sweep_line_frames(
    ReceiverSelection receiver, std::size_t max_items) {
    return impl_->poll_receiver_sweep_line_frames(receiver, max_items);
}
std::vector<sdr_core::IqBlock> FixedBandEngine::poll_receiver_recorded_iq_blocks(
    ReceiverSelection receiver, std::size_t max_items) {
    return impl_->poll_receiver_recorded_iq_blocks(receiver, max_items);
}

AppliedConfig FixedBandEngine::reconfigure(const FixedBandConfig& config) {
    return impl_->reconfigure(config);
}
void FixedBandEngine::start() { impl_->start(); }
bool FixedBandEngine::start_guarded(const std::shared_ptr<StartAdmissionGate>& gate) {
    if (!gate) invalid("guarded Start requires an admission gate");
    return impl_->start_guarded(gate);
}
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
LatestSpectrumFrameDrain FixedBandEngine::drain_latest_spectrum_frame() {
    return impl_->drain_latest_spectrum_frame();
}
std::vector<sdr_core::SweepLineFrame> FixedBandEngine::poll_sweep_line_frames(
    const std::size_t max_items
) {
    return impl_->poll_sweep_line_frames(max_items);
}
std::vector<sdr_core::IqBlock> FixedBandEngine::poll_recorded_iq_blocks(
    const std::size_t max_items
) {
    return impl_->poll_recorded_iq_blocks(max_items);
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

void FixedBandEngine::set_dsp_delay_for_test(const std::uint32_t milliseconds) noexcept {
    impl_->set_dsp_delay_for_test(milliseconds);
}
#endif
sdr_core::AnalyticalReadyDrain FixedBandEngine::drain_analytical_ready_events(
    ReceiverSelection receiver, std::size_t max_items) {
    return impl_->drain_analytical_ready_events(receiver, max_items);
}
}  // namespace sdr_pluto

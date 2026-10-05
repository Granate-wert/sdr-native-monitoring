#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <chrono>
#include <limits>
#include <mutex>
#include <utility>

namespace sdr_hackrf {
namespace {

#ifndef SDR_CORE_PROFILING_ENABLED
#define SDR_CORE_PROFILING_ENABLED 0
#endif

[[noreturn]] void invalid(const char* const message) {
    throw sdr_core::ConfigurationError(message);
}

[[nodiscard]] std::uint64_t saturating_add(
    const std::uint64_t left,
    const std::uint64_t right
) noexcept {
    const auto maximum = std::numeric_limits<std::uint64_t>::max();
    return right > maximum - left ? maximum : left + right;
}

#if SDR_CORE_PROFILING_ENABLED
[[nodiscard]] std::uint64_t elapsed_ns(
    const std::chrono::steady_clock::time_point before,
    const std::chrono::steady_clock::time_point after
) noexcept {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(after - before).count()
    );
}
#endif

}  // namespace

void validate_hackrf_fixed_band_dsp_config(const HackrfFixedBandDspConfig& config) {
    sdr_core::validate(config.dsp);
    const auto journal_bytes = sdr_core::analytical_ready_reserved_bytes(config.analytical_event_capacity);
    const auto output_bytes = (2ULL * config.dsp_output_capacity + config.presentation_capacity) *
        (16ULL * config.dsp.fft_size + sizeof(sdr_core::SpectrumFrame));
    if (config.analytical_event_capacity != 0U && output_bytes + journal_bytes > 128ULL * 1024U * 1024U)
        invalid("HackRF analytical/presentation/journal payload exceeds 128 MiB");
    sdr_core::validate(config.source);
    if (config.source.source_type != sdr_core::SourceType::LiveIq) {
        invalid("HackRF fixed-band source_type must be LiveIq");
    }
    if (!config.source.uri.empty() || !config.source.device_serial.empty()) {
        invalid("HackRF fixed-band source must not expose URI or device serial");
    }
    if (!config.source.metadata_json.empty()) {
        invalid("HackRF fixed-band source metadata must remain route-free and empty");
    }
    if (config.source.backend_id != "native.libhackrf.rx.v1") {
        invalid("HackRF fixed-band backend_id must be native.libhackrf.rx.v1");
    }
    if (config.dsp.unit != sdr_core::SpectrumUnit::DbfsBin &&
        config.dsp.unit != sdr_core::SpectrumUnit::DbfsHz) {
        invalid("HackRF fixed-band DSP admits only uncalibrated dBFS units");
    }
    if (config.dsp.calibration_status != sdr_core::CalibrationStatus::Uncalibrated ||
        !config.dsp.calibration_profile_id.empty()) {
        invalid("HackRF fixed-band DSP has no applicable calibration profile");
    }
    if (config.dsp_output_capacity < config.dsp.batch_size ||
        config.dsp_output_capacity > hackrf_fixed_band_max_dsp_output_capacity) {
        invalid("HackRF DSP output capacity must cover one batch and stay bounded");
    }
    if (config.presentation_capacity == 0U ||
        config.presentation_capacity > hackrf_fixed_band_max_presentation_capacity) {
        invalid("HackRF presentation capacity is outside the bounded range");
    }
    sdr_core::validate(config.persistence);
    if (config.layer_event_capacity > sdr_core::LayerReadyJournal::max_capacity ||
        (config.layer_event_capacity && !config.persistence.enabled)) {
        invalid("HackRF density journal requires enabled persistence and capacity in [0,4096]");
    }
    if (config.persistence.power_bins < 16U || config.persistence.power_bins > 4096U ||
        config.persistence.window_frames > 1000000U) {
        invalid("HackRF persistence dimensions are outside the bounded range");
    }
    if (config.persistence.enabled) {
        const auto n = static_cast<std::uint64_t>(config.dsp.fft_size);
        const auto cells = n * config.persistence.power_bins;
        const auto ring = config.persistence.mode == sdr_core::PersistenceMode::RollingExact
                              ? n * config.persistence.window_frames * 4U : 0U;
        const auto bytes = cells * 4U * 5U + n * 8U * 4U + ring +
            sdr_core::density_layer_scalar_reservation_bytes +
            (config.layer_event_capacity ? sdr_core::LayerReadyJournal::reserved_bytes(config.layer_event_capacity) : 0U);
        if (bytes > 256U * 1024U * 1024U) {
            invalid("HackRF persistence exceeds the 256 MiB allocation policy");
        }
    }
}

struct HackrfFixedBandDsp::Impl final {
    explicit Impl(HackrfFixedBandDspConfig value)
        : config(std::move(value)),
          presentation(config.presentation_capacity, hackrf_fixed_band_presentation_overflow_policy),
          persistence_queue(2U, sdr_core::OverflowPolicy::DropOldest),
          density_journal(config.layer_event_capacity
              ? std::make_shared<sdr_core::LayerReadyJournal>(config.layer_event_capacity) : nullptr),
          persistence(config.persistence, density_journal) {
        sdr_core::DspOptions options;
        options.dc_removal = config.dc_removal;
        options.source = config.source;
        options.output_capacity = config.dsp_output_capacity;
        options.analytical_event_capacity = config.analytical_event_capacity;
        dsp = sdr_core::make_cpu_dsp_backend(std::move(options));
        dsp->configure(config.dsp);
        dsp->enable_owner_presentation();
    }

    HackrfFixedBandDspConfig config;
    std::unique_ptr<sdr_core::DspBackend> dsp;
    sdr_core::BoundedQueue<sdr_core::SpectrumFrame> presentation;
    sdr_core::BoundedQueue<sdr_core::PersistenceSnapshot> persistence_queue;
    std::shared_ptr<sdr_core::LayerReadyJournal> density_journal;
    sdr_core::PersistenceAccumulator persistence;
    mutable std::mutex mutex;
    std::uint64_t iq_blocks_processed{};
    std::uint64_t iq_samples_processed{};
    std::uint64_t locked_push_ns{};
    std::uint64_t dsp_push_poll_ns{};
    std::uint64_t persistence_call_ns{};
    std::uint64_t publication_queue_ns{};
    std::uint64_t source_sequence_discontinuities{};
    std::uint64_t source_sample_index_discontinuities{};
    std::uint64_t source_blocks_missing{};
    std::uint64_t source_samples_missing{};
    std::uint64_t source_timestamp_regressions{};
    std::uint64_t source_estimated_timestamp_blocks{};
    std::uint64_t expected_source_sequence{};
    std::uint64_t expected_sample_index{};
    std::int64_t last_timestamp_ns{};
    bool source_seen{};
};

HackrfFixedBandDsp::HackrfFixedBandDsp(HackrfFixedBandDspConfig config) {
    validate_hackrf_fixed_band_dsp_config(config);
    impl_ = std::make_unique<Impl>(std::move(config));
}

HackrfFixedBandDsp::~HackrfFixedBandDsp() = default;

void HackrfFixedBandDsp::push(HackrfRxLease lease) {
    if (!lease) {
        invalid("HackRF fixed-band DSP requires a non-empty RX lease");
    }
    auto block = lease.take_iq_block();
    if (block.sample_format != sdr_core::SampleFormat::ComplexInt8Interleaved) {
        invalid("HackRF fixed-band DSP accepts only ComplexInt8Interleaved");
    }
    sdr_core::validate(block);
    if (block.source_sequence == std::numeric_limits<std::uint64_t>::max() ||
        block.first_sample_index >
            std::numeric_limits<std::uint64_t>::max() - block.sample_count) {
        invalid("HackRF source cursor cannot advance without overflow");
    }

    std::lock_guard lock(impl_->mutex);
#if SDR_CORE_PROFILING_ENABLED
    const auto locked_started = std::chrono::steady_clock::now();
#endif
    const auto sequence_discontinuous = impl_->source_seen
                                            ? block.source_sequence != impl_->expected_source_sequence
                                            : block.source_sequence != 0U;
    const auto sample_discontinuous = impl_->source_seen
                                          ? block.first_sample_index != impl_->expected_sample_index
                                          : block.first_sample_index != 0U;
    const auto timestamp_regressed = impl_->source_seen &&
                                     block.timestamp_ns < impl_->last_timestamp_ns;

#if SDR_CORE_PROFILING_ENABLED
    const auto dsp_started = std::chrono::steady_clock::now();
#endif
    impl_->dsp->push_iq(block);
    auto frames = impl_->dsp->poll_spectrum(0U, false);
#if SDR_CORE_PROFILING_ENABLED
    impl_->dsp_push_poll_ns = saturating_add(
        impl_->dsp_push_poll_ns,
        elapsed_ns(dsp_started, std::chrono::steady_clock::now())
    );
#endif

    if (sequence_discontinuous) {
        ++impl_->source_sequence_discontinuities;
        if (block.source_sequence > impl_->expected_source_sequence) {
            impl_->source_blocks_missing = saturating_add(
                impl_->source_blocks_missing,
                block.source_sequence - impl_->expected_source_sequence
            );
        }
    }
    if (sample_discontinuous) {
        ++impl_->source_sample_index_discontinuities;
        if (block.first_sample_index > impl_->expected_sample_index) {
            impl_->source_samples_missing = saturating_add(
                impl_->source_samples_missing,
                block.first_sample_index - impl_->expected_sample_index
            );
        }
    }
    if (timestamp_regressed) {
        ++impl_->source_timestamp_regressions;
    }
    if (sdr_core::has_flag(block.flags, sdr_core::QualityFlag::TimestampEstimated)) {
        ++impl_->source_estimated_timestamp_blocks;
    }
    ++impl_->iq_blocks_processed;
    impl_->iq_samples_processed = saturating_add(
        impl_->iq_samples_processed,
        block.sample_count
    );
    impl_->expected_source_sequence = block.source_sequence + 1U;
    impl_->expected_sample_index = block.first_sample_index + block.sample_count;
    impl_->last_timestamp_ns = block.timestamp_ns;
    impl_->source_seen = true;

    const auto missing_blocks = impl_->source_blocks_missing;
    const auto missing_samples = impl_->source_samples_missing;
    // Preserve the canonical CPU detector-output sequence. It is monotonic
    // across polls and includes evicted outputs; analytical FFT count is a
    // DIFFERENT domain when averaging_frames > 1 and cannot derive this ID.
    for (auto& frame : frames) {
        frame.dropped_iq_blocks_before = missing_blocks;
        frame.dropped_samples_before = missing_samples;
        // `dropped_fft_frames_before` and FftDropped are canonical analytical
        // DSP provenance. A fresh-window eviction here happens *after* the
        // frame was computed; preserving it in QueueStats rather than writing
        // it into the frame prevents presentation coalescing from masquerading
        // as input/FFT loss.
        if (frame.dropped_fft_frames_before != 0U) {
            frame.quality_flags =
                frame.quality_flags | sdr_core::QualityFlag::FftDropped;
        }
        // SAME canonical accumulation on detector outputs, BEFORE the lossy
        // presentation boundary. No raw IQ/Python/UI frame averaging.
#if SDR_CORE_PROFILING_ENABLED
        const auto persistence_started = std::chrono::steady_clock::now();
#endif
        auto density = impl_->persistence.update(frame);
#if SDR_CORE_PROFILING_ENABLED
        impl_->persistence_call_ns = saturating_add(
            impl_->persistence_call_ns,
            elapsed_ns(persistence_started, std::chrono::steady_clock::now())
        );
        const auto publication_started = std::chrono::steady_clock::now();
#endif
        if (density) {
            static_cast<void>(impl_->persistence_queue.try_push(std::move(*density)));
        }
        const auto receipt = frame.analytical_ready;
        const auto queued = impl_->presentation.push_with_eviction(std::move(frame),
            [this](const sdr_core::SpectrumFrame& evicted) noexcept {
                if (evicted.analytical_ready) impl_->dsp->record_owner_presentation(*evicted.analytical_ready,
                    sdr_core::OwnerPresentationDisposition::Superseded);
            }); // LatestWins never waits; its actual evicted item is the BACK, not the front.
        if (receipt && queued != sdr_core::PushResult::Pushed && queued != sdr_core::PushResult::Evicted)
            impl_->dsp->record_owner_presentation(*receipt, sdr_core::OwnerPresentationDisposition::Cancelled);
#if SDR_CORE_PROFILING_ENABLED
        impl_->publication_queue_ns = saturating_add(
            impl_->publication_queue_ns,
            elapsed_ns(publication_started, std::chrono::steady_clock::now())
        );
#endif
    }
#if SDR_CORE_PROFILING_ENABLED
    impl_->locked_push_ns = saturating_add(
        impl_->locked_push_ns,
        elapsed_ns(locked_started, std::chrono::steady_clock::now())
    );
#endif
}

std::vector<sdr_core::SpectrumFrame> HackrfFixedBandDsp::poll_spectrum_frames(
    const std::size_t max_items
) {
    std::vector<sdr_core::SpectrumFrame> result;
    sdr_core::SpectrumFrame frame;
    while ((max_items == 0U || result.size() < max_items) &&
           impl_->presentation.try_pop(frame)) {
        if (frame.analytical_ready) impl_->dsp->record_owner_presentation(*frame.analytical_ready,
            sdr_core::OwnerPresentationDisposition::Forwarded);
        result.push_back(std::move(frame));
    }
    return result;
}

HackrfLatestSpectrumFrameDrain HackrfFixedBandDsp::drain_latest_spectrum_frame() {
    HackrfLatestSpectrumFrameDrain result;
    sdr_core::SpectrumFrame frame;
    std::uint32_t consumed = 0U;
    // A producer can refill concurrently; one bridge call must never chase it
    // indefinitely or delay the GUI. The next poll handles any newer frames.
    while (consumed < impl_->config.presentation_capacity &&
           impl_->presentation.try_pop(frame)) {
        if (result.frame.has_value()) {
            if (result.frame->analytical_ready) impl_->dsp->record_owner_presentation(*result.frame->analytical_ready,
                sdr_core::OwnerPresentationDisposition::Coalesced);
            ++result.coalesced_frames;
        }
        result.frame = std::move(frame);
        ++consumed;
    }
    if (result.frame && result.frame->analytical_ready)
        impl_->dsp->record_owner_presentation(*result.frame->analytical_ready,
            sdr_core::OwnerPresentationDisposition::Forwarded);
    return result;
}

std::vector<sdr_core::PersistenceSnapshot> HackrfFixedBandDsp::poll_persistence_snapshots(
    const std::size_t max_items
) {
    std::vector<sdr_core::PersistenceSnapshot> result;
    sdr_core::PersistenceSnapshot frame;
    while ((max_items == 0U || result.size() < max_items) &&
           impl_->persistence_queue.try_pop(frame)) {
        result.push_back(std::move(frame));
    }
    return result;
}

HackrfFixedBandDspMetrics HackrfFixedBandDsp::metrics() const {
    std::lock_guard lock(impl_->mutex);
    HackrfFixedBandDspMetrics result;
    result.iq_blocks_processed = impl_->iq_blocks_processed;
    result.iq_samples_processed = impl_->iq_samples_processed;
    result.stage_timing_available = SDR_CORE_PROFILING_ENABLED != 0;
    result.locked_push_ns = impl_->locked_push_ns;
    result.dsp_push_poll_ns = impl_->dsp_push_poll_ns;
    result.persistence_call_ns = impl_->persistence_call_ns;
    const auto persistence_timing = impl_->persistence.profiling_timing();
    result.persistence_histogram_update_ns = persistence_timing.histogram_update_ns;
    result.persistence_snapshot_build_ns = persistence_timing.snapshot_build_ns;
    result.persistence_snapshot_count = persistence_timing.snapshot_count;
    result.publication_queue_ns = impl_->publication_queue_ns;
    result.source_sequence_discontinuities = impl_->source_sequence_discontinuities;
    result.source_sample_index_discontinuities =
        impl_->source_sample_index_discontinuities;
    result.source_blocks_missing = impl_->source_blocks_missing;
    result.source_samples_missing = impl_->source_samples_missing;
    result.source_timestamp_regressions = impl_->source_timestamp_regressions;
    result.source_estimated_timestamp_blocks = impl_->source_estimated_timestamp_blocks;
    result.dsp = impl_->dsp->metrics();
    result.presentation = impl_->presentation.stats();
    result.persistence_updates = impl_->persistence.processed_frames();
    result.persistence = impl_->persistence_queue.stats();
    result.presentation_frames_superseded = result.presentation.dropped;
    result.presentation_frames_abandoned = result.presentation.abandoned;
    return result;
}

HackrfFixedBandDspDeliveryAssessment
assess_hackrf_fixed_band_dsp_delivery(const HackrfFixedBandDspMetrics& metrics) noexcept {
    HackrfFixedBandDspDeliveryAssessment result;
    result.source_cursor_continuity_clean =
        metrics.source_sequence_discontinuities == 0U &&
        metrics.source_sample_index_discontinuities == 0U &&
        metrics.source_blocks_missing == 0U &&
        metrics.source_samples_missing == 0U;
    result.cpu_fft_loss_free = metrics.dsp.fft_frames_dropped == 0U;
    result.analytical_pipeline_clean =
        result.source_cursor_continuity_clean && result.cpu_fft_loss_free;
    result.presentation_delivery_clean =
        metrics.presentation_frames_superseded == 0U &&
        metrics.presentation_frames_abandoned == 0U;
    result.analytical_fft_frames_computed = metrics.dsp.fft_frames_computed;
    result.analytical_fft_frames_dropped = metrics.dsp.fft_frames_dropped;
    result.presentation_frames_superseded = metrics.presentation_frames_superseded;
    result.presentation_frames_abandoned = metrics.presentation_frames_abandoned;
    result.presentation_capacity = metrics.presentation.capacity;
    result.presentation_high_water = metrics.presentation.high_water;
    return result;
}

std::uint64_t HackrfFixedBandDsp::discard_presentation_frames() {
    std::lock_guard lock(impl_->mutex);
    return impl_->presentation.abandon_with([this](const sdr_core::SpectrumFrame& frame) noexcept {
        if (frame.analytical_ready) impl_->dsp->record_owner_presentation(*frame.analytical_ready,
            sdr_core::OwnerPresentationDisposition::Cancelled);
    });
}

sdr_core::AnalyticalReadyDrain HackrfFixedBandDsp::drain_analytical_ready_events(std::size_t max_items) {
    return impl_->dsp->drain_analytical_ready(max_items);
}
sdr_core::LayerReadyDrain HackrfFixedBandDsp::drain_density_layer_ready_events(std::size_t max_items) {
    if (!impl_->density_journal) invalid("HackRF density creation journal is disabled");
    return impl_->density_journal->drain(max_items);
}
}  // namespace sdr_hackrf

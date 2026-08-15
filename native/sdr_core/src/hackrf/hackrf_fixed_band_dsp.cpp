#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <limits>
#include <mutex>
#include <utility>

namespace sdr_hackrf {
namespace {

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

}  // namespace

void validate_hackrf_fixed_band_dsp_config(const HackrfFixedBandDspConfig& config) {
    sdr_core::validate(config.dsp);
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
}

struct HackrfFixedBandDsp::Impl final {
    explicit Impl(HackrfFixedBandDspConfig value)
        : config(std::move(value)),
          presentation(config.presentation_capacity, hackrf_fixed_band_presentation_overflow_policy) {
        sdr_core::DspOptions options;
        options.dc_removal = config.dc_removal;
        options.source = config.source;
        options.output_capacity = config.dsp_output_capacity;
        dsp = sdr_core::make_cpu_dsp_backend(std::move(options));
        dsp->configure(config.dsp);
    }

    HackrfFixedBandDspConfig config;
    std::unique_ptr<sdr_core::DspBackend> dsp;
    sdr_core::BoundedQueue<sdr_core::SpectrumFrame> presentation;
    mutable std::mutex mutex;
    std::uint64_t iq_blocks_processed{};
    std::uint64_t iq_samples_processed{};
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
    const auto sequence_discontinuous = impl_->source_seen
                                            ? block.source_sequence != impl_->expected_source_sequence
                                            : block.source_sequence != 0U;
    const auto sample_discontinuous = impl_->source_seen
                                          ? block.first_sample_index != impl_->expected_sample_index
                                          : block.first_sample_index != 0U;
    const auto timestamp_regressed = impl_->source_seen &&
                                     block.timestamp_ns < impl_->last_timestamp_ns;

    impl_->dsp->push_iq(block);
    auto frames = impl_->dsp->poll_spectrum(0U, false);

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
    // CPU DSP batches restart their local frame sequence at each poll.  Its
    // global computed-frame counter is canonical across all admitted input,
    // including an intentional analytical drop, so derive publication order
    // from that monotonic source rather than a batch-local frame field.
    const auto computed_fft_frames = impl_->dsp->metrics().fft_frames_computed;
    if (frames.size() > computed_fft_frames) {
        invalid("HackRF publication frame count exceeds computed FFT count");
    }
    auto presentation_frame_sequence =
        computed_fft_frames - static_cast<std::uint64_t>(frames.size());
    for (auto& frame : frames) {
        frame.frame_sequence = presentation_frame_sequence++;
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
        static_cast<void>(impl_->presentation.try_push(std::move(frame)));
    }
}

std::vector<sdr_core::SpectrumFrame> HackrfFixedBandDsp::poll_spectrum_frames(
    const std::size_t max_items
) {
    std::vector<sdr_core::SpectrumFrame> result;
    sdr_core::SpectrumFrame frame;
    while ((max_items == 0U || result.size() < max_items) &&
           impl_->presentation.try_pop(frame)) {
        result.push_back(std::move(frame));
    }
    return result;
}

HackrfFixedBandDspMetrics HackrfFixedBandDsp::metrics() const {
    std::lock_guard lock(impl_->mutex);
    HackrfFixedBandDspMetrics result;
    result.iq_blocks_processed = impl_->iq_blocks_processed;
    result.iq_samples_processed = impl_->iq_samples_processed;
    result.source_sequence_discontinuities = impl_->source_sequence_discontinuities;
    result.source_sample_index_discontinuities =
        impl_->source_sample_index_discontinuities;
    result.source_blocks_missing = impl_->source_blocks_missing;
    result.source_samples_missing = impl_->source_samples_missing;
    result.source_timestamp_regressions = impl_->source_timestamp_regressions;
    result.source_estimated_timestamp_blocks = impl_->source_estimated_timestamp_blocks;
    result.dsp = impl_->dsp->metrics();
    result.presentation = impl_->presentation.stats();
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

}  // namespace sdr_hackrf

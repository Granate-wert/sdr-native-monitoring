#include "sdr_core/dual_rx_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <utility>

namespace sdr_core {

namespace {

[[nodiscard]] bool same_numerical_dsp(
    const DspConfig& left,
    const DspConfig& right
) noexcept {
    return left.fft_size == right.fft_size && left.hop_size == right.hop_size &&
           left.window == right.window && left.detector == right.detector &&
           left.unit == right.unit && left.precision_mode == right.precision_mode &&
           left.batch_size == right.batch_size &&
           left.averaging_frames == right.averaging_frames &&
           left.kaiser_beta == right.kaiser_beta && left.schema_version == right.schema_version;
}

[[nodiscard]] bool same_capture_epoch(const IqBlock& left, const IqBlock& right) noexcept {
    return left.source_sequence == right.source_sequence &&
           left.first_sample_index == right.first_sample_index &&
           left.timestamp_ns == right.timestamp_ns &&
           left.config_generation == right.config_generation &&
           left.sample_count == right.sample_count && left.sample_format == right.sample_format &&
           left.sample_rate_hz == right.sample_rate_hz &&
           left.center_frequency_hz == right.center_frequency_hz;
}

[[nodiscard]] bool same_spectrum_epoch(
    const SpectrumFrame& left,
    const SpectrumFrame& right
) noexcept {
    return left.first_sample_index == right.first_sample_index &&
           left.timestamp_ns == right.timestamp_ns &&
           left.config_generation == right.config_generation &&
           left.fft_size == right.fft_size && left.hop_size == right.hop_size &&
           left.sample_rate_hz == right.sample_rate_hz &&
           left.center_frequency_hz == right.center_frequency_hz;
}

void require_configured(const bool configured) {
    if (!configured) {
        throw ConfigurationError("dual-RX DSP publisher is not configured");
    }
}

}  // namespace

void validate(const DualRxDspConfig& value) {
    validate(value.primary.source);
    validate(value.secondary.source);
    validate(value.primary.dsp);
    validate(value.secondary.dsp);
    if (value.primary.source.source_id == value.secondary.source.source_id) {
        throw ConfigurationError("dual-RX DSP channels require distinct source identifiers");
    }
    if (!same_numerical_dsp(value.primary.dsp, value.secondary.dsp)) {
        throw ConfigurationError(
            "dual-RX DSP channels require identical numerical FFT/window/detector configuration"
        );
    }
    if (value.output_queue_capacity == 0U || value.output_queue_capacity > 64U) {
        throw ConfigurationError("dual-RX DSP output queue capacity must be in [1, 64]");
    }
}

void DualRxDspPublisher::configure(const DualRxDspConfig& config) {
    validate(config);
    const auto shared_plan = make_cpu_dsp_shared_plan(config.primary.dsp);
    DspOptions primary_options;
    primary_options.dc_removal = config.dc_removal_block_mean
                                     ? DcRemovalMode::BlockMean
                                     : DcRemovalMode::Off;
    primary_options.source = config.primary.source;
    primary_options.output_capacity = config.output_queue_capacity;
    primary_options.cpu_shared_plan = shared_plan;
    DspOptions secondary_options = primary_options;
    secondary_options.source = config.secondary.source;

    auto primary = make_cpu_dsp_backend(std::move(primary_options));
    auto secondary = make_cpu_dsp_backend(std::move(secondary_options));
    primary->configure(config.primary.dsp);
    secondary->configure(config.secondary.dsp);

    config_ = config;
    primary_backend_ = std::move(primary);
    secondary_backend_ = std::move(secondary);
    output_queue_ = std::make_unique<BoundedQueue<DualRxSpectrumFrame>>(
        config.output_queue_capacity, OverflowPolicy::LatestWins
    );
    configured_ = true;
    synchronization_epoch_ = 1U;
    shared_input_gaps_ = 0U;
    pairing_mismatches_ = 0U;
    input_epochs_received_ = 0U;
    paired_frames_published_ = 0U;
    paired_frames_superseded_ = 0U;
    last_source_sequence_ = 0U;
    last_sample_end_ = 0U;
    last_config_generation_ = 0U;
    input_epoch_valid_ = false;
}

void DualRxDspPublisher::push(const IqBlock& primary, const IqBlock& secondary) {
    require_configured(configured_);
    if (!same_capture_epoch(primary, secondary)) {
        ++pairing_mismatches_;
        reset_for_shared_gap();
        return;
    }
    if (input_epoch_valid_ &&
        (primary.source_sequence != last_source_sequence_ + 1U ||
         primary.first_sample_index != last_sample_end_ ||
         primary.config_generation != last_config_generation_)) {
        reset_for_shared_gap();
    }
    input_epoch_valid_ = true;
    last_source_sequence_ = primary.source_sequence;
    last_sample_end_ = primary.first_sample_index + primary.sample_count;
    last_config_generation_ = primary.config_generation;
    ++input_epochs_received_;

    primary_backend_->push_iq(primary);
    secondary_backend_->push_iq(secondary);
    publish_ready_frames();
}

void DualRxDspPublisher::mark_shared_gap() {
    require_configured(configured_);
    reset_for_shared_gap();
}

void DualRxDspPublisher::reset() {
    require_configured(configured_);
    primary_backend_->reset();
    secondary_backend_->reset();
    output_queue_ = std::make_unique<BoundedQueue<DualRxSpectrumFrame>>(
        config_.output_queue_capacity, OverflowPolicy::LatestWins
    );
    ++synchronization_epoch_;
    input_epoch_valid_ = false;
}

std::vector<DualRxSpectrumFrame> DualRxDspPublisher::poll_spectrum_frames(
    const std::size_t max_items
) {
    require_configured(configured_);
    std::vector<DualRxSpectrumFrame> result;
    DualRxSpectrumFrame frame;
    while ((max_items == 0U || result.size() < max_items) && output_queue_->try_pop(frame)) {
        result.push_back(std::move(frame));
    }
    return result;
}

LatestDualRxSpectrumFrameDrain DualRxDspPublisher::drain_latest_spectrum_frame() {
    require_configured(configured_);
    LatestDualRxSpectrumFrameDrain result;
    DualRxSpectrumFrame frame;
    while (output_queue_->try_pop(frame)) {
        if (result.frame.has_value()) {
            ++result.coalesced_frames;
        }
        result.frame = std::move(frame);
    }
    return result;
}

DualRxDspMetrics DualRxDspPublisher::metrics() const {
    require_configured(configured_);
    DualRxDspMetrics result;
    result.input_epochs_received = input_epochs_received_;
    result.shared_input_gaps = shared_input_gaps_;
    result.pairing_mismatches = pairing_mismatches_;
    result.paired_frames_published = paired_frames_published_;
    result.paired_frames_superseded = paired_frames_superseded_;
    result.output_queue = output_queue_->stats();
    result.primary = primary_backend_->metrics();
    result.secondary = secondary_backend_->metrics();
    return result;
}

void DualRxDspPublisher::reset_for_shared_gap() {
    primary_backend_->reset();
    secondary_backend_->reset();
    ++shared_input_gaps_;
    ++synchronization_epoch_;
    input_epoch_valid_ = false;
}

void DualRxDspPublisher::publish_ready_frames() {
    auto primary = primary_backend_->poll_spectrum(0U, false);
    auto secondary = secondary_backend_->poll_spectrum(0U, false);
    if (primary.size() != secondary.size()) {
        ++pairing_mismatches_;
        reset_for_shared_gap();
        return;
    }
    for (std::size_t index = 0U; index < primary.size(); ++index) {
        if (!same_spectrum_epoch(primary[index], secondary[index])) {
            ++pairing_mismatches_;
            reset_for_shared_gap();
            return;
        }
        DualRxSpectrumFrame frame;
        frame.synchronization_epoch = synchronization_epoch_;
        frame.first_sample_index = primary[index].first_sample_index;
        frame.timestamp_ns = primary[index].timestamp_ns;
        frame.config_generation = primary[index].config_generation;
        frame.shared_input_gaps_before = shared_input_gaps_;
        frame.primary = std::move(primary[index]);
        frame.secondary = std::move(secondary[index]);
        const auto result = output_queue_->try_push(std::move(frame));
        if (result == PushResult::Stopped || result == PushResult::Dropped ||
            result == PushResult::Full) {
            throw SdrNativeError(
                "dual-RX publication queue unexpectedly rejected a latest-wins frame"
            );
        }
        if (result == PushResult::Evicted) {
            ++paired_frames_superseded_;
        }
        ++paired_frames_published_;
    }
}

}  // namespace sdr_core

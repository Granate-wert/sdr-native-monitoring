#include "sdr_core/dual_rx_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <limits>
#include <string>
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

[[nodiscard]] std::uint64_t checked_multiply(
    const std::uint64_t left, const std::uint64_t right
) {
    if (left != 0U && right > std::numeric_limits<std::uint64_t>::max() / left) {
        throw ConfigurationError("dual-RX DSP resource accounting overflow");
    }
    return left * right;
}

[[nodiscard]] std::uint64_t checked_add(
    const std::uint64_t left, const std::uint64_t right
) {
    if (right > std::numeric_limits<std::uint64_t>::max() - left) {
        throw ConfigurationError("dual-RX DSP resource accounting overflow");
    }
    return left + right;
}

void validate_input(const IqBlock& block, const std::uint32_t maximum) {
    if (block.sample_count == 0U || block.sample_count > maximum || !block.samples) {
        throw ConfigurationError("dual-RX input exceeds admitted sample bound or has no payload");
    }
    std::uint64_t width = 0U;
    switch (block.sample_format) {
    case SampleFormat::ComplexInt8Interleaved: width = 2U; break;
    case SampleFormat::ComplexInt12InInt16Le:
    case SampleFormat::ComplexInt16Le: width = 4U; break;
    case SampleFormat::ComplexFloat32Le: width = 8U; break;
    default: throw ConfigurationError("dual-RX input sample format is invalid");
    }
    if (block.samples->size() != checked_multiply(block.sample_count, width) ||
        !std::isfinite(block.sample_rate_hz) || block.sample_rate_hz <= 0.0 ||
        !std::isfinite(block.center_frequency_hz) || block.center_frequency_hz <= 0.0 ||
        block.first_sample_index > static_cast<std::uint64_t>(
            std::numeric_limits<std::int64_t>::max()) - block.sample_count) {
        throw ConfigurationError("dual-RX input payload, RF geometry or sample-index range is invalid");
    }
    if (block.sample_format == SampleFormat::ComplexFloat32Le) {
        for (std::size_t offset = 0U; offset < block.samples->size(); offset += sizeof(float)) {
            float sample = 0.0F;
            std::memcpy(&sample, block.samples->data() + offset, sizeof(sample));
            if (!std::isfinite(sample)) {
                throw ConfigurationError("dual-RX input contains a non-finite sample");
            }
        }
    }
}

}  // namespace

void validate(const DualRxDspConfig& value) {
    validate(value.primary.source);
    validate(value.secondary.source);
    validate(value.primary.dsp);
    validate(value.secondary.dsp);
    validate(value.backend);
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
    static_cast<void>(dual_rx_dsp_resource_budget(value));
}

DualRxDspResourceBudget dual_rx_dsp_resource_budget(const DualRxDspConfig& value) {
    if (value.max_input_samples_per_push == 0U || value.primary.dsp.hop_size == 0U) {
        throw ConfigurationError("dual-RX DSP input bound and hop must be positive");
    }
    const auto capacity = checked_add(
        value.max_input_samples_per_push / value.primary.dsp.hop_size,
        static_cast<std::uint64_t>(value.primary.dsp.batch_size) + 2U
    );
    if (capacity > std::numeric_limits<std::uint32_t>::max()) {
        throw ConfigurationError("dual-RX analytical output capacity exceeds accounting range");
    }
    DualRxDspResourceBudget result;
    result.analytical_output_capacity = static_cast<std::uint32_t>(capacity);
    // Reserve both payloads at the widest supported CF32 representation.
    result.input_payload_bytes = checked_multiply(value.max_input_samples_per_push, 16U);
    result.dsp_working_bytes = checked_multiply(
        checked_multiply(value.primary.dsp.fft_size, 2U),
        116U + 48U * static_cast<std::uint64_t>(value.primary.dsp.batch_size)
    );
    // Both analytical buffers/drained vectors, existing final pairs and one
    // constructing pair. Moving shared spectral arrays does not duplicate them.
    result.spectrum_backlog_bytes = checked_multiply(
        checked_multiply(value.primary.dsp.fft_size, 32U),
        checked_add(capacity, static_cast<std::uint64_t>(value.output_queue_capacity) + 1U)
    );
    result.spectrum_backlog_bytes = checked_add(result.spectrum_backlog_bytes,
        checked_multiply(2U * sizeof(std::optional<AnalyticalReadyRef>),
            checked_add(capacity, static_cast<std::uint64_t>(value.output_queue_capacity) + 1U)));
    result.total_bytes = checked_add(
        checked_add(result.input_payload_bytes, result.dsp_working_bytes),
        result.spectrum_backlog_bytes
    );
    // Same combined component/total ceilings as the single live engine; never
    // two independent reservations of 128/512 MiB for a synchronized pair.
    constexpr std::uint64_t mib = 1024U * 1024U;
    if (result.input_payload_bytes > 128U * mib || result.dsp_working_bytes > 128U * mib ||
        result.spectrum_backlog_bytes > 128U * mib || result.total_bytes > 512U * mib) {
        throw ConfigurationError("dual-RX DSP configuration exceeds combined host payload budget");
    }
    return result;
}

void DualRxDspPublisher::configure(const DualRxDspConfig& config) {
    validate(config);
    const auto resource_budget = dual_rx_dsp_resource_budget(config);
    const auto shared_plan = make_cpu_dsp_shared_plan(config.primary.dsp);
    DspOptions primary_options;
    primary_options.dc_removal = config.dc_removal_block_mean
                                     ? DcRemovalMode::BlockMean
                                     : DcRemovalMode::Off;
    primary_options.source = config.primary.source;
    primary_options.output_capacity = resource_budget.analytical_output_capacity;
    primary_options.cpu_shared_plan = shared_plan;
    DspOptions secondary_options = primary_options;
    secondary_options.source = config.secondary.source;

    auto primary = make_dsp_backend(config.backend, std::move(primary_options));
    auto secondary = make_dsp_backend(config.backend, std::move(secondary_options));
    // The host reservation below does not cover cuFFT workspaces or the
    // generic GPU failover replay ledger. Do not admit a vendor pair under a
    // CPU-only memory promise. Forced unavailable choices retain the factory's
    // typed refusal; a self-tested GPU still needs explicit paired admission.
    if (primary->info().kind != ComputeBackendKind::Cpu ||
        secondary->info().kind != ComputeBackendKind::Cpu) {
        throw ConfigurationError("dual-RX vendor working-set/replay budget is not yet qualified");
    }
    primary->configure(config.primary.dsp);
    secondary->configure(config.secondary.dsp);
    auto output = std::make_unique<BoundedQueue<DualRxSpectrumFrame>>(
        config.output_queue_capacity, OverflowPolicy::LatestWins
    );

    config_ = config;
    resource_budget_ = resource_budget;
    primary_backend_ = std::move(primary);
    secondary_backend_ = std::move(secondary);
    output_queue_ = std::move(output);
    configured_ = true;
    synchronization_epoch_ = 1U;
    shared_input_gaps_ = 0U;
    pairing_mismatches_ = 0U;
    paired_frames_formed_ = 0U;
    analytical_consumer_ = {};
    shared_gap_consumer_ = {};
    input_epochs_received_ = 0U;
    paired_frames_published_ = 0U;
    paired_frames_superseded_ = 0U;
    paired_frames_abandoned_ = 0U;
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
    // Check BOTH payloads before either channel mutates its DSP history.
    validate_input(primary, config_.max_input_samples_per_push);
    validate_input(secondary, config_.max_input_samples_per_push);
    if (input_epoch_valid_ &&
        (last_source_sequence_ == std::numeric_limits<std::uint64_t>::max() ||
         primary.source_sequence != last_source_sequence_ + 1U ||
         primary.first_sample_index != last_sample_end_ ||
         primary.config_generation != last_config_generation_ ||
         primary.sample_rate_hz != last_sample_rate_hz_ ||
         primary.center_frequency_hz != last_center_frequency_hz_ ||
         primary.sample_format != last_sample_format_)) {
        reset_for_shared_gap();
    }
    input_epoch_valid_ = true;
    last_source_sequence_ = primary.source_sequence;
    last_sample_end_ = primary.first_sample_index + primary.sample_count;
    last_config_generation_ = primary.config_generation;
    last_sample_rate_hz_ = primary.sample_rate_hz;
    last_center_frequency_hz_ = primary.center_frequency_hz;
    last_sample_format_ = primary.sample_format;
    ++input_epochs_received_;

    const auto processing_epoch = synchronization_epoch_;
    try {
        primary_backend_->push_iq(primary);
        secondary_backend_->push_iq(secondary);
        publish_ready_frames();
    } catch (...) {
        // A typed backend failure must not leave a processed primary waiting
        // to be paired with a later secondary. Preserve the failure for owner.
        const auto first_failure = std::current_exception();
        if (synchronization_epoch_ == processing_epoch) {
            try { reset_for_shared_gap(); } catch (...) {
                // Best-effort cleanup must not replace the first backend/sink
                // cause. The owner receives that failure and terminates; this
                // is not permission to continue or retry a failed callback.
            }
        }
        std::rethrow_exception(first_failure);
    }
}

void DualRxDspPublisher::mark_shared_gap() {
    require_configured(configured_);
    reset_for_shared_gap();
}

void DualRxDspPublisher::set_analytical_consumer(AnalyticalConsumer consumer) {
    require_configured(configured_);
    analytical_consumer_ = std::move(consumer);
}

void DualRxDspPublisher::flush() {
    require_configured(configured_);
    const auto processing_epoch = synchronization_epoch_;
    try {
        publish_ready_frames(true);
    } catch (...) {
        const auto first_failure = std::current_exception();
        if (synchronization_epoch_ == processing_epoch) {
            try { reset_for_shared_gap(); } catch (...) {
                // Same first-cause rule for a terminal partial FFT batch.
            }
        }
        std::rethrow_exception(first_failure);
    }
}

void DualRxDspPublisher::set_shared_gap_consumer(std::function<void()> consumer) {
    require_configured(configured_);
    shared_gap_consumer_ = std::move(consumer);
}

void DualRxDspPublisher::reset() {
    require_configured(configured_);
    primary_backend_->reset();
    secondary_backend_->reset();
    paired_frames_abandoned_ += output_queue_->abandon();
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
    result.paired_frames_formed = paired_frames_formed_;
    result.paired_frames_published = paired_frames_published_;
    result.paired_frames_superseded = paired_frames_superseded_;
    result.paired_frames_abandoned = paired_frames_abandoned_;
    result.resource_budget = resource_budget_;
    result.output_queue = output_queue_->stats();
    result.primary = primary_backend_->metrics();
    result.secondary = secondary_backend_->metrics();
    // AUTO currently resolves to safe CPU in the common factory. Retain the
    // requested policy as well as the actual per-channel backend/fallback.
    result.primary.requested_preference = config_.backend.preference;
    result.secondary.requested_preference = config_.backend.preference;
    return result;
}

void DualRxDspPublisher::reset_for_shared_gap() {
    primary_backend_->reset();
    secondary_backend_->reset();
    paired_frames_abandoned_ += output_queue_->abandon();
    ++shared_input_gaps_;
    ++synchronization_epoch_;
    input_epoch_valid_ = false;
    if (shared_gap_consumer_) shared_gap_consumer_();
}

void DualRxDspPublisher::publish_ready_frames(const bool flush_partial_batch) {
    auto primary = primary_backend_->poll_spectrum(0U, flush_partial_batch);
    auto secondary = secondary_backend_->poll_spectrum(0U, flush_partial_batch);
    if (primary.size() != secondary.size()) {
        ++pairing_mismatches_;
        reset_for_shared_gap();
        return;
    }
    // Validate the entire burst before publishing any constituent pair.
    const auto primary_metrics = primary_backend_->metrics();
    const auto secondary_metrics = secondary_backend_->metrics();
    for (std::size_t index = 0U; index < primary.size(); ++index) {
        if (!same_spectrum_epoch(primary[index], secondary[index])) {
            ++pairing_mismatches_;
            reset_for_shared_gap();
            return;
        }
    }
    for (std::size_t index = 0U; index < primary.size(); ++index) {
        DualRxSpectrumFrame frame;
        frame.synchronization_epoch = synchronization_epoch_;
        frame.first_sample_index = primary[index].first_sample_index;
        frame.timestamp_ns = primary[index].timestamp_ns;
        frame.config_generation = primary[index].config_generation;
        frame.shared_input_gaps_before = shared_input_gaps_;
        frame.primary = std::move(primary[index]);
        frame.secondary = std::move(secondary[index]);
        if (shared_input_gaps_ != 0U) {
            frame.primary.quality_flags = frame.primary.quality_flags | QualityFlag::BackendDiscontinuity;
            frame.secondary.quality_flags = frame.secondary.quality_flags | QualityFlag::BackendDiscontinuity;
        }
        ++paired_frames_formed_;
        if (analytical_consumer_ &&
            !analytical_consumer_(frame, primary_metrics, secondary_metrics)) {
            continue;
        }
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

#include "sdr_hackrf/hackrf_sweep_analysis.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <stdexcept>
#include <utility>

namespace sdr_hackrf {
namespace {

constexpr std::uint64_t one_mhz = 1'000'000U;
constexpr std::uint32_t pinned_sample_rate_hz = 20'000'000U;
constexpr std::uint32_t pinned_filter_hz = 15'000'000U;
constexpr std::uint32_t pinned_offset_hz = 7'500'000U;
constexpr std::uint32_t pinned_step_hz = 20'000'000U;
constexpr std::uint32_t maximum_fft = 4096U;
constexpr std::size_t complex_samples_per_block = hackrf_sweep_ci8_bytes / 2U;
constexpr std::size_t maximum_analysis_blocks = sdr_core::sweep_max_segments / 2U;

void validate_config(const HackrfSweepAnalysisConfig& config) {
    sdr_core::validate(config.source);
    if (config.source.source_type != sdr_core::SourceType::LiveIq ||
        config.source.backend_id != "native.libhackrf.sweep.v1" ||
        !config.source.uri.empty() || !config.source.device_serial.empty() ||
        !config.source.metadata_json.empty()) {
        throw sdr_core::ConfigurationError("HackRF Sweep analysis source is invalid");
    }
    if (config.acquisition_epoch == 0U ||
        config.acquisition.config_generation == 0U ||
        config.acquisition.sequence.ranges.size() != 1U ||
        config.acquisition.sequence.style != HackrfSweepStyle::Interleaved ||
        config.acquisition.sequence.step_width_hz != pinned_step_hz ||
        config.acquisition.sequence.offset_hz != pinned_offset_hz ||
        config.acquisition.sample_rate_hz != pinned_sample_rate_hz ||
        config.acquisition.baseband_filter_hz != pinned_filter_hz ||
        // The shared line contract requires at least 256 analysis bins per
        // usable window. A 5 MHz crop contains N/4 bins at this pinned Fs,
        // so smaller transforms cannot publish truthful FFT geometry yet.
        config.fft_size < 1024U || config.fft_size > maximum_fft ||
        (config.fft_size & (config.fft_size - 1U)) != 0U ||
        (config.unit != sdr_core::SpectrumUnit::DbfsBin &&
         config.unit != sdr_core::SpectrumUnit::DbfsHz)) {
        throw sdr_core::ConfigurationError("HackRF Sweep FFT/RF geometry is not admitted");
    }
    sdr_core::DspConfig dsp;
    dsp.fft_size = config.fft_size;
    dsp.hop_size = config.fft_size;
    dsp.window = config.window;
    dsp.detector = config.detector;
    dsp.unit = config.unit;
    dsp.batch_size = 1U;
    dsp.averaging_frames = 1U;
    dsp.calibration_status = sdr_core::CalibrationStatus::Uncalibrated;
    sdr_core::validate(dsp);
}

std::uint32_t segment_index(const std::size_t plan_index,
                            const bool upper_subband) {
    const auto step = plan_index / 2U;
    const auto phase = plan_index % 2U;
    return static_cast<std::uint32_t>(
        step * 4U + (upper_subband ? 2U : 0U) + phase
    );
}

sdr_core::SweepLineDefinition make_definition(
    const HackrfSweepAnalysisConfig& config,
    const HackrfSweepSequenceGate& gate
) {
    const auto expected = gate.expected_blocks();
    if (expected.empty() || expected.size() > maximum_analysis_blocks) {
        throw sdr_core::ConfigurationError("HackRF Sweep analysis span exceeds 2048 segments");
    }
    const auto range = config.acquisition.sequence.ranges.front();
    const auto bin_hz = static_cast<double>(pinned_sample_rate_hz) /
                        static_cast<double>(config.fft_size);
    sdr_core::SweepLineDefinition definition;
    definition.source = config.source;
    definition.epoch = config.acquisition_epoch;
    // The official host excludes the first crop bin. The declaration begins
    // at the first retained FFT bin rather than claiming the requested start.
    definition.start_frequency_hz = static_cast<double>(range.start_mhz) * one_mhz + bin_hz;
    const auto hardware_stop_hz = static_cast<double>(range.stop_mhz) * one_mhz;
    const auto requested_stop_hz = config.analysis_stop_hz == 0.0
        ? hardware_stop_hz : config.analysis_stop_hz;
    if (!std::isfinite(requested_stop_hz) || requested_stop_hz > hardware_stop_hz ||
        requested_stop_hz <= definition.start_frequency_hz ||
        hardware_stop_hz - requested_stop_hz >= pinned_step_hz) {
        throw sdr_core::ConfigurationError("HackRF exact analysis end differs from its whole-step capture plan");
    }
    definition.stop_frequency_hz = requested_stop_hz;
    definition.target_spacing_hz = bin_hz;
    // One firmware FFT yields two distinct 5 MHz subbands. Do not present
    // their union as a contiguous 10 or 20 MHz analysis window.
    definition.analysis_window_hz = 5'000'000.0;
    definition.analysis_bins_per_usable_window = config.fft_size / 4U;
    definition.physical_fft_bin_width_hz = bin_hz;
    definition.physical_fft_size = config.fft_size;
    definition.unit = config.unit;
    definition.max_inflight_lines = 1U;
    definition.segments.reserve(expected.size() * 2U);
    for (std::size_t index = 0U; index < expected.size(); ++index) {
        const double base_hz = static_cast<double>(expected[index].reported_tuned_frequency_hz);
        const auto low = segment_index(index, false);
        const auto high = segment_index(index, true);
        for (const auto upper : {false, true}) {
            const auto start = base_hz + (upper ? 10'000'000.0 : 0.0) + bin_hz;
            const auto stop = std::min(requested_stop_hz, base_hz + (upper ? 15'000'000.0 : 5'000'000.0));
            if (start < stop) {
                definition.segments.push_back({upper ? high : low,
                    config.acquisition.config_generation, start, stop});
            }
        }
    }
    std::sort(definition.segments.begin(), definition.segments.end(), [](const auto& a, const auto& b) {
        return a.segment_index < b.segment_index;
    });
    sdr_core::validate(definition);
    const auto bins = static_cast<std::uint64_t>(std::ceil(
        (definition.stop_frequency_hz - definition.start_frequency_hz) / bin_hz - 1e-12));
    const auto reduced_bytes = bins * 128U +
        definition.segments.size() * static_cast<std::uint64_t>(config.fft_size) * 16U + config.fft_size * 8U;
    if (reduced_bytes > sdr_core::sweep_max_reduced_bytes) {
        throw sdr_core::ConfigurationError("HackRF Sweep reduced spectrum backlog exceeds 128 MiB");
    }
    return definition;
}

void append(std::vector<sdr_core::SweepLineFrame>& destination,
            std::vector<sdr_core::SweepLineFrame> source) {
    destination.insert(destination.end(),
                       std::make_move_iterator(source.begin()),
                       std::make_move_iterator(source.end()));
}

}  // namespace

struct HackrfSweepAnalysis::Impl final {
    explicit Impl(HackrfSweepAnalysisConfig requested)
        : config(std::move(requested)),
          gate(config.acquisition.sequence),
          line_definition(make_definition(config, gate)),
          assembler(line_definition) {
        sdr_core::DspOptions options;
        options.source = config.source;
        options.dc_removal = sdr_core::DcRemovalMode::Off;
        options.output_capacity = 2U;
        dsp = sdr_core::make_cpu_dsp_backend(std::move(options));
        sdr_core::DspConfig dsp_config;
        dsp_config.fft_size = config.fft_size;
        dsp_config.hop_size = config.fft_size;
        dsp_config.window = config.window;
        dsp_config.detector = config.detector;
        dsp_config.unit = config.unit;
        dsp_config.batch_size = 1U;
        dsp_config.averaging_frames = 1U;
        dsp_config.calibration_status = sdr_core::CalibrationStatus::Uncalibrated;
        dsp->configure(dsp_config);
    }

    void validate_block(const HackrfSweepQueuedBlock& block) const {
        const auto expected = gate.expected_blocks();
        if (block.plan_index >= expected.size() ||
            block.config_generation != config.acquisition.config_generation ||
            block.scan_epoch == 0U || block.continuity_epoch == 0U ||
            block.host_timestamp_ns <= 0 ||
            block.new_scan != (block.plan_index == 0U)) {
            throw sdr_core::ConfigurationError("HackRF Sweep block provenance is invalid");
        }
        const auto& header = expected[block.plan_index];
        if (block.reported_tuned_frequency_hz != header.reported_tuned_frequency_hz ||
            block.range_index != header.range_index ||
            block.step_index != header.step_index ||
            block.interleave_phase != header.interleave_phase) {
            throw sdr_core::ConfigurationError("HackRF Sweep block differs from RF plan");
        }
    }

    sdr_core::SpectrumFrame spectrum_for(const HackrfSweepQueuedBlock& block) {
        const auto tail_start = complex_samples_per_block - config.fft_size;
        const auto byte_start = tail_start * 2U;
        auto tail = std::make_shared<const std::vector<std::uint8_t>>(
            block.interleaved_ci8.begin() + static_cast<std::ptrdiff_t>(byte_start),
            block.interleaved_ci8.end()
        );
        sdr_core::IqBlock iq;
        iq.source_sequence = block.plan_index;
        // Scan-local location in admitted firmware payload, not a claim of
        // continuous ADC samples across retunes or missing callbacks.
        iq.first_sample_index =
            static_cast<std::uint64_t>(block.plan_index) * complex_samples_per_block +
            tail_start;
        iq.timestamp_ns = block.host_timestamp_ns;
        iq.center_frequency_hz =
            static_cast<double>(block.reported_tuned_frequency_hz) +
            config.acquisition.sequence.offset_hz;
        iq.sample_rate_hz = config.acquisition.sample_rate_hz;
        iq.sample_format = sdr_core::SampleFormat::ComplexInt8Interleaved;
        iq.sample_count = config.fft_size;
        iq.flags = sdr_core::QualityFlag::TimestampEstimated |
                   sdr_core::QualityFlag::SettlingIncomplete;
        iq.samples = std::move(tail);
        iq.config_generation = block.config_generation;

        // Every firmware header is a new tune. Never mix retained ring or
        // detector state across blocks, even if a future plan repeats a LO.
        dsp->reset();
        dsp->push_iq(iq);
        auto frames = dsp->poll_spectrum(0U, true);
        if (frames.size() != 1U) {
            throw sdr_core::DeviceError("HackRF Sweep block did not yield exactly one FFT");
        }
        auto frame = std::move(frames.front());
        // CPU DSP defaults this field to Fs. The actual value here is the
        // requested (not read-back) analogue filter setting.
        frame.analog_bandwidth_hz = config.acquisition.baseband_filter_hz;
        sdr_core::validate(frame);
        return frame;
    }

    HackrfSweepAnalysisConfig config;
    HackrfSweepSequenceGate gate;
    sdr_core::SweepLineDefinition line_definition;
    sdr_core::ContinuousSweepLineAssembler assembler;
    std::unique_ptr<sdr_core::DspBackend> dsp;
    std::optional<std::uint64_t> active_scan;
    std::uint64_t active_continuity{};
    std::uint32_t next_plan_index{};
    std::uint64_t accepted_blocks{};
    std::uint64_t suppressed_after_gap{};
    std::uint64_t gap_events{};
    bool suppress_scan{};
    bool finished{};
};

HackrfSweepAnalysis::HackrfSweepAnalysis(HackrfSweepAnalysisConfig config) {
    validate_config(config);
    impl_ = std::make_unique<Impl>(std::move(config));
}

HackrfSweepAnalysis::~HackrfSweepAnalysis() = default;

std::vector<sdr_core::SweepLineFrame> HackrfSweepAnalysis::admit(
    const HackrfSweepQueuedBlock& block
) {
    if (impl_->finished) {
        throw sdr_core::ConfigurationError("HackRF Sweep analysis already finished");
    }
    impl_->validate_block(block);
    std::vector<sdr_core::SweepLineFrame> emitted;
    if (block.new_scan) {
        if (impl_->active_scan && block.scan_epoch <= *impl_->active_scan) {
            throw sdr_core::ConfigurationError("HackRF Sweep scan epoch regressed");
        }
        append(emitted, impl_->assembler.flush(sdr_core::SweepLineGapReason::MissingSegment));
        impl_->active_scan = block.scan_epoch;
        impl_->active_continuity = block.continuity_epoch;
        impl_->next_plan_index = 0U;
        impl_->suppress_scan = false;
    } else if (!impl_->active_scan || block.scan_epoch != *impl_->active_scan) {
        throw sdr_core::ConfigurationError("HackRF Sweep block has no matching scan origin");
    }
    if (impl_->suppress_scan) {
        ++impl_->suppressed_after_gap;
        return emitted;
    }
    if ((!block.new_scan && block.gap_before) ||
        block.continuity_epoch != impl_->active_continuity ||
        block.plan_index != impl_->next_plan_index ||
        (!block.new_scan && block.known_skipped_headers_before != 0U)) {
        ++impl_->gap_events;
        append(emitted, impl_->assembler.flush(sdr_core::SweepLineGapReason::MissingSegment));
        impl_->suppress_scan = true;
        ++impl_->suppressed_after_gap;
        return emitted;
    }

    auto frame = impl_->spectrum_for(block);
    const auto low = segment_index(block.plan_index, false);
    const auto high = segment_index(block.plan_index, true);
    for (const auto index : {low, high}) {
        const auto& segments = impl_->line_definition.segments;
        const auto found = std::lower_bound(segments.begin(), segments.end(), index,
            [](const auto& segment, const auto id) { return segment.segment_index < id; });
        if (found != segments.end() && found->segment_index == index) {
            append(emitted, impl_->assembler.admit(
                block.scan_epoch, block.host_timestamp_ns, {index, frame}));
        }
    }
    ++impl_->accepted_blocks;
    impl_->next_plan_index = block.plan_index + 1U;
    return emitted;
}

std::optional<sdr_core::SweepProgressFrame> HackrfSweepAnalysis::preview() const {
    if (impl_->finished || impl_->suppress_scan || !impl_->active_scan) {
        return std::nullopt;
    }
    return impl_->assembler.preview(*impl_->active_scan);
}

std::vector<sdr_core::SweepLineFrame> HackrfSweepAnalysis::finish() {
    if (impl_->finished) {
        return {};
    }
    impl_->finished = true;
    impl_->active_scan.reset();
    return impl_->assembler.flush(sdr_core::SweepLineGapReason::Cancellation);
}

const sdr_core::SweepLineDefinition& HackrfSweepAnalysis::definition() const noexcept {
    return impl_->line_definition;
}

HackrfSweepAnalysisMetrics HackrfSweepAnalysis::metrics() const {
    return {
        .accepted_blocks = impl_->accepted_blocks,
        .suppressed_after_gap = impl_->suppressed_after_gap,
        .gap_events = impl_->gap_events,
        .dsp = impl_->dsp->metrics(),
        .lines = impl_->assembler.metrics(),
    };
}

}  // namespace sdr_hackrf

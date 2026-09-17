// Explicit R10-E6 physical RX evidence tool.  It never has a default URI,
// requires an exact confirmation phrase, emits scalar JSON only and keeps all
// canonical I/Q blocks inside C++.  It is intentionally not a CTest target.

#include "sdr_core/dual_rx_dsp.hpp"
#include "sdr_pluto/pluto_backend.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr std::string_view confirmation_phrase = "I CONFIRM R10-E6 PHYSICAL RX";

enum class Selection { Rx1, Rx2, Both };
enum class EvidenceMode { Transport, RfPath };

struct Arguments {
    std::string uri;
    Selection selection{Selection::Rx1};
    EvidenceMode evidence_mode{EvidenceMode::Transport};
    double center_hz{2'450'000'000.0};
    double sample_rate_hz{3'000'000.0};
    double analog_bandwidth_hz{1'500'000.0};
    double gain_db{20.0};
    std::uint32_t buffer_samples{16'384U};
    std::uint32_t fft_size{1'024U};
    double warmup_seconds{1.0};
    double measurement_seconds{5.0};
    std::string physical_condition;
    double signal_hz{std::numeric_limits<double>::quiet_NaN()};
    double tone_search_hz{25'000.0};
    std::string confirmation;
};

struct ChannelScalars {
    std::uint64_t samples{};
    long double power_sum{};
    std::uint64_t nonzero_complex_samples{};
};

struct PairScalars {
    bool exact_duplicate{true};
    std::uint64_t compared_samples{};
    long double left_power_sum{};
    long double right_power_sum{};
    long double cross_real{};
    long double cross_imag{};
};

struct SequenceScalars {
    bool seen{};
    std::uint64_t last_sequence{};
    std::uint64_t last_sample_end{};
    std::uint64_t sequence_or_sample_gaps{};
};

// This state contains only reduced scalar power observations.  SpectrumFrame
// arrays are consumed within the same native loop and never retained or
// exposed outside this executable.
struct RfPathScalars {
    std::uint64_t spectra_observed{};
    long double peak_power_sum{};
    long double noise_power_sum{};
    long double peak_offset_hz_sum{};
};

[[nodiscard]] std::int16_t read_i16_le(const std::uint8_t* value) {
    const auto packed = static_cast<std::uint16_t>(value[0]) |
                        static_cast<std::uint16_t>(value[1]) << 8U;
    return static_cast<std::int16_t>(packed);
}

void observe_channel(ChannelScalars& scalars, const sdr_core::IqBlock& block) {
    for (std::uint32_t index = 0U; index < block.sample_count; ++index) {
        const auto* sample = block.samples->data() + static_cast<std::size_t>(index) * 4U;
        const auto in_phase = static_cast<long double>(read_i16_le(sample));
        const auto quadrature = static_cast<long double>(read_i16_le(sample + 2U));
        scalars.power_sum += in_phase * in_phase + quadrature * quadrature;
        scalars.nonzero_complex_samples += in_phase != 0.0L || quadrature != 0.0L;
        ++scalars.samples;
    }
}

void observe_pair(
    PairScalars& scalars,
    const sdr_core::IqBlock& left,
    const sdr_core::IqBlock& right
) {
    if (left.sample_count != right.sample_count || left.samples->size() != right.samples->size() ||
        !std::equal(left.samples->cbegin(), left.samples->cend(), right.samples->cbegin())) {
        scalars.exact_duplicate = false;
    }
    const auto count = std::min(left.sample_count, right.sample_count);
    for (std::uint32_t index = 0U; index < count; ++index) {
        const auto* left_sample = left.samples->data() + static_cast<std::size_t>(index) * 4U;
        const auto* right_sample = right.samples->data() + static_cast<std::size_t>(index) * 4U;
        const auto li = static_cast<long double>(read_i16_le(left_sample));
        const auto lq = static_cast<long double>(read_i16_le(left_sample + 2U));
        const auto ri = static_cast<long double>(read_i16_le(right_sample));
        const auto rq = static_cast<long double>(read_i16_le(right_sample + 2U));
        scalars.left_power_sum += li * li + lq * lq;
        scalars.right_power_sum += ri * ri + rq * rq;
        scalars.cross_real += li * ri + lq * rq;
        scalars.cross_imag += lq * ri - li * rq;
        ++scalars.compared_samples;
    }
}

void observe_sequence(SequenceScalars& scalars, const sdr_core::IqBlock& block) {
    if (scalars.seen &&
        (block.source_sequence != scalars.last_sequence + 1U ||
         block.first_sample_index != scalars.last_sample_end)) {
        ++scalars.sequence_or_sample_gaps;
    }
    scalars.seen = true;
    scalars.last_sequence = block.source_sequence;
    scalars.last_sample_end = block.first_sample_index + block.sample_count;
}

void observe_rf_path(
    RfPathScalars& scalars,
    const std::vector<sdr_core::SpectrumFrame>& frames,
    const Arguments& arguments
) {
    for (const auto& frame : frames) {
        if (!frame.frequencies_hz || !frame.values ||
            frame.frequencies_hz->size() != frame.values->size() || frame.values->empty()) {
            throw std::runtime_error("R10-E6 RF-path capture received an invalid native SpectrumFrame");
        }
        std::size_t peak_index = frame.values->size();
        float peak_db = -std::numeric_limits<float>::infinity();
        for (std::size_t index = 0U; index < frame.values->size(); ++index) {
            const auto offset = std::abs((*frame.frequencies_hz)[index] - arguments.signal_hz);
            const auto value = (*frame.values)[index];
            if (offset <= arguments.tone_search_hz && std::isfinite(value) &&
                (peak_index == frame.values->size() || value > peak_db)) {
                peak_index = index;
                peak_db = value;
            }
        }
        if (peak_index == frame.values->size()) {
            throw std::runtime_error("R10-E6 RF-path capture could not locate a finite target-frequency bin");
        }

        // A Hann-windowed tone is deliberately excluded with a five-bin guard
        // before estimating the local spectrum floor. This is a scalar path
        // discrimination statistic, not a calibrated RF-power measurement.
        std::vector<float> noise_bins;
        noise_bins.reserve(frame.values->size());
        for (std::size_t index = 0U; index < frame.values->size(); ++index) {
            const auto distance = index > peak_index ? index - peak_index : peak_index - index;
            const auto value = (*frame.values)[index];
            if (distance > 4U && std::isfinite(value)) noise_bins.push_back(value);
        }
        if (noise_bins.empty()) {
            throw std::runtime_error("R10-E6 RF-path capture has no finite noise-floor bins");
        }
        const auto middle = noise_bins.begin() + static_cast<std::ptrdiff_t>(noise_bins.size() / 2U);
        std::nth_element(noise_bins.begin(), middle, noise_bins.end());
        const auto noise_db = *middle;
        scalars.peak_power_sum += std::pow(10.0L, static_cast<long double>(peak_db) / 10.0L);
        scalars.noise_power_sum += std::pow(10.0L, static_cast<long double>(noise_db) / 10.0L);
        scalars.peak_offset_hz_sum += (*frame.frequencies_hz)[peak_index] - arguments.signal_hz;
        ++scalars.spectra_observed;
    }
}

[[nodiscard]] sdr_pluto::ReceiverSelection native_selection(const Selection selection) {
    switch (selection) {
    case Selection::Rx1: return sdr_pluto::ReceiverSelection::Rx1;
    case Selection::Rx2: return sdr_pluto::ReceiverSelection::Rx2;
    case Selection::Both: return sdr_pluto::ReceiverSelection::Both;
    }
    throw std::runtime_error("R10-E6 has an unknown receiver selection");
}

[[nodiscard]] const char* selection_name(const Selection selection) {
    switch (selection) {
    case Selection::Rx1: return "rx1";
    case Selection::Rx2: return "rx2";
    case Selection::Both: return "both";
    }
    return "unknown";
}

[[nodiscard]] std::string json_number_or_null(const double value) {
    if (!std::isfinite(value)) return "null";
    std::ostringstream result;
    result << std::setprecision(17) << value;
    return result.str();
}

[[nodiscard]] sdr_core::IqBlock refill_selected(
    sdr_pluto::PlutoDevice& device,
    const Selection selection
) {
    if (selection == Selection::Rx1) return device.refill();
    const auto blocks = device.refill_receivers();
    if (selection == Selection::Rx2 && blocks.rx2.has_value()) return *blocks.rx2;
    throw std::runtime_error("R10-E6 selected receiver refill did not return its bounded native I/Q block");
}

[[nodiscard]] sdr_core::DeviceConfig device_config(const Arguments& arguments) {
    return {
        .source_id = "r10-e6-physical-scalar-only",
        .context_uri = arguments.uri,
        .center_frequency_hz = arguments.center_hz,
        .sample_rate_hz = arguments.sample_rate_hz,
        .analog_bandwidth_hz = arguments.analog_bandwidth_hz,
        .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = arguments.gain_db,
        .channel_index = 0U,
        .buffer_samples = arguments.buffer_samples,
        .schema_version = sdr_core::contract_schema_version,
    };
}

[[nodiscard]] sdr_core::DspConfig dsp_config(const Arguments& arguments) {
    return {
        .fft_size = arguments.fft_size,
        .hop_size = arguments.fft_size / 2U,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum,
        .batch_size = 1U,
        .averaging_frames = 1U,
        .calibration_status = sdr_core::CalibrationStatus::Uncalibrated,
        .schema_version = sdr_core::contract_schema_version,
    };
}

[[nodiscard]] sdr_core::SourceDescriptor source(const std::string& id) {
    return {
        .source_type = sdr_core::SourceType::LiveIq,
        .source_id = id,
        .display_name = id,
        .uri = "route-redacted",
        .backend_id = "pluto-libiio",
        .schema_version = sdr_core::contract_schema_version,
    };
}

[[nodiscard]] sdr_pluto::StreamMetrics subtract(
    const sdr_pluto::StreamMetrics& after,
    const sdr_pluto::StreamMetrics& before
) {
    return {
        .blocks_received = after.blocks_received - before.blocks_received,
        .samples_received = after.samples_received - before.samples_received,
        .refill_wait_ns = after.refill_wait_ns - before.refill_wait_ns,
        .refill_calls = after.refill_calls - before.refill_calls,
        .refill_wait_over_nominal_period = after.refill_wait_over_nominal_period - before.refill_wait_over_nominal_period,
        .refill_wait_over_two_nominal_periods = after.refill_wait_over_two_nominal_periods - before.refill_wait_over_two_nominal_periods,
        .canonicalization_ns = after.canonicalization_ns - before.canonicalization_ns,
        .inter_refill_gap_ns = after.inter_refill_gap_ns - before.inter_refill_gap_ns,
        .inter_refill_gap_count = after.inter_refill_gap_count - before.inter_refill_gap_count,
        .short_reads = after.short_reads - before.short_reads,
        .refill_errors = after.refill_errors - before.refill_errors,
        .output_pool_exhaustions = after.output_pool_exhaustions - before.output_pool_exhaustions,
        .output_blocks_dropped = after.output_blocks_dropped - before.output_blocks_dropped,
        .estimated_dropped_samples = after.estimated_dropped_samples - before.estimated_dropped_samples,
    };
}

void consume_warmup(sdr_pluto::PlutoDevice& device, const Selection selection, const double seconds) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(seconds);
    while (std::chrono::steady_clock::now() < deadline) {
        if (selection == Selection::Rx1) {
            static_cast<void>(device.refill());
        } else {
            static_cast<void>(device.refill_receivers());
        }
    }
}

[[nodiscard]] Arguments parse_arguments(const int argc, char** argv) {
    Arguments result;
    for (int index = 1; index < argc; ++index) {
        const std::string_view option(argv[index]);
        if (option == "--help") {
            std::cout << "Usage: sdr_core_r10e6_rx_evidence --uri ROUTE --selection rx1|rx2|both "
                         "--confirm-rx 'I CONFIRM R10-E6 PHYSICAL RX' [bounded transport options]\n"
                         "RF path: add --evidence-mode rf-path --physical-condition signal-on-a|signal-on-b "
                         "--signal-hz HZ; the route must be selected rx1 or rx2.\n";
            std::exit(0);
        }
        if (index + 1 >= argc) throw std::runtime_error("missing value for " + std::string(option));
        const std::string_view value(argv[++index]);
        if (option == "--uri") result.uri = value;
        else if (option == "--selection") {
            if (value == "rx1") result.selection = Selection::Rx1;
            else if (value == "rx2") result.selection = Selection::Rx2;
            else if (value == "both") result.selection = Selection::Both;
            else throw std::runtime_error("--selection must be rx1, rx2 or both");
        } else if (option == "--evidence-mode") {
            if (value == "transport") result.evidence_mode = EvidenceMode::Transport;
            else if (value == "rf-path") result.evidence_mode = EvidenceMode::RfPath;
            else throw std::runtime_error("--evidence-mode must be transport or rf-path");
        } else if (option == "--center-hz") result.center_hz = std::stod(std::string(value));
        else if (option == "--sample-rate-hz") result.sample_rate_hz = std::stod(std::string(value));
        else if (option == "--analog-bandwidth-hz") result.analog_bandwidth_hz = std::stod(std::string(value));
        else if (option == "--gain-db") result.gain_db = std::stod(std::string(value));
        else if (option == "--buffer-samples") result.buffer_samples = static_cast<std::uint32_t>(std::stoul(std::string(value)));
        else if (option == "--fft-size") result.fft_size = static_cast<std::uint32_t>(std::stoul(std::string(value)));
        else if (option == "--warmup-seconds") result.warmup_seconds = std::stod(std::string(value));
        else if (option == "--measurement-seconds") result.measurement_seconds = std::stod(std::string(value));
        else if (option == "--physical-condition") result.physical_condition = value;
        else if (option == "--signal-hz") result.signal_hz = std::stod(std::string(value));
        else if (option == "--tone-search-hz") result.tone_search_hz = std::stod(std::string(value));
        else if (option == "--confirm-rx") result.confirmation = value;
        else throw std::runtime_error("unsupported argument: " + std::string(option));
    }
    const bool conservative_profile =
        (result.sample_rate_hz == 3'000'000.0 && result.analog_bandwidth_hz == 1'500'000.0) ||
        (result.sample_rate_hz == 5'000'000.0 && result.analog_bandwidth_hz == 2'500'000.0);
    const bool rf_path = result.evidence_mode == EvidenceMode::RfPath;
    const bool valid_physical_condition =
        result.physical_condition == "signal-on-a" || result.physical_condition == "signal-on-b";
    const auto dc_guard_hz = std::max(50'000.0, result.tone_search_hz + 5.0 * result.sample_rate_hz / result.fft_size);
    if (result.uri.empty() || result.confirmation != confirmation_phrase ||
        !conservative_profile ||
        (result.buffer_samples != 4'096U && result.buffer_samples != 16'384U) ||
        result.fft_size != 1'024U || !std::isfinite(result.warmup_seconds) ||
        !std::isfinite(result.measurement_seconds) || result.warmup_seconds < 0.0 ||
        result.warmup_seconds > 5.0 || result.measurement_seconds <= 0.0 ||
        result.measurement_seconds > 30.0 || !std::isfinite(result.center_hz) ||
        !std::isfinite(result.gain_db) || !std::isfinite(result.tone_search_hz) ||
        result.tone_search_hz <= 0.0 || result.tone_search_hz > 100'000.0 ||
        (rf_path && (result.selection == Selection::Both || !valid_physical_condition ||
                     result.sample_rate_hz != 3'000'000.0 || result.analog_bandwidth_hz != 1'500'000.0 ||
                     !std::isfinite(result.signal_hz) || result.signal_hz <= 0.0 ||
                     std::abs(result.signal_hz - result.center_hz) < dc_guard_hz ||
                     result.signal_hz < result.center_hz - result.sample_rate_hz / 2.0 ||
                     result.signal_hz > result.center_hz + result.sample_rate_hz / 2.0)) ||
        (!rf_path && (!result.physical_condition.empty() || std::isfinite(result.signal_hz)))) {
        throw std::runtime_error("R10-E6 requires an explicit route/confirmation and a finite 3/1.5 or 5/2.5 MS/s/MHz profile");
    }
    return result;
}

int run(const Arguments& arguments) {
    const auto topology = sdr_pluto::probe_receiver_topology(arguments.uri);
    const bool dual_layout_observed = topology.input_scan_elements.size() == 4U;
    if ((arguments.selection == Selection::Both || arguments.selection == Selection::Rx2) && !dual_layout_observed) {
        throw std::runtime_error("R10-E6 RX2/BOTH selection rejected: the read-only topology lacks exactly four input scan elements");
    }

    sdr_pluto::PlutoDevice device(arguments.uri);
    try {
        const auto applied = device.configure(
            device_config(arguments),
            native_selection(arguments.selection),
            4U
        );
        device.start_stream();
        consume_warmup(device, arguments.selection, arguments.warmup_seconds);
        const auto metrics_before = device.metrics();
        const auto started = std::chrono::steady_clock::now();
        const auto deadline = started + std::chrono::duration<double>(arguments.measurement_seconds);
        ChannelScalars primary;
        ChannelScalars secondary;
        PairScalars pair;
        SequenceScalars sequence;
        std::uint64_t spectrum_frames_drained{};
        std::uint64_t spectrum_frames_coalesced{};
        std::uint64_t primary_fft_frames{};
        std::uint64_t secondary_fft_frames{};
        std::uint64_t primary_fft_dropped{};
        std::uint64_t secondary_fft_dropped{};
        std::uint64_t pair_frames_published{};
        std::uint64_t pair_frames_superseded{};
        const auto dsp = dsp_config(arguments);

        if (arguments.selection == Selection::Rx1 || arguments.selection == Selection::Rx2) {
            sdr_core::DspOptions options;
            options.source = source(arguments.selection == Selection::Rx1 ? "r10-e6-rx1" : "r10-e6-rx2");
            // See the dual-RX branch below: one 16,384-sample block can
            // yield 32 FFT1024/hop512 frames, so a four-frame queue would
            // make this scalar evidence tool fabricate DSP publication loss.
            options.output_capacity = 64U;
            auto publisher = sdr_core::make_cpu_dsp_backend(std::move(options));
            publisher->configure(dsp);
            RfPathScalars rf_path;
            while (std::chrono::steady_clock::now() < deadline) {
                auto block = refill_selected(device, arguments.selection);
                observe_channel(primary, block);
                observe_sequence(sequence, block);
                publisher->push_iq(block);
                auto frames = publisher->poll_spectrum(0U, false);
                spectrum_frames_drained += frames.size();
                if (arguments.evidence_mode == EvidenceMode::RfPath) {
                    observe_rf_path(rf_path, frames, arguments);
                }
            }
            primary_fft_frames = publisher->metrics().fft_frames_computed;
            primary_fft_dropped = publisher->metrics().fft_frames_dropped;
            if (arguments.evidence_mode == EvidenceMode::RfPath && rf_path.spectra_observed == 0U) {
                throw std::runtime_error("R10-E6 RF-path capture produced no native spectrum frames");
            }
            const auto rf_peak_db = rf_path.spectra_observed == 0U ? std::numeric_limits<double>::quiet_NaN() :
                10.0 * std::log10(static_cast<double>(rf_path.peak_power_sum / rf_path.spectra_observed));
            const auto rf_noise_db = rf_path.spectra_observed == 0U ? std::numeric_limits<double>::quiet_NaN() :
                10.0 * std::log10(static_cast<double>(rf_path.noise_power_sum / rf_path.spectra_observed));
            const auto rf_offset_hz = rf_path.spectra_observed == 0U ? std::numeric_limits<double>::quiet_NaN() :
                static_cast<double>(rf_path.peak_offset_hz_sum / rf_path.spectra_observed);
            const auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
            const auto metrics = subtract(device.metrics(), metrics_before);
            device.stop_stream();
            device.disconnect();
            const auto primary_mean_power = primary.samples == 0U ? 0.0 : static_cast<double>(primary.power_sum / primary.samples);
            std::cout << std::setprecision(17)
                      << "{\"schema\":\"sdr-native-r10e6-physical-scalar-v2\""
                      << ",\"evidence_mode\":\"" << (arguments.evidence_mode == EvidenceMode::RfPath ? "rf-path" : "transport") << "\""
                      << ",\"selection\":\"" << selection_name(arguments.selection) << "\""
                      << ",\"physical_condition\":\"" << (arguments.physical_condition.empty() ? "not_applicable" : arguments.physical_condition) << "\""
                      << ",\"raw_iq_persisted\":false"
                      << ",\"raw_iq_crossed_python_or_qt\":false"
                      << ",\"topology\":{\"input_scan_elements\":" << topology.input_scan_elements.size()
                      << ",\"dual_layout_observed\":" << (dual_layout_observed ? "true" : "false") << "}"
                      << ",\"applied\":{\"center_hz\":" << applied.center_frequency_hz
                      << ",\"sample_rate_hz\":" << applied.sample_rate_hz
                      << ",\"analog_bandwidth_hz\":" << applied.analog_bandwidth_hz
                      << ",\"manual_gain_db\":" << applied.manual_gain_db
                      << ",\"buffer_samples\":" << arguments.buffer_samples << "}"
                      << ",\"measurement_seconds\":" << elapsed
                      << ",\"transport\":{\"source_blocks\":" << metrics.blocks_received
                      << ",\"source_complex_samples\":" << metrics.samples_received
                      << ",\"per_chain_mcomplex_per_second\":" << (static_cast<double>(metrics.samples_received) / elapsed / 1.0e6)
                      << ",\"short_reads\":" << metrics.short_reads
                      << ",\"refill_errors\":" << metrics.refill_errors
                      << ",\"output_pool_exhaustions\":" << metrics.output_pool_exhaustions
                      << ",\"output_blocks_dropped\":" << metrics.output_blocks_dropped
                      << ",\"estimated_dropped_samples\":" << metrics.estimated_dropped_samples
                      << ",\"host_sequence_or_sample_gaps\":" << sequence.sequence_or_sample_gaps << "}"
                      << ",\"dsp\":{\"fft_size\":" << arguments.fft_size
                      << ",\"fft_frames_computed\":" << primary_fft_frames
                      << ",\"fft_frames_dropped\":" << primary_fft_dropped
                      << ",\"fft_per_second\":" << (static_cast<double>(primary_fft_frames) / elapsed) << "}"
                      << ",\"channel\":{\"complex_samples\":" << primary.samples
                      << ",\"nonzero_complex_samples\":" << primary.nonzero_complex_samples
                      << ",\"mean_power_iq_units\":" << primary_mean_power << "}"
                      << ",\"rf_path\":{\"signal_hz\":" << json_number_or_null(arguments.signal_hz)
                      << ",\"tone_search_hz\":" << arguments.tone_search_hz
                      << ",\"spectra_observed\":" << rf_path.spectra_observed
                      << ",\"target_peak_dbfs_bin\":" << json_number_or_null(rf_peak_db)
                      << ",\"median_noise_dbfs_bin\":" << json_number_or_null(rf_noise_db)
                      << ",\"target_contrast_db\":" << json_number_or_null(rf_peak_db - rf_noise_db)
                      << ",\"mean_peak_offset_hz\":" << json_number_or_null(rf_offset_hz) << "}"
                      << ",\"continuity_status\":\"host_clean_continuity_unverified\""
                      << ",\"not_verified\":[\"No device or FPGA overflow counter was observed.\",\"This one condition alone does not prove RF routing; compare all four declared cells and cable-swap decision thresholds.\",\"No phase, calibration, Sweep, visible DPI or Spectrozir claim is made.\"]"
                      << "}\n";
            return 0;
        } else {
            sdr_core::DualRxDspPublisher publisher;
            publisher.configure({
                .primary = {.source = source("r10-e6-rx1"), .dsp = dsp},
                .secondary = {.source = source("r10-e6-rx2"), .dsp = dsp},
                .dc_removal_block_mean = false,
                // At the permitted 16,384-sample physical buffer geometry an
                // FFT1024/hop512 block can produce 32 FFTs.  Keep the full
                // finite 64-frame native capacity so this scalar evidence
                // tool does not turn its own per-refill queue into a hidden
                // publication cap. The later drain remains latest-wins and
                // reports every coalesced reduced frame separately.
                .output_queue_capacity = 64U,
            });
            while (std::chrono::steady_clock::now() < deadline) {
                auto blocks = device.refill_receivers();
                if (!blocks.rx1.has_value() || !blocks.rx2.has_value()) {
                    throw std::runtime_error("R10-E6 BOTH refill did not return both bounded native blocks");
                }
                observe_channel(primary, *blocks.rx1);
                observe_channel(secondary, *blocks.rx2);
                observe_pair(pair, *blocks.rx1, *blocks.rx2);
                observe_sequence(sequence, *blocks.rx1);
                publisher.push(*blocks.rx1, *blocks.rx2);
                auto drained = publisher.drain_latest_spectrum_frame();
                spectrum_frames_drained += drained.frame.has_value() ? 1U : 0U;
                spectrum_frames_coalesced += drained.coalesced_frames;
            }
            const auto dsp_metrics = publisher.metrics();
            primary_fft_frames = dsp_metrics.primary.fft_frames_computed;
            secondary_fft_frames = dsp_metrics.secondary.fft_frames_computed;
            primary_fft_dropped = dsp_metrics.primary.fft_frames_dropped;
            secondary_fft_dropped = dsp_metrics.secondary.fft_frames_dropped;
            pair_frames_published = dsp_metrics.paired_frames_published;
            pair_frames_superseded = dsp_metrics.paired_frames_superseded;
        }
        const auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        const auto metrics = subtract(device.metrics(), metrics_before);
        device.stop_stream();
        device.disconnect();

        const auto primary_mean_power = primary.samples == 0U ? 0.0 : static_cast<double>(primary.power_sum / primary.samples);
        const auto secondary_mean_power = secondary.samples == 0U ? 0.0 : static_cast<double>(secondary.power_sum / secondary.samples);
        const auto correlation_denominator = std::sqrt(pair.left_power_sum * pair.right_power_sum);
        const auto correlation = correlation_denominator == 0.0L ? 0.0 :
            static_cast<double>(std::sqrt(pair.cross_real * pair.cross_real + pair.cross_imag * pair.cross_imag) / correlation_denominator);
        const auto chains = arguments.selection == Selection::Both ? 2.0 : 1.0;
        std::cout << std::setprecision(17)
                  << "{\"schema\":\"sdr-native-r10e6-physical-scalar-v1\""
                  << ",\"selection\":\"" << (arguments.selection == Selection::Rx1 ? "rx1" : "both") << "\""
                  << ",\"raw_iq_persisted\":false"
                  << ",\"raw_iq_crossed_python_or_qt\":false"
                  << ",\"topology\":{\"input_scan_elements\":" << topology.input_scan_elements.size()
                  << ",\"dual_layout_observed\":" << (dual_layout_observed ? "true" : "false") << "}"
                  << ",\"applied\":{\"center_hz\":" << applied.center_frequency_hz
                  << ",\"sample_rate_hz\":" << applied.sample_rate_hz
                  << ",\"analog_bandwidth_hz\":" << applied.analog_bandwidth_hz
                  << ",\"manual_gain_db\":" << applied.manual_gain_db
                  << ",\"buffer_samples\":" << arguments.buffer_samples << "}"
                  << ",\"measurement_seconds\":" << elapsed
                  << ",\"transport\":{\"source_blocks\":" << metrics.blocks_received
                  << ",\"source_complex_samples\":" << metrics.samples_received
                  << ",\"per_chain_mcomplex_per_second\":" << (static_cast<double>(metrics.samples_received) / elapsed / 1.0e6)
                  << ",\"aggregate_mcomplex_per_second\":" << (static_cast<double>(metrics.samples_received) * chains / elapsed / 1.0e6)
                  << ",\"short_reads\":" << metrics.short_reads
                  << ",\"refill_errors\":" << metrics.refill_errors
                  << ",\"output_pool_exhaustions\":" << metrics.output_pool_exhaustions
                  << ",\"output_blocks_dropped\":" << metrics.output_blocks_dropped
                  << ",\"estimated_dropped_samples\":" << metrics.estimated_dropped_samples
                  << ",\"host_sequence_or_sample_gaps\":" << sequence.sequence_or_sample_gaps
                  << ",\"refill_wait_ns\":" << metrics.refill_wait_ns
                  << ",\"canonicalization_ns\":" << metrics.canonicalization_ns << "}"
                  << ",\"dsp\":{\"fft_size\":" << arguments.fft_size
                  << ",\"rx1_fft_frames_computed\":" << primary_fft_frames
                  << ",\"rx2_fft_frames_computed\":" << secondary_fft_frames
                  << ",\"rx1_fft_frames_dropped\":" << primary_fft_dropped
                  << ",\"rx2_fft_frames_dropped\":" << secondary_fft_dropped
                  << ",\"rx1_fft_per_second\":" << (static_cast<double>(primary_fft_frames) / elapsed)
                  << ",\"rx2_fft_per_second\":" << (static_cast<double>(secondary_fft_frames) / elapsed)
                  << ",\"pair_frames_published\":" << pair_frames_published
                  << ",\"pair_frames_superseded\":" << pair_frames_superseded
                  << ",\"reduced_spectrum_frames_drained\":" << spectrum_frames_drained
                  << ",\"reduced_spectrum_frames_coalesced\":" << spectrum_frames_coalesced << "}"
                  << ",\"rx1\":{\"complex_samples\":" << primary.samples
                  << ",\"nonzero_complex_samples\":" << primary.nonzero_complex_samples
                  << ",\"mean_power_iq_units\":" << primary_mean_power << "}"
                  << ",\"rx2\":{\"complex_samples\":" << secondary.samples
                  << ",\"nonzero_complex_samples\":" << secondary.nonzero_complex_samples
                  << ",\"mean_power_iq_units\":" << secondary_mean_power << "}"
                  << ",\"pair\":{\"exact_duplicate\":" << (pair.exact_duplicate ? "true" : "false")
                  << ",\"compared_complex_samples\":" << pair.compared_samples
                  << ",\"complex_correlation_magnitude\":" << correlation << "}"
                  << ",\"continuity_status\":\"host_clean_continuity_unverified\""
                  << ",\"not_verified\":[\"No device or FPGA overflow counter was observed.\",\"No external-signal RX routing, cable-swap, phase or calibration protocol was performed.\",\"No Spectrozir comparison, Sweep LPS, visible DPI or soak claim is made.\"]"
                  << "}\n";
        return 0;
    } catch (...) {
        device.stop_stream();
        device.disconnect();
        throw;
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        return run(parse_arguments(argc, argv));
    } catch (const std::exception& error) {
        std::cerr << "sdr_core_r10e6_rx_evidence: " << error.what() << '\n';
        return 1;
    }
}

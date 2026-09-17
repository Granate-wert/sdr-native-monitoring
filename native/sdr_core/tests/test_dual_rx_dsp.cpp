#include "sdr_core/dual_rx_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <cmath>
#include <complex>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr double two_pi = 6.28318530717958647692528676655900577;

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

sdr_core::SourceDescriptor source(const std::string& id) {
    return {
        .source_type = sdr_core::SourceType::LiveIq,
        .source_id = id,
        .display_name = id,
        .uri = "mock:dual-rx",
        .backend_id = "dual-rx-test",
        .schema_version = sdr_core::contract_schema_version,
    };
}

sdr_core::DspConfig dsp_config() {
    return {
        .fft_size = 256U,
        .hop_size = 128U,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .precision_mode = sdr_core::PrecisionMode::ReferenceF64,
        .batch_size = 1U,
        .averaging_frames = 1U,
        .schema_version = sdr_core::contract_schema_version,
    };
}

sdr_core::DualRxDspConfig config(const std::uint32_t output_capacity = 4U) {
    const auto dsp = dsp_config();
    return {
        .primary = {.source = source("receiver-rx1"), .dsp = dsp},
        .secondary = {.source = source("receiver-rx2"), .dsp = dsp},
        .dc_removal_block_mean = false,
        .output_queue_capacity = output_capacity,
    };
}

sdr_core::IqBlock block(
    const std::uint64_t sequence,
    const std::uint64_t first_sample_index,
    const std::int64_t timestamp_ns,
    const double bin,
    const std::uint64_t generation = 7U
) {
    constexpr std::uint32_t count = 256U;
    constexpr double sample_rate = 256'000.0;
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(count * 8U);
    for (std::uint32_t index = 0U; index < count; ++index) {
        const auto phase = two_pi * bin * static_cast<double>(index) / static_cast<double>(count);
        const float real = static_cast<float>(std::cos(phase));
        const float imag = static_cast<float>(std::sin(phase));
        std::memcpy(bytes->data() + index * 8U, &real, sizeof(real));
        std::memcpy(bytes->data() + index * 8U + 4U, &imag, sizeof(imag));
    }
    return {
        .source_sequence = sequence,
        .first_sample_index = first_sample_index,
        .timestamp_ns = timestamp_ns,
        .center_frequency_hz = 433'920'000.0,
        .sample_rate_hz = sample_rate,
        .sample_format = sdr_core::SampleFormat::ComplexFloat32Le,
        .sample_count = count,
        .flags = sdr_core::QualityFlag::None,
        .samples = std::move(bytes),
        .config_generation = generation,
    };
}

void test_paired_cpu_spectra_keep_channel_identity() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config());
    publisher.push(block(10U, 0U, 1'000'000U, 13.0), block(10U, 0U, 1'000'000U, 31.0));
    const auto frames = publisher.poll_spectrum_frames(0U);
    expect(frames.size() == 1U, "a synchronized 256-sample epoch must yield one paired frame");
    const auto& frame = frames.front();
    expect(frame.synchronization_epoch == 1U && frame.first_sample_index == 0U &&
               frame.timestamp_ns == 1'000'000U && frame.config_generation == 7U,
           "pair metadata must retain the common input epoch");
    expect(frame.primary.source.source_id == "receiver-rx1" &&
               frame.secondary.source.source_id == "receiver-rx2",
           "paired spectra must retain independent channel provenance");
    expect(frame.primary.values && frame.secondary.values &&
               frame.primary.values->size() == 256U && frame.secondary.values->size() == 256U,
           "only reduced spectral arrays may be published");
    expect((*frame.primary.values)[128U + 13U] > -0.001F &&
               (*frame.secondary.values)[128U + 31U] > -0.001F,
           "each channel must preserve its own CPU spectrum");
    const auto metrics = publisher.metrics();
    expect(metrics.input_epochs_received == 1U && metrics.paired_frames_published == 1U &&
               metrics.shared_input_gaps == 0U && metrics.pairing_mismatches == 0U,
           "normal synchronized input must not invent a gap or mismatch");
}

void test_shared_gap_restarts_both_channel_histories() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config());
    publisher.push(block(0U, 0U, 1'000U, 3.0), block(0U, 0U, 1'000U, 7.0));
    static_cast<void>(publisher.poll_spectrum_frames(0U));
    publisher.push(block(2U, 512U, 3'000U, 3.0), block(2U, 512U, 3'000U, 7.0));
    const auto frames = publisher.poll_spectrum_frames(0U);
    expect(frames.size() == 1U, "post-gap blocks must form a fresh paired spectrum only");
    expect(frames.front().synchronization_epoch == 2U &&
               frames.front().shared_input_gaps_before == 1U &&
               frames.front().first_sample_index == 512U,
           "sequence discontinuity must be one explicit shared epoch gap");
    const auto metrics = publisher.metrics();
    expect(metrics.shared_input_gaps == 1U && metrics.primary.fft_frames_dropped == 0U &&
               metrics.secondary.fft_frames_dropped == 0U,
           "a shared input loss resets both channels rather than producing per-channel stale FFTs");
}

void test_sample_index_gap_is_shared_even_when_sequence_is_consecutive() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config());
    publisher.push(block(0U, 0U, 1'000U, 3.0), block(0U, 0U, 1'000U, 7.0));
    static_cast<void>(publisher.poll_spectrum_frames(0U));
    publisher.push(block(1U, 768U, 4'000U, 3.0), block(1U, 768U, 4'000U, 7.0));
    const auto frames = publisher.poll_spectrum_frames(0U);
    expect(frames.size() == 1U && frames.front().synchronization_epoch == 2U &&
               frames.front().shared_input_gaps_before == 1U &&
               frames.front().first_sample_index == 768U,
           "sample-index discontinuity must be an explicit shared gap even with a consecutive sequence");
}

void test_mismatched_pair_is_not_partially_processed() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config());
    publisher.push(block(1U, 0U, 1'000U, 3.0), block(2U, 0U, 1'000U, 7.0));
    expect(publisher.poll_spectrum_frames(0U).empty(), "mismatched input must not publish one channel");
    const auto metrics = publisher.metrics();
    expect(metrics.pairing_mismatches == 1U && metrics.shared_input_gaps == 1U &&
               metrics.input_epochs_received == 0U,
           "mismatch must become one shared gap before any DSP input is admitted");
}

void test_latest_wins_is_pair_atomic() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config(1U));
    publisher.push(block(0U, 0U, 1'000U, 3.0), block(0U, 0U, 1'000U, 7.0));
    publisher.push(block(1U, 256U, 2'000U, 3.0), block(1U, 256U, 2'000U, 7.0));
    const auto drained = publisher.drain_latest_spectrum_frame();
    expect(drained.frame.has_value() && drained.coalesced_frames == 0U &&
               drained.frame->first_sample_index == 256U &&
               drained.frame->primary.source.source_id == "receiver-rx1" &&
               drained.frame->secondary.source.source_id == "receiver-rx2",
           "latest-wins must supersede a whole pair, never one receiver frame");
    const auto metrics = publisher.metrics();
    expect(metrics.paired_frames_superseded == 1U && metrics.output_queue.dropped == 1U,
           "pair-level publication supersession must be exact and visible");
}

void test_invalid_channel_dsp_is_rejected_before_processing() {
    auto invalid = config();
    invalid.secondary.dsp.hop_size = 64U;
    bool rejected = false;
    try {
        sdr_core::DualRxDspPublisher publisher;
        publisher.configure(invalid);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "different numerical DSP contracts cannot be paired");
}

void test_shared_plan_preserves_single_cpu_numerics() {
    const auto shared = sdr_core::make_cpu_dsp_shared_plan(dsp_config());
    sdr_core::CpuDspOptions shared_options;
    shared_options.source = source("shared-plan");
    shared_options.cpu_shared_plan = shared;
    auto shared_backend = sdr_core::make_cpu_dsp_backend(shared_options);
    auto baseline_backend = sdr_core::make_cpu_dsp_backend({.source = source("baseline")});
    shared_backend->configure(dsp_config());
    baseline_backend->configure(dsp_config());
    const auto input = block(0U, 0U, 1'000U, 19.0);
    shared_backend->push_iq(input);
    baseline_backend->push_iq(input);
    const auto shared_frames = shared_backend->poll_spectrum(0U);
    const auto baseline_frames = baseline_backend->poll_spectrum(0U);
    expect(shared_frames.size() == 1U && baseline_frames.size() == 1U,
           "shared and baseline CPU backends must emit the same frame count");
    expect(*shared_frames.front().values == *baseline_frames.front().values &&
               *shared_frames.front().frequencies_hz == *baseline_frames.front().frequencies_hz,
           "shared CPU window resources must preserve single-RX numerical output exactly");
}

}  // namespace

int main() {
    try {
        test_paired_cpu_spectra_keep_channel_identity();
        test_shared_gap_restarts_both_channel_histories();
        test_sample_index_gap_is_shared_even_when_sequence_is_consecutive();
        test_mismatched_pair_is_not_partially_processed();
        test_latest_wins_is_pair_atomic();
        test_invalid_channel_dsp_is_rejected_before_processing();
        test_shared_plan_preserves_single_cpu_numerics();
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    return 0;
}

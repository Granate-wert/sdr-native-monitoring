#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr std::uint32_t kBlocks = 32U;
constexpr std::uint32_t kFftsPerBlock = 16U;

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

sdr_core::SourceDescriptor source() {
    sdr_core::SourceDescriptor value;
    value.source_type = sdr_core::SourceType::LiveIq;
    value.source_id = "hackrf-r13f-synthetic";
    value.display_name = "HackRF R13-F synthetic CI8";
    value.backend_id = "native.libhackrf.rx.v1";
    return value;
}

sdr_hackrf::HackrfFixedBandDspConfig dsp_config(
    const std::uint32_t fft_size,
    const std::uint32_t presentation_capacity
) {
    sdr_hackrf::HackrfFixedBandDspConfig value;
    value.dsp.fft_size = fft_size;
    value.dsp.hop_size = fft_size;
    value.dsp.window = sdr_core::WindowType::Hann;
    value.dsp.detector = sdr_core::DetectorType::Sample;
    value.dsp.unit = sdr_core::SpectrumUnit::DbfsBin;
    value.dsp.precision_mode = sdr_core::PrecisionMode::ReferenceF64;
    value.source = source();
    value.dsp_output_capacity = 1024U;
    value.presentation_capacity = presentation_capacity;
    return value;
}

sdr_hackrf::HackrfRxIngressConfig ingress_config(const std::uint32_t samples) {
    return {
        .slot_count = 4U,
        .slot_bytes = samples * 2U,
        .ready_capacity = 3U,
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = static_cast<double>(samples),
        .config_generation = 13U,
    };
}

std::vector<std::uint8_t> tone_ci8(const std::uint32_t samples) {
    std::vector<std::uint8_t> result(static_cast<std::size_t>(samples) * 2U);
    for (std::uint32_t index = 0U; index < samples; ++index) {
        const auto phase = static_cast<double>(index % 64U) * 0.09817477042468103;
        const auto i = static_cast<std::int8_t>(std::lround(96.0 * std::cos(phase)));
        const auto q = static_cast<std::int8_t>(std::lround(96.0 * std::sin(phase)));
        std::memcpy(result.data() + index * 2U, &i, 1U);
        std::memcpy(result.data() + index * 2U + 1U, &q, 1U);
    }
    return result;
}

void push_block(
    sdr_hackrf::HackrfRxIngress& ingress,
    sdr_hackrf::HackrfFixedBandDsp& dsp,
    const std::vector<std::uint8_t>& bytes,
    const std::int64_t timestamp_ns
) {
    expect(
        ingress.admit_callback(bytes, timestamp_ns) == sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "synthetic CI8 block was not admitted"
    );
    sdr_hackrf::HackrfRxLease lease;
    expect(ingress.try_pop(lease), "synthetic CI8 block was not poppable");
    dsp.push(std::move(lease));
}

struct RelayRun {
    std::vector<sdr_core::SpectrumFrame> frames;
    sdr_hackrf::HackrfFixedBandDspMetrics metrics;
    sdr_hackrf::HackrfFixedBandDspDeliveryAssessment assessment;
    double analytical_fft_per_second{};
};

RelayRun run_slow_consumer(
    const std::uint32_t fft_size,
    const std::uint32_t presentation_capacity
) {
    const auto samples_per_block = fft_size * kFftsPerBlock;
    sdr_hackrf::HackrfRxIngress ingress(ingress_config(samples_per_block));
    sdr_hackrf::HackrfFixedBandDsp dsp(dsp_config(fft_size, presentation_capacity));
    const auto bytes = tone_ci8(samples_per_block);
    const auto started = std::chrono::steady_clock::now();
    for (std::uint32_t block = 0U; block < kBlocks; ++block) {
        push_block(
            ingress,
            dsp,
            bytes,
            static_cast<std::int64_t>(block + 1U) * 1'000'000
        );
    }
    const auto elapsed = std::chrono::steady_clock::now() - started;
    ingress.request_stop();
    expect(
        ingress.wait_callbacks_quiescent(std::chrono::milliseconds(100)),
        "synthetic ingress did not quiesce"
    );
    expect(ingress.abandon_ready() == 0U, "processed synthetic ingress retained ready blocks");

    RelayRun result;
    result.frames = dsp.poll_spectrum_frames();
    result.metrics = dsp.metrics();
    result.assessment = sdr_hackrf::assess_hackrf_fixed_band_dsp_delivery(result.metrics);
    const auto elapsed_seconds = std::chrono::duration<double>(elapsed).count();
    expect(elapsed_seconds > 0.0, "synthetic FFT elapsed time was not positive");
    result.analytical_fft_per_second =
        static_cast<double>(result.metrics.dsp.fft_frames_computed) / elapsed_seconds;
    return result;
}

void assert_slow_consumer_contract(
    const std::uint32_t fft_size,
    const std::uint32_t presentation_capacity
) {
    const auto run = run_slow_consumer(fft_size, presentation_capacity);
    const auto expected_fft_frames = static_cast<std::uint64_t>(kBlocks) * kFftsPerBlock;
    const auto expected_retained = std::min<std::uint64_t>(expected_fft_frames, presentation_capacity);
    const auto expected_superseded = expected_fft_frames - expected_retained;

    expect(run.metrics.dsp.fft_frames_computed == expected_fft_frames,
           "synthetic analytical FFT count mismatch");
    expect(run.metrics.dsp.fft_frames_dropped == 0U,
           "presentation pressure was misclassified as analytical FFT loss");
    expect(run.metrics.source_sequence_discontinuities == 0U,
           "synthetic source sequence gap was introduced");
    expect(run.metrics.source_sample_index_discontinuities == 0U,
           "synthetic sample cursor gap was introduced");
    expect(run.metrics.source_timestamp_regressions == 0U,
           "synthetic timestamp regression was introduced");
    expect(run.assessment.analytical_pipeline_clean,
           "slow presentation consumer contaminated analytical pipeline completeness");
    expect(!run.assessment.presentation_delivery_clean,
           "slow presentation consumer supersession was hidden");
    expect(run.assessment.presentation_capacity == presentation_capacity,
           "presentation capacity was not preserved");
    expect(run.assessment.presentation_high_water == presentation_capacity,
           "presentation high water did not reach bounded capacity");
    expect(run.assessment.presentation_frames_superseded == expected_superseded,
           "presentation supersession count mismatch");
    expect(run.assessment.presentation_frames_abandoned == 0U,
           "processed presentation frames were incorrectly abandoned");
    expect(run.frames.size() == expected_retained,
           "latest-wins relay retained an unexpected number of frames");
    expect(run.analytical_fft_per_second > 0.0,
           "synthetic analytical FFT rate was not measurable");

    const auto first_sequence = expected_fft_frames - expected_retained;
    for (std::size_t index = 0U; index < run.frames.size(); ++index) {
        const auto& frame = run.frames[index];
        expect(
            frame.frame_sequence == first_sequence + index,
            "retained presentation frame sequence mismatch: expected " +
                std::to_string(first_sequence + index) + ", got " +
                std::to_string(frame.frame_sequence)
        );
        expect(frame.config_generation == 13U,
               "presentation relay changed immutable generation provenance");
        expect(frame.dropped_fft_frames_before == 0U,
               "presentation supersession entered analytical FFT provenance");
        expect(!sdr_core::has_flag(frame.quality_flags, sdr_core::QualityFlag::FftDropped),
               "presentation supersession set the analytical FFT-loss flag");
    }
    std::cout << "R13-F synthetic FFT " << fft_size
              << " capacity " << presentation_capacity
              << ": " << run.analytical_fft_per_second << " analytical FFT/s\n";
}

void test_slow_presentation_relay_preserves_analytical_fft() {
    for (const auto fft_size : {1024U, 4096U}) {
        for (const auto presentation_capacity : {1U, 64U, 256U}) {
            assert_slow_consumer_contract(fft_size, presentation_capacity);
        }
    }
}

}  // namespace

int main() {
    try {
        test_slow_presentation_relay_preserves_analytical_fft();
        std::cout << "R13-F presentation relay contract OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

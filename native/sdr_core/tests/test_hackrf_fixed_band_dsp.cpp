#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

sdr_core::SourceDescriptor source() {
    sdr_core::SourceDescriptor value;
    value.source_type = sdr_core::SourceType::LiveIq;
    value.source_id = "hackrf-fake-0";
    value.display_name = "HackRF fake RX";
    value.backend_id = "native.libhackrf.rx.v1";
    return value;
}

sdr_hackrf::HackrfFixedBandDspConfig dsp_config(
    const std::uint32_t presentation_capacity = 4U
) {
    sdr_hackrf::HackrfFixedBandDspConfig value;
    value.dsp.fft_size = 256U;
    value.dsp.hop_size = 256U;
    value.dsp.window = sdr_core::WindowType::Rectangular;
    value.dsp.detector = sdr_core::DetectorType::Sample;
    value.dsp.unit = sdr_core::SpectrumUnit::DbfsBin;
    value.dsp.precision_mode = sdr_core::PrecisionMode::ReferenceF64;
    value.source = source();
    value.dsp_output_capacity = 8U;
    value.presentation_capacity = presentation_capacity;
    return value;
}

sdr_hackrf::HackrfRxIngressConfig ingress_config(const std::uint32_t slot_bytes) {
    return {
        .slot_count = 4U,
        .slot_bytes = slot_bytes,
        .ready_capacity = 3U,
        .center_frequency_hz = 50'000'000.0,
        .sample_rate_hz = 256'000.0,
        .config_generation = 9U,
    };
}

std::vector<std::uint8_t> constant_ci8(
    const std::uint32_t samples,
    const std::int8_t i = 64,
    const std::int8_t q = 0
) {
    std::vector<std::uint8_t> result(static_cast<std::size_t>(samples) * 2U);
    for (std::uint32_t index = 0U; index < samples; ++index) {
        std::memcpy(result.data() + index * 2U, &i, 1U);
        std::memcpy(result.data() + index * 2U + 1U, &q, 1U);
    }
    return result;
}

void push_one(
    sdr_hackrf::HackrfRxIngress& ingress,
    sdr_hackrf::HackrfFixedBandDsp& dsp,
    const std::vector<std::uint8_t>& bytes,
    const std::int64_t timestamp
) {
    expect(
        ingress.admit_callback(bytes, timestamp) ==
            sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "fake callback was not admitted"
    );
    sdr_hackrf::HackrfRxLease lease;
    expect(ingress.try_pop(lease), "admitted fake callback was not poppable");
    dsp.push(std::move(lease));
}

void test_config_and_empty_lease_fail_closed() {
    auto invalid = dsp_config();
    invalid.source.uri = "usb:forbidden";
    bool rejected = false;
    try {
        const sdr_hackrf::HackrfFixedBandDsp dsp(invalid);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "route-bearing source was accepted");

    invalid = dsp_config();
    invalid.source.metadata_json.emplace("usb_route", "\"forbidden\"");
    rejected = false;
    try {
        const sdr_hackrf::HackrfFixedBandDsp dsp(invalid);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "route-bearing source metadata was accepted");

    sdr_hackrf::HackrfFixedBandDsp dsp(dsp_config());
    rejected = false;
    try {
        dsp.push({});
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "empty lease was accepted");
    const auto metrics = dsp.metrics();
    expect(metrics.iq_blocks_processed == 0U, "empty lease changed block metrics");
    expect(metrics.dsp.samples_processed == 0U, "empty lease reached the DSP backend");
}

void test_fake_lease_produces_canonical_cpu_spectrum() {
    sdr_hackrf::HackrfRxIngress ingress(ingress_config(512U));
    sdr_hackrf::HackrfFixedBandDsp dsp(dsp_config());
    push_one(ingress, dsp, constant_ci8(256U), 1'000'000);

    const auto frames = dsp.poll_spectrum_frames();
    expect(frames.size() == 1U, "one fake block did not produce one spectrum frame");
    const auto& frame = frames.front();
    sdr_core::validate(frame);
    expect(frame.source.source_id == "hackrf-fake-0", "source identity was not propagated");
    expect(frame.source.uri.empty() && frame.source.device_serial.empty(), "route data leaked");
    expect(frame.config_generation == 9U, "generation was not propagated");
    expect(frame.first_sample_index == 0U, "first sample index was not propagated");
    expect(frame.timestamp_ns == 1'000'000, "estimated timestamp was not propagated");
    expect(frame.center_frequency_hz == 50'000'000.0, "center frequency mismatch");
    const auto peak = static_cast<std::size_t>(std::distance(
        frame.values->begin(),
        std::max_element(frame.values->begin(), frame.values->end())
    ));
    expect(peak == 128U, "CI8 DC tone landed in the wrong shifted FFT bin");

    const auto metrics = dsp.metrics();
    expect(metrics.iq_blocks_processed == 1U, "processed block count mismatch");
    expect(metrics.iq_samples_processed == 256U, "processed sample count mismatch");
    expect(metrics.source_estimated_timestamp_blocks == 1U, "timestamp quality was hidden");
    expect(metrics.dsp.active_backend == sdr_core::ComputeBackendKind::Cpu, "CPU was not explicit");
    expect(metrics.dsp.fft_frames_computed == 1U, "FFT count mismatch");
}

void test_ci8_rails_preserve_cpu_overload_provenance() {
    sdr_hackrf::HackrfRxIngress ingress(ingress_config(512U));
    sdr_hackrf::HackrfFixedBandDsp dsp(dsp_config());
    push_one(ingress, dsp, constant_ci8(256U, 127, -128), 1'000'000);

    const auto frames = dsp.poll_spectrum_frames();
    expect(frames.size() == 1U, "rail CI8 block did not produce one spectrum frame");
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::AdcOverload),
        "CI8 rails did not preserve ADC-overload provenance"
    );
}

void test_gap_flushes_partial_state_and_retains_loss_flag() {
    sdr_hackrf::HackrfRxIngress ingress(ingress_config(256U));
    sdr_hackrf::HackrfFixedBandDsp dsp(dsp_config());
    const auto half = constant_ci8(128U);
    push_one(ingress, dsp, half, 100);
    expect(dsp.poll_spectrum_frames().empty(), "partial FFT emitted too early");

    const auto short_block = constant_ci8(127U);
    expect(
        ingress.admit_callback(short_block, 200) ==
            sdr_hackrf::HackrfRxAdmissionResult::Short,
        "short fake callback was not rejected"
    );
    push_one(ingress, dsp, half, 2'000'000);
    expect(dsp.poll_spectrum_frames().empty(), "gap stitched stale pre-gap samples");
    push_one(ingress, dsp, half, 2'500'000);

    const auto frames = dsp.poll_spectrum_frames();
    expect(frames.size() == 1U, "fresh post-gap FFT frame is missing");
    expect(frames.front().first_sample_index == 255U, "post-gap frame did not rebase");
    expect(frames.front().timestamp_ns == 2'000'000, "post-gap timestamp did not rebase");
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::IqDropped),
        "post-gap frame lost the R11-G IqDropped flag across its second input block"
    );
    expect(frames.front().dropped_iq_blocks_before == 1U, "missing block count mismatch");
    expect(frames.front().dropped_samples_before == 127U, "missing sample count mismatch");

    const auto metrics = dsp.metrics();
    expect(metrics.source_sequence_discontinuities == 1U, "sequence gap was hidden");
    expect(metrics.source_sample_index_discontinuities == 1U, "sample gap was hidden");
    expect(metrics.source_blocks_missing == 1U, "missing callback count mismatch");
    expect(metrics.source_samples_missing == 127U, "missing callback samples mismatch");

    const auto assessment = sdr_hackrf::assess_hackrf_fixed_band_dsp_delivery(metrics);
    expect(!assessment.source_cursor_continuity_clean,
           "source discontinuity was hidden by the delivery assessment");
    expect(assessment.cpu_fft_loss_free,
           "input discontinuity was misclassified as CPU FFT loss");
    expect(!assessment.analytical_pipeline_clean,
           "input discontinuity did not make the analytical pipeline incomplete");
    expect(assessment.presentation_delivery_clean,
           "clean presentation delivery was contaminated by input discontinuity");
}

void test_presentation_latest_wins_is_exact_and_separate_from_fft_loss() {
    sdr_hackrf::HackrfRxIngress ingress(ingress_config(1536U));
    sdr_hackrf::HackrfFixedBandDsp dsp(dsp_config(1U));
    push_one(ingress, dsp, constant_ci8(768U), 500);

    const auto frames = dsp.poll_spectrum_frames();
    expect(frames.size() == 1U, "latest-wins queue retained more than one frame");
    expect(frames.front().frame_sequence == 2U, "latest spectrum frame did not win");
    expect(frames.front().dropped_fft_frames_before == 0U,
           "presentation supersession was reported as analytical FFT loss");
    expect(
        !sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::FftDropped),
        "presentation supersession set the analytical FFT-loss flag"
    );
    const auto metrics = dsp.metrics();
    expect(metrics.dsp.fft_frames_computed == 3U, "analytical FFT count mismatch");
    expect(metrics.dsp.fft_frames_dropped == 0U, "presentation loss polluted DSP loss");
    expect(metrics.presentation.capacity == 1U, "presentation capacity mismatch");
    expect(metrics.presentation.high_water == 1U, "presentation bound was exceeded");
    expect(metrics.presentation.dropped == 2U, "presentation drop counter mismatch");
    expect(metrics.presentation_frames_superseded == 2U,
           "named presentation supersession counter mismatch");
    expect(metrics.presentation_frames_abandoned == 0U,
           "presentation abandonment counter mismatch");

    const auto assessment = sdr_hackrf::assess_hackrf_fixed_band_dsp_delivery(metrics);
    expect(assessment.source_cursor_continuity_clean,
           "clean source cursor was not classified independently");
    expect(assessment.cpu_fft_loss_free,
           "clean CPU FFT was not classified independently");
    expect(assessment.analytical_pipeline_clean,
           "presentation supersession contaminated analytical completeness");
    expect(!assessment.presentation_delivery_clean,
           "latest-wins supersession was hidden from presentation delivery");
    expect(assessment.presentation_frames_superseded == 2U,
           "assessment lost explicit presentation supersession count");
    expect(assessment.analytical_fft_frames_dropped == 0U,
           "assessment relabelled presentation supersession as FFT loss");
}

void test_cpu_fft_drop_remains_analytical_and_sets_frame_quality() {
    sdr_hackrf::HackrfRxIngress ingress(ingress_config(1536U));
    auto config = dsp_config(4U);
    config.dsp_output_capacity = 1U;
    sdr_hackrf::HackrfFixedBandDsp dsp(std::move(config));
    push_one(ingress, dsp, constant_ci8(768U), 500);

    const auto frames = dsp.poll_spectrum_frames();
    expect(frames.size() == 1U, "bounded CPU output did not retain one frame");
    expect(frames.front().dropped_fft_frames_before != 0U,
           "CPU FFT drop provenance was removed from the retained frame");
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::FftDropped),
        "CPU FFT drop did not set the analytical quality flag"
    );
    const auto metrics = dsp.metrics();
    expect(metrics.dsp.fft_frames_dropped == 2U, "CPU FFT drop count mismatch");
    expect(metrics.presentation_frames_superseded == 0U,
           "CPU FFT drop was misclassified as presentation supersession");
    const auto assessment = sdr_hackrf::assess_hackrf_fixed_band_dsp_delivery(metrics);
    expect(!assessment.cpu_fft_loss_free, "CPU FFT drop was hidden by assessment");
    expect(!assessment.analytical_pipeline_clean,
           "CPU FFT drop left analytical pipeline classified clean");
    expect(assessment.presentation_delivery_clean,
           "CPU FFT drop contaminated independent presentation delivery");
}

}  // namespace

int main() {
    try {
        test_config_and_empty_lease_fail_closed();
        test_fake_lease_produces_canonical_cpu_spectrum();
        test_ci8_rails_preserve_cpu_overload_provenance();
        test_gap_flushes_partial_state_and_retains_loss_flag();
        test_presentation_latest_wins_is_exact_and_separate_from_fft_loss();
        test_cpu_fft_drop_remains_analytical_and_sets_frame_quality();
        std::cout << "R11-I HackRF fixed-band CPU DSP OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

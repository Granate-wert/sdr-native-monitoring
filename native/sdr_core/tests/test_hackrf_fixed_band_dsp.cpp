#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cmath>
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

void test_real_transfer_sized_burst_capacity_keeps_analysis_loss_free() {
    // One normal 262144-byte CI8 callback contains 131072 complex samples.
    // A 64-frame output queue used to lose 192 FFT1024/hop512 outputs per
    // steady-state callback, even though acquisition and CPU kept up.
    constexpr std::uint32_t samples = 131'072U;
    const auto bytes = constant_ci8(samples);
    for (const auto fft : {1024U, 4096U, 16384U}) {
        for (const auto hop : {fft / 2U, fft - 7U}) {
            sdr_hackrf::HackrfRxIngress ingress(ingress_config(samples * 2U));
            auto config = dsp_config(4U);
            config.dsp.fft_size = fft;
            config.dsp.hop_size = hop;
            config.dsp_output_capacity = (samples + hop - 1U) / hop;
            sdr_hackrf::HackrfFixedBandDsp dsp(std::move(config));
            for (std::uint32_t block = 0; block < 3U; ++block) {
                push_one(ingress, dsp, bytes, 1'000'000 + block * 600'000'000LL);
                const auto frames = dsp.poll_spectrum_frames();
                expect(!frames.empty(), "transfer-sized burst did not produce output");
                for (const auto& frame : frames) {
                    expect(frame.dropped_fft_frames_before == 0U,
                           "burst-sized queue lost analytical FFT output");
                }
            }
            const auto metrics = dsp.metrics();
            expect(metrics.dsp.fft_frames_computed == (3U * samples - fft) / hop + 1U,
                   "overlap FFT count across transfer boundaries is incorrect");
            expect(metrics.dsp.fft_frames_dropped == 0U,
                   "bounded burst queue induced analytical loss");
            expect(metrics.presentation_frames_superseded > 0U,
                   "freshest-window supersession must remain separate and visible");
            expect(metrics.presentation.high_water <= 4U,
                   "analytical buffer enlargement changed presentation bound");
        }
    }
}

void test_persistence_precedes_presentation_and_is_bounded() {
    for (const auto mode : {sdr_core::PersistenceMode::RollingExact,
                            sdr_core::PersistenceMode::ExponentialDecay}) {
        auto config = dsp_config(1U);
        config.dsp.averaging_frames = 4U;
        config.persistence.enabled = true;
        config.persistence.mode = mode;
        config.persistence.power_bins = 16U;
        config.persistence.window_frames = 3U;
        config.persistence.snapshot_rate_hz = 15.0;
        sdr_hackrf::HackrfFixedBandDsp dsp(config);
        sdr_hackrf::HackrfRxIngress ingress(ingress_config(2048U));
        const auto input = constant_ci8(1024U);
        sdr_core::PersistenceSnapshot retained;
        std::vector<float> retained_copy;
        for (std::int64_t index = 0; index < 12; ++index) {
            push_one(ingress, dsp, input, 1000000000LL + index * 100000000LL);
            if (index == 0) {
                auto first = dsp.poll_persistence_snapshots(1U);
                expect(first.size() == 1U, "first native density missing");
                retained = first.front();
                retained_copy = *retained.density;
            }
        }
        const auto snapshots = dsp.poll_persistence_snapshots(0U);
        expect(snapshots.size() == 2U, "native density queue is not bounded to two");
        const auto& latest = snapshots.back();
        expect(latest.processed_frames == 12U && latest.update_sequence == 12U,
               "density must count detector outputs, not polled display frames");
        expect(latest.source_frame_sequence == 11U,
               "native density endpoint lost the canonical spectrum sequence");
        expect(latest.source.source_id == config.source.source_id &&
               latest.config_generation == 9U && latest.unit == sdr_core::SpectrumUnit::DbfsBin,
               "native persistence lost producer identity/unit");
        expect(sdr_core::has_flag(latest.quality_flags, sdr_core::QualityFlag::TimestampEstimated),
               "estimated producer timestamp quality missing");
        expect(*retained.density == retained_copy, "a retained snapshot was mutated");
        for (std::uint32_t column = 0U; column < latest.frequency_bins; ++column) {
            double count = 0;
            for (std::uint32_t row = 0U; row < latest.power_bins; ++row) {
                count += (*latest.density)[row * latest.frequency_bins + column];
            }
            if (column == latest.frequency_bins / 2U) {
                expect(std::abs(count * latest.probability_scale - 1.0) < 0.0001,
                       "normalized finite-tone density lost its distribution");
                if (mode == sdr_core::PersistenceMode::RollingExact) {
                    expect(count == 3.0, "exact rolling detector-output window is incorrect");
                }
            } else {
                // Rectangular constant CI8 has EXACT zero power (-infinity)
                // outside DC. The common accumulator does not invent hits
                // for non-finite values or normalize an unobserved column.
                expect(count == 0.0, "non-finite FFT bins acquired invented density");
            }
        }
        const auto metrics = dsp.metrics();
        expect(metrics.persistence_updates == 12U && metrics.dsp.fft_frames_computed == 48U,
               "density, analytical FFT and presentation counts were conflated");
        expect(metrics.presentation.dropped == 11U && metrics.dsp.fft_frames_dropped == 0U,
               "presentation loss contaminated analytical persistence");
        expect(metrics.persistence.capacity == 2U && metrics.persistence.high_water == 2U &&
               metrics.persistence.dropped > 0U, "density queue bound/supersession missing");
        expect(dsp.poll_spectrum_frames(0U).size() == 1U,
               "density enlarged the spectrum presentation queue");
    }
    auto invalid = dsp_config();
    invalid.dsp.fft_size = 262144U;
    invalid.persistence.enabled = true;
    invalid.persistence.mode = sdr_core::PersistenceMode::ExponentialDecay;
    invalid.persistence.power_bins = 4096U;
    bool rejected = false;
    try {
        sdr_hackrf::validate_hackrf_fixed_band_dsp_config(invalid);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "excessive density memory was admitted");
}

void test_detector_group_sequence_retains_native_output_identity_across_polls() {
    auto config = dsp_config();
    config.dsp.averaging_frames = 4U;
    sdr_hackrf::HackrfFixedBandDsp dsp(config);
    sdr_hackrf::HackrfRxIngress ingress(ingress_config(4096U));
    const auto input = constant_ci8(2048U);
    for (std::uint64_t batch = 0; batch < 2U; ++batch) {
        push_one(ingress, dsp, input, 1000000000LL + static_cast<std::int64_t>(batch) * 100000000LL);
        const auto frames = dsp.poll_spectrum_frames(0U);
        expect(frames.size() == 2U, "two complete detector groups were not retained");
        expect(frames[0].frame_sequence == batch * 2U && frames[1].frame_sequence == batch * 2U + 1U,
               "detector-output sequence was fabricated from the analytical FFT count");
    }
    expect(dsp.metrics().dsp.fft_frames_computed == 16U,
           "preserving output identity changed the analytical FFT count");
}

void test_latest_drain_preserves_identity_and_separate_coalescing() {
    auto config = dsp_config(4U);
    config.persistence.enabled = true;
    config.persistence.mode = sdr_core::PersistenceMode::ExponentialDecay;
    config.persistence.power_bins = 16U;
    sdr_hackrf::HackrfFixedBandDsp dsp(config);
    sdr_hackrf::HackrfRxIngress ingress(ingress_config(2048U));
    const auto empty = dsp.drain_latest_spectrum_frame();
    expect(!empty.frame.has_value() && empty.coalesced_frames == 0U,
           "empty latest drain fabricated a frame or coalescing");
    push_one(ingress, dsp, constant_ci8(1024U), 1000000000LL);
    const auto before = dsp.metrics();
    const auto latest = dsp.drain_latest_spectrum_frame();
    expect(latest.frame.has_value() && latest.coalesced_frames == 3U,
           "latest drain did not coalesce the bounded four-frame queue");
    expect(latest.frame->frame_sequence == 3U &&
           latest.frame->source.source_id == config.source.source_id &&
           latest.frame->config_generation == 9U,
           "latest drain changed native output identity");
    expect(latest.frame->dropped_fft_frames_before == 0U,
           "bridge coalescing became an analytical FFT drop");
    const auto after = dsp.metrics();
    expect(before.persistence_updates == 4U && after.persistence_updates == 4U &&
           after.dsp.fft_frames_computed == 4U && after.dsp.fft_frames_dropped == 0U,
           "presentation drain altered native FFT or upstream persistence");
    if (after.stage_timing_available) {
        expect(after.dsp_push_poll_ns > 0U &&
               after.locked_push_ns >= after.dsp_push_poll_ns +
                   after.persistence_call_ns + after.publication_queue_ns,
               "native stage timing did not stay inside the locked DSP push");
    } else {
        expect(after.locked_push_ns == 0U && after.dsp_push_poll_ns == 0U &&
               after.persistence_call_ns == 0U && after.publication_queue_ns == 0U,
               "ordinary native build unexpectedly profiled the hot path");
    }
    expect(after.locked_push_ns == before.locked_push_ns &&
           after.dsp_push_poll_ns == before.dsp_push_poll_ns &&
           after.persistence_call_ns == before.persistence_call_ns &&
           after.publication_queue_ns == before.publication_queue_ns,
           "presentation-only drain changed producer-stage duration counters");
    expect(after.presentation.popped - before.presentation.popped == 4U &&
           after.presentation.dropped == before.presentation.dropped,
           "bridge drain was misreported as producer queue overflow");
    expect(!dsp.drain_latest_spectrum_frame().frame.has_value(),
           "latest drain retained a stale frame");
}

}  // namespace

int main() {
    try {
        test_config_and_empty_lease_fail_closed();
        test_fake_lease_produces_canonical_cpu_spectrum();
        test_gap_flushes_partial_state_and_retains_loss_flag();
        test_presentation_latest_wins_is_exact_and_separate_from_fft_loss();
        test_cpu_fft_drop_remains_analytical_and_sets_frame_quality();
        test_real_transfer_sized_burst_capacity_keeps_analysis_loss_free();
        test_persistence_precedes_presentation_and_is_bounded();
        test_detector_group_sequence_retains_native_output_identity_across_polls();
        test_latest_drain_preserves_identity_and_separate_coalescing();
        std::cout << "R11-I HackRF fixed-band CPU DSP OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

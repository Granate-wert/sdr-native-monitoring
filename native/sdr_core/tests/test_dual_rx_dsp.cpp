#include "sdr_core/dual_rx_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <cmath>
#include <complex>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
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

sdr_core::IqBlock burst_block(
    const std::uint64_t sequence, const std::uint64_t first_index, const double bin
) {
    constexpr std::uint32_t count = 262'144U;
    auto result = block(sequence, first_index, 1'000'000U + sequence * 4'266'667U, bin);
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(count * 8U);
    for (std::uint32_t index = 0U; index < count; ++index) {
        const auto phase = two_pi * bin * static_cast<double>(first_index + index) / 1024.0;
        const float real = static_cast<float>(std::cos(phase));
        const float imag = static_cast<float>(std::sin(phase));
        std::memcpy(bytes->data() + index * 8U, &real, sizeof(real));
        std::memcpy(bytes->data() + index * 8U + 4U, &imag, sizeof(imag));
    }
    result.sample_count = count;
    result.sample_rate_hz = 61'440'000.0;
    result.samples = std::move(bytes);
    return result;
}


void test_native_consumer_precedes_coalescing_and_flushes_partial_batch() {
    auto value = config(1U);
    value.primary.dsp.batch_size = value.secondary.dsp.batch_size = 4U;
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(value);
    std::uint64_t received{};
    publisher.set_analytical_consumer([&](auto& pair, const auto& p, const auto& q) {
        expect(pair.primary.first_sample_index == pair.secondary.first_sample_index &&
            p.fft_frames_computed == q.fft_frames_computed, "native pair consumer epoch");
        ++received;
        return false; // Same-owner engine controls its final publication.
    });
    publisher.push(block(0, 0, 1'000'000, 12), block(0, 0, 1'000'000, -20));
    expect(received == 0, "partial batch retained until terminal flush");
    publisher.flush();
    auto metrics = publisher.metrics();
    expect(received == 1 && metrics.paired_frames_formed == 1 &&
        metrics.paired_frames_published == 0 && metrics.output_queue.depth == 0,
        "native consumer receives terminal partial pair without bridge publication");
    publisher.configure(value);
    publisher.push(block(0, 0, 1'000'000, 12), block(0, 0, 1'000'000, -20));
    publisher.flush();
    expect(received == 1 && publisher.poll_spectrum_frames(0).size() == 1,
        "successful reconfigure cannot retain old owner callback");
}
void test_first_consumer_failure_survives_throwing_gap_cleanup(bool at_flush) {
    auto value = config(1U);
    if (at_flush) value.primary.dsp.batch_size = value.secondary.dsp.batch_size = 4;
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(value);
    std::uint64_t consumes{}, gaps{};
    publisher.set_analytical_consumer([&](auto&, const auto&, const auto&) -> bool {
        ++consumes;
        throw std::runtime_error("first analytical failure");
    });
    publisher.set_shared_gap_consumer([&] {
        ++gaps;
        throw std::runtime_error("secondary cleanup failure");
    });
    std::string cause;
    try {
        publisher.push(block(0, 0, 1'000'000, 12), block(0, 0, 1'000'000, -20));
        if (at_flush) {
            expect(consumes == 0, "terminal fault must occur in partial flush, not push");
            publisher.flush();
        }
    } catch (const std::runtime_error& error) { cause = error.what(); }
    expect(cause == "first analytical failure" && consumes == 1 && gaps == 1 &&
        publisher.metrics().shared_input_gaps == 1 && publisher.poll_spectrum_frames(0).empty(),
        "push/flush must preserve first failure and invalidate once despite throwing cleanup");
}

void test_failed_gap_callback_is_not_reentered_during_unwind() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config(1U));
    std::uint64_t gaps{};
    publisher.set_shared_gap_consumer([&] {
        throw std::runtime_error("gap failure " + std::to_string(++gaps));
    });
    publisher.set_analytical_consumer([&](auto&, const auto&, const auto&) {
        publisher.mark_shared_gap();
        return false;
    });
    std::string cause;
    try { publisher.push(block(0, 0, 1'000'000, 12), block(0, 0, 1'000'000, -20)); }
    catch (const std::runtime_error& error) { cause = error.what(); }
    expect(cause == "gap failure 1" && gaps == 1 && publisher.metrics().shared_input_gaps == 1 &&
        publisher.poll_spectrum_frames(0).empty(), "already invalidated gap callback must not be retried");
}

void test_native_consumer_sees_every_large_burst_before_latest_wins() {
    auto value = config(1U);
    value.primary.dsp.fft_size = value.secondary.dsp.fft_size = 1024;
    value.primary.dsp.hop_size = value.secondary.dsp.hop_size = 512;
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(value);
    std::uint64_t received{};
    publisher.set_analytical_consumer([&](auto&, const auto&, const auto&) {
        ++received;
        return true;
    });
    publisher.push(burst_block(0, 0, 12), burst_block(0, 0, -20));
    auto metrics = publisher.metrics();
    expect(received == 511 && metrics.paired_frames_formed == 511 &&
        metrics.paired_frames_published == 511 && metrics.paired_frames_superseded == 510 &&
        metrics.primary.fft_frames_dropped == 0 && metrics.secondary.fft_frames_dropped == 0,
        "native pre-coalescing consumer must see every analytical FFT pair");
}
void test_gap_observer_precedes_new_epoch_delivery() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config(1U));
    std::uint64_t observed_gaps{}, delivered_after_gap{};
    publisher.set_shared_gap_consumer([&] { ++observed_gaps; });
    publisher.set_analytical_consumer([&](auto& frame, const auto&, const auto&) {
        if (frame.shared_input_gaps_before) {
            expect(observed_gaps == frame.shared_input_gaps_before,
                "owner gap notification BEFORE new epoch delivery");
            ++delivered_after_gap;
        }
        return true;
    });
    publisher.push(block(0, 0, 1'000'000, 12), block(0, 0, 1'000'000, -20));
    publisher.push(block(2, 512, 3'000'000, 12), block(2, 512, 3'000'000, -20));
    expect(observed_gaps == 1 && delivered_after_gap == 1,
        "one common discontinuity, both channels delivered only after owner notification");
}

void test_large_burst_retains_analytical_fft_before_pair_coalescing() {
    auto value = config(4U);
    value.primary.dsp.fft_size = value.secondary.dsp.fft_size = 1024U;
    value.primary.dsp.hop_size = value.secondary.dsp.hop_size = 512U;
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(value);
    const auto primary = burst_block(0U, 0U, 13.0);
    const auto secondary = burst_block(0U, 0U, 31.0);

    // Reproduce the old coupling without modifying the baseline implementation:
    // four analytical slots for a 511-frame input discard 507 FFT outputs.
    auto coupled = sdr_core::make_cpu_dsp_backend({
        .source = value.primary.source, .output_capacity = value.output_queue_capacity});
    coupled->configure(value.primary.dsp);
    coupled->push_iq(primary);
    expect(coupled->metrics().fft_frames_computed == 511U &&
               coupled->metrics().fft_frames_dropped == 507U,
           "small analytical capacity must reproduce the former burst-loss defect");

    publisher.push(primary, secondary);
    auto metrics = publisher.metrics();
    expect(metrics.resource_budget.analytical_output_capacity == 515U &&
               metrics.primary.fft_frames_computed == 511U &&
               metrics.secondary.fft_frames_computed == 511U &&
               metrics.primary.fft_frames_dropped == 0U &&
               metrics.secondary.fft_frames_dropped == 0U &&
               metrics.paired_frames_published == 511U &&
               metrics.paired_frames_superseded == 507U,
           "render cap4 may supersede pairs only after all 511 channel FFTs are paired");
    const auto drained = publisher.drain_latest_spectrum_frame();
    expect(drained.frame && drained.coalesced_frames == 3U &&
               drained.frame->first_sample_index == 510U * 512U &&
               drained.frame->primary.dropped_fft_frames_before == 0U &&
               drained.frame->secondary.dropped_fft_frames_before == 0U &&
               (*drained.frame->primary.values)[512U + 13U] > -0.001F &&
               (*drained.frame->secondary.values)[512U + 31U] > -0.001F,
           "latest pair must retain both numerics and honest analytical-loss provenance");
    publisher.push(burst_block(1U, 262'144U, 13.0), burst_block(1U, 262'144U, 31.0));
    metrics = publisher.metrics();
    expect(metrics.primary.fft_frames_computed == 1023U &&
               metrics.secondary.fft_frames_computed == 1023U &&
               metrics.paired_frames_published == 1023U &&
               metrics.primary.fft_frames_dropped == 0U &&
               metrics.secondary.fft_frames_dropped == 0U && metrics.shared_input_gaps == 0U,
           "steady input must retain all additional 512 FFTs without an invented transport gap");
}

void test_batched_burst_keeps_partial_batch_without_internal_loss() {
    auto value = config(1U);
    value.primary.dsp.fft_size = value.secondary.dsp.fft_size = 1024U;
    value.primary.dsp.hop_size = value.secondary.dsp.hop_size = 512U;
    value.primary.dsp.batch_size = value.secondary.dsp.batch_size = 4U;
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(value);
    publisher.push(burst_block(0U, 0U, 13.0), burst_block(0U, 0U, 31.0));
    expect(publisher.metrics().paired_frames_published == 508U,
           "paired polling must not flush a partial analytical batch for render cadence");
    publisher.push(burst_block(1U, 262'144U, 13.0), burst_block(1U, 262'144U, 31.0));
    const auto metrics = publisher.metrics();
    expect(metrics.paired_frames_published == 1020U &&
               metrics.primary.fft_frames_dropped == 0U &&
               metrics.secondary.fft_frames_dropped == 0U &&
               metrics.resource_budget.analytical_output_capacity == 518U,
           "both channels must retain identical partial batches across contiguous inputs");
}

void test_combined_budget_and_failed_reconfigure_preserve_previous_result() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config());
    publisher.push(block(0U, 0U, 1'000U, 3.0), block(0U, 0U, 1'000U, 7.0));
    std::vector<sdr_core::DualRxDspConfig> invalid;
    auto bad = config();
    bad.max_input_samples_per_push = 0U;
    invalid.push_back(bad);
    bad = config();
    bad.max_input_samples_per_push = 8'388'609U; // two CF32 payloads > 128 MiB
    invalid.push_back(bad);
    bad = config();
    bad.primary.dsp.fft_size = bad.secondary.dsp.fft_size = 8192U;
    bad.primary.dsp.hop_size = bad.secondary.dsp.hop_size = 1U;
    bad.max_input_samples_per_push = 512U; // combined spectra >128 MiB, each <128
    invalid.push_back(bad);
    bad = config();
    bad.primary.dsp.fft_size = bad.secondary.dsp.fft_size = 262'144U;
    bad.primary.dsp.hop_size = bad.secondary.dsp.hop_size = 131'072U;
    bad.primary.dsp.batch_size = bad.secondary.dsp.batch_size = 4U;
    invalid.push_back(bad); // combined DSP >128 MiB, each <128
    bad = config();
    bad.max_input_samples_per_push = std::numeric_limits<std::uint32_t>::max();
    bad.primary.dsp.hop_size = bad.secondary.dsp.hop_size = 1U;
    invalid.push_back(bad);
    for (const auto& candidate : invalid) {
        bool refused = false;
        try { publisher.configure(candidate); }
        catch (const sdr_core::ConfigurationError&) { refused = true; }
        expect(refused && publisher.metrics().input_epochs_received == 1U &&
                   publisher.metrics().output_queue.depth == 1U,
               "budget refusal must precede allocation/commit and preserve existing paired result");
    }
    expect(publisher.poll_spectrum_frames(0U).size() == 1U,
           "failed reconfigure must not discard accepted output");
}

void test_input_preflight_is_pair_atomic_and_refuses_oversized_blocks() {
    auto value = config();
    value.max_input_samples_per_push = 256U;
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(value);
    auto left = block(0U, 0U, 1'000U, 3.0);
    auto right = block(0U, 0U, 1'000U, 7.0);
    right.samples = std::make_shared<std::vector<std::uint8_t>>(1U);
    bool refused = false;
    try { publisher.push(left, right); }
    catch (const sdr_core::ConfigurationError&) { refused = true; }
    expect(refused && publisher.metrics().primary.samples_processed == 0U &&
               publisher.metrics().secondary.samples_processed == 0U &&
               publisher.metrics().input_epochs_received == 0U,
           "invalid secondary payload must be refused before primary DSP changes");
    refused = false;
    try { publisher.push(burst_block(0U, 0U, 3.0), burst_block(0U, 0U, 7.0)); }
    catch (const sdr_core::ConfigurationError&) { refused = true; }
    expect(refused && publisher.metrics().input_epochs_received == 0U,
           "actual input cannot expand the declared burst reservation");
    left.first_sample_index = right.first_sample_index =
        static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max());
    right.samples = block(0U, 0U, 1'000U, 7.0).samples;
    refused = false;
    try { publisher.push(left, right); }
    catch (const sdr_core::ConfigurationError&) { refused = true; }
    expect(refused && publisher.metrics().input_epochs_received == 0U,
           "sample index must not wrap the signed CPU stream range");
    publisher.push(block(0U, 0U, 1'000U, 3.0), block(0U, 0U, 1'000U, 7.0));
    expect(publisher.metrics().paired_frames_published == 1U,
           "refused inputs must leave the previous configuration usable");
}

void test_gap_discards_stale_pairs_with_exact_abandon_accounting() {
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config());
    publisher.push(block(0U, 0U, 1'000U, 3.0), block(0U, 0U, 1'000U, 7.0));
    publisher.mark_shared_gap();
    expect(publisher.poll_spectrum_frames(0U).empty() &&
               publisher.metrics().paired_frames_abandoned == 1U &&
               publisher.metrics().output_queue.abandoned == 1U &&
               publisher.metrics().primary.fft_frames_dropped == 0U,
           "explicit gap must not leak pre-gap pending snapshots or count them as DSP loss");
    publisher.push(block(1U, 256U, 2'000U, 3.0), block(1U, 256U, 2'000U, 7.0));
    publisher.reset();
    expect(publisher.metrics().paired_frames_abandoned == 2U &&
               publisher.metrics().shared_input_gaps == 1U &&
               publisher.poll_spectrum_frames(0U).empty(),
           "explicit reset abandons pending pairs without inventing a second transport gap");
}

void test_nonfinite_peer_is_refused_before_partial_batch_history() {
    auto value = config();
    value.primary.dsp.batch_size = value.secondary.dsp.batch_size = 4U;
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(value);
    auto bad = block(0U, 0U, 1'000U, 7.0);
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(*bad.samples);
    const float nan = std::numeric_limits<float>::quiet_NaN();
    std::memcpy(bytes->data(), &nan, sizeof(nan));
    bad.samples = std::move(bytes);
    bool refused = false;
    try { publisher.push(block(0U, 0U, 1'000U, 3.0), bad); }
    catch (const sdr_core::ConfigurationError&) { refused = true; }
    expect(refused && publisher.metrics().primary.samples_processed == 0U &&
               publisher.metrics().secondary.samples_processed == 0U &&
               publisher.metrics().paired_frames_published == 0U &&
               publisher.metrics().input_epochs_received == 0U,
           "a non-finite peer must not silently strand an unpaired primary partial batch");
}

void test_common_rf_geometry_change_creates_shared_epoch_and_clears_pending_pairs() {
    for (const bool change_rate : {false, true}) {
        sdr_core::DualRxDspPublisher publisher;
        publisher.configure(config());
        publisher.push(block(0U, 0U, 1'000U, 3.0), block(0U, 0U, 1'000U, 7.0));
        auto left = block(1U, 256U, 2'000U, 3.0);
        auto right = block(1U, 256U, 2'000U, 7.0);
        if (change_rate) {
            left.sample_rate_hz = right.sample_rate_hz = 512'000.0;
        } else {
            left.center_frequency_hz = right.center_frequency_hz = 434'920'000.0;
        }
        publisher.push(left, right);
        const auto frames = publisher.poll_spectrum_frames(0U);
        expect(frames.size() == 1U && frames.front().synchronization_epoch == 2U &&
                   frames.front().shared_input_gaps_before == 1U &&
                   frames.front().first_sample_index == 256U &&
                   publisher.metrics().paired_frames_abandoned == 1U,
               "common rate/center changes cannot leak old snapshots under an unchanged pair epoch");
    }
}

void test_factory_policy_is_honest_and_forced_unavailable_does_not_fallback() {
    auto value = config();
    value.backend.preference = sdr_core::ComputeBackendKind::Auto;
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(value);
    auto metrics = publisher.metrics();
    expect(metrics.primary.requested_preference == sdr_core::ComputeBackendKind::Auto &&
               metrics.secondary.requested_preference == sdr_core::ComputeBackendKind::Auto &&
               metrics.primary.active_backend == sdr_core::ComputeBackendKind::Cpu &&
               metrics.secondary.active_backend == sdr_core::ComputeBackendKind::Cpu &&
               metrics.primary.backend_fallback_count == 0U,
           "safe AUTO CPU policy must retain requested AUTO, not claim CUDA or runtime fallback");
    publisher.push(block(0U, 0U, 1'000U, 3.0), block(0U, 0U, 1'000U, 7.0));
    value.backend.preference = sdr_core::ComputeBackendKind::Hip;
    bool refused = false;
    try { publisher.configure(value); }
    catch (const sdr_core::BackendUnavailableError&) { refused = true; }
    expect(refused && publisher.metrics().output_queue.depth == 1U &&
               publisher.metrics().primary.requested_preference == sdr_core::ComputeBackendKind::Auto,
           "forced unavailable factory choice must refuse without silent CPU replacement");
    if (!sdr_core::backend_availability(sdr_core::ComputeBackendKind::Cuda).compiled) {
        value.backend.preference = sdr_core::ComputeBackendKind::Cuda;
        refused = false;
        try { publisher.configure(value); }
        catch (const sdr_core::BackendUnavailableError&) { refused = true; }
        expect(refused && publisher.metrics().output_queue.depth == 1U,
               "CPU build must refuse forced CUDA, not publish a CPU pair labelled CUDA");
    }
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
    expect(metrics.paired_frames_published == 3U && metrics.paired_frames_superseded == 2U &&
               metrics.output_queue.dropped == 2U && metrics.primary.fft_frames_dropped == 0U &&
               metrics.secondary.fft_frames_dropped == 0U,
           "all three analytical pairs must precede two exact publication supersessions");
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
        test_native_consumer_precedes_coalescing_and_flushes_partial_batch();
        test_first_consumer_failure_survives_throwing_gap_cleanup(false);
        test_first_consumer_failure_survives_throwing_gap_cleanup(true);
        test_failed_gap_callback_is_not_reentered_during_unwind();
        test_gap_observer_precedes_new_epoch_delivery();
        test_native_consumer_sees_every_large_burst_before_latest_wins();
        test_paired_cpu_spectra_keep_channel_identity();
        test_shared_gap_restarts_both_channel_histories();
        test_sample_index_gap_is_shared_even_when_sequence_is_consecutive();
        test_mismatched_pair_is_not_partially_processed();
        test_latest_wins_is_pair_atomic();
        test_invalid_channel_dsp_is_rejected_before_processing();
        test_shared_plan_preserves_single_cpu_numerics();
        test_large_burst_retains_analytical_fft_before_pair_coalescing();
        test_batched_burst_keeps_partial_batch_without_internal_loss();
        test_combined_budget_and_failed_reconfigure_preserve_previous_result();
        test_input_preflight_is_pair_atomic_and_refuses_oversized_blocks();
        test_gap_discards_stale_pairs_with_exact_abandon_accounting();
        test_nonfinite_peer_is_refused_before_partial_batch_history();
        test_common_rf_geometry_change_creates_shared_epoch_and_clears_pending_pairs();
        test_factory_policy_is_honest_and_forced_unavailable_does_not_fallback();
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    return 0;
}

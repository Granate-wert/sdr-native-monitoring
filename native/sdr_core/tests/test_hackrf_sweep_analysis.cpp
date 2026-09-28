#include "sdr_hackrf/hackrf_sweep_analysis.hpp"

#include "sdr_core/errors.hpp"

#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

sdr_hackrf::HackrfSweepAnalysisConfig config() {
    sdr_hackrf::HackrfSweepAnalysisConfig result;
    result.acquisition.sequence.ranges = {{2400U, 2480U}};
    result.acquisition.sequence.step_width_hz = 20'000'000U;
    result.acquisition.sequence.offset_hz = 7'500'000U;
    result.acquisition.sequence.style = sdr_hackrf::HackrfSweepStyle::Interleaved;
    result.acquisition.sample_rate_hz = 20'000'000.0;
    result.acquisition.baseband_filter_hz = 15'000'000U;
    result.acquisition.config_generation = 9U;
    result.source.source_type = sdr_core::SourceType::LiveIq;
    result.source.source_id = "hackrf:test-source";
    result.source.display_name = "HackRF Sweep test";
    result.source.backend_id = "native.libhackrf.sweep.v1";
    result.acquisition_epoch = 7U;
    result.fft_size = 4096U;
    return result;
}

sdr_hackrf::HackrfSweepQueuedBlock block(
    const std::uint32_t index,
    const std::uint64_t scan_epoch,
    const std::uint64_t continuity_epoch,
    const bool gap_before = false,
    const std::uint32_t skipped = 0U
) {
    sdr_hackrf::HackrfSweepQueuedBlock result;
    result.reported_tuned_frequency_hz =
        2'400'000'000U + (index / 2U) * 20'000'000U +
        (index % 2U) * 5'000'000U;
    result.scan_epoch = scan_epoch;
    result.continuity_epoch = continuity_epoch;
    result.plan_index = index;
    result.range_index = 0U;
    result.step_index = index / 2U;
    result.interleave_phase = static_cast<std::uint8_t>(index % 2U);
    result.known_skipped_headers_before = skipped;
    result.host_timestamp_ns = 10'000'000 + static_cast<std::int64_t>(index) * 10'000;
    result.config_generation = 9U;
    result.gap_before = gap_before;
    result.new_scan = index == 0U;
    // Two equal ±5 MHz complex tones at Fs=20 MS/s. The last 4096 of the
    // real 8187 CI8 samples produce peaks in the two noncontiguous crops.
    for (std::size_t sample = 0U;
         sample < sdr_hackrf::hackrf_sweep_ci8_bytes / 2U;
         ++sample) {
        const auto phase = sample % 4U;
        const auto real = phase == 0U ? 80 : phase == 2U ? -80 : 0;
        result.interleaved_ci8[2U * sample] = static_cast<std::uint8_t>(real);
        result.interleaved_ci8[2U * sample + 1U] = 0U;
    }
    return result;
}

std::size_t grid_index(const std::vector<double>& frequencies, const double frequency) {
    for (std::size_t index = 0U; index < frequencies.size(); ++index) {
        if (std::abs(frequencies[index] - frequency) < 1e-6) {
            return index;
        }
    }
    throw std::runtime_error("expected RF frequency is absent from output grid");
}

void test_two_disjoint_subbands_and_progressive_line() {
    sdr_hackrf::HackrfSweepAnalysis analysis(config());
    const auto& definition = analysis.definition();
    const double bin_hz = 20'000'000.0 / 4096.0;
    expect(definition.start_frequency_hz == 2'400'000'000.0 + bin_hz &&
               definition.stop_frequency_hz == 2'480'000'000.0 &&
               definition.target_spacing_hz == bin_hz &&
               definition.analysis_window_hz == 5'000'000.0 &&
               definition.analysis_bins_per_usable_window == 1024U &&
               definition.physical_fft_bin_width_hz == bin_hz &&
               definition.physical_fft_size == 4096U &&
               definition.segments.size() == 16U &&
               definition.epoch == 7U,
           "Sweep line declaration invented the first excluded edge bin");
    expect(definition.segments[0].usable_start_hz == 2'400'000'000.0 + bin_hz &&
               definition.segments[0].usable_stop_hz == 2'405'000'000.0 &&
               definition.segments[1].usable_start_hz == 2'405'000'000.0 + bin_hz &&
               definition.segments[2].usable_start_hz == 2'410'000'000.0 + bin_hz,
           "header base/phase was mistaken for one contiguous 20 MHz FFT");

    expect(analysis.admit(block(0U, 1U, 1U)).empty(),
           "first block was incorrectly published as a complete Sweep line");
    const auto progress = analysis.preview();
    expect(progress && progress->line_sequence == 1U &&
               progress->revision == 2U &&
               progress->acquired_segments.size() == 2U &&
               progress->pending_segment_indices.size() == 14U &&
               progress->unit == sdr_core::SpectrumUnit::DbfsBin,
           "first block did not expose two nonterminal segments");
    const auto& frequency = *progress->frequencies_hz;
    expect(frequency.front() == 2'400'000'000.0 + bin_hz &&
               frequency.back() < 2'480'000'000.0 &&
               frequency.size() == 16'383U,
           "HackRF Sweep grid did not keep the requested stop half-open");
    const auto low_peak = grid_index(frequency, 2'402'500'000.0);
    const auto high_peak = grid_index(frequency, 2'412'500'000.0);
    const auto hole = grid_index(frequency, 2'407'500'000.0);
    expect(std::isfinite((*progress->values)[low_peak]) &&
               std::isfinite((*progress->values)[high_peak]) &&
               std::isnan((*progress->values)[hole]) &&
               ((*progress->quality_flags_per_bin)[hole] &
                static_cast<std::uint32_t>(sdr_core::QualityFlag::MissingSegment)) != 0U,
           "partial FFT fabricated power through the interleaved RF hole");
    expect(progress->segment_acquisition.size() == 2U &&
               progress->segment_acquisition[0].fft_size == 4096U &&
               progress->segment_acquisition[0].sample_rate_hz == 20'000'000.0 &&
               progress->segment_acquisition[0].first_sample_index == 4091U &&
               sdr_core::has_flag(progress->segment_acquisition[0].quality_flags,
                                  sdr_core::QualityFlag::SettlingIncomplete) &&
               sdr_core::has_flag(progress->segment_acquisition[0].quality_flags,
                                  sdr_core::QualityFlag::TimestampEstimated),
           "tail FFT or unproven RF/timestamp provenance was lost");

    std::vector<sdr_core::SweepLineFrame> terminal;
    for (std::uint32_t index = 1U; index < 8U; ++index) {
        auto emitted = analysis.admit(block(index, 1U, 1U));
        terminal.insert(terminal.end(), emitted.begin(), emitted.end());
    }
    expect(terminal.size() == 1U && terminal[0].line_sequence == 1U &&
               terminal[0].state == sdr_core::SweepLineState::Complete &&
               terminal[0].missing_segment_indices.empty() &&
               terminal[0].acquired_segments.size() == 16U &&
               !analysis.preview(),
           "all eight firmware blocks did not form one complete 80 MHz line");
    const auto metrics = analysis.metrics();
    expect(metrics.accepted_blocks == 8U &&
               metrics.dsp.fft_frames_computed == 8U &&
               metrics.dsp.fft_frames_dropped == 0U &&
               metrics.lines.completed_lines == 1U &&
               metrics.lines.gapped_lines == 0U &&
               analysis.finish().empty(),
           "one FFT per tune or terminal-line accounting changed");
}

void test_gap_flush_and_next_scan_without_stale_repair() {
    sdr_hackrf::HackrfSweepAnalysis analysis(config());
    static_cast<void>(analysis.admit(block(0U, 1U, 1U)));
    static_cast<void>(analysis.admit(block(1U, 1U, 1U)));
    const auto gap = analysis.admit(block(3U, 1U, 2U, true, 1U));
    expect(gap.size() == 1U && gap[0].state == sdr_core::SweepLineState::Gap &&
               gap[0].line_sequence == 1U &&
               !gap[0].missing_segment_indices.empty() &&
               !analysis.preview(),
           "skipped header repaired a stale partial scan");
    expect(analysis.admit(block(4U, 1U, 2U)).empty(),
           "remaining blocks of a broken scan were admitted");
    expect(analysis.admit(block(0U, 2U, 2U, true)).empty(),
           "new origin reused the old scan's terminal line");
    std::vector<sdr_core::SweepLineFrame> terminal;
    for (std::uint32_t index = 1U; index < 8U; ++index) {
        auto emitted = analysis.admit(block(index, 2U, 2U));
        terminal.insert(terminal.end(), emitted.begin(), emitted.end());
    }
    expect(terminal.size() == 1U && terminal[0].state == sdr_core::SweepLineState::Complete &&
               terminal[0].line_sequence == 2U && terminal[0].epoch == 7U,
           "the next clean scan did not recover after explicit gap");
    const auto metrics = analysis.metrics();
    expect(metrics.gap_events == 1U && metrics.suppressed_after_gap == 2U &&
               metrics.lines.completed_lines == 1U && metrics.lines.gapped_lines == 1U,
           "gap/suppression accounting is not distinct from FFT output");
}

void test_incomplete_finish_and_fail_closed_admission() {
    {
        sdr_hackrf::HackrfSweepAnalysis analysis(config());
        static_cast<void>(analysis.admit(block(0U, 1U, 1U)));
        auto terminal = analysis.finish();
        expect(terminal.size() == 1U &&
                   terminal[0].state == sdr_core::SweepLineState::Gap &&
                   terminal[0].gap_reasons.size() == 1U &&
                   terminal[0].gap_reasons[0] == sdr_core::SweepLineGapReason::Cancellation &&
                   !analysis.preview() && analysis.finish().empty(),
               "Stop hid a partial line instead of publishing a terminal gap");
        bool refused = false;
        try {
            static_cast<void>(analysis.admit(block(1U, 1U, 1U)));
        } catch (const sdr_core::ConfigurationError&) {
            refused = true;
        }
        expect(refused, "post-Stop block was admitted");
    }
    {
        auto narrow = config();
        narrow.fft_size = 1024U;
        sdr_hackrf::HackrfSweepAnalysis analysis(std::move(narrow));
        expect(analysis.definition().analysis_bins_per_usable_window == 256U &&
                   analysis.definition().physical_fft_size == 1024U,
               "minimum admitted FFT has incorrect physical geometry");
    }
    for (std::uint32_t case_id = 0U; case_id < 7U; ++case_id) {
        auto invalid = config();
        switch (case_id) {
        case 0U: invalid.fft_size = 8192U; break;
        case 1U: invalid.acquisition.sample_rate_hz = 16'000'000.0; break;
        case 2U: invalid.acquisition.baseband_filter_hz = 20'000'000U; break;
        case 3U: invalid.unit = sdr_core::SpectrumUnit::Dbm; break;
        case 4U: invalid.source.backend_id = "native.libhackrf.rx.v1"; break;
        case 5U: invalid.acquisition.sequence.ranges = {{2400U, 2740U}}; break;
        case 6U: invalid.fft_size = 512U; break;
        default: break;
        }
        bool refused = false;
        try {
            sdr_hackrf::HackrfSweepAnalysis analysis(std::move(invalid));
        } catch (const sdr_core::ConfigurationError&) {
            refused = true;
        }
        expect(refused, "unqualified Sweep geometry/unit was admitted");
    }
    {
        sdr_hackrf::HackrfSweepAnalysis analysis(config());
        auto bad = block(0U, 1U, 1U);
        bad.reported_tuned_frequency_hz += 1U;
        bool refused = false;
        try {
            static_cast<void>(analysis.admit(bad));
        } catch (const sdr_core::ConfigurationError&) {
            refused = true;
        }
        expect(refused && analysis.metrics().dsp.fft_frames_computed == 0U,
               "out-of-plan header reached the FFT");
    }
}

}  // namespace

int main() {
    try {
        test_two_disjoint_subbands_and_progressive_line();
        test_gap_flush_and_next_scan_without_stale_repair();
        test_incomplete_finish_and_fail_closed_admission();
        std::cout << "HackRF Sweep native FFT/line analysis OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

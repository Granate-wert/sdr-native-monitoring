#pragma once

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/sweep_line_assembler.hpp"
#include "sdr_hackrf/hackrf_sweep_session.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

namespace sdr_hackrf {

// This is the pinned 20 MS/s / 15 MHz / 7.5 MHz-offset one-block-per-tune
// mapping used by the official host tool. It is a *numerical* FFT/crop policy,
// not an RF-flatness, device-overrun, analogue settling or release claim.
struct HackrfSweepAnalysisConfig {
    HackrfSweepProfile acquisition;
    sdr_core::SourceDescriptor source;
    std::uint64_t acquisition_epoch{1U};
    std::uint32_t fft_size{4096U};
    sdr_core::WindowType window{sdr_core::WindowType::Hann};
    sdr_core::DetectorType detector{sdr_core::DetectorType::Sample};
    sdr_core::SpectrumUnit unit{sdr_core::SpectrumUnit::DbfsBin};
};

struct HackrfSweepAnalysisMetrics {
    std::uint64_t accepted_blocks{};
    std::uint64_t suppressed_after_gap{};
    std::uint64_t gap_events{};
    sdr_core::DspBackendMetrics dsp;
    sdr_core::SweepLineAssemblyMetrics lines;
};

// Native CPU DSP and canonical line assembler on already-copied Sweep blocks.
// It never opens a device or transports CI8 through Python. One owner calls
// admit()/preview()/finish(); preview is nonterminal and its caller MUST bound
// polling cadence. A production source/worker/GUI route is still separate.
class HackrfSweepAnalysis final {
public:
    explicit HackrfSweepAnalysis(HackrfSweepAnalysisConfig config);
    ~HackrfSweepAnalysis();
    HackrfSweepAnalysis(const HackrfSweepAnalysis&) = delete;
    HackrfSweepAnalysis& operator=(const HackrfSweepAnalysis&) = delete;

    [[nodiscard]] std::vector<sdr_core::SweepLineFrame> admit(
        const HackrfSweepQueuedBlock& block
    );
    [[nodiscard]] std::optional<sdr_core::SweepProgressFrame> preview() const;
    [[nodiscard]] std::vector<sdr_core::SweepLineFrame> finish();
    [[nodiscard]] const sdr_core::SweepLineDefinition& definition() const noexcept;
    [[nodiscard]] HackrfSweepAnalysisMetrics metrics() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_hackrf

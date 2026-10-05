#pragma once

#include "sdr_hackrf/hackrf_sweep_analysis.hpp"

#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <variant>

namespace sdr_hackrf {

struct HackrfSweepRuntimeAnalysisConfig {
    HackrfSweepAnalysisConfig analysis;
    // Native, on-demand partial-line snapshots. Not analytical FFT/LPS or
    // GUI/DWM FPS. The single latest slot is intentionally coalescing.
    std::uint32_t preview_rate_hz{50U};
};

struct HackrfSweepRuntimeAnalysisMetrics {
    bool lifecycle_open{};
    bool worker_exited{};
    bool worker_joined{};
    bool worker_failed{};
    std::uint64_t worker_blocks_processed{};
    std::uint64_t progress_superseded{};
    std::uint64_t progress_cleared_by_terminal{};
    std::uint64_t terminal_superseded{};
    bool progress_pending{};
    bool terminal_pending{};
    HackrfSweepSessionMetrics source;
    HackrfSweepAnalysisMetrics analysis;
};

struct HackrfSweepRuntimeAnalysisStopResult {
    HackrfSweepStopResult source;
    bool worker_joined{};
    bool worker_failed{};

    [[nodiscard]] bool complete() const noexcept {
        return source.complete() && worker_joined;
    }
    [[nodiscard]] bool clean() const noexcept {
        return complete() && source.clean() && !worker_failed;
    }
};

using HackrfSweepPublication = std::variant<
    std::monostate, sdr_core::SweepProgressFrame, sdr_core::SweepLineFrame>;

// One receive-only owner: vendor callback -> bounded copied CI8 ring -> native
// worker/FFT/line assembler -> at most one progress and one terminal line.
// Only reduced immutable frames cross a future binding. Stop retains the SAME
// source/worker on failure; there is no alternate SDK handle or hidden retry.
class HackrfSweepRuntimeAnalysisSession final {
public:
    [[nodiscard]] static std::unique_ptr<HackrfSweepRuntimeAnalysisSession> start(
        std::unique_ptr<HackrfSweepRuntimePort> runtime,
        HackrfSweepRuntimeAnalysisConfig config
    );

    ~HackrfSweepRuntimeAnalysisSession();
    HackrfSweepRuntimeAnalysisSession(const HackrfSweepRuntimeAnalysisSession&) = delete;
    HackrfSweepRuntimeAnalysisSession& operator=(const HackrfSweepRuntimeAnalysisSession&) = delete;

    // A single ordered drain prevents a newer preview overtaking an older
    // terminal line when both coalescing slots are populated.
    [[nodiscard]] HackrfSweepPublication poll_next_publication();
    // Same analysis owner, independent of reduced-frame slot coalescing.
    // Joined Stop retains this journal; draining never calls SDK or RF controls.
    [[nodiscard]] sdr_core::LayerReadyDrain drain_sweep_layer_ready_events(std::size_t max_items);
    [[nodiscard]] HackrfSweepRuntimeAnalysisMetrics metrics() const;
    [[nodiscard]] HackrfSweepRuntimeAnalysisStopResult stop(
        std::chrono::milliseconds callback_timeout
    ) noexcept;

private:
    struct Impl;
    explicit HackrfSweepRuntimeAnalysisSession(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_hackrf

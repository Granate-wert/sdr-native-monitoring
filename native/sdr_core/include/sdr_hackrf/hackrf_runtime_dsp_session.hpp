#pragma once

#include "sdr_core/types.hpp"
#include "sdr_hackrf/hackrf_acquisition_dsp_session.hpp"
#include "sdr_hackrf/hackrf_rx_session.hpp"

#include <chrono>
#include <cstddef>
#include <memory>
#include <vector>

namespace sdr_hackrf {

struct HackrfRuntimeDspSessionConfig {
    HackrfRxProfile rx;
    HackrfAcquisitionDspSessionConfig processing;
};

struct HackrfRuntimeDspStopResult {
    HackrfRxQuiesceResult source_quiesce;
    HackrfAcquisitionDspStopResult processing;
    HackrfRxFinalizeResult source_finalize;

    [[nodiscard]] bool complete() const noexcept {
        return source_quiesce.complete() && processing.complete() &&
               source_finalize.complete();
    }
};

struct HackrfRuntimeDspMetrics {
    // True until close/exit complete. Stream admission is reported separately
    // by source.accepting_callbacks and must not be inferred from this flag.
    bool lifecycle_open{};
    HackrfRxIngressMetrics source;
    HackrfAcquisitionDspMetrics processing;
};

// R11-K software/mock composition. The injected runtime is owned by the
// existing R11-H session; the shared bounded ingress is consumed by R11-J.
// This class adds ordering only and contains no official SDK operation.
class HackrfRuntimeDspSession final {
public:
    [[nodiscard]] static std::unique_ptr<HackrfRuntimeDspSession> start(
        std::unique_ptr<HackrfRxRuntimePort> runtime,
        HackrfRuntimeDspSessionConfig config
    );

    ~HackrfRuntimeDspSession();
    HackrfRuntimeDspSession(const HackrfRuntimeDspSession&) = delete;
    HackrfRuntimeDspSession& operator=(const HackrfRuntimeDspSession&) = delete;

    [[nodiscard]] std::vector<sdr_core::SpectrumFrame> poll_spectrum_frames(
        std::size_t max_items = 0U
    );
    [[nodiscard]] HackrfRuntimeDspMetrics metrics() const;
    [[nodiscard]] HackrfRuntimeDspStopResult stop(
        std::chrono::milliseconds callback_timeout
    ) noexcept;

private:
    struct Impl;
    explicit HackrfRuntimeDspSession(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_hackrf

#pragma once

#include "sdr_core/types.hpp"
#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"
#include "sdr_hackrf/hackrf_rx_ingress.hpp"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

namespace sdr_hackrf {

enum class HackrfAcquisitionDspState : std::uint8_t {
    Running,
    StopPending,
    Stopped,
    Failed,
};

struct HackrfAcquisitionDspSessionConfig {
    HackrfFixedBandDspConfig dsp;
};

struct HackrfAcquisitionDspStopResult {
    bool callbacks_quiescent{};
    bool worker_joined{};
    bool processing_failed{};
    std::uint64_t worker_abandoned_blocks{};

    [[nodiscard]] bool complete() const noexcept {
        return callbacks_quiescent && worker_joined;
    }
};

struct HackrfAcquisitionDspMetrics {
    HackrfAcquisitionDspState state{HackrfAcquisitionDspState::Stopped};
    bool worker_exited{};
    bool worker_joined{};
    std::uint64_t worker_blocks_processed{};
    std::uint64_t worker_failures{};
    std::uint64_t worker_abandoned_blocks{};
    HackrfRxIngressMetrics ingress;
    HackrfFixedBandDspMetrics dsp;
};

// Runtime-free R11-J owner. It composes the real bounded R11-G ingress with
// the R11-I CPU DSP adapter and owns exactly one joined consumer worker. The
// producer/runtime keeps its own shared ingress reference; this class performs
// no official HackRF lifecycle or RF operation.
class HackrfAcquisitionDspSession final {
public:
    [[nodiscard]] static std::unique_ptr<HackrfAcquisitionDspSession> start(
        std::shared_ptr<HackrfRxIngress> ingress,
        HackrfAcquisitionDspSessionConfig config
    );

    ~HackrfAcquisitionDspSession();
    HackrfAcquisitionDspSession(const HackrfAcquisitionDspSession&) = delete;
    HackrfAcquisitionDspSession& operator=(const HackrfAcquisitionDspSession&) = delete;

    [[nodiscard]] std::vector<sdr_core::SpectrumFrame> poll_spectrum_frames(
        std::size_t max_items = 0U
    );
    [[nodiscard]] HackrfAcquisitionDspMetrics metrics() const;
    [[nodiscard]] HackrfAcquisitionDspStopResult stop(
        std::chrono::milliseconds callback_timeout
    ) noexcept;

private:
    struct Impl;
    explicit HackrfAcquisitionDspSession(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_hackrf

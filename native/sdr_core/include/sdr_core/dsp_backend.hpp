#pragma once

#include "sdr_core/configuration.hpp"
#include "sdr_core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

namespace sdr_core {

// DC removal mode for the CPU DSP stage (master doc §9.2). P05 keeps it a
// backend option outside the canonical DspConfig wire contract; promoting it
// into the wire schema is a later-package decision.
enum class DcRemovalMode : std::uint8_t {
    Off,
    BlockMean,
};

struct CpuDspOptions {
    DcRemovalMode dc_removal{DcRemovalMode::Off};
    SourceDescriptor source{};
    std::uint32_t output_capacity{8U};
};

struct DspBackendMetrics {
    std::uint64_t fft_frames_computed{};
    std::uint64_t fft_frames_dropped{};
    std::uint64_t samples_processed{};
    std::uint64_t output_pending{};
};

// DSP backend contract (P05 §7): configure / push_iq / poll_spectrum /
// reset / metrics. Implementations must be replaceable without changing the
// call sites.
class DspBackend {
public:
    virtual ~DspBackend() = default;

    virtual void configure(const DspConfig& config) = 0;
    virtual void push_iq(const IqBlock& block) = 0;
    // max_items == 0 drains everything currently buffered.
    [[nodiscard]] virtual std::vector<SpectrumFrame> poll_spectrum(
        std::size_t max_items,
        bool flush_partial_batch = true
    ) = 0;
    virtual void reset() = 0;
    [[nodiscard]] virtual DspBackendMetrics metrics() const = 0;
};

[[nodiscard]] std::unique_ptr<DspBackend> make_cpu_dsp_backend(CpuDspOptions options);

}  // namespace sdr_core

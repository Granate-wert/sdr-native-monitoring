#pragma once

#include "sdr_core/backend_info.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/metrics.hpp"
#include "sdr_core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

namespace sdr_core {

// Immutable CPU-side resources for one numerical FFT/window configuration.
// A pair of synchronized receiver chains may retain one instance instead of
// allocating two equivalent window tables.  The FFT provider remains owned by
// each DspBackend: a backend must stay independently resettable and no shared
// mutable transform state may cross receiver boundaries.
class CpuDspSharedPlan final {
public:
    [[nodiscard]] std::uint32_t fft_size() const noexcept;
    [[nodiscard]] WindowType window() const noexcept;
    [[nodiscard]] double kaiser_beta() const noexcept;
    [[nodiscard]] PrecisionMode precision_mode() const noexcept;
    [[nodiscard]] double coherent_gain() const noexcept;
    [[nodiscard]] double enbw_bins() const noexcept;
    [[nodiscard]] double sum_w2() const noexcept;
    [[nodiscard]] const std::vector<double>& coefficients_f64() const noexcept;
    [[nodiscard]] const std::vector<float>& coefficients_f32() const noexcept;

    CpuDspSharedPlan() = default;

private:
    friend std::shared_ptr<const CpuDspSharedPlan> make_cpu_dsp_shared_plan(
        const DspConfig& config
    );

    std::uint32_t fft_size_{};
    WindowType window_{WindowType::Hann};
    double kaiser_beta_{8.6};
    PrecisionMode precision_mode_{PrecisionMode::AccurateF32F64Accum};
    double coherent_gain_{};
    double enbw_bins_{};
    double sum_w2_{};
    std::vector<double> coefficients_f64_;
    std::vector<float> coefficients_f32_;
};

// DC removal mode for the CPU DSP stage (master doc §9.2). P05 keeps it a
// backend option outside the canonical DspConfig wire contract; promoting it
// into the wire schema is a later-package decision.
enum class DcRemovalMode : std::uint8_t {
    Off,
    BlockMean,
};

struct DspOptions {
    DcRemovalMode dc_removal{DcRemovalMode::Off};
    SourceDescriptor source{};
    std::uint32_t output_capacity{8U};
    // Optional immutable resource sharing for same-configuration CPU
    // backends.  It is deliberately CPU-specific and ignored by non-CPU
    // implementations; the factory validates compatibility on configure.
    std::shared_ptr<const CpuDspSharedPlan> cpu_shared_plan;
};

// Source compatibility for the accepted CPU P05 API. The generic type is the owner.
using CpuDspOptions = DspOptions;
enum class FftTransformKind : std::uint8_t { ComplexForward, };
enum class FftDataLayout : std::uint8_t { InterleavedComplex, };
struct FftPlanKey {
    ComputeBackendKind backend_kind{ComputeBackendKind::Cpu};
    int device_id{-1};
    std::uint32_t fft_size{};
    std::uint32_t batch_size{};
    PrecisionMode precision{PrecisionMode::AccurateF32F64Accum};
    FftTransformKind transform{FftTransformKind::ComplexForward};
    FftDataLayout input_layout{FftDataLayout::InterleavedComplex};
    FftDataLayout output_layout{FftDataLayout::InterleavedComplex};
    std::uint32_t input_stride{};
    std::uint32_t output_stride{};
    [[nodiscard]] bool operator==(const FftPlanKey&) const noexcept = default;
};

// P08 backend selection contract (execution instruction §8.1).
struct DspBackendSelectionOptions {
    ComputeBackendKind preference{ComputeBackendKind::Auto};
    bool allow_runtime_fallback{true};
    int device_id{-1};  // -1 means policy-selected
    std::uint32_t plan_cache_capacity{8U};
};

void validate(const DspBackendSelectionOptions& value);

struct DspBackendMetrics {
    std::uint64_t fft_frames_computed{};
    std::uint64_t fft_frames_dropped{};
    std::uint64_t samples_processed{};
    std::uint64_t output_pending{};
    // P08 generic selection/fallback metrics (§8.2). CPU-only backends report
    // preference=Cpu, active=Cpu, zero counts and BackendErrorCode::None.
    ComputeBackendKind requested_preference{ComputeBackendKind::Cpu};
    ComputeBackendKind active_backend{ComputeBackendKind::Cpu};
    bool backend_self_test_passed{};
    std::uint64_t backend_fallback_count{};
    std::uint64_t backend_switch_count{};
    BackendErrorCode last_backend_error{BackendErrorCode::None};
    // Cumulative optional stage timing. A duration is usable only when its
    // bit is present in stage_timing_mask; ordinary CPU builds intentionally
    // leave these timers disabled to avoid perturbing production throughput.
    std::uint32_t stage_timing_mask{};
    std::uint64_t input_unpack_ns{};
    std::uint64_t window_ns{};
    std::uint64_t fft_ns{};
    std::uint64_t detector_ns{};
    // Cumulative optional GPU transfer/work timings.
    std::uint64_t gpu_processing_ns{};
    std::uint64_t h2d_ns{};
    std::uint64_t d2h_ns{};
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
    // P08/P08H-00 vendor-neutral identity of this backend implementation.
    [[nodiscard]] virtual BackendInfo info() const = 0;
};

[[nodiscard]] std::unique_ptr<DspBackend> make_cpu_dsp_backend(CpuDspOptions options);
[[nodiscard]] std::shared_ptr<const CpuDspSharedPlan> make_cpu_dsp_shared_plan(
    const DspConfig& config
);

// P08 vendor-neutral availability/self-test boundary. Answers are
// compiled-vs-runtime aware: a CPU-only build reports compiled=false with a
// stable reason; Hip always reports compiled=false until the P08H branch.
[[nodiscard]] BackendAvailability backend_availability(ComputeBackendKind kind);
[[nodiscard]] BackendAvailability run_backend_self_test(ComputeBackendKind kind);

// P08 selection factory. Resolves preference, self-test and (optionally)
// wraps the chosen backend into the runtime failover contract.
[[nodiscard]] std::unique_ptr<DspBackend> make_dsp_backend(
    const DspBackendSelectionOptions& selection,
    DspOptions options
);

}  // namespace sdr_core

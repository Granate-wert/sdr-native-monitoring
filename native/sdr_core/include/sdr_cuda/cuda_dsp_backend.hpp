#pragma once

#include "sdr_core/dsp_backend.hpp"
#include "sdr_cuda/cufft_plan_cache.hpp"
#include "sdr_cuda/cuda_runtime.hpp"

#include <complex>
#include <deque>
#include <vector>

namespace sdr_cuda {

// Optional CUDA-only performance snapshot (P08 §8.2): cumulative nanoseconds
// plus counts so averages stay reproducible.
struct CudaPerfSnapshot {
    std::uint64_t batches_processed{};
    std::uint64_t h2d_ns{};
    std::uint64_t preprocess_ns{};
    std::uint64_t fft_ns{};
    std::uint64_t detector_ns{};
    std::uint64_t d2h_ns{};
    std::uint64_t pinned_host_bytes{};
    std::uint64_t device_bytes{};
    CufftPlanCacheStats plan_cache{};
};

// CUDA implementation of the P05 DSP backend contract. Host side keeps the
// same overlap/gap/staging semantics as CpuDspBackend; the heavy stages
// (DC/window, FFT, power scaling, detector accumulation) run on the GPU.
// D2H happens only when an averaging group emits a SpectrumFrame.
class CudaDspBackend final : public sdr_core::DspBackend {
public:
    CudaDspBackend(
        sdr_core::DspOptions options,
        int device_id,
        std::uint32_t plan_cache_capacity
    );
    ~CudaDspBackend() noexcept override;

    CudaDspBackend(const CudaDspBackend&) = delete;
    CudaDspBackend& operator=(const CudaDspBackend&) = delete;

    void configure(const sdr_core::DspConfig& config) override;
    void push_iq(const sdr_core::IqBlock& block) override;
    [[nodiscard]] std::vector<sdr_core::SpectrumFrame> poll_spectrum(
        std::size_t max_items,
        bool flush_partial_batch = true
    ) override;
    void reset() override;
    [[nodiscard]] sdr_core::DspBackendMetrics metrics() const override;
    [[nodiscard]] sdr_core::BackendInfo info() const override;

    [[nodiscard]] CudaPerfSnapshot perf_snapshot() const;

private:
    struct FrameMeta {
        std::uint64_t first_sample_index{};
        std::int64_t timestamp_ns{};
        std::uint64_t config_generation{};
        double center_frequency_hz{};
        double sample_rate_hz{};
        sdr_core::QualityFlag input_flags{sdr_core::QualityFlag::None};
        bool clipped{};
    };

    void allocate_resources();
    void release_resources() noexcept;
    void stage_frame(const sdr_core::IqBlock& block);
    void run_fft_batch();
    void emit_frame(const FrameMeta& meta);
    void flush_pipeline(bool account_drops = true);

    template <typename T>
    void feed_sample(std::uint32_t index, const std::uint8_t* bytes, sdr_core::SampleFormat format);

    sdr_core::DspOptions options_;
    int device_id_{-1};
    std::uint32_t plan_cache_capacity_;
    sdr_core::DspConfig config_{};
    bool configured_{false};

    // Host mirrors of the CPU backend state.
    std::vector<double> coeffs_d_;
    std::vector<float> coeffs_f_;
    double coherent_gain_{};
    double sum_w2_{};
    double enbw_bins_{};
    bool use_f64_{true};
    bool use_f32_accum_{false};
    std::vector<std::complex<double>> ring_d_;
    std::vector<std::complex<float>> ring_f_;
    std::size_t ring_pos_{};
    std::uint64_t next_frame_end_{};
    std::vector<FrameMeta> meta_batch_;
    std::uint32_t staged_{};
    std::uint32_t accum_count_{};
    sdr_core::QualityFlag accum_quality_flags_{sdr_core::QualityFlag::None};
    std::deque<sdr_core::SpectrumFrame> output_;
    sdr_core::DspBackendMetrics metrics_{};
    std::uint64_t frame_sequence_{};
    std::int64_t base_sample_index_{-1};
    std::uint64_t stream_pos_{};
    std::uint64_t clipped_until_pos_{};
    double current_fs_{};
    double current_fc_{};
    std::uint64_t current_generation_{};
    bool generation_valid_{};
    std::shared_ptr<const std::vector<double>> axis_shared_;
    bool axis_valid_{false};

    // GPU resources (allocated at configure).
    std::unique_ptr<Stream> stream_;
    std::unique_ptr<Event> ev_start_;
    std::unique_ptr<Event> ev_h2d_;
    std::unique_ptr<Event> ev_preprocess_;
    std::unique_ptr<Event> ev_fft_;
    std::unique_ptr<Event> ev_detector_;
    std::unique_ptr<Event> ev_d2h_;
    std::unique_ptr<DeviceBuffer> dev_coeffs_;
    std::unique_ptr<DeviceBuffer> dev_stage_;
    std::unique_ptr<DeviceBuffer> dev_spectrum_;
    std::unique_ptr<DeviceBuffer> dev_powers_;
    std::unique_ptr<DeviceBuffer> dev_accum_;
    std::unique_ptr<PinnedBuffer> pinned_stage_;
    std::unique_ptr<CufftPlanCache> plan_cache_;
    std::vector<double> host_accum_d_;
    std::vector<float> host_accum_f_;

    CudaPerfSnapshot perf_{};
    // Deterministic failure injection for selector/fallback tests (like the
    // P06 mock hooks): SDR_CUDA_FAIL_ON_BATCH=N throws a typed FftExecution
    // failure at the Nth batch. 0 = disabled.
    std::uint64_t fail_on_batch_{};
};

}  // namespace sdr_cuda

#include "sdr_cuda/cuda_dsp_backend.hpp"

#include "sdr_core/errors.hpp"
#include "sdr_core/window.hpp"
#include "sdr_cuda/cuda_kernels.hpp"

#include <cmath>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <utility>

namespace sdr_cuda {

namespace {

constexpr double full_scale_i16 = 32768.0;
constexpr double full_scale_i12 = 2048.0;

[[nodiscard]] double integer_full_scale(const sdr_core::SampleFormat format) {
    return format == sdr_core::SampleFormat::ComplexInt12InInt16Le ? full_scale_i12 : full_scale_i16;
}

[[nodiscard]] std::uint32_t bytes_per_sample(const sdr_core::SampleFormat format) {
    switch (format) {
    case sdr_core::SampleFormat::ComplexInt12InInt16Le:
    case sdr_core::SampleFormat::ComplexInt16Le:
        return 4U;
    case sdr_core::SampleFormat::ComplexFloat32Le:
        return 8U;
    default:
        throw sdr_core::ConfigurationError(
            "P08 CUDA DSP supports ComplexInt12InInt16Le, ComplexInt16Le and ComplexFloat32Le"
        );
    }
}

[[nodiscard]] float read_le_float(const std::uint8_t* bytes) {
    float value = 0.0F;
    std::memcpy(&value, bytes, sizeof(value));
    return value;
}

[[nodiscard]] std::int16_t read_le_i16(const std::uint8_t* bytes) {
    const auto raw = static_cast<std::uint16_t>(
        static_cast<std::uint16_t>(bytes[0]) |
        (static_cast<std::uint16_t>(bytes[1]) << 8U)
    );
    std::int16_t value = 0;
    std::memcpy(&value, &raw, sizeof(value));
    return value;
}

[[nodiscard]] sdr_core::SourceDescriptor effective_source(const sdr_core::SourceDescriptor& source) {
    if (!source.source_id.empty() && !source.display_name.empty()) {
        return source;
    }
    sdr_core::SourceDescriptor fallback = source;
    fallback.source_type = sdr_core::SourceType::Synthetic;
    fallback.source_id = "cuda-dsp-backend";
    fallback.display_name = "CUDA DSP backend";
    return fallback;
}

}  // namespace

CudaDspBackend::CudaDspBackend(
    sdr_core::DspOptions options,
    const int device_id,
    const std::uint32_t plan_cache_capacity
)
    : options_(std::move(options)),
      device_id_(device_id),
      plan_cache_capacity_(plan_cache_capacity) {
    if (options_.output_capacity == 0U) {
        throw sdr_core::ConfigurationError("DSP output capacity must be positive");
    }
    options_.source = effective_source(options_.source);
    if (plan_cache_capacity == 0U) {
        throw sdr_core::ConfigurationError("CUDA plan cache capacity must be positive");
    }
    // Plan cache lives across configures: keys carry fft/batch/precision/
    // device, and bounded eviction must work over the backend lifetime.
    plan_cache_ = std::make_unique<CufftPlanCache>(plan_cache_capacity_);
}

CudaDspBackend::~CudaDspBackend() noexcept {
    release_resources();
}

void CudaDspBackend::configure(const sdr_core::DspConfig& config) {
    sdr_core::validate(config);
    if (config.unit == sdr_core::SpectrumUnit::Dbm || config.unit == sdr_core::SpectrumUnit::DbmBin ||
        config.unit == sdr_core::SpectrumUnit::DbmHz) {
        throw sdr_core::ConfigurationError("calibrated dBm units are introduced by P09");
    }
    release_resources();
    config_ = config;
    const auto n = config_.fft_size;
    const auto metrics = sdr_core::window_metrics(config_.window, n, 1.0, config_.kaiser_beta);
    coeffs_d_ = metrics.coefficients;
    coeffs_f_.assign(coeffs_d_.begin(), coeffs_d_.end());
    coherent_gain_ = metrics.coherent_gain;
    enbw_bins_ = metrics.enbw_bins;
    sum_w2_ = 0.0;
    for (const double coefficient : coeffs_d_) {
        sum_w2_ += coefficient * coefficient;
    }
    use_f64_ = config_.precision_mode == sdr_core::PrecisionMode::ReferenceF64;
    use_f32_accum_ = config_.precision_mode == sdr_core::PrecisionMode::FastF32;

    ring_d_.assign(n, std::complex<double>{});
    ring_f_.assign(n, std::complex<float>{});
    meta_batch_.assign(config_.batch_size, FrameMeta{});
    host_accum_d_.assign(4U * n, 0.0);
    host_accum_f_.assign(4U * n, 0.0F);

    metrics_ = sdr_core::DspBackendMetrics{};
    perf_ = CudaPerfSnapshot{};
    frame_sequence_ = 0U;
    if (const char* hook = std::getenv("SDR_CUDA_FAIL_ON_BATCH")) {
        fail_on_batch_ = std::strtoull(hook, nullptr, 10);
    } else {
        fail_on_batch_ = 0U;
    }
    allocate_resources();
    configured_ = true;
    reset();
}

void CudaDspBackend::allocate_resources() {
    DeviceGuard guard(device_id_);
    const auto n = config_.fft_size;
    const auto batch = config_.batch_size;
    const auto complex_bytes = use_f64_ ? sizeof(cufftDoubleComplex) : sizeof(cufftComplex);
    const auto power_bytes = use_f32_accum_ ? sizeof(float) : sizeof(double);

    stream_ = std::make_unique<Stream>();
    ev_start_ = std::make_unique<Event>();
    ev_h2d_ = std::make_unique<Event>();
    ev_fft_ = std::make_unique<Event>();
    ev_preprocess_ = std::make_unique<Event>();
    ev_detector_ = std::make_unique<Event>();
    ev_d2h_ = std::make_unique<Event>();
    dev_coeffs_ = std::make_unique<DeviceBuffer>(n * (use_f64_ ? sizeof(double) : sizeof(float)));
    dev_stage_ = std::make_unique<DeviceBuffer>(
        static_cast<std::size_t>(batch) * n * complex_bytes
    );
    dev_spectrum_ = std::make_unique<DeviceBuffer>(
        static_cast<std::size_t>(batch) * n * complex_bytes
    );
    dev_powers_ = std::make_unique<DeviceBuffer>(
        static_cast<std::size_t>(batch) * n * power_bytes
    );
    dev_accum_ = std::make_unique<DeviceBuffer>(4U * n * power_bytes);
    pinned_stage_ = std::make_unique<PinnedBuffer>(
        static_cast<std::size_t>(batch) * n * complex_bytes
    );

    check_cuda(
        sdr_core::BackendErrorCode::CopyFailed,
        "cudaMemcpy coefficients",
        cudaMemcpy(
            dev_coeffs_->get(),
            use_f64_ ? static_cast<const void*>(coeffs_d_.data())
                     : static_cast<const void*>(coeffs_f_.data()),
            n * (use_f64_ ? sizeof(double) : sizeof(float)),
            cudaMemcpyHostToDevice
        )
    );

    perf_.pinned_host_bytes = pinned_stage_->bytes();
    perf_.device_bytes = dev_coeffs_->bytes() + dev_stage_->bytes() +
                         dev_spectrum_->bytes() + dev_powers_->bytes() + dev_accum_->bytes();
}

void CudaDspBackend::release_resources() noexcept {
    try {
        if (stream_) {
            static_cast<void>(cudaDeviceSynchronize());
        }
    } catch (...) {
    }
    // plan_cache_ intentionally survives: cached plans remain valid for
    // their keys and bounded eviction must span the backend lifetime.
    pinned_stage_.reset();
    dev_accum_.reset();
    dev_powers_.reset();
    dev_spectrum_.reset();
    dev_stage_.reset();
    dev_coeffs_.reset();
    ev_d2h_.reset();
    ev_detector_.reset();
    ev_preprocess_.reset();
    ev_fft_.reset();
    ev_h2d_.reset();
    ev_start_.reset();
    stream_.reset();
}

void CudaDspBackend::push_iq(const sdr_core::IqBlock& block) {
    if (!configured_) {
        throw sdr_core::ConfigurationError("DSP backend is not configured");
    }
    if (!block.samples || block.sample_count == 0U) {
        throw sdr_core::ConfigurationError("IqBlock requires samples and positive sample_count");
    }
    const auto width = bytes_per_sample(block.sample_format);
    const std::size_t expected = static_cast<std::size_t>(block.sample_count) * width;
    if (block.samples->size() != expected) {
        throw sdr_core::ConfigurationError("IqBlock byte length disagrees with sample format/count");
    }
    if (!std::isfinite(block.sample_rate_hz) || block.sample_rate_hz <= 0.0 ||
        !std::isfinite(block.center_frequency_hz) || block.center_frequency_hz <= 0.0) {
        throw sdr_core::ConfigurationError("IqBlock rate/center must be finite and positive");
    }

    // Non-finite input never enters the pipeline: the whole block is dropped
    // (one dropped frame opportunity; see the CPU contract).
    if (block.sample_format == sdr_core::SampleFormat::ComplexFloat32Le) {
        const auto* bytes = block.samples->data();
        for (std::uint32_t index = 0U; index < block.sample_count; ++index) {
            const float re = read_le_float(bytes + index * 8U);
            const float im = read_le_float(bytes + index * 8U + 4U);
            if (!std::isfinite(re) || !std::isfinite(im)) {
                ++metrics_.fft_frames_dropped;
                return;
            }
        }
    }

    if (base_sample_index_ < 0) {
        base_sample_index_ = static_cast<std::int64_t>(block.first_sample_index);
    } else if (
        static_cast<std::uint64_t>(base_sample_index_) + stream_pos_ != block.first_sample_index
    ) {
        flush_pipeline();
        base_sample_index_ = static_cast<std::int64_t>(block.first_sample_index);
        stream_pos_ = 0U;
    }
    if (block.sample_rate_hz != current_fs_) {
        flush_pipeline();
        base_sample_index_ += static_cast<std::int64_t>(stream_pos_);
        stream_pos_ = 0U;
        current_fs_ = block.sample_rate_hz;
        axis_valid_ = false;
    }
    if (generation_valid_ && block.config_generation != current_generation_) {
        flush_pipeline();
        base_sample_index_ += static_cast<std::int64_t>(stream_pos_);
        stream_pos_ = 0U;
    }
    current_generation_ = block.config_generation;
    generation_valid_ = true;
    if (current_fc_ != 0.0 && block.center_frequency_hz != current_fc_) {
        flush_pipeline();
        base_sample_index_ += static_cast<std::int64_t>(stream_pos_);
        stream_pos_ = 0U;
    }
    if (block.center_frequency_hz != current_fc_) {
        current_fc_ = block.center_frequency_hz;
        axis_valid_ = false;
    }

    const bool integer_format =
        block.sample_format == sdr_core::SampleFormat::ComplexInt12InInt16Le ||
        block.sample_format == sdr_core::SampleFormat::ComplexInt16Le;
    const bool clipped = integer_format && [this, &block] {
        for (std::uint32_t index = 0U; index < block.sample_count; ++index) {
            const auto re = read_le_i16(block.samples->data() + index * 4U);
            const auto im = read_le_i16(block.samples->data() + index * 4U + 2U);
            const bool hit = block.sample_format == sdr_core::SampleFormat::ComplexInt12InInt16Le
                                 ? (re == 2047 || re == -2048 || im == 2047 || im == -2048)
                                 : (re == 32767 || re == -32768 || im == 32767 || im == -32768);
            if (hit) return true;
        }
        return false;
    }();
    const auto* bytes = block.samples->data();
    for (std::uint32_t index = 0U; index < block.sample_count; ++index) {
        if (use_f64_) {
            const auto* cell = bytes + static_cast<std::size_t>(index) * width;
            std::complex<double> value;
            if (block.sample_format == sdr_core::SampleFormat::ComplexFloat32Le) {
                value = {static_cast<double>(read_le_float(cell)),
                         static_cast<double>(read_le_float(cell + 4U))};
            } else {
                value = {static_cast<double>(read_le_i16(cell)) / integer_full_scale(block.sample_format),
                         static_cast<double>(read_le_i16(cell + 2U)) / integer_full_scale(block.sample_format)};
            }
            ring_d_[ring_pos_] = value;
        } else {
            const auto* cell = bytes + static_cast<std::size_t>(index) * width;
            std::complex<float> value;
            if (block.sample_format == sdr_core::SampleFormat::ComplexFloat32Le) {
                value = {read_le_float(cell), read_le_float(cell + 4U)};
            } else {
                value = {static_cast<float>(static_cast<double>(read_le_i16(cell)) / integer_full_scale(block.sample_format)),
                         static_cast<float>(static_cast<double>(read_le_i16(cell + 2U)) / integer_full_scale(block.sample_format))};
            }
            ring_f_[ring_pos_] = value;
        }
        ring_pos_ = (ring_pos_ + 1U) % config_.fft_size;
        ++stream_pos_;
        if (clipped) {
            clipped_until_pos_ = stream_pos_;
        }
        if (stream_pos_ == next_frame_end_) {
            stage_frame(block);
            next_frame_end_ += config_.hop_size;
            if (staged_ == config_.batch_size) {
                run_fft_batch();
            }
        }
    }
    metrics_.samples_processed += block.sample_count;
}

void CudaDspBackend::stage_frame(const sdr_core::IqBlock& block) {
    const auto n = config_.fft_size;
    const auto frame_first = static_cast<std::uint64_t>(base_sample_index_) + (stream_pos_ - n);
    auto& meta = meta_batch_[staged_];
    meta.first_sample_index = frame_first;
    const long double offset_samples =
        static_cast<long double>(frame_first) - static_cast<long double>(block.first_sample_index);
    meta.timestamp_ns = block.timestamp_ns + static_cast<std::int64_t>(std::llround(
        offset_samples * 1.0e9L / static_cast<long double>(block.sample_rate_hz)
    ));
    meta.config_generation = block.config_generation;
    meta.center_frequency_hz = block.center_frequency_hz;
    meta.sample_rate_hz = block.sample_rate_hz;
    meta.input_flags = block.flags;
    meta.clipped = (stream_pos_ - n) < clipped_until_pos_;

    auto* pinned = static_cast<std::uint8_t*>(pinned_stage_->get());
    if (use_f64_) {
        auto* out = reinterpret_cast<cufftDoubleComplex*>(
            pinned + static_cast<std::size_t>(staged_) * n * sizeof(cufftDoubleComplex)
        );
        for (std::uint32_t k = 0U; k < n; ++k) {
            const auto value = ring_d_[(ring_pos_ + k) % n];
            out[k].x = value.real();
            out[k].y = value.imag();
        }
    } else {
        auto* out = reinterpret_cast<cufftComplex*>(
            pinned + static_cast<std::size_t>(staged_) * n * sizeof(cufftComplex)
        );
        for (std::uint32_t k = 0U; k < n; ++k) {
            const auto value = ring_f_[(ring_pos_ + k) % n];
            out[k].x = value.real();
            out[k].y = value.imag();
        }
    }
    ++staged_;
}

void CudaDspBackend::run_fft_batch() {
    if (staged_ == 0U) {
        return;
    }
    // Deterministic failure injection for fallback tests.
    if (fail_on_batch_ != 0U && perf_.batches_processed + 1U == fail_on_batch_) {
        throw sdr_core::DeviceError(
            "injected CUDA batch failure (SDR_CUDA_FAIL_ON_BATCH)",
            sdr_core::BackendErrorCode::FftExecutionFailed
        );
    }
    DeviceGuard guard(device_id_);
    const auto n = config_.fft_size;
    const auto batch = static_cast<int>(staged_);
    const auto complex_bytes = use_f64_ ? sizeof(cufftDoubleComplex) : sizeof(cufftComplex);
    const bool psd = config_.unit == sdr_core::SpectrumUnit::DbfsHz;
    const double denominator =
        psd ? current_fs_ * sum_w2_
            : (static_cast<double>(n) * coherent_gain_) * (static_cast<double>(n) * coherent_gain_);

    ev_start_->record(*stream_);
    check_cuda(
        sdr_core::BackendErrorCode::CopyFailed,
        "cudaMemcpyAsync H2D",
        cudaMemcpyAsync(
            dev_stage_->get(),
            pinned_stage_->get(),
            static_cast<std::size_t>(staged_) * n * complex_bytes,
            cudaMemcpyHostToDevice,
            stream_->get()
        )
    );
    ev_h2d_->record(*stream_);
    if (use_f64_) {
        launch_dc_window_f64(
            static_cast<const cufftDoubleComplex*>(dev_stage_->get()),
            static_cast<cufftDoubleComplex*>(dev_stage_->get()),
            static_cast<const double*>(dev_coeffs_->get()),
            batch,
            static_cast<int>(n),
            options_.dc_removal == sdr_core::DcRemovalMode::BlockMean,
            stream_->get()
        );
    } else {
        launch_dc_window_f32(
            static_cast<const cufftComplex*>(dev_stage_->get()),
            static_cast<cufftComplex*>(dev_stage_->get()),
            static_cast<const float*>(dev_coeffs_->get()),
            batch,
            static_cast<int>(n),
            options_.dc_removal == sdr_core::DcRemovalMode::BlockMean,
            stream_->get()
        );
    }
    check_cuda(
        sdr_core::BackendErrorCode::KernelLaunchFailed,
        "dc_window kernel",
        cudaGetLastError()
    );
    ev_preprocess_->record(*stream_);

    const auto& api = CufftApi::instance();
    const CufftPlanKey key{
        sdr_core::ComputeBackendKind::Cuda,
        device_id_ >= 0 ? device_id_ : 0,
        n,
        static_cast<std::uint32_t>(batch),
        config_.precision_mode,
        sdr_core::FftTransformKind::ComplexForward,
        sdr_core::FftDataLayout::InterleavedComplex,
        sdr_core::FftDataLayout::InterleavedComplex,
        n,
        n,
    };
    cufftHandle plan = plan_cache_->acquire(key, stream_->get());
    cufftResult fft_status;
    if (use_f64_) {
        fft_status = api.exec_z2z(
            plan,
            static_cast<cufftDoubleComplex*>(dev_stage_->get()),
            static_cast<cufftDoubleComplex*>(dev_spectrum_->get()),
            CUFFT_FORWARD
        );
    } else {
        fft_status = api.exec_c2c(
            plan,
            static_cast<cufftComplex*>(dev_stage_->get()),
            static_cast<cufftComplex*>(dev_spectrum_->get()),
            CUFFT_FORWARD
        );
    }
    if (fft_status != CUFFT_SUCCESS) {
        api.throw_failure(sdr_core::BackendErrorCode::FftExecutionFailed, "cufftExec", fft_status);
    }
    ev_fft_->record(*stream_);

    if (use_f32_accum_) {
        launch_power_stage_f32(
            static_cast<const cufftComplex*>(dev_spectrum_->get()),
            static_cast<float*>(dev_powers_->get()),
            denominator,
            batch,
            static_cast<int>(n),
            stream_->get()
        );
    } else if (use_f64_) {
        launch_power_stage_f64(
            static_cast<const cufftDoubleComplex*>(dev_spectrum_->get()),
            static_cast<double*>(dev_powers_->get()),
            denominator,
            batch,
            static_cast<int>(n),
            stream_->get()
        );
    } else {
        // ACCURATE_F32_F64_ACCUM: float spectrum, double accumulation.
        launch_power_stage_f32_to_f64(
            static_cast<const cufftComplex*>(dev_spectrum_->get()),
            static_cast<double*>(dev_powers_->get()),
            denominator,
            batch,
            static_cast<int>(n),
            stream_->get()
        );
    }
    check_cuda(
        sdr_core::BackendErrorCode::KernelLaunchFailed,
        "power_stage kernel",
        cudaGetLastError()
    );

    // Detector accumulation per averaging group; D2H only at emission.
    const auto power_element = use_f32_accum_ ? sizeof(float) : sizeof(double);
    std::uint32_t processed = 0U;
    bool d2h_performed = false;
    while (processed < staged_) {
        const std::uint32_t group_remaining = config_.averaging_frames - accum_count_;
        const std::uint32_t chunk = std::min(group_remaining, staged_ - processed);
        const auto* chunk_powers =
            static_cast<const std::uint8_t*>(dev_powers_->get()) +
            static_cast<std::size_t>(processed) * n * power_element;
        if (use_f32_accum_) {
            auto* base = static_cast<float*>(dev_accum_->get());
            launch_accum_f32(
                reinterpret_cast<const float*>(chunk_powers),
                static_cast<int>(chunk),
                static_cast<int>(n),
                base,
                base + n,
                base + 2U * n,
                base + 3U * n,
                stream_->get()
            );
        } else {
            auto* base = static_cast<double*>(dev_accum_->get());
            launch_accum_f64(
                reinterpret_cast<const double*>(chunk_powers),
                static_cast<int>(chunk),
                static_cast<int>(n),
                base,
                base + n,
                base + 2U * n,
                base + 3U * n,
                stream_->get()
            );
        }
        check_cuda(
            sdr_core::BackendErrorCode::KernelLaunchFailed,
            "accum kernel",
            cudaGetLastError()
        );
        accum_count_ += chunk;
        processed += chunk;
        metrics_.fft_frames_computed += chunk;
        for (std::uint32_t frame = processed - chunk; frame < processed; ++frame) {
            accum_quality_flags_ = accum_quality_flags_ | meta_batch_[frame].input_flags;
            if (meta_batch_[frame].clipped) {
                accum_quality_flags_ = accum_quality_flags_ | sdr_core::QualityFlag::AdcOverload;
            }
        }
        ev_detector_->record(*stream_);
        if (accum_count_ == config_.averaging_frames) {
            check_cuda(
                sdr_core::BackendErrorCode::CopyFailed,
                "cudaMemcpyAsync D2H accum",
                cudaMemcpyAsync(
                    use_f32_accum_ ? static_cast<void*>(host_accum_f_.data())
                                   : static_cast<void*>(host_accum_d_.data()),
                    dev_accum_->get(),
                    4U * n * power_element,
                    cudaMemcpyDeviceToHost,
                    stream_->get()
                )
            );
            ev_d2h_->record(*stream_);
            ev_d2h_->synchronize();
            d2h_performed = true;
            perf_.d2h_ns += static_cast<std::uint64_t>(ev_d2h_->elapsed_ms_since(*ev_detector_) * 1e6F);
            emit_frame(meta_batch_[processed - 1U]);
            accum_count_ = 0U;
    accum_quality_flags_ = sdr_core::QualityFlag::None;
            // Reset accumulators: sum/max/last = 0, min = +inf.
            if (use_f32_accum_) {
                std::fill(host_accum_f_.begin(), host_accum_f_.end(), 0.0F);
                std::fill(
                    host_accum_f_.begin() + static_cast<std::ptrdiff_t>(2U * n),
                    host_accum_f_.begin() + static_cast<std::ptrdiff_t>(3U * n),
                    std::numeric_limits<float>::infinity()
                );
                check_cuda(
                    sdr_core::BackendErrorCode::CopyFailed,
                    "cudaMemcpyAsync accum reset",
                    cudaMemcpyAsync(
                        dev_accum_->get(),
                        host_accum_f_.data(),
                        4U * n * sizeof(float),
                        cudaMemcpyHostToDevice,
                        stream_->get()
                    )
                );
            } else {
                std::fill(host_accum_d_.begin(), host_accum_d_.end(), 0.0);
                std::fill(
                    host_accum_d_.begin() + static_cast<std::ptrdiff_t>(2U * n),
                    host_accum_d_.begin() + static_cast<std::ptrdiff_t>(3U * n),
                    std::numeric_limits<double>::infinity()
                );
                check_cuda(
                    sdr_core::BackendErrorCode::CopyFailed,
                    "cudaMemcpyAsync accum reset",
                    cudaMemcpyAsync(
                        dev_accum_->get(),
                        host_accum_d_.data(),
                        4U * n * sizeof(double),
                        cudaMemcpyHostToDevice,
                        stream_->get()
                    )
                );
            }
        }
    }
    // Batch completion boundary: make every recorded event ready before the
    // stage timings are read (one synchronization per batch).
    if (!d2h_performed) {
        check_cuda(
            sdr_core::BackendErrorCode::Unknown,
            "cudaStreamSynchronize timing",
            cudaStreamSynchronize(stream_->get())
        );
    } else {
        ev_d2h_->synchronize();
    }
    ev_detector_->synchronize();
    ++perf_.batches_processed;
    perf_.h2d_ns += static_cast<std::uint64_t>(ev_h2d_->elapsed_ms_since(*ev_start_) * 1e6F);
    perf_.preprocess_ns += static_cast<std::uint64_t>(ev_preprocess_->elapsed_ms_since(*ev_h2d_) * 1e6F);
    perf_.fft_ns += static_cast<std::uint64_t>(ev_fft_->elapsed_ms_since(*ev_preprocess_) * 1e6F);
    perf_.detector_ns += static_cast<std::uint64_t>(ev_detector_->elapsed_ms_since(*ev_fft_) * 1e6F);
    staged_ = 0U;
}

void CudaDspBackend::emit_frame(const FrameMeta& meta) {
    const auto n = config_.fft_size;
    if (!axis_valid_) {
        axis_shared_ = std::make_shared<const std::vector<double>>(
            sdr_core::complex_frequency_axis(n, current_fs_, current_fc_)
        );
        axis_valid_ = true;
    }
    auto values = std::make_shared<std::vector<float>>(n);
    const double count = static_cast<double>(config_.averaging_frames);
    const auto* sum_d = host_accum_d_.data();
    const auto* max_d = sum_d + n;
    const auto* min_d = sum_d + 2U * n;
    const auto* last_d = sum_d + 3U * n;
    const auto* sum_f = host_accum_f_.data();
    const auto* max_f = sum_f + n;
    const auto* min_f = sum_f + 2U * n;
    const auto* last_f = sum_f + 3U * n;
    for (std::uint32_t k = 0U; k < n; ++k) {
        double linear = 0.0;
        if (use_f32_accum_) {
            switch (config_.detector) {
            case sdr_core::DetectorType::Sample:
                linear = static_cast<double>(last_f[k]);
                break;
            case sdr_core::DetectorType::Peak:
                linear = static_cast<double>(max_f[k]);
                break;
            case sdr_core::DetectorType::NegativePeak:
                linear = static_cast<double>(min_f[k]);
                break;
            case sdr_core::DetectorType::Rms:
            case sdr_core::DetectorType::AveragePower:
                linear = static_cast<double>(sum_f[k]) / count;
                break;
            }
        } else {
            switch (config_.detector) {
            case sdr_core::DetectorType::Sample:
                linear = last_d[k];
                break;
            case sdr_core::DetectorType::Peak:
                linear = max_d[k];
                break;
            case sdr_core::DetectorType::NegativePeak:
                linear = min_d[k];
                break;
            case sdr_core::DetectorType::Rms:
            case sdr_core::DetectorType::AveragePower:
                linear = sum_d[k] / count;
                break;
            }
        }
        (*values)[k] = linear > 0.0
                           ? static_cast<float>(10.0 * std::log10(linear))
                           : -std::numeric_limits<float>::infinity();
    }

    sdr_core::QualityFlag flags =
        sdr_core::QualityFlag::Uncalibrated | sdr_core::QualityFlag::TimestampEstimated;
    if (options_.dc_removal == sdr_core::DcRemovalMode::BlockMean) {
        flags = flags | sdr_core::QualityFlag::DcRemoved;
    }
    flags = flags | accum_quality_flags_;


    sdr_core::SpectrumFrame frame;
    frame.source = options_.source;
    frame.frame_sequence = frame_sequence_++;
    frame.first_sample_index = meta.first_sample_index;
    frame.timestamp_ns = meta.timestamp_ns;
    frame.config_generation = meta.config_generation;
    frame.center_frequency_hz = meta.center_frequency_hz;
    frame.sample_rate_hz = meta.sample_rate_hz;
    frame.analog_bandwidth_hz = current_fs_;
    frame.fft_bin_width_hz = current_fs_ / static_cast<double>(n);
    frame.enbw_hz = enbw_bins_ * current_fs_ / static_cast<double>(n);
    frame.nominal_rbw_hz = frame.enbw_hz;
    frame.fft_size = n;
    frame.hop_size = config_.hop_size;
    frame.window = config_.window;
    frame.detector = config_.detector;
    frame.precision_mode = config_.precision_mode;
    frame.unit = config_.unit;
    frame.frequencies_hz = axis_shared_;
    frame.values = std::move(values);
    frame.calibration_status = config_.calibration_status;
    frame.calibration_profile_id = config_.calibration_profile_id;
    frame.estimated_uncertainty_db = std::numeric_limits<double>::quiet_NaN();
    frame.dropped_samples_before = 0U;
    frame.dropped_iq_blocks_before = 0U;
    frame.dropped_fft_frames_before = metrics_.fft_frames_dropped;
    frame.quality_flags = flags;

    if (output_.size() >= options_.output_capacity) {
        output_.pop_front();
        ++metrics_.fft_frames_dropped;
    }
    output_.push_back(std::move(frame));
    metrics_.output_pending = output_.size();
}

std::vector<sdr_core::SpectrumFrame> CudaDspBackend::poll_spectrum(
    const std::size_t max_items,
    const bool flush_partial_batch
) {
    if (flush_partial_batch && staged_ != 0U) {
        run_fft_batch();
    }
    std::vector<sdr_core::SpectrumFrame> result;
    while (!output_.empty() && (max_items == 0U || result.size() < max_items)) {
        result.push_back(std::move(output_.front()));
        output_.pop_front();
    }
    metrics_.output_pending = output_.size();
    return result;
}

void CudaDspBackend::reset() {
    if (stream_) {
        check_cuda(
            sdr_core::BackendErrorCode::Unknown,
            "cudaDeviceSynchronize reset",
            cudaDeviceSynchronize()
        );
    }
    flush_pipeline(false);
    ring_pos_ = 0U;
    next_frame_end_ = config_.fft_size;
    staged_ = 0U;
    accum_count_ = 0U;
    accum_quality_flags_ = sdr_core::QualityFlag::None;
    stream_pos_ = 0U;
    base_sample_index_ = -1;
    clipped_until_pos_ = 0U;
    current_fs_ = 0.0;
    current_fc_ = 0.0;
    current_generation_ = 0U;
    generation_valid_ = false;
    axis_valid_ = false;
    output_.clear();
    std::fill(ring_d_.begin(), ring_d_.end(), std::complex<double>{});
    std::fill(ring_f_.begin(), ring_f_.end(), std::complex<float>{});
    if (dev_accum_ && configured_) {
        DeviceGuard guard(device_id_);
        const auto n = config_.fft_size;
        if (use_f32_accum_) {
            std::fill(host_accum_f_.begin(), host_accum_f_.end(), 0.0F);
            std::fill(
                host_accum_f_.begin() + static_cast<std::ptrdiff_t>(2U * n),
                host_accum_f_.begin() + static_cast<std::ptrdiff_t>(3U * n),
                std::numeric_limits<float>::infinity()
            );
            check_cuda(
                sdr_core::BackendErrorCode::CopyFailed,
                "cudaMemcpy accum init",
                cudaMemcpy(
                    dev_accum_->get(),
                    host_accum_f_.data(),
                    4U * n * sizeof(float),
                    cudaMemcpyHostToDevice
                )
            );
        } else {
            std::fill(host_accum_d_.begin(), host_accum_d_.end(), 0.0);
            std::fill(
                host_accum_d_.begin() + static_cast<std::ptrdiff_t>(2U * n),
                host_accum_d_.begin() + static_cast<std::ptrdiff_t>(3U * n),
                std::numeric_limits<double>::infinity()
            );
            check_cuda(
                sdr_core::BackendErrorCode::CopyFailed,
                "cudaMemcpy accum init",
                cudaMemcpy(
                    dev_accum_->get(),
                    host_accum_d_.data(),
                    4U * n * sizeof(double),
                    cudaMemcpyHostToDevice
                )
            );
        }
    }
    metrics_.output_pending = 0U;
}

void CudaDspBackend::flush_pipeline(const bool account_drops) {
    if (account_drops) {
        metrics_.fft_frames_dropped += static_cast<std::uint64_t>(staged_) + accum_count_;
    }
    ring_pos_ = 0U;
    next_frame_end_ = config_.fft_size;
    staged_ = 0U;
    accum_count_ = 0U;
    accum_quality_flags_ = sdr_core::QualityFlag::None;
    clipped_until_pos_ = 0U;
    if (dev_accum_ && stream_) {
        static_cast<void>(cudaDeviceSynchronize());
        const auto n = config_.fft_size;
        if (use_f32_accum_) {
            std::fill(host_accum_f_.begin(), host_accum_f_.end(), 0.0F);
            std::fill(
                host_accum_f_.begin() + static_cast<std::ptrdiff_t>(2U * n),
                host_accum_f_.begin() + static_cast<std::ptrdiff_t>(3U * n),
                std::numeric_limits<float>::infinity()
            );
            static_cast<void>(cudaMemcpy(
                dev_accum_->get(),
                host_accum_f_.data(),
                4U * n * sizeof(float),
                cudaMemcpyHostToDevice
            ));
        } else {
            std::fill(host_accum_d_.begin(), host_accum_d_.end(), 0.0);
            std::fill(
                host_accum_d_.begin() + static_cast<std::ptrdiff_t>(2U * n),
                host_accum_d_.begin() + static_cast<std::ptrdiff_t>(3U * n),
                std::numeric_limits<double>::infinity()
            );
            static_cast<void>(cudaMemcpy(
                dev_accum_->get(),
                host_accum_d_.data(),
                4U * n * sizeof(double),
                cudaMemcpyHostToDevice
            ));
        }
    }
}

sdr_core::DspBackendMetrics CudaDspBackend::metrics() const {
    sdr_core::DspBackendMetrics result = metrics_;
    result.output_pending = output_.size();
    result.requested_preference = sdr_core::ComputeBackendKind::Cuda;
    result.active_backend = sdr_core::ComputeBackendKind::Cuda;
    result.backend_self_test_passed = true;
    result.gpu_processing_ns = perf_.preprocess_ns + perf_.fft_ns + perf_.detector_ns;
    result.h2d_ns = perf_.h2d_ns;
    result.d2h_ns = perf_.d2h_ns;
    return result;
}

sdr_core::BackendInfo CudaDspBackend::info() const {
    return device_info(device_id_, true);
}

CudaPerfSnapshot CudaDspBackend::perf_snapshot() const {
    CudaPerfSnapshot result = perf_;
    if (plan_cache_) {
        result.plan_cache = plan_cache_->stats();
    }
    return result;
}

}  // namespace sdr_cuda

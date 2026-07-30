#include "sdr_core/dsp_backend.hpp"

#include "sdr_core/errors.hpp"
#include "sdr_core/fft_provider.hpp"
#include "sdr_core/window.hpp"

#include <cmath>
#include <complex>
#include <cstring>
#include <deque>
#include <limits>
#include <utility>

namespace sdr_core {

namespace {

constexpr double full_scale_i16 = 32768.0;
constexpr double full_scale_i12 = 2048.0;

[[nodiscard]] std::uint32_t bytes_per_sample(const SampleFormat format) {
    switch (format) {
    case SampleFormat::ComplexInt12InInt16Le:
    case SampleFormat::ComplexInt16Le:
        return 4U;
    case SampleFormat::ComplexFloat32Le:
        return 8U;
    default:
        throw ConfigurationError(
            "CPU DSP supports ComplexInt12InInt16Le, ComplexInt16Le and ComplexFloat32Le"
        );
    }
}

[[nodiscard]] float read_le_float(const std::uint8_t* bytes) {
    float value = 0.0F;
    static_assert(sizeof(value) == 4U);
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

[[nodiscard]] SourceDescriptor effective_source(const SourceDescriptor& source) {
    if (!source.source_id.empty() && !source.display_name.empty()) {
        return source;
    }
    SourceDescriptor fallback = source;
    fallback.source_type = SourceType::Synthetic;
    fallback.source_id = "cpu-dsp-backend";
    fallback.display_name = "CPU DSP backend";
    return fallback;
}

}  // namespace

class CpuDspBackend final : public DspBackend {
public:
    explicit CpuDspBackend(CpuDspOptions options)
        : options_(std::move(options)) {
        if (options_.output_capacity == 0U) {
            throw ConfigurationError("DSP output capacity must be positive");
        }
        options_.source = effective_source(options_.source);
    }

    void configure(const DspConfig& config) override {
        validate(config);
        if (config.unit == SpectrumUnit::Dbm || config.unit == SpectrumUnit::DbmBin ||
            config.unit == SpectrumUnit::DbmHz) {
            throw ConfigurationError("calibrated dBm units are introduced by P09");
        }
        config_ = config;
        const auto n = config_.fft_size;
        const auto metrics = window_metrics(config_.window, n, 1.0, config_.kaiser_beta);
        coeffs_d_ = metrics.coefficients;
        coeffs_f_.assign(coeffs_d_.begin(), coeffs_d_.end());
        coherent_gain_ = metrics.coherent_gain;
        enbw_bins_ = metrics.enbw_bins;
        sum_w2_ = 0.0;
        for (const double coefficient : coeffs_d_) {
            sum_w2_ += coefficient * coefficient;
        }
        use_f64_ = config_.precision_mode == PrecisionMode::ReferenceF64;
        use_f32_accum_ = config_.precision_mode == PrecisionMode::FastF32;

        fft_ = make_pocketfft_provider();
        fft_->configure(n);

        ring_d_.assign(n, std::complex<double>{});
        ring_f_.assign(n, std::complex<float>{});
        stage_d_.assign(static_cast<std::size_t>(config_.batch_size) * n, std::complex<double>{});
        stage_f_.assign(static_cast<std::size_t>(config_.batch_size) * n, std::complex<float>{});
        fft_out_d_.assign(static_cast<std::size_t>(config_.batch_size) * n, std::complex<double>{});
        fft_out_f_.assign(static_cast<std::size_t>(config_.batch_size) * n, std::complex<float>{});
        meta_batch_.assign(config_.batch_size, FrameMeta{});
        sum_d_.assign(n, 0.0);
        max_d_.assign(n, 0.0);
        min_d_.assign(n, std::numeric_limits<double>::infinity());
        last_d_.assign(n, 0.0);
        sum_f_.assign(n, 0.0F);
        max_f_.assign(n, 0.0F);
        min_f_.assign(n, std::numeric_limits<float>::infinity());
        last_f_.assign(n, 0.0F);

        metrics_ = DspBackendMetrics{};
        frame_sequence_ = 0U;
        configured_ = true;
        reset();
    }

    void push_iq(const IqBlock& block) override {
        if (!configured_) {
            throw ConfigurationError("DSP backend is not configured");
        }
        if (!block.samples || block.sample_count == 0U) {
            throw ConfigurationError("IqBlock requires samples and positive sample_count");
        }
        const auto width = bytes_per_sample(block.sample_format);
        const std::size_t expected =
            static_cast<std::size_t>(block.sample_count) * width;
        if (block.samples->size() != expected) {
            throw ConfigurationError("IqBlock byte length disagrees with sample format/count");
        }
        if (!std::isfinite(block.sample_rate_hz) || block.sample_rate_hz <= 0.0 ||
            !std::isfinite(block.center_frequency_hz) || block.center_frequency_hz <= 0.0) {
            throw ConfigurationError("IqBlock rate/center must be finite and positive");
        }

        // Non-finite input never enters the pipeline: the whole block is
        // dropped. Counting convention: one rejected block equals one dropped
        // frame opportunity (spec §6.7); the count surfaces in
        // fft_frames_dropped / dropped_fft_frames_before.
        if (block.sample_format == SampleFormat::ComplexFloat32Le) {
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

        // Stream discontinuity: flush partial state and rebase the index map
        // to the new block. stream_pos_ restarts at zero so the first frame
        // after a gap requires fft_size FRESH samples (no stale ring data
        // and no fabricated indices inside the gap).
        if (base_sample_index_ < 0) {
            base_sample_index_ = static_cast<std::int64_t>(block.first_sample_index);
        } else if (
            static_cast<std::uint64_t>(base_sample_index_) + stream_pos_ !=
            block.first_sample_index
        ) {
            flush_pipeline();
            base_sample_index_ = static_cast<std::int64_t>(block.first_sample_index);
            stream_pos_ = 0U;
        }
        if (block.sample_rate_hz != current_fs_) {
            // A sample-rate change invalidates staged and accumulated frames.
            // The index map stays continuous: rebase to the current position.
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
            // A retune changes the physical meaning of every ring sample.
            // Never form an FFT from samples belonging to two centers.
            flush_pipeline();
            base_sample_index_ += static_cast<std::int64_t>(stream_pos_);
            stream_pos_ = 0U;
        }
        if (block.center_frequency_hz != current_fc_) {
            current_fc_ = block.center_frequency_hz;
            axis_valid_ = false;
        }

        const bool integer_format =
            block.sample_format == SampleFormat::ComplexInt12InInt16Le ||
            block.sample_format == SampleFormat::ComplexInt16Le;
        const bool clipped = integer_format &&
                             detect_clipping(*block.samples, block.sample_count, block.sample_format);
        const auto* bytes = block.samples->data();
        for (std::uint32_t index = 0U; index < block.sample_count; ++index) {
            if (use_f64_) {
                ring_d_[ring_pos_] = unpack<double>(bytes, index, block.sample_format);
            } else {
                ring_f_[ring_pos_] = unpack<float>(bytes, index, block.sample_format);
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

    [[nodiscard]] std::vector<SpectrumFrame> poll_spectrum(
        const std::size_t max_items,
        const bool flush_partial_batch
    ) override {
        // Explicit callers may flush a partial batch. The native engine polls
        // ready output without flushing so configured batches span I/Q blocks.
        if (flush_partial_batch && staged_ != 0U) {
            run_fft_batch();
        }
        std::vector<SpectrumFrame> result;
        while (!output_.empty() && (max_items == 0U || result.size() < max_items)) {
            result.push_back(std::move(output_.front()));
            output_.pop_front();
        }
        metrics_.output_pending = output_.size();
        return result;
    }

    void reset() override {
        ring_pos_ = 0U;
        staged_ = 0U;
        accum_count_ = 0U;
        accum_quality_flags_ = QualityFlag::None;
        stream_pos_ = 0U;
        next_frame_end_ = config_.fft_size;
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
        reset_accumulators();
        metrics_.output_pending = 0U;
    }

    [[nodiscard]] DspBackendMetrics metrics() const override {
        DspBackendMetrics result = metrics_;
        result.output_pending = output_.size();
        result.requested_preference = ComputeBackendKind::Cpu;
        result.active_backend = ComputeBackendKind::Cpu;
        return result;
    }

    [[nodiscard]] BackendInfo info() const override {
        BackendInfo result;
        result.kind = ComputeBackendKind::Cpu;
        result.backend_id = "cpu-pocketfft";
        result.vendor = "portable-cpu";
        result.device_name = "host-cpu";
        result.architecture = "portable-c++20";
        result.fft_library = "pocketfft";
        result.fft_library_version = "header-only";
        result.supports_fp64 = true;
        result.supports_pinned_host = false;
        result.supports_async_copy = false;
        result.validated = true;
        return result;
    }

private:
    struct FrameMeta {
        std::uint64_t first_sample_index{};
        std::int64_t timestamp_ns{};
        std::uint64_t config_generation{};
        bool clipped{};
        QualityFlag input_flags{QualityFlag::None};
    };

    template <typename T>
    [[nodiscard]] static std::complex<T> unpack(
        const std::uint8_t* bytes,
        const std::uint32_t index,
        const SampleFormat format
    ) {
        if (format == SampleFormat::ComplexFloat32Le) {
            const float re = read_le_float(bytes + index * 8U);
            const float im = read_le_float(bytes + index * 8U + 4U);
            return {static_cast<T>(re), static_cast<T>(im)};
        }
        const auto re = static_cast<double>(read_le_i16(bytes + index * 4U));
        const auto im = static_cast<double>(read_le_i16(bytes + index * 4U + 2U));
        const double full_scale = format == SampleFormat::ComplexInt12InInt16Le
                                      ? full_scale_i12
                                      : full_scale_i16;
        return {static_cast<T>(re / full_scale), static_cast<T>(im / full_scale)};
    }

    [[nodiscard]] static bool detect_clipping(
        const std::vector<std::uint8_t>& samples,
        const std::uint32_t sample_count,
        const SampleFormat format
    ) {
        for (std::uint32_t index = 0U; index < sample_count; ++index) {
            const auto re = read_le_i16(samples.data() + index * 4U);
            const auto im = read_le_i16(samples.data() + index * 4U + 2U);
            const bool clipped = format == SampleFormat::ComplexInt12InInt16Le
                                     ? (re == 2047 || re == -2048 || im == 2047 || im == -2048)
                                     : (re == 32767 || re == -32768 || im == 32767 || im == -32768);
            if (clipped) {
                return true;
            }
        }
        return false;
    }

    void flush_pipeline() {
        // Ready FFTs and computed averaging contributions must not disappear
        // silently across a gap, retune or generation change.
        metrics_.fft_frames_dropped +=
            static_cast<std::uint64_t>(staged_) + accum_count_;
        ring_pos_ = 0U;
        staged_ = 0U;
        accum_count_ = 0U;
        accum_quality_flags_ = QualityFlag::None;
        clipped_until_pos_ = 0U;
        next_frame_end_ = config_.fft_size;
        reset_accumulators();
    }

    void reset_accumulators() {
        std::fill(sum_d_.begin(), sum_d_.end(), 0.0);
        std::fill(max_d_.begin(), max_d_.end(), 0.0);
        std::fill(min_d_.begin(), min_d_.end(), std::numeric_limits<double>::infinity());
        std::fill(last_d_.begin(), last_d_.end(), 0.0);
        std::fill(sum_f_.begin(), sum_f_.end(), 0.0F);
        std::fill(max_f_.begin(), max_f_.end(), 0.0F);
        std::fill(min_f_.begin(), min_f_.end(), std::numeric_limits<float>::infinity());
        std::fill(last_f_.begin(), last_f_.end(), 0.0F);
    }

    // Copies the oldest fft_size samples from the ring into the staging
    // buffer and records frame metadata.
    void stage_frame(const IqBlock& block) {
        const auto n = config_.fft_size;
        const auto frame_first = static_cast<std::uint64_t>(base_sample_index_) +
                                 (stream_pos_ - n);
        auto& meta = meta_batch_[staged_];
        meta.first_sample_index = frame_first;
        const long double offset_samples =
            static_cast<long double>(frame_first) -
            static_cast<long double>(block.first_sample_index);
        const auto offset_ns = static_cast<std::int64_t>(std::llround(
            offset_samples * 1.0e9L /
            static_cast<long double>(block.sample_rate_hz)
        ));
        meta.timestamp_ns = block.timestamp_ns + offset_ns;
        meta.config_generation = block.config_generation;
        // Stream-relative comparison: frame covers [stream_pos_-n, stream_pos_).
        meta.clipped = (stream_pos_ - n) < clipped_until_pos_;
        meta.input_flags = block.flags;

        if (use_f64_) {
            auto* out = stage_d_.data() + static_cast<std::size_t>(staged_) * n;
            for (std::uint32_t k = 0U; k < n; ++k) {
                out[k] = ring_d_[(ring_pos_ + k) % n];
            }
        } else {
            auto* out = stage_f_.data() + static_cast<std::size_t>(staged_) * n;
            for (std::uint32_t k = 0U; k < n; ++k) {
                out[k] = ring_f_[(ring_pos_ + k) % n];
            }
        }
        ++staged_;
    }

    void run_fft_batch() {
        const auto n = config_.fft_size;
        if (staged_ == 0U) {
            return;
        }
        if (use_f64_) {
            apply_window_and_fft(stage_d_.data(), fft_out_d_.data());
        } else {
            apply_window_and_fft(stage_f_.data(), fft_out_f_.data());
        }
        for (std::uint32_t frame = 0U; frame < staged_; ++frame) {
            const auto* spectrum_d = use_f64_ ? fft_out_d_.data() + frame * n : nullptr;
            const auto* spectrum_f = use_f64_ ? nullptr : fft_out_f_.data() + frame * n;
            accumulate_frame(spectrum_d, spectrum_f);
            accum_quality_flags_ = accum_quality_flags_ | meta_batch_[frame].input_flags;
            if (meta_batch_[frame].clipped) {
                accum_quality_flags_ = accum_quality_flags_ | QualityFlag::AdcOverload;
            }
            ++metrics_.fft_frames_computed;
            ++accum_count_;
            if (accum_count_ == config_.averaging_frames) {
                emit_frame(meta_batch_[frame]);
                accum_count_ = 0U;
                accum_quality_flags_ = QualityFlag::None;
                reset_accumulators();
            }
        }
        staged_ = 0U;
    }

    template <typename T>
    void apply_window_and_fft(std::complex<T>* staged, std::complex<T>* output) {
        const auto n = config_.fft_size;
        const bool dc_block_mean = options_.dc_removal == DcRemovalMode::BlockMean;
        for (std::uint32_t frame = 0U; frame < staged_; ++frame) {
            auto* frame_in = staged + static_cast<std::size_t>(frame) * n;
            std::complex<T> mean{};
            if (dc_block_mean) {
                for (std::uint32_t k = 0U; k < n; ++k) {
                    mean += frame_in[k];
                }
                mean /= static_cast<T>(n);
            }
            for (std::uint32_t k = 0U; k < n; ++k) {
                const auto coefficient = static_cast<T>(
                    use_f64_ ? coeffs_d_[k] : static_cast<double>(coeffs_f_[k])
                );
                frame_in[k] = (frame_in[k] - mean) * coefficient;
            }
        }
        fft_->execute_batch(staged, output, staged_);
    }

    void accumulate_frame(
        const std::complex<double>* spectrum_d,
        const std::complex<float>* spectrum_f
    ) {
        const auto n = config_.fft_size;
        const bool psd = config_.unit == SpectrumUnit::DbfsHz;
        const double denominator =
            psd ? current_fs_ * sum_w2_
                : (static_cast<double>(n) * coherent_gain_) *
                      (static_cast<double>(n) * coherent_gain_);
        for (std::uint32_t k = 0U; k < n; ++k) {
            double magnitude_squared = 0.0;
            if (use_f64_) {
                const auto value = spectrum_d[k];
                magnitude_squared =
                    static_cast<double>(value.real()) * static_cast<double>(value.real()) +
                    static_cast<double>(value.imag()) * static_cast<double>(value.imag());
            } else {
                const auto value = spectrum_f[k];
                magnitude_squared =
                    static_cast<double>(value.real()) * static_cast<double>(value.real()) +
                    static_cast<double>(value.imag()) * static_cast<double>(value.imag());
            }
            const double power = magnitude_squared / denominator;
            const std::size_t target = fftshift_index(k, n);
            if (use_f32_accum_) {
                const auto power_f = static_cast<float>(power);
                sum_f_[target] += power_f;
                if (power_f > max_f_[target]) {
                    max_f_[target] = power_f;
                }
                if (power_f < min_f_[target]) {
                    min_f_[target] = power_f;
                }
                last_f_[target] = power_f;
            } else {
                sum_d_[target] += power;
                if (power > max_d_[target]) {
                    max_d_[target] = power;
                }
                if (power < min_d_[target]) {
                    min_d_[target] = power;
                }
                last_d_[target] = power;
            }
        }
    }

    void emit_frame(const FrameMeta& meta) {
        const auto n = config_.fft_size;
        if (!axis_valid_) {
            axis_shared_ = std::make_shared<const std::vector<double>>(
                complex_frequency_axis(n, current_fs_, current_fc_)
            );
            axis_valid_ = true;
        }
        auto values = std::make_shared<std::vector<float>>(n);
        const double count = static_cast<double>(config_.averaging_frames);
        for (std::uint32_t k = 0U; k < n; ++k) {
            double linear = 0.0;
            if (use_f32_accum_) {
                switch (config_.detector) {
                case DetectorType::Sample:
                    linear = static_cast<double>(last_f_[k]);
                    break;
                case DetectorType::Peak:
                    linear = static_cast<double>(max_f_[k]);
                    break;
                case DetectorType::NegativePeak:
                    linear = static_cast<double>(min_f_[k]);
                    break;
                case DetectorType::Rms:
                case DetectorType::AveragePower:
                    linear = static_cast<double>(sum_f_[k]) / count;
                    break;
                }
            } else {
                switch (config_.detector) {
                case DetectorType::Sample:
                    linear = last_d_[k];
                    break;
                case DetectorType::Peak:
                    linear = max_d_[k];
                    break;
                case DetectorType::NegativePeak:
                    linear = min_d_[k];
                    break;
                case DetectorType::Rms:
                case DetectorType::AveragePower:
                    linear = sum_d_[k] / count;
                    break;
                }
            }
            (*values)[k] = linear > 0.0
                               ? static_cast<float>(10.0 * std::log10(linear))
                               : -std::numeric_limits<float>::infinity();
        }

        QualityFlag flags = QualityFlag::Uncalibrated | QualityFlag::TimestampEstimated;
        if (options_.dc_removal == DcRemovalMode::BlockMean) {
            flags = flags | QualityFlag::DcRemoved;
        }
        flags = flags | accum_quality_flags_;

        SpectrumFrame frame;
        frame.source = options_.source;
        frame.frame_sequence = frame_sequence_++;
        frame.first_sample_index = meta.first_sample_index;
        frame.timestamp_ns = meta.timestamp_ns;
        frame.config_generation = meta.config_generation;
        frame.center_frequency_hz = current_fc_;
        frame.sample_rate_hz = current_fs_;
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

    CpuDspOptions options_;
    DspConfig config_{};
    bool configured_{false};
    std::unique_ptr<FftProvider> fft_;
    std::vector<double> coeffs_d_;
    std::vector<float> coeffs_f_;
    double coherent_gain_{};
    double enbw_bins_{};
    double sum_w2_{};
    bool use_f64_{true};
    bool use_f32_accum_{false};

    std::vector<std::complex<double>> ring_d_;
    std::vector<std::complex<float>> ring_f_;
    std::size_t ring_pos_{};
    std::uint64_t next_frame_end_{};
    std::vector<std::complex<double>> stage_d_;
    std::vector<std::complex<float>> stage_f_;
    std::vector<std::complex<double>> fft_out_d_;
    std::vector<std::complex<float>> fft_out_f_;
    std::vector<FrameMeta> meta_batch_;
    std::uint32_t staged_{};

    std::vector<double> sum_d_;
    std::vector<double> max_d_;
    std::vector<double> min_d_;
    std::vector<double> last_d_;
    std::vector<float> sum_f_;
    std::vector<float> max_f_;
    std::vector<float> min_f_;
    std::vector<float> last_f_;
    std::uint32_t accum_count_{};
    QualityFlag accum_quality_flags_{QualityFlag::None};

    std::deque<SpectrumFrame> output_;
    DspBackendMetrics metrics_{};
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
};

std::unique_ptr<DspBackend> make_cpu_dsp_backend(CpuDspOptions options) {
    return std::make_unique<CpuDspBackend>(std::move(options));
}

}  // namespace sdr_core

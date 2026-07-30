#include "sdr_core/cuda_backend_link.hpp"

#include "sdr_core/window.hpp"
#include "sdr_core/version.hpp"
#include "sdr_cuda/cuda_dsp_backend.hpp"
#include "sdr_cuda/cuda_runtime.hpp"

#include <cmath>
#include <complex>
#include <cstring>
#include <limits>
#include <vector>

namespace sdr_core::cuda_link {

bool compiled() noexcept {
    return true;
}

BackendAvailability availability(const int device_id) {
    return sdr_cuda::availability(device_id);
}

BackendAvailability self_test(const int device_id) {
    auto result = sdr_cuda::availability(device_id);
    if (!result.device_supported) {
        return result;
    }
    try {
        constexpr std::uint32_t n = 1024U;
        constexpr double rate = 1'024'000.0;
        constexpr double center = 100'000'000.0;
        DspConfig config;
        config.fft_size = n;
        config.hop_size = n;
        config.window = WindowType::Rectangular;
        config.detector = DetectorType::Sample;
        config.unit = SpectrumUnit::DbfsBin;
        config.precision_mode = PrecisionMode::ReferenceF64;
        config.batch_size = 1U;
        config.averaging_frames = 1U;

        auto make_block = [&](const SampleFormat format) {
            const std::size_t width = format == SampleFormat::ComplexFloat32Le ? 8U : 4U;
            auto samples = std::make_shared<std::vector<std::uint8_t>>(n * width);
            for (std::uint32_t k = 0U; k < n; ++k) {
                const double phase = 2.0 * 3.14159265358979323846 * 32.0 * static_cast<double>(k) / n;
                if (format == SampleFormat::ComplexFloat32Le) {
                    const float re = static_cast<float>(0.25 * std::cos(phase));
                    const float im = static_cast<float>(0.25 * std::sin(phase));
                    std::memcpy(samples->data() + k * 8U, &re, sizeof(re));
                    std::memcpy(samples->data() + k * 8U + 4U, &im, sizeof(im));
                } else {
                    const auto re = static_cast<std::int16_t>(std::llround(1500.0 * std::cos(phase)));
                    const auto im = static_cast<std::int16_t>(std::llround(1500.0 * std::sin(phase)));
                    std::memcpy(samples->data() + k * 4U, &re, sizeof(re));
                    std::memcpy(samples->data() + k * 4U + 2U, &im, sizeof(im));
                }
            }
            IqBlock block;
            block.first_sample_index = 0U;
            block.timestamp_ns = 123'456'789;
            block.center_frequency_hz = center;
            block.sample_rate_hz = rate;
            block.sample_format = format;
            block.sample_count = n;
            block.flags = QualityFlag::GainModeAgc;
            block.samples = std::move(samples);
            block.config_generation = 19U;
            return block;
        };

        auto run = [&](DspBackend& backend, const SampleFormat format) {
            backend.configure(config);
            backend.push_iq(make_block(format));
            const auto frames = backend.poll_spectrum(0U, true);
            if (frames.size() != 1U) {
                throw DeviceError("CUDA self-test produced no frame", BackendErrorCode::NumericalSelfTestFailed);
            }
            return frames.front();
        };

        auto cpu = make_cpu_dsp_backend({});
        sdr_cuda::CudaDspBackend cuda({}, device_id, 4U);
        for (const auto format : {SampleFormat::ComplexFloat32Le, SampleFormat::ComplexInt12InInt16Le}) {
            const auto cpu_frame = run(*cpu, format);
            const auto cuda_frame = run(cuda, format);
            if (cuda_frame.first_sample_index != 0U || cuda_frame.timestamp_ns != 123'456'789 ||
                cuda_frame.config_generation != 19U || cuda_frame.center_frequency_hz != center ||
                cuda_frame.sample_rate_hz != rate || !has_flag(cuda_frame.quality_flags, QualityFlag::GainModeAgc)) {
                throw DeviceError("CUDA self-test metadata mismatch", BackendErrorCode::NumericalSelfTestFailed);
            }
            if (cuda_frame.frequencies_hz->size() != n || cuda_frame.values->size() != n) {
                throw DeviceError("CUDA self-test axis mismatch", BackendErrorCode::NumericalSelfTestFailed);
            }
            for (std::uint32_t k = 0U; k < n; ++k) {
                const float left = (*cpu_frame.values)[k];
                const float right = (*cuda_frame.values)[k];
                if (std::isnan(right) || (std::isfinite(left) && std::isfinite(right) && std::fabs(static_cast<double>(left - right)) > 1e-3)) {
                    throw DeviceError("CUDA self-test CPU parity mismatch", BackendErrorCode::NumericalSelfTestFailed);
                }
                if (std::isfinite((*cuda_frame.frequencies_hz)[k]) == false) {
                    throw DeviceError("CUDA self-test found non-finite axis", BackendErrorCode::NumericalSelfTestFailed);
                }
            }
        }
        result.self_test_passed = true;
    } catch (const SdrNativeError& error) {
        result.self_test_passed = false;
        result.reason_code = std::string(to_wire(BackendErrorCode::NumericalSelfTestFailed));
        result.details = error.what();
    } catch (const std::exception& error) {
        result.self_test_passed = false;
        result.reason_code = std::string(to_wire(BackendErrorCode::Unknown));
        result.details = error.what();
    }
    return result;
}

std::string self_test_cache_key(const int device_id) {
    const auto info = sdr_cuda::device_info(device_id, false);
    return std::string("cuda|") + info.device_uuid + "|" + info.driver_version + "|" +
           info.runtime_version + "|" + info.fft_library_version + "|" + std::string(sdr_core::version);
}

std::unique_ptr<DspBackend> make_backend(
    DspOptions options,
    const int device_id,
    const std::uint32_t plan_cache_capacity
) {
    return std::make_unique<sdr_cuda::CudaDspBackend>(
        std::move(options),
        device_id,
        plan_cache_capacity
    );
}

}  // namespace sdr_core::cuda_link

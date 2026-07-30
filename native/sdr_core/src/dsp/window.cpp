#include "sdr_core/window.hpp"

#include "sdr_core/errors.hpp"

#include <cmath>

namespace sdr_core {

namespace {

constexpr double two_pi = 6.28318530717958647692528676655900577;

// Modified Bessel function I0(x) via its power series; converges to float64
// precision for the Kaiser beta range used by the contracts.
[[nodiscard]] double bessel_i0(const double x) noexcept {
    const double quarter_x_squared = (x * x) / 4.0;
    double term = 1.0;
    double sum = 1.0;
    for (std::uint32_t k = 1U; k < 10'000U; ++k) {
        term *= quarter_x_squared / (static_cast<double>(k) * static_cast<double>(k));
        sum += term;
        if (term <= sum * 1e-18) {
            break;
        }
    }
    return sum;
}

}  // namespace

WindowMetrics window_metrics(
    const WindowType window,
    const std::uint32_t size,
    const double sample_rate_hz,
    const double kaiser_beta
) {
    static_cast<void>(to_wire(window));
    if (size == 0U) {
        throw ConfigurationError("window size must be positive");
    }
    if (!std::isfinite(sample_rate_hz) || sample_rate_hz <= 0.0) {
        throw ConfigurationError("sample_rate_hz must be finite and positive");
    }
    if (!std::isfinite(kaiser_beta) || kaiser_beta < 0.0) {
        throw ConfigurationError("kaiser_beta must be finite and non-negative");
    }

    WindowMetrics result;
    result.coefficients.resize(size);
    if (size == 1U) {
        result.coefficients[0] = 1.0;
    } else {
        const double denominator = static_cast<double>(size - 1U);
        for (std::uint32_t n = 0U; n < size; ++n) {
            const double phase = two_pi * static_cast<double>(n) / denominator;
            double coefficient = 0.0;
            switch (window) {
            case WindowType::Rectangular:
                coefficient = 1.0;
                break;
            case WindowType::Hann:
                coefficient = 0.5 - 0.5 * std::cos(phase);
                break;
            case WindowType::BlackmanHarris4Term:
                coefficient = 0.35875 - 0.48829 * std::cos(phase) +
                              0.14128 * std::cos(2.0 * phase) -
                              0.01168 * std::cos(3.0 * phase);
                break;
            case WindowType::FlatTop:
                coefficient = 0.21557895 - 0.41663158 * std::cos(phase) +
                              0.277263158 * std::cos(2.0 * phase) -
                              0.083578947 * std::cos(3.0 * phase) +
                              0.006947368 * std::cos(4.0 * phase);
                break;
            case WindowType::Nuttall:
                coefficient = 0.355768 - 0.487396 * std::cos(phase) +
                              0.144232 * std::cos(2.0 * phase) -
                              0.012604 * std::cos(3.0 * phase);
                break;
            case WindowType::Kaiser: {
                const double alpha = 0.5 * static_cast<double>(size - 1U);
                const double ratio = (static_cast<double>(n) - alpha) / alpha;
                const double argument = kaiser_beta * std::sqrt(std::max(0.0, 1.0 - ratio * ratio));
                coefficient = bessel_i0(argument) / bessel_i0(kaiser_beta);
                break;
            }
            }
            result.coefficients[n] = coefficient;
        }
    }

    double sum_w = 0.0;
    double sum_w2 = 0.0;
    for (const double coefficient : result.coefficients) {
        sum_w += coefficient;
        sum_w2 += coefficient * coefficient;
    }
    const double n = static_cast<double>(size);
    result.coherent_gain = sum_w / n;
    result.enbw_bins = n * sum_w2 / (sum_w * sum_w);
    result.enbw_hz = result.enbw_bins * sample_rate_hz / n;
    return result;
}

std::vector<double> complex_frequency_axis(
    const std::uint32_t size,
    const double sample_rate_hz,
    const double center_frequency_hz
) {
    if (size == 0U) {
        throw ConfigurationError("axis size must be positive");
    }
    if (!std::isfinite(sample_rate_hz) || sample_rate_hz <= 0.0) {
        throw ConfigurationError("sample_rate_hz must be finite and positive");
    }
    std::vector<double> axis(size);
    const double bin_width = sample_rate_hz / static_cast<double>(size);
    const auto half = static_cast<std::int64_t>(size / 2U);
    for (std::uint32_t k = 0U; k < size; ++k) {
        axis[k] = center_frequency_hz +
                  (static_cast<std::int64_t>(k) - half) * bin_width;
    }
    return axis;
}

}  // namespace sdr_core

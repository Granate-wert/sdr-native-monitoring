#pragma once

#include "sdr_core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace sdr_core {

struct WindowMetrics {
    std::vector<double> coefficients;
    double coherent_gain{};
    double enbw_bins{};
    double enbw_hz{};
};

// Window coefficients, coherent gain and ENBW per the P03 golden reference.
// Windows are SYMMETRIC (denominator size-1, not periodic); Kaiser uses the
// float64 Bessel I0. All arithmetic is float64, matching the Python oracle.
[[nodiscard]] WindowMetrics window_metrics(
    WindowType window,
    std::uint32_t size,
    double sample_rate_hz,
    double kaiser_beta = 8.6
);

// Complex centered frequency axis: numpy fftshift(fftfreq) semantics.
// f_k = center + (k - floor(N/2)) * Fs/N for both even and odd N.
[[nodiscard]] std::vector<double> complex_frequency_axis(
    std::uint32_t size,
    double sample_rate_hz,
    double center_frequency_hz
);

// Index mapping matching numpy fftshift for complex spectra:
// out[k] = X[fftshift_index(k, N)] with shift = ceil(N/2).
[[nodiscard]] constexpr std::size_t fftshift_index(
    const std::size_t k,
    const std::size_t n
) noexcept {
    return (k + (n + 1U) / 2U) % n;
}

}  // namespace sdr_core

#include "sdr_core/errors.hpp"
#include "sdr_core/window.hpp"

#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void expect_close(
    const double actual,
    const double expected,
    const double tolerance,
    const std::string& message
) {
    if (std::fabs(actual - expected) > tolerance) {
        throw std::runtime_error(
            message + ": actual=" + std::to_string(actual) +
            " expected=" + std::to_string(expected)
        );
    }
}

// Analytic coherent-gain references mirrored from the Python oracle tests.
void test_coherent_gain() {
    constexpr std::uint32_t size = 65536U;
    constexpr double delta = 2e-12;
    const double n = static_cast<double>(size);
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::Rectangular, size, 1.0).coherent_gain,
        1.0,
        delta,
        "rect CG"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::Hann, size, 1.0).coherent_gain,
        0.5 * (n - 1.0) / n,
        delta,
        "hann CG"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::BlackmanHarris4Term, size, 1.0)
            .coherent_gain,
        0.35875 + (-0.48829 + 0.14128 - 0.01168) / n,
        delta,
        "bh4 CG"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::FlatTop, size, 1.0).coherent_gain,
        0.21557895 +
            (-0.41663158 + 0.277263158 - 0.083578947 + 0.006947368) / n,
        delta,
        "flattop CG"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::Nuttall, size, 1.0).coherent_gain,
        0.355768 + (-0.487396 + 0.144232 - 0.012604) / n,
        delta,
        "nuttall CG"
    );
    const auto kaiser = sdr_core::window_metrics(sdr_core::WindowType::Kaiser, size, 1.0);
    expect(std::isfinite(kaiser.coherent_gain) && kaiser.coherent_gain > 0.0, "kaiser CG");
}

// Known ENBW values from the Python oracle tests (delta 2e-9).
void test_enbw() {
    constexpr std::uint32_t size = 65536U;
    constexpr double delta = 2e-9;
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::Rectangular, size, 1.0).enbw_bins,
        1.0,
        delta,
        "rect ENBW"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::Hann, size, 1.0).enbw_bins,
        1.500022888532845,
        delta,
        "hann ENBW"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::BlackmanHarris4Term, size, 1.0)
            .enbw_bins,
        2.004383512452347,
        delta,
        "bh4 ENBW"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::FlatTop, size, 1.0).enbw_bins,
        3.7703042025049287,
        delta,
        "flattop ENBW"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::Nuttall, size, 1.0).enbw_bins,
        2.0212634203318767,
        delta,
        "nuttall ENBW"
    );
    expect_close(
        sdr_core::window_metrics(sdr_core::WindowType::Kaiser, size, 1.0).enbw_bins,
        1.7214003273207084,
        delta,
        "kaiser ENBW"
    );
}

void test_symmetric_coefficients() {
    constexpr std::uint32_t size = 1024U;
    const auto hann = sdr_core::window_metrics(sdr_core::WindowType::Hann, size, 1.0);
    expect_close(hann.coefficients.front(), 0.0, 1e-18, "hann edge");
    // Symmetric (size-1) Hann: w[n] = 0.5 - 0.5*cos(2*pi*n/(N-1)).
    const std::uint32_t probe = 173U;
    const double expected =
        0.5 - 0.5 * std::cos(2.0 * 3.14159265358979323846 * 173.0 / 1023.0);
    expect_close(hann.coefficients[probe], expected, 1e-15, "hann symmetric coefficient");
    const auto kaiser = sdr_core::window_metrics(sdr_core::WindowType::Kaiser, size, 1.0);
    // Symmetric Kaiser peaks at (N-1)/2 = 511.5; for even N the two center
    // samples share the maximum slightly below 1.0 and stay symmetric.
    expect(kaiser.coefficients[512] == kaiser.coefficients[511], "kaiser center symmetry");
    expect(
        kaiser.coefficients[512] > 0.9999 && kaiser.coefficients[512] <= 1.0,
        "kaiser center maximum"
    );
}

void test_frequency_axis() {
    constexpr std::uint32_t size = 1024U;
    constexpr double rate = 1'024'000.0;
    constexpr double center = 100'000'000.0;
    const auto axis = sdr_core::complex_frequency_axis(size, rate, center);
    expect(axis.size() == size, "axis size");
    expect_close(axis[0], center - rate / 2.0, 1e-6, "axis first bin");
    expect_close(axis[size / 2], center, 1e-6, "axis center bin");
    expect_close(
        axis[size - 1] - axis[size - 2],
        rate / static_cast<double>(size),
        1e-9,
        "axis step"
    );
    // Odd-size semantics: f_k = center + (k - floor(N/2)) * Fs/N.
    const auto odd = sdr_core::complex_frequency_axis(1023U, rate, center);
    expect_close(odd[0], center - 511.0 * (rate / 1023.0), 1e-6, "odd axis first bin");
    expect_close(odd[511], center, 1e-6, "odd axis center bin");
}

void test_fftshift_index() {
    expect(sdr_core::fftshift_index(0, 4) == 2, "fftshift even 0");
    expect(sdr_core::fftshift_index(1, 4) == 3, "fftshift even 1");
    expect(sdr_core::fftshift_index(2, 4) == 0, "fftshift even 2");
    expect(sdr_core::fftshift_index(3, 4) == 1, "fftshift even 3");
    expect(sdr_core::fftshift_index(0, 5) == 3, "fftshift odd 0");
    expect(sdr_core::fftshift_index(1, 5) == 4, "fftshift odd 1");
    expect(sdr_core::fftshift_index(2, 5) == 0, "fftshift odd 2");
    expect(sdr_core::fftshift_index(3, 5) == 1, "fftshift odd 3");
    expect(sdr_core::fftshift_index(4, 5) == 2, "fftshift odd 4");
}

void test_invalid_inputs() {
    bool rejected = false;
    try {
        static_cast<void>(sdr_core::window_metrics(sdr_core::WindowType::Hann, 0U, 1.0));
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "zero window size must be rejected");
    rejected = false;
    try {
        static_cast<void>(sdr_core::window_metrics(sdr_core::WindowType::Kaiser, 256U, 1.0, -1.0));
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "negative kaiser_beta must be rejected");
}

}  // namespace

int main() {
    try {
        test_coherent_gain();
        test_enbw();
        test_symmetric_coefficients();
        test_frequency_axis();
        test_fftshift_index();
        test_invalid_inputs();
        std::cout << "P05 window OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

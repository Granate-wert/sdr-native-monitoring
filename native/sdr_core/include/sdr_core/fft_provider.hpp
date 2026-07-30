#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>
#include <memory>

namespace sdr_core {

// Replaceable FFT provider contract (P05 §7). Transforms are NOT normalized
// (no 1/N factor), matching the numpy/golden-reference convention: scaling
// is applied by the DSP stage, not by the provider.
class FftProvider {
public:
    virtual ~FftProvider() = default;

    virtual void configure(std::uint32_t fft_size) = 0;

    virtual void execute_batch(
        const std::complex<double>* input,
        std::complex<double>* output,
        std::size_t batch_count
    ) = 0;

    virtual void execute_batch(
        const std::complex<float>* input,
        std::complex<float>* output,
        std::size_t batch_count
    ) = 0;

    [[nodiscard]] virtual std::uint32_t fft_size() const = 0;
};

// Default portable provider (vendored pocketfft, BSD-3-Clause).
[[nodiscard]] std::unique_ptr<FftProvider> make_pocketfft_provider();

}  // namespace sdr_core

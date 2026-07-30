#include "sdr_core/fft_provider.hpp"

#include "sdr_core/errors.hpp"

#include <vector>

#include <pocketfft/pocketfft_hdronly.h>

namespace sdr_core {

namespace {

class PocketFftProvider final : public FftProvider {
public:
    void configure(const std::uint32_t fft_size) override {
        if (fft_size == 0U) {
            throw ConfigurationError("fft size must be positive");
        }
        fft_size_ = fft_size;
    }

    void execute_batch(
        const std::complex<double>* input,
        std::complex<double>* output,
        const std::size_t batch_count
    ) override {
        execute<double>(input, output, batch_count);
    }

    void execute_batch(
        const std::complex<float>* input,
        std::complex<float>* output,
        const std::size_t batch_count
    ) override {
        execute<float>(input, output, batch_count);
    }

    [[nodiscard]] std::uint32_t fft_size() const override {
        return fft_size_;
    }

private:
    template <typename T>
    void execute(const std::complex<T>* input, std::complex<T>* output, const std::size_t batch_count) {
        if (fft_size_ == 0U) {
            throw ConfigurationError("FFT provider is not configured");
        }
        if (batch_count == 0U) {
            return;
        }
        if (input == nullptr || output == nullptr) {
            throw ConfigurationError("FFT batch requires non-null buffers");
        }
        const auto n = static_cast<std::size_t>(fft_size_);
        const pocketfft::shape_t shape{batch_count, n};
        const auto stride_element = static_cast<std::ptrdiff_t>(sizeof(std::complex<T>));
        const pocketfft::stride_t stride_in{
            static_cast<std::ptrdiff_t>(n) * stride_element,
            stride_element,
        };
        const pocketfft::stride_t stride_out{
            static_cast<std::ptrdiff_t>(n) * stride_element,
            stride_element,
        };
        const pocketfft::shape_t axes{1U};
        pocketfft::c2c(
            shape,
            stride_in,
            stride_out,
            axes,
            pocketfft::FORWARD,
            input,
            output,
            static_cast<T>(1.0),
            1U
        );
    }

    std::uint32_t fft_size_{};
};

}  // namespace

std::unique_ptr<FftProvider> make_pocketfft_provider() {
    return std::make_unique<PocketFftProvider>();
}

}  // namespace sdr_core

#include "dsp_binding.hpp"

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/events.hpp"

#include <complex>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <vector>

#include <pybind11/numpy.h>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {

namespace {

// Test/feed bridge: wraps a NumPy complex array into a ComplexFloat32Le
// IqBlock (input is cast to float32; the conversion is documented and
// covered by the golden-parity tolerances).
IqBlock block_from_numpy(
    const py::array& samples,
    const double sample_rate_hz,
    const double center_frequency_hz,
    const std::uint64_t first_sample_index
) {
    const auto info = samples.request();
    const bool is_c128 = info.format == py::format_descriptor<std::complex<double>>::format();
    const bool is_c64 = info.format == py::format_descriptor<std::complex<float>>::format();
    if (info.ndim != 1 || (!is_c128 && !is_c64)) {
        throw ConfigurationError("samples must be a 1-D complex64/complex128 array");
    }
    if (info.strides[0] != static_cast<std::ptrdiff_t>(info.itemsize)) {
        throw ConfigurationError("samples must be C-contiguous (no strided views)");
    }
    const auto count = static_cast<std::size_t>(info.shape[0]);
    if (count == 0U || count > 0xFFFFFFFFU) {
        throw ConfigurationError("samples length is out of range");
    }
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(count * 8U);
    for (std::size_t index = 0; index < count; ++index) {
        float re = 0.0F;
        float im = 0.0F;
        if (is_c128) {
            const auto& value = static_cast<const std::complex<double>*>(info.ptr)[index];
            re = static_cast<float>(value.real());
            im = static_cast<float>(value.imag());
        } else {
            const auto& value = static_cast<const std::complex<float>*>(info.ptr)[index];
            re = value.real();
            im = value.imag();
        }
        std::memcpy(bytes->data() + index * 8U, &re, sizeof(re));
        std::memcpy(bytes->data() + index * 8U + 4U, &im, sizeof(im));
    }
    IqBlock block;
    block.source_sequence = 0U;
    block.first_sample_index = first_sample_index;
    block.timestamp_ns = host_monotonic_ns();
    block.center_frequency_hz = center_frequency_hz;
    block.sample_rate_hz = sample_rate_hz;
    block.sample_format = SampleFormat::ComplexFloat32Le;
    block.sample_count = static_cast<std::uint32_t>(count);
    block.flags = QualityFlag::None;
    block.samples = std::move(bytes);
    block.config_generation = 1U;
    return block;
}

}  // namespace

void bind_dsp(py::module_& module) {
    py::class_<DspBackendMetrics>(module, "DspBackendMetrics")
        .def_readonly("fft_frames_computed", &DspBackendMetrics::fft_frames_computed)
        .def_readonly("fft_frames_dropped", &DspBackendMetrics::fft_frames_dropped)
        .def_readonly("samples_processed", &DspBackendMetrics::samples_processed)
        .def_readonly("output_pending", &DspBackendMetrics::output_pending);

    // Bound under the CPU implementation name through the replaceable
    // DspBackend interface (P05 §7).
    py::class_<DspBackend, std::shared_ptr<DspBackend>>(module, "CpuDspBackend")
        .def(py::init([]() {
            return std::shared_ptr<DspBackend>(make_cpu_dsp_backend({}));
        }))
        .def("configure", &DspBackend::configure, py::arg("config"))
        .def(
            "push_iq",
            [](DspBackend& backend, const IqBlock& block) {
                py::gil_scoped_release release;
                backend.push_iq(block);
            },
            py::arg("block")
        )
        .def(
            "push_samples",
            [](DspBackend& backend,
               const py::array& samples,
               const double sample_rate_hz,
               const double center_frequency_hz,
               const std::uint64_t first_sample_index) {
                auto block = block_from_numpy(
                    samples,
                    sample_rate_hz,
                    center_frequency_hz,
                    first_sample_index
                );
                py::gil_scoped_release release;
                backend.push_iq(block);
            },
            py::arg("samples"),
            py::arg("sample_rate_hz"),
            py::arg("center_frequency_hz"),
            py::arg("first_sample_index") = 0U
        )
        .def(
            "poll_spectrum",
            &DspBackend::poll_spectrum,
            py::arg("max_items") = 0U,
            py::arg("flush_partial_batch") = true
        )
        .def("reset", &DspBackend::reset)
        .def("metrics", &DspBackend::metrics);
}

}  // namespace sdr_core::python

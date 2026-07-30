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
        .def_readonly("output_pending", &DspBackendMetrics::output_pending)
        .def_readonly("requested_preference", &DspBackendMetrics::requested_preference)
        .def_readonly("active_backend", &DspBackendMetrics::active_backend)
        .def_readonly("backend_self_test_passed", &DspBackendMetrics::backend_self_test_passed)
        .def_readonly("backend_fallback_count", &DspBackendMetrics::backend_fallback_count)
        .def_readonly("backend_switch_count", &DspBackendMetrics::backend_switch_count)
        .def_readonly("last_backend_error", &DspBackendMetrics::last_backend_error)
        .def_readonly("gpu_processing_ns", &DspBackendMetrics::gpu_processing_ns)
        .def_readonly("h2d_ns", &DspBackendMetrics::h2d_ns)
        .def_readonly("d2h_ns", &DspBackendMetrics::d2h_ns);

    py::class_<BackendInfo>(module, "BackendInfo")
        .def_readonly("kind", &BackendInfo::kind)
        .def_readonly("backend_id", &BackendInfo::backend_id)
        .def_readonly("vendor", &BackendInfo::vendor)
        .def_readonly("device_name", &BackendInfo::device_name)
        .def_readonly("architecture", &BackendInfo::architecture)
        .def_readonly("driver_version", &BackendInfo::driver_version)
        .def_readonly("runtime_version", &BackendInfo::runtime_version)
        .def_readonly("fft_library", &BackendInfo::fft_library)
        .def_readonly("fft_library_version", &BackendInfo::fft_library_version)
        .def_readonly("total_memory_bytes", &BackendInfo::total_memory_bytes)
        .def_readonly("supports_fp64", &BackendInfo::supports_fp64)
        .def_readonly("supports_pinned_host", &BackendInfo::supports_pinned_host)
        .def_readonly("supports_async_copy", &BackendInfo::supports_async_copy)
        .def_readonly("validated", &BackendInfo::validated);

    py::class_<BackendAvailability>(module, "BackendAvailability")
        .def_readonly("compiled", &BackendAvailability::compiled)
        .def_readonly("runtime_present", &BackendAvailability::runtime_present)
        .def_readonly("device_count", &BackendAvailability::device_count)
        .def_readonly("device_supported", &BackendAvailability::device_supported)
        .def_readonly("self_test_passed", &BackendAvailability::self_test_passed)
        .def_readonly("reason_code", &BackendAvailability::reason_code)
        .def_readonly("details", &BackendAvailability::details);

    py::class_<DspBackendSelectionOptions>(module, "DspBackendSelectionOptions")
        .def(py::init([](
                 const ComputeBackendKind preference,
                 const bool allow_runtime_fallback,
                 const int device_id,
                 const std::uint32_t plan_cache_capacity
             ) {
            DspBackendSelectionOptions result;
            result.preference = preference;
            result.allow_runtime_fallback = allow_runtime_fallback;
            result.device_id = device_id;
            result.plan_cache_capacity = plan_cache_capacity;
            validate(result);
            return result;
        }),
            py::arg("preference") = ComputeBackendKind::Auto,
            py::arg("allow_runtime_fallback") = true,
            py::arg("device_id") = -1,
            py::arg("plan_cache_capacity") = 8U
        )
        .def_readonly("preference", &DspBackendSelectionOptions::preference)
        .def_readonly("allow_runtime_fallback", &DspBackendSelectionOptions::allow_runtime_fallback)
        .def_readonly("device_id", &DspBackendSelectionOptions::device_id)
        .def_readonly("plan_cache_capacity", &DspBackendSelectionOptions::plan_cache_capacity);

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
        .def("metrics", &DspBackend::metrics)
        .def("info", &DspBackend::info);

    module.def(
        "make_dsp_backend",
        [](const DspBackendSelectionOptions& selection) {
            return std::shared_ptr<DspBackend>(make_dsp_backend(selection, {}));
        },
        py::arg("selection")
    );
    module.def(
        "backend_availability",
        &backend_availability,
        py::arg("kind"),
        py::call_guard<py::gil_scoped_release>()
    );
    module.def(
        "run_backend_self_test",
        &run_backend_self_test,
        py::arg("kind"),
        py::call_guard<py::gil_scoped_release>()
    );
}

}  // namespace sdr_core::python

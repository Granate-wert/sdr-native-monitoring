#include "aux_contracts_binding.hpp"
#include "contracts_binding.hpp"
#include "dsp_binding.hpp"
#include "hackrf_binding.hpp"
#include "lifecycle_binding.hpp"
#include "calibration_binding.hpp"
#if SDR_CORE_PLUTO_COMPILED
#include "pluto_binding.hpp"
#endif
#include "synthetic_binding.hpp"

#include "sdr_core/api.hpp"
#include "sdr_core/errors.hpp"

#include <cstdint>
#include <stdexcept>

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

PYBIND11_MODULE(_sdr_native, module) {
    module.doc() = "Portable SDR native-core contracts module";

    const auto native_error = py::register_exception<sdr_core::SdrNativeError>(module, "SdrNativeError");
    py::register_exception<sdr_core::ConfigurationError>(module, "ConfigurationError", native_error.ptr());
    py::register_exception<sdr_core::BackendUnavailableError>(
        module,
        "BackendUnavailableError",
        native_error.ptr()
    );
    py::register_exception<sdr_core::DeviceError>(module, "DeviceError", native_error.ptr());
    py::register_exception<sdr_core::OperationCancelled>(module, "OperationCancelled", native_error.ptr());

    sdr_core::python::bind_contracts(module);
    sdr_core::python::bind_calibration(module);
    sdr_core::python::bind_auxiliary_contracts(module);
    sdr_core::python::bind_synthetic(module);
    // After bind_synthetic: EngineConfig defaults reference SyntheticScenario.
    sdr_core::python::bind_lifecycle(module);
    sdr_core::python::bind_dsp(module);
    sdr_core::python::bind_hackrf(module);
#if defined(SDR_CORE_HACKRF_OFFICIAL_COMPILED)
    sdr_core::python::bind_hackrf_factory(module);
#endif
#if SDR_CORE_PLUTO_COMPILED
    sdr_core::python::bind_pluto(module);
#endif

    module.def("build_info", []() {
        const auto info = sdr_core::build_info();
        py::dict result;
        result["version"] = info.version;
        result["compiler"] = info.compiler;
        result["platform"] = info.platform;
        result["architecture"] = info.architecture;
        result["build_type"] = info.build_type;
        result["cuda_compiled"] = info.cuda_compiled;
        result["pluto_compiled"] = info.pluto_compiled;
        return result;
    });

    module.def("available_backends", &sdr_core::available_backends);

    // Private APP-05 display row kernel. Its caller must prove that both input
    // arrays came from the finite [0, 1] worker display pipeline.
    module.def("_visual_smooth_row_into", [](py::array mapped, py::array old, py::array target) {
        const auto validate = [](const py::array& value, const bool writable) {
            if (value.ndim() != 1 || value.size() == 0 || value.size() > 65'536
                    || !value.dtype().is(py::dtype::of<float>())
                    || (value.flags() & py::array::c_style) == 0
                    || (writable && !value.writeable())) {
                throw std::invalid_argument("visual smoothing requires bounded C float32 row arrays");
            }
        };
        validate(mapped, false);
        validate(old, false);
        validate(target, true);
        if (mapped.size() != old.size() || mapped.size() != target.size()) {
            throw std::invalid_argument("visual smoothing rows must have equal lengths");
        }
        const auto overlaps = [](const py::array& left, const py::array& right) {
            const auto left_start = reinterpret_cast<std::uintptr_t>(left.data());
            const auto right_start = reinterpret_cast<std::uintptr_t>(right.data());
            return left_start < right_start + static_cast<std::uintptr_t>(right.nbytes())
                && right_start < left_start + static_cast<std::uintptr_t>(left.nbytes());
        };
        if (overlaps(mapped, old) || overlaps(mapped, target) || overlaps(old, target)) {
            throw std::invalid_argument("visual smoothing rows must not overlap");
        }
        const auto* mapped_data = static_cast<const float*>(mapped.data());
        const auto* old_data = static_cast<const float*>(old.data());
        auto* target_data = static_cast<float*>(target.mutable_data());
        const auto count = static_cast<std::size_t>(mapped.size());
        const float release = static_cast<float>(.18);
        const float attack_over_release = static_cast<float>(.65 / .18);
        py::gil_scoped_release release_gil;
        // The product caller admits only worker-produced, bounded histories;
        // source mapping has already clipped the current row to finite [0, 1].
        for (std::size_t index = 0; index < count; ++index) {
            const float current = mapped_data[index];
            const float previous = old_data[index];
            float delta = current - previous;
            delta *= release;
            if (current >= previous) {
                delta *= attack_over_release;
            }
            target_data[index] = previous + delta;
        }
    }, py::arg("mapped"), py::arg("old"), py::arg("target"),
       "Private Visual smoothing on trusted finite [0, 1] display rows.");

    module.def("_raise_test_error", [](const std::string& error_name) {
        if (error_name == "ConfigurationError") {
            throw sdr_core::ConfigurationError("configuration test error");
        }
        if (error_name == "BackendUnavailableError") {
            throw sdr_core::BackendUnavailableError("backend test error");
        }
        if (error_name == "DeviceError") {
            throw sdr_core::DeviceError("device test error");
        }
        if (error_name == "OperationCancelled") {
            throw sdr_core::OperationCancelled("cancellation test error");
        }
        throw sdr_core::SdrNativeError("base test error");
    });

    module.def("run_self_test", []() {
        const auto outcome = sdr_core::run_self_test();
        py::dict result;
        result["ok"] = outcome.ok;
        result["message"] = outcome.message;
        return result;
    });

    module.def(
        "sleep_without_gil",
        [](const std::uint64_t milliseconds) {
            py::gil_scoped_release release;
            sdr_core::sleep_for_milliseconds(milliseconds);
        },
        py::arg("milliseconds")
    );
}

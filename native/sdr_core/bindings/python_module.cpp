#include "aux_contracts_binding.hpp"
#include "contracts_binding.hpp"
#include "dsp_binding.hpp"
#include "lifecycle_binding.hpp"
#include "calibration_binding.hpp"
#if SDR_CORE_PLUTO_COMPILED
#include "pluto_binding.hpp"
#endif
#include "synthetic_binding.hpp"

#include "sdr_core/api.hpp"
#include "sdr_core/errors.hpp"

#include <cstdint>

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

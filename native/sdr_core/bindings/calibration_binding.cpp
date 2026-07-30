#include "calibration_binding.hpp"

#include "sdr_core/calibration.hpp"

#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {

void bind_calibration(py::module_& module) {
    py::class_<CalibrationSample>(module, "CalibrationSample")
        .def_readonly("correction_db", &CalibrationSample::correction_db)
        .def_readonly("uncertainty_db", &CalibrationSample::uncertainty_db)
        .def_property_readonly("status", [](const CalibrationSample& value) {
            return std::string(to_wire(value.status));
        });
    py::class_<PreparedCalibration>(module, "PreparedCalibration")
        .def_property_readonly("status", [](const PreparedCalibration& value) {
            return std::string(to_wire(value.status));
        })
        .def_readonly("correction_db", &PreparedCalibration::correction_db)
        .def_readonly("uncertainty_db", &PreparedCalibration::uncertainty_db);
    py::class_<CalibrationCurve>(module, "CalibrationCurve")
        .def(
            py::init<std::vector<double>, std::vector<double>, std::vector<double>>(),
            py::arg("frequencies_hz"),
            py::arg("correction_db"),
            py::arg("uncertainty_db")
        )
        .def(
            "evaluate",
            &CalibrationCurve::evaluate,
            py::arg("frequency_hz"),
            py::arg("allow_extrapolation") = false
        )
        .def(
            "prepare",
            &CalibrationCurve::prepare,
            py::arg("frequencies_hz"),
            py::arg("allow_extrapolation") = false
        )
        .def_property_readonly("frequencies_hz", &CalibrationCurve::frequencies_hz);
}

}  // namespace sdr_core::python

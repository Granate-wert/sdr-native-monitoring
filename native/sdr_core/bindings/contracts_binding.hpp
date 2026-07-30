#pragma once

#include <pybind11/pybind11.h>

namespace sdr_core::python {

void bind_contracts(pybind11::module_& module);

}  // namespace sdr_core::python

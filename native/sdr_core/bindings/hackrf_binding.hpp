#pragma once

#include <pybind11/pybind11.h>

namespace sdr_core::python {

// Coarse R11-L control-plane boundary. It is intentionally separate from the
// Pluto binding so the canonical extension can expose no device-family SDK
// type and no Python-side acquisition constructor.
void bind_hackrf(pybind11::module_& module);

}  // namespace sdr_core::python

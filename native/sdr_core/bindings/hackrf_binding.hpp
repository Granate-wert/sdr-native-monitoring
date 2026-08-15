#pragma once

#include <pybind11/pybind11.h>

namespace sdr_core::python {

// Coarse R11-L control-plane boundary. It is intentionally separate from the
// Pluto binding so the canonical extension can expose no device-family SDK
// type and no Python-side acquisition constructor.
void bind_hackrf(pybind11::module_& module);

// R11-N is defined in a separate translation unit and linked only by the
// explicit official-libhackrf build. Ordinary extensions never call it.
void bind_hackrf_factory(pybind11::module_& module);

}  // namespace sdr_core::python

#pragma once

#include <pybind11/pybind11.h>

namespace sdr_core::python {

// Coarse R11-L control-plane boundary. It is intentionally separate from the
// Pluto binding so the canonical extension can expose no device-family SDK
// type and no Python-side acquisition constructor.
void bind_hackrf(pybind11::module_& module);

// R11-N lives in a separate translation unit and is compiled only into an
// explicit official-libhackrf build. Keeping this declaration separate
// preserves the R11-L control facade's no-device source boundary.
void bind_hackrf_factory(pybind11::module_& module);

}  // namespace sdr_core::python

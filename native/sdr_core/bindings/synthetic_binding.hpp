#pragma once

namespace pybind11 {
class module_;
}

namespace sdr_core::python {

void bind_synthetic(pybind11::module_& module);

}  // namespace sdr_core::python

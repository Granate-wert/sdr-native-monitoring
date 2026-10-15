#pragma once

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include "sdr_core/persistence.hpp"

#include <cstdint>
#include <limits>
#include <memory>
#include <type_traits>
#include <utility>
#include <vector>

namespace sdr_core::python {

namespace py = pybind11;

class PersistenceStorage final {
public:
    PersistenceStorage(std::shared_ptr<const std::vector<float>> density,
                       std::shared_ptr<const std::vector<double>> frequencies,
                       std::uint32_t rows, std::uint32_t columns)
        : density_(std::move(density)), frequencies_(std::move(frequencies)),
          rows_(rows), columns_(columns) {}

    [[nodiscard]] const std::shared_ptr<const std::vector<float>>& density() const noexcept {
        return density_;
    }
    [[nodiscard]] const std::shared_ptr<const std::vector<double>>& frequencies() const noexcept {
        return frequencies_;
    }
    [[nodiscard]] std::uint32_t rows() const noexcept { return rows_; }
    [[nodiscard]] std::uint32_t columns() const noexcept { return columns_; }

    [[nodiscard]] bool valid() const noexcept {
        if (!density_ || !frequencies_ || rows_ == 0 || columns_ == 0) return false;
        const auto rows = static_cast<std::uint64_t>(rows_);
        const auto columns = static_cast<std::uint64_t>(columns_);
        return rows <= std::numeric_limits<std::uint64_t>::max() / columns &&
            rows * columns == density_->size() && columns == frequencies_->size() &&
            rows * columns <= static_cast<std::uint64_t>(std::numeric_limits<py::ssize_t>::max()) /
                sizeof(float) && columns <= static_cast<std::uint64_t>(std::numeric_limits<py::ssize_t>::max()) /
                sizeof(double);
    }

private:
    std::shared_ptr<const std::vector<float>> density_;
    std::shared_ptr<const std::vector<double>> frequencies_;
    std::uint32_t rows_{};
    std::uint32_t columns_{};
};

template <typename T>
class PersistenceVectorBuffer final {
public:
    PersistenceVectorBuffer(std::shared_ptr<const std::vector<T>> values,
                            std::uint32_t rows, std::uint32_t columns)
        : values_(std::move(values)), rows_(rows), columns_(columns) {}

    [[nodiscard]] py::buffer_info buffer() const {
        if (!valid()) throw py::buffer_error("persistence buffer is invalid");
        const auto* data = static_cast<const void*>(values_->data());
        if constexpr (std::is_same_v<T, float>) {
            return py::buffer_info(const_cast<void*>(data), sizeof(float), py::format_descriptor<float>::format(),
                2, std::vector<py::ssize_t>{static_cast<py::ssize_t>(rows_),
                    static_cast<py::ssize_t>(columns_)},
                std::vector<py::ssize_t>{static_cast<py::ssize_t>(columns_ * sizeof(float)),
                    static_cast<py::ssize_t>(sizeof(float))}, true);
        } else {
            return py::buffer_info(const_cast<void*>(data), sizeof(double), py::format_descriptor<double>::format(),
                1, std::vector<py::ssize_t>{static_cast<py::ssize_t>(columns_)},
                std::vector<py::ssize_t>{static_cast<py::ssize_t>(sizeof(double))}, true);
        }
    }

    [[nodiscard]] const std::shared_ptr<const std::vector<T>>& values() const noexcept { return values_; }
    [[nodiscard]] std::uint32_t rows() const noexcept { return rows_; }
    [[nodiscard]] std::uint32_t columns() const noexcept { return columns_; }
    [[nodiscard]] bool valid() const noexcept {
        if (!values_ || rows_ == 0 || columns_ == 0) return false;
        if constexpr (std::is_same_v<T, float>) {
            return static_cast<std::uint64_t>(rows_) * columns_ == values_->size();
        }
        return columns_ == values_->size();
    }

private:
    // Each exporter owns only the vector represented by its own buffer. In
    // particular a frequency-axis view cannot retain the full density grid.
    std::shared_ptr<const std::vector<T>> values_;
    std::uint32_t rows_{};
    std::uint32_t columns_{};
};

inline py::array persistence_storage_array(const std::shared_ptr<const PersistenceStorage>& storage,
                                           bool density) {
    if (!storage || !storage->valid()) throw py::value_error("persistence storage is invalid");
    py::object exporter;
    if (density) {
        exporter = py::cast(std::make_shared<PersistenceVectorBuffer<float>>(
            storage->density(), storage->rows(), storage->columns()));
    } else {
        exporter = py::cast(std::make_shared<PersistenceVectorBuffer<double>>(
            storage->frequencies(), storage->rows(), storage->columns()));
    }
    PyObject* raw_memoryview = PyMemoryView_FromObject(exporter.ptr());
    if (raw_memoryview == nullptr) throw py::error_already_set();
    py::object memoryview = py::reinterpret_steal<py::object>(raw_memoryview);
    return py::module_::import("numpy").attr("asarray")(memoryview).cast<py::array>();
}

template <typename T>
inline bool persistence_array_matches(py::handle value,
                                      const std::shared_ptr<const PersistenceStorage>& storage,
                                      bool density) {
    py::object ndarray_type_object = py::module_::import("numpy").attr("ndarray");
    auto* ndarray_type = reinterpret_cast<PyTypeObject*>(ndarray_type_object.ptr());
    if (!storage || !storage->valid() || Py_TYPE(value.ptr()) != ndarray_type) return false;
    py::array array = py::reinterpret_borrow<py::array>(value);
    auto* expected_type = reinterpret_cast<PyTypeObject*>(py::type::of<PersistenceVectorBuffer<T>>().ptr());
    py::object owner;
    py::object current = py::reinterpret_borrow<py::object>(array);
    for (int depth = 0; depth < 8 && !current.is_none(); ++depth) {
        if (Py_TYPE(current.ptr()) == expected_type) {
            owner = current;
            break;
        }
        if (PyMemoryView_Check(current.ptr())) {
            const auto* view = PyMemoryView_GET_BUFFER(current.ptr());
            if (view != nullptr && view->obj != nullptr && Py_TYPE(view->obj) == expected_type) {
                owner = py::reinterpret_borrow<py::object>(view->obj);
                break;
            }
            return false;
        }
        if (Py_TYPE(current.ptr()) != ndarray_type) return false;
        current = current.attr("base");
    }
    if (!owner) return false;
    const auto& buffer = owner.cast<const PersistenceVectorBuffer<T>&>();
    const void* expected_values = nullptr;
    if constexpr (std::is_same_v<T, float>) expected_values = storage->density().get();
    else expected_values = storage->frequencies().get();
    if (!buffer.valid() || buffer.values().get() != expected_values ||
        (density && (buffer.rows() != storage->rows() || buffer.columns() != storage->columns())) ||
        (!density && buffer.columns() != storage->columns()) ||
        (density != std::is_same_v<T, float>) || !array.dtype().is(py::dtype::of<T>()) ||
        !array.dtype().attr("isnative").cast<bool>() || array.writeable()) return false;
    if (density) {
        return array.ndim() == 2 && array.shape(0) == buffer.rows() &&
            array.shape(1) == buffer.columns() &&
            array.strides(0) == static_cast<py::ssize_t>(buffer.columns() * sizeof(float)) &&
            array.strides(1) == static_cast<py::ssize_t>(sizeof(float)) &&
            array.data() == storage->density()->data();
    }
    return array.ndim() == 1 && array.shape(0) == buffer.columns() &&
        array.strides(0) == static_cast<py::ssize_t>(sizeof(double)) &&
        array.data() == storage->frequencies()->data();
}

inline void bind_persistence_storage(py::module_& module) {
    py::class_<PersistenceStorage, std::shared_ptr<PersistenceStorage>>(module, "PersistenceStorage")
        .def("density", [](const std::shared_ptr<PersistenceStorage>& value) {
            return persistence_storage_array(value, true);
        })
        .def("frequencies", [](const std::shared_ptr<PersistenceStorage>& value) {
            return persistence_storage_array(value, false);
        })
        .def("matches", [](const std::shared_ptr<PersistenceStorage>& value,
                            py::handle density, py::handle frequencies) {
            auto immutable = std::shared_ptr<const PersistenceStorage>(value);
            return persistence_array_matches<float>(density, immutable, true) &&
                persistence_array_matches<double>(frequencies, immutable, false);
        });
    py::class_<PersistenceVectorBuffer<float>, std::shared_ptr<PersistenceVectorBuffer<float>>>(
        module, "_PersistenceFloatBuffer", py::buffer_protocol())
        .def_buffer([](const PersistenceVectorBuffer<float>& value) { return value.buffer(); });
    py::class_<PersistenceVectorBuffer<double>, std::shared_ptr<PersistenceVectorBuffer<double>>>(
        module, "_PersistenceDoubleBuffer", py::buffer_protocol())
        .def_buffer([](const PersistenceVectorBuffer<double>& value) { return value.buffer(); });
    module.attr("PERSISTENCE_IMMUTABLE_STORAGE_PROTOCOL_VERSION") = 1;
    module.def("_persistence_storage_builtin_type_probe", [](py::handle snapshot) {
        auto* snapshot_type = reinterpret_cast<PyTypeObject*>(
            py::type::of<sdr_core::PersistenceSnapshot>().ptr());
        return Py_TYPE(snapshot.ptr()) == snapshot_type;
    });
}

}  // namespace sdr_core::python

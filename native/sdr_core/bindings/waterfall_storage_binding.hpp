#pragma once

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace sdr_core::python {
namespace py = pybind11;

namespace waterfall_storage_detail {

inline constexpr std::size_t maximum_rows = 4096;
inline constexpr std::size_t maximum_columns = 2048;

inline std::atomic<std::uint64_t> next_allocation_id{1};

inline std::uint64_t allocate_id() {
    auto current = next_allocation_id.load(std::memory_order_relaxed);
    while (true) {
        if (current == std::numeric_limits<std::uint64_t>::max()) {
            throw std::overflow_error("waterfall allocation identity exhausted");
        }
        if (next_allocation_id.compare_exchange_weak(
                current, current + 1, std::memory_order_relaxed, std::memory_order_relaxed)) {
            return current;
        }
    }
}

inline std::size_t strict_size(py::handle value, const char* name) {
    if (PyBool_Check(value.ptr()) || !PyLong_CheckExact(value.ptr())) {
        throw py::type_error(std::string(name) + " must be an exact builtin integer, not bool");
    }
    const long long converted = PyLong_AsLongLong(value.ptr());
    if (converted == -1 && PyErr_Occurred()) {
        throw py::error_already_set();
    }
    if (converted < 0) {
        throw py::value_error(std::string(name) + " must be positive");
    }
    if (static_cast<unsigned long long>(converted)
            > static_cast<unsigned long long>(std::numeric_limits<std::size_t>::max())) {
        throw std::overflow_error(std::string(name) + " exceeds native size range");
    }
    return static_cast<std::size_t>(converted);
}

inline std::shared_ptr<std::vector<float>> allocate_floats(std::size_t count) {
    return std::make_shared<std::vector<float>>(count, 0.0F);
}

inline std::shared_ptr<std::vector<std::uint64_t>> allocate_revisions(std::size_t count) {
    return std::make_shared<std::vector<std::uint64_t>>(count, 0);
}

}  // namespace waterfall_storage_detail

class WaterfallFloatBuffer {
public:
    WaterfallFloatBuffer(std::shared_ptr<std::vector<float>> values, std::uint64_t id)
        : values_(std::move(values)), allocation_id_(id) {}

    std::size_t allocation_nbytes() const noexcept {
        return values_->size() * sizeof(float);
    }

    std::uint64_t allocation_id() const noexcept {
        return allocation_id_;
    }

    py::buffer_info buffer() const {
        return py::buffer_info(
            const_cast<float*>(values_->data()), sizeof(float), py::format_descriptor<float>::format(),
            2,
            {static_cast<py::ssize_t>(rows_), static_cast<py::ssize_t>(columns_)},
            {static_cast<py::ssize_t>(columns_ * sizeof(float)), static_cast<py::ssize_t>(sizeof(float))},
            true
        );
    }

    void set_shape(std::size_t rows, std::size_t columns) noexcept {
        rows_ = rows;
        columns_ = columns;
    }

private:
    std::shared_ptr<std::vector<float>> values_;
    std::uint64_t allocation_id_;
    std::size_t rows_ = 0;
    std::size_t columns_ = 0;
};

class WaterfallRevisionBuffer {
public:
    WaterfallRevisionBuffer(std::shared_ptr<std::vector<std::uint64_t>> values, std::uint64_t id)
        : values_(std::move(values)), allocation_id_(id) {}

    std::size_t allocation_nbytes() const noexcept {
        return values_->size() * sizeof(std::uint64_t);
    }

    std::uint64_t allocation_id() const noexcept {
        return allocation_id_;
    }

    py::buffer_info buffer() const {
        return py::buffer_info(
            const_cast<std::uint64_t*>(values_->data()), sizeof(std::uint64_t),
            py::format_descriptor<std::uint64_t>::format(), 1,
            {static_cast<py::ssize_t>(values_->size())},
            {static_cast<py::ssize_t>(sizeof(std::uint64_t))}, true
        );
    }

private:
    std::shared_ptr<std::vector<std::uint64_t>> values_;
    std::uint64_t allocation_id_;
};

class WaterfallProtectedStorage {
public:
    WaterfallProtectedStorage(py::object rows_value, py::object columns_value)
        : creator_thread_(current_python_thread()), creator_native_thread_(std::this_thread::get_id()),
          ndarray_type_(py::module_::import("numpy").attr("ndarray")) {
        const auto rows = waterfall_storage_detail::strict_size(rows_value, "rows");
        const auto columns = waterfall_storage_detail::strict_size(columns_value, "columns");
        if (rows < 1 || rows > waterfall_storage_detail::maximum_rows
                || columns < 1 || columns > waterfall_storage_detail::maximum_columns) {
            throw py::value_error("waterfall dimensions are outside the protected storage bounds");
        }
        if (rows > std::numeric_limits<std::size_t>::max() / columns) {
            throw std::overflow_error("waterfall storage shape overflow");
        }
        const std::size_t cells = rows * columns;
        if (cells > std::numeric_limits<std::size_t>::max() / sizeof(float)
                || rows > std::numeric_limits<std::size_t>::max() / sizeof(std::uint64_t)) {
            throw std::overflow_error("waterfall storage byte count overflow");
        }
        const std::size_t float_bytes = cells * sizeof(float);
        const std::size_t revision_bytes = rows * sizeof(std::uint64_t);
        if (float_bytes > std::numeric_limits<std::size_t>::max() - revision_bytes) {
            throw std::overflow_error("waterfall storage total byte count overflow");
        }

        rows_ = rows;
        columns_ = columns;
        storage_bytes_ = float_bytes + revision_bytes;
        float_allocation_id_ = waterfall_storage_detail::allocate_id();
        revision_allocation_id_ = waterfall_storage_detail::allocate_id();
        data_ = waterfall_storage_detail::allocate_floats(cells);
        row_revisions_ = waterfall_storage_detail::allocate_revisions(rows);
    }

    py::array data() const {
        require_creator_thread();
        WaterfallFloatBuffer exporter(data_, float_allocation_id_);
        exporter.set_shape(rows_, columns_);
        return py::module_::import("numpy").attr("asarray")(py::cast(std::move(exporter))).cast<py::array>();
    }

    py::array row_revisions() const {
        require_creator_thread();
        WaterfallRevisionBuffer exporter(row_revisions_, revision_allocation_id_);
        return py::module_::import("numpy").attr("asarray")(py::cast(std::move(exporter))).cast<py::array>();
    }

    std::uint64_t revision() const {
        require_creator_thread();
        return revision_;
    }

    std::size_t storage_bytes() const noexcept {
        return storage_bytes_;
    }

    void write_row(py::object index_value, py::object values_value) {
        require_creator_thread();
        const std::size_t index = waterfall_storage_detail::strict_size(index_value, "row index");
        if (index >= rows_) {
            throw py::index_error("waterfall row index is out of range");
        }
        if (Py_TYPE(values_value.ptr()) != reinterpret_cast<PyTypeObject*>(ndarray_type_.ptr())) {
            throw py::type_error("waterfall row must be a plain NumPy ndarray");
        }
        auto values = py::reinterpret_borrow<py::array>(values_value);
        if (!values.dtype().is(py::dtype::of<float>()) || values.ndim() != 1
                || static_cast<std::size_t>(values.shape(0)) != columns_
                || (values.flags() & py::array::c_style) == 0) {
            throw py::value_error("waterfall row requires exact native float32, 1D width, C-contiguous data");
        }
        if (revision_ == std::numeric_limits<std::uint64_t>::max()) {
            throw std::overflow_error("waterfall revision exhausted");
        }

        const auto* source = static_cast<const float*>(values.data());
        float* destination = data_->data() + index * columns_;
        std::memmove(destination, source, columns_ * sizeof(float));
        ++revision_;
        (*row_revisions_)[index] = revision_;
    }

    void clear() {
        require_creator_thread();
        if (revision_ == std::numeric_limits<std::uint64_t>::max()) {
            throw std::overflow_error("waterfall revision exhausted");
        }
        const auto next_revision = revision_ + 1;
        std::fill(data_->begin(), data_->end(), 0.0F);
        revision_ = next_revision;
        std::fill(row_revisions_->begin(), row_revisions_->end(), revision_);
    }

private:
    static py::object current_python_thread() {
        return py::module_::import("threading").attr("current_thread")();
    }

    void require_creator_thread() const {
        if (PyGILState_Check() == 0) {
            throw std::runtime_error("waterfall storage requires the Python GIL");
        }
        if (std::this_thread::get_id() != creator_native_thread_ || !current_python_thread().is(creator_thread_)) {
            throw std::runtime_error("waterfall storage is restricted to its creator thread");
        }
    }

    std::shared_ptr<std::vector<float>> data_;
    std::shared_ptr<std::vector<std::uint64_t>> row_revisions_;
    py::object creator_thread_;
    std::thread::id creator_native_thread_;
    py::object ndarray_type_;
    std::size_t rows_ = 0;
    std::size_t columns_ = 0;
    std::size_t storage_bytes_ = 0;
    std::uint64_t float_allocation_id_ = 0;
    std::uint64_t revision_allocation_id_ = 0;
    std::uint64_t revision_ = 0;
};

inline void bind_waterfall_protected_storage(py::module_& module) {
    py::class_<WaterfallFloatBuffer>(module, "_WaterfallFloatBuffer", py::buffer_protocol())
        .def_buffer([](const WaterfallFloatBuffer& value) { return value.buffer(); })
        .def_property_readonly("allocation_nbytes", &WaterfallFloatBuffer::allocation_nbytes,
                               "Full live float allocation payload bytes.")
        .def_property_readonly("allocation_id", &WaterfallFloatBuffer::allocation_id,
                               "Stable identity for this live float allocation.");
    py::class_<WaterfallRevisionBuffer>(module, "_WaterfallRevisionBuffer", py::buffer_protocol())
        .def_buffer([](const WaterfallRevisionBuffer& value) { return value.buffer(); })
        .def_property_readonly("allocation_nbytes", &WaterfallRevisionBuffer::allocation_nbytes,
                               "Full live revision allocation payload bytes.")
        .def_property_readonly("allocation_id", &WaterfallRevisionBuffer::allocation_id,
                               "Stable identity for this live revision allocation.");
    py::class_<WaterfallProtectedStorage>(
        module, "_WaterfallProtectedStorage",
        "Controlled-mutable storage with live readonly exports; not an immutable snapshot. "
        "Mutations and live reads are restricted to the creator thread and must be serialized."
    )
        .def(py::init<py::object, py::object>(), py::arg("rows"), py::arg("columns"))
        .def_property_readonly("data", &WaterfallProtectedStorage::data,
            "Live readonly 2D float32 view. Only read serialized on the creator thread; "
            "sanctioned writes remain visible through older exports.")
        .def_property_readonly("row_revisions", &WaterfallProtectedStorage::row_revisions,
            "Live readonly uint64 row revisions with the same serialized-reader contract.")
        .def_property_readonly("revision", &WaterfallProtectedStorage::revision)
        .def_property_readonly("storage_bytes", &WaterfallProtectedStorage::storage_bytes)
        .def("write_row", &WaterfallProtectedStorage::write_row,
             py::arg("index"), py::arg("values"),
             "Copy exact float32 row bits and commit its revision on the creator thread.")
        .def("clear", &WaterfallProtectedStorage::clear,
             "Zero all live rows and commit one revision on the creator thread.");
    module.attr("WATERFALL_PROTECTED_STORAGE_PROTOCOL_VERSION") = 1;
    module.attr("WATERFALL_PROTECTED_ACCOUNTING_PROTOCOL_VERSION") = 1;
    module.def("waterfall_storage_types", []() {
        return py::make_tuple(py::type::of<WaterfallProtectedStorage>(),
                              py::type::of<WaterfallFloatBuffer>(),
                              py::type::of<WaterfallRevisionBuffer>());
    });
}

}  // namespace sdr_core::python

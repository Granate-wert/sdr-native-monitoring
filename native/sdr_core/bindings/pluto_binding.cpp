#include "pluto_binding.hpp"

#include "sdr_pluto/fixed_band_engine.hpp"
#include "sdr_pluto/pluto_backend.hpp"

#include <algorithm>
#include <memory>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {

void bind_pluto(py::module_& module) {
    py::class_<sdr_pluto::RuntimeInfo>(module, "PlutoRuntimeInfo")
        .def_readonly("available", &sdr_pluto::RuntimeInfo::available)
        .def_readonly("library_path", &sdr_pluto::RuntimeInfo::library_path)
        .def_readonly("major", &sdr_pluto::RuntimeInfo::major)
        .def_readonly("minor", &sdr_pluto::RuntimeInfo::minor)
        .def_readonly("git_tag", &sdr_pluto::RuntimeInfo::git_tag)
        .def_readonly("backends", &sdr_pluto::RuntimeInfo::backends)
        .def_readonly("error", &sdr_pluto::RuntimeInfo::error);

    py::class_<sdr_pluto::ContextInfo>(module, "PlutoContextInfo")
        .def_readonly("uri", &sdr_pluto::ContextInfo::uri)
        .def_readonly("description", &sdr_pluto::ContextInfo::description);

    py::class_<sdr_pluto::ContextProbe>(module, "PlutoContextProbe")
        .def_readonly("uri", &sdr_pluto::ContextProbe::uri)
        .def_readonly("context_name", &sdr_pluto::ContextProbe::context_name)
        .def_readonly("description", &sdr_pluto::ContextProbe::description)
        .def_readonly("backend_major", &sdr_pluto::ContextProbe::backend_major)
        .def_readonly("backend_minor", &sdr_pluto::ContextProbe::backend_minor)
        .def_readonly("backend_tag", &sdr_pluto::ContextProbe::backend_tag)
        .def_readonly("model", &sdr_pluto::ContextProbe::model)
        .def_readonly("serial", &sdr_pluto::ContextProbe::serial)
        .def_readonly("firmware", &sdr_pluto::ContextProbe::firmware)
        .def_readonly("device_ids", &sdr_pluto::ContextProbe::device_ids)
        .def_readonly("phy_device_id", &sdr_pluto::ContextProbe::phy_device_id)
        .def_readonly("rx_stream_device_id", &sdr_pluto::ContextProbe::rx_stream_device_id);

    py::class_<sdr_pluto::SampleLayout>(module, "PlutoSampleLayout")
        .def_readonly("storage_bits", &sdr_pluto::SampleLayout::storage_bits)
        .def_readonly("significant_bits", &sdr_pluto::SampleLayout::significant_bits)
        .def_readonly("shift", &sdr_pluto::SampleLayout::shift)
        .def_readonly("is_signed", &sdr_pluto::SampleLayout::is_signed)
        .def_readonly("is_big_endian", &sdr_pluto::SampleLayout::is_big_endian)
        .def_readonly("repeat", &sdr_pluto::SampleLayout::repeat)
        .def_readonly("stride_bytes", &sdr_pluto::SampleLayout::stride_bytes)
        .def_readonly("output_format", &sdr_pluto::SampleLayout::output_format);

    py::class_<sdr_pluto::AppliedConfig>(module, "PlutoAppliedConfig")
        .def_readonly("requested", &sdr_pluto::AppliedConfig::requested)
        .def_readonly("center_frequency_hz", &sdr_pluto::AppliedConfig::center_frequency_hz)
        .def_readonly("sample_rate_hz", &sdr_pluto::AppliedConfig::sample_rate_hz)
        .def_readonly("analog_bandwidth_hz", &sdr_pluto::AppliedConfig::analog_bandwidth_hz)
        .def_readonly("gain_mode", &sdr_pluto::AppliedConfig::gain_mode)
        .def_readonly("manual_gain_db", &sdr_pluto::AppliedConfig::manual_gain_db)
        .def_readonly("config_generation", &sdr_pluto::AppliedConfig::config_generation)
        .def_readonly("sample_layout", &sdr_pluto::AppliedConfig::sample_layout);

    py::class_<sdr_pluto::StreamMetrics>(module, "PlutoStreamMetrics")
        .def_readonly("blocks_received", &sdr_pluto::StreamMetrics::blocks_received)
        .def_readonly("samples_received", &sdr_pluto::StreamMetrics::samples_received)
        .def_readonly("short_reads", &sdr_pluto::StreamMetrics::short_reads)
        .def_readonly("refill_errors", &sdr_pluto::StreamMetrics::refill_errors)
        .def_readonly("output_pool_exhaustions", &sdr_pluto::StreamMetrics::output_pool_exhaustions)
        .def_readonly("output_blocks_dropped", &sdr_pluto::StreamMetrics::output_blocks_dropped)
        .def_readonly("estimated_dropped_samples", &sdr_pluto::StreamMetrics::estimated_dropped_samples);

    py::class_<sdr_core::PersistenceSnapshot>(module, "PersistenceSnapshot")
        .def_readonly("update_sequence", &sdr_core::PersistenceSnapshot::update_sequence)
        .def_readonly("timestamp_ns", &sdr_core::PersistenceSnapshot::timestamp_ns)
        .def_readonly("source_frame_sequence", &sdr_core::PersistenceSnapshot::source_frame_sequence)
        .def_readonly("power_min_db", &sdr_core::PersistenceSnapshot::power_min_db)
        .def_readonly("power_max_db", &sdr_core::PersistenceSnapshot::power_max_db)
        .def_readonly("power_bins", &sdr_core::PersistenceSnapshot::power_bins)
        .def_readonly("frequency_bins", &sdr_core::PersistenceSnapshot::frequency_bins)
        .def_readonly("processed_frames", &sdr_core::PersistenceSnapshot::processed_frames)
        .def_readonly("exponential_decay", &sdr_core::PersistenceSnapshot::exponential_decay)
        .def_property_readonly("frequencies_hz", [](const sdr_core::PersistenceSnapshot& value) {
            if (!value.frequencies_hz) {
                return py::array_t<double>();
            }
            py::array_t<double> result(static_cast<py::ssize_t>(value.frequencies_hz->size()));
            std::copy(value.frequencies_hz->begin(), value.frequencies_hz->end(), result.mutable_data());
            return result;
        })
        .def_property_readonly("density", [](const sdr_core::PersistenceSnapshot& value) {
            if (!value.density) {
                return py::array_t<float>();
            }
            py::array_t<float> result(static_cast<py::ssize_t>(value.density->size()));
            std::copy(value.density->begin(), value.density->end(), result.mutable_data());
            return result;
        });

    py::class_<sdr_pluto::FixedBandConfig>(module, "FixedBandConfig")
        .def(py::init([](
            const DeviceConfig& device,
            const DspConfig& dsp,
            const ComputeBackendKind backend,
            const bool allow_runtime_fallback,
            const std::uint32_t acquisition_queue_capacity,
            const OverflowPolicy acquisition_overflow,
            const std::uint32_t spectrum_queue_capacity,
            const std::uint32_t event_queue_capacity,
            const double snapshot_rate_hz,
            const std::uint32_t discard_blocks_after_start,
            const bool dc_removal_block_mean,
            const PersistenceConfig& persistence
        ) {
            sdr_pluto::FixedBandConfig result;
            result.device = device;
            result.dsp = dsp;
            result.backend = backend;
            result.allow_runtime_fallback = allow_runtime_fallback;
            result.acquisition_queue_capacity = acquisition_queue_capacity;
            result.acquisition_overflow = acquisition_overflow;
            result.spectrum_queue_capacity = spectrum_queue_capacity;
            result.event_queue_capacity = event_queue_capacity;
            result.snapshot_rate_hz = snapshot_rate_hz;
            result.discard_blocks_after_start = discard_blocks_after_start;
            result.dc_removal_block_mean = dc_removal_block_mean;
            result.persistence = persistence;
            sdr_pluto::validate(result);
            return result;
        }),
            py::arg("device"),
            py::arg("dsp"),
            py::arg("backend") = ComputeBackendKind::Auto,
            py::arg("allow_runtime_fallback") = true,
            py::arg("acquisition_queue_capacity") = 16U,
            py::arg("acquisition_overflow") = OverflowPolicy::DropNewest,
            py::arg("spectrum_queue_capacity") = 4U,
            py::arg("event_queue_capacity") = 64U,
            py::arg("snapshot_rate_hz") = 60.0,
            py::arg("discard_blocks_after_start") = 2U,
            py::arg("dc_removal_block_mean") = false,
            py::arg("persistence") = PersistenceConfig{}
        )
        .def_readonly("device", &sdr_pluto::FixedBandConfig::device)
        .def_readonly("dsp", &sdr_pluto::FixedBandConfig::dsp)
        .def_readonly("persistence", &sdr_pluto::FixedBandConfig::persistence)
        .def_readonly("backend", &sdr_pluto::FixedBandConfig::backend)
        .def_readonly("allow_runtime_fallback", &sdr_pluto::FixedBandConfig::allow_runtime_fallback)
        .def_readonly("acquisition_queue_capacity", &sdr_pluto::FixedBandConfig::acquisition_queue_capacity)
        .def_readonly("acquisition_overflow", &sdr_pluto::FixedBandConfig::acquisition_overflow)
        .def_readonly("spectrum_queue_capacity", &sdr_pluto::FixedBandConfig::spectrum_queue_capacity)
        .def_readonly("event_queue_capacity", &sdr_pluto::FixedBandConfig::event_queue_capacity)
        .def_readonly("snapshot_rate_hz", &sdr_pluto::FixedBandConfig::snapshot_rate_hz)
        .def_readonly("discard_blocks_after_start", &sdr_pluto::FixedBandConfig::discard_blocks_after_start)
        .def_readonly("dc_removal_block_mean", &sdr_pluto::FixedBandConfig::dc_removal_block_mean)
        .def_readonly("schema_version", &sdr_pluto::FixedBandConfig::schema_version);

    py::class_<sdr_pluto::FixedBandMetrics>(module, "FixedBandMetrics")
        .def_readonly("state", &sdr_pluto::FixedBandMetrics::state)
        .def_readonly("has_error", &sdr_pluto::FixedBandMetrics::has_error)
        .def_readonly("engine", &sdr_pluto::FixedBandMetrics::engine)
        .def_readonly("device", &sdr_pluto::FixedBandMetrics::device)
        .def_readonly("acquisition_queue", &sdr_pluto::FixedBandMetrics::acquisition_queue)
        .def_readonly("spectrum_queue", &sdr_pluto::FixedBandMetrics::spectrum_queue)
        .def_readonly("persistence_queue", &sdr_pluto::FixedBandMetrics::persistence_queue)
        .def_readonly("transient_blocks_discarded", &sdr_pluto::FixedBandMetrics::transient_blocks_discarded)
        .def_readonly("transient_samples_discarded", &sdr_pluto::FixedBandMetrics::transient_samples_discarded)
        .def_readonly("spectrum_snapshots_superseded", &sdr_pluto::FixedBandMetrics::spectrum_snapshots_superseded)
        .def_readonly("persistence_snapshots_superseded", &sdr_pluto::FixedBandMetrics::persistence_snapshots_superseded)
        .def_readonly("shutdown_blocks_discarded", &sdr_pluto::FixedBandMetrics::shutdown_blocks_discarded)
        .def_readonly("shutdown_samples_discarded", &sdr_pluto::FixedBandMetrics::shutdown_samples_discarded)
        .def_readonly("expected_cancellations", &sdr_pluto::FixedBandMetrics::expected_cancellations)
        .def_readonly("diagnostic_events_lost", &sdr_pluto::FixedBandMetrics::diagnostic_events_lost)
        .def_readonly("requested_backend", &sdr_pluto::FixedBandMetrics::requested_backend)
        .def_readonly("active_backend", &sdr_pluto::FixedBandMetrics::active_backend)
        .def_readonly("backend_self_test_passed", &sdr_pluto::FixedBandMetrics::backend_self_test_passed)
        .def_readonly("backend_fallback_count", &sdr_pluto::FixedBandMetrics::backend_fallback_count)
        .def_readonly("backend_switch_count", &sdr_pluto::FixedBandMetrics::backend_switch_count)
        .def_readonly("last_backend_error", &sdr_pluto::FixedBandMetrics::last_backend_error);

    py::class_<sdr_pluto::FixedBandEngine>(module, "PlutoFixedBandEngine")
        .def(py::init<std::string, std::uint32_t>(), py::arg("uri"), py::arg("timeout_ms") = 3000U, py::call_guard<py::gil_scoped_release>())
        .def("configure", &sdr_pluto::FixedBandEngine::configure, py::arg("config"), py::call_guard<py::gil_scoped_release>())
        .def("reconfigure", &sdr_pluto::FixedBandEngine::reconfigure, py::arg("config"), py::call_guard<py::gil_scoped_release>())
        .def("start", &sdr_pluto::FixedBandEngine::start, py::call_guard<py::gil_scoped_release>())
        .def("request_stop", &sdr_pluto::FixedBandEngine::request_stop, py::call_guard<py::gil_scoped_release>())
        .def("join", &sdr_pluto::FixedBandEngine::join, py::call_guard<py::gil_scoped_release>())
        .def("stop", &sdr_pluto::FixedBandEngine::stop, py::call_guard<py::gil_scoped_release>())
        .def("disconnect", &sdr_pluto::FixedBandEngine::disconnect, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("connected", &sdr_pluto::FixedBandEngine::connected, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("streaming", &sdr_pluto::FixedBandEngine::streaming, py::call_guard<py::gil_scoped_release>())
        .def("state", &sdr_pluto::FixedBandEngine::state, py::call_guard<py::gil_scoped_release>())
        .def("config_generation", &sdr_pluto::FixedBandEngine::config_generation, py::call_guard<py::gil_scoped_release>())
        .def("config", &sdr_pluto::FixedBandEngine::config, py::call_guard<py::gil_scoped_release>())
        .def("applied_config", &sdr_pluto::FixedBandEngine::applied_config, py::call_guard<py::gil_scoped_release>())
        .def("metrics", &sdr_pluto::FixedBandEngine::metrics, py::call_guard<py::gil_scoped_release>())
        .def("poll_spectrum_frames", &sdr_pluto::FixedBandEngine::poll_spectrum_frames, py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def("poll_persistence_snapshots", &sdr_pluto::FixedBandEngine::poll_persistence_snapshots, py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def("poll_events", &sdr_pluto::FixedBandEngine::poll_events, py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>());

    py::class_<sdr_pluto::PlutoDevice>(module, "PlutoDevice")
        .def(py::init<std::string, std::uint32_t>(), py::arg("uri"), py::arg("timeout_ms") = 3000U, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("connected", &sdr_pluto::PlutoDevice::connected, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("streaming", &sdr_pluto::PlutoDevice::streaming, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("uri", &sdr_pluto::PlutoDevice::uri, py::call_guard<py::gil_scoped_release>())
        .def("probe", &sdr_pluto::PlutoDevice::probe, py::call_guard<py::gil_scoped_release>())
        .def("capabilities", &sdr_pluto::PlutoDevice::capabilities, py::call_guard<py::gil_scoped_release>())
        .def("configure", [](sdr_pluto::PlutoDevice& device, const DeviceConfig& config, const std::uint32_t output_pool_blocks) { return device.configure(config, output_pool_blocks); }, py::arg("config"), py::arg("output_pool_blocks") = 8U, py::call_guard<py::gil_scoped_release>())
        .def("applied_config", &sdr_pluto::PlutoDevice::applied_config, py::call_guard<py::gil_scoped_release>())
        .def("start_stream", &sdr_pluto::PlutoDevice::start_stream, py::call_guard<py::gil_scoped_release>())
        .def("refill", &sdr_pluto::PlutoDevice::refill, py::call_guard<py::gil_scoped_release>())
        .def("cancel", &sdr_pluto::PlutoDevice::cancel, py::call_guard<py::gil_scoped_release>())
        .def("stop_stream", &sdr_pluto::PlutoDevice::stop_stream, py::call_guard<py::gil_scoped_release>())
        .def("disconnect", &sdr_pluto::PlutoDevice::disconnect, py::call_guard<py::gil_scoped_release>())
        .def("metrics", &sdr_pluto::PlutoDevice::metrics, py::call_guard<py::gil_scoped_release>());

    module.def("pluto_runtime_info", &sdr_pluto::runtime_info, py::call_guard<py::gil_scoped_release>());
    module.def("scan_pluto_contexts", &sdr_pluto::scan_contexts, py::arg("filter") = "usb,ip", py::call_guard<py::gil_scoped_release>());
    module.def("probe_pluto_context", &sdr_pluto::probe_context, py::arg("uri"), py::arg("timeout_ms") = 3000U, py::call_guard<py::gil_scoped_release>());
}

}  // namespace sdr_core::python
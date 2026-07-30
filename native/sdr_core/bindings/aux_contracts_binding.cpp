#include "aux_contracts_binding.hpp"

#include "sdr_core/capabilities.hpp"
#include "sdr_core/metrics.hpp"

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {

void bind_auxiliary_contracts(py::module_& module) {
    py::class_<DeviceCapabilities>(module, "DeviceCapabilities")
        .def(py::init([](
                 std::string backend_id,
                 std::string device_id,
                 std::string serial,
                 std::string model,
                 std::string firmware,
                 NumericRange tuning_range_hz,
                 std::vector<NumericRange> sample_rate_ranges_hz,
                 std::vector<NumericRange> analog_bandwidth_ranges_hz,
                 NumericRange gain_range_db,
                 std::vector<GainMode> gain_modes,
                 std::vector<SampleFormat> sample_formats,
                 const bool supports_hardware_timestamps,
                 const bool supports_fastlock,
                 const bool supports_temperature,
                 const bool supports_overflow_counter,
                 const bool supports_continuous_iq,
                 const std::uint32_t schema_version
             ) {
            DeviceCapabilities result{
                std::move(backend_id),
                std::move(device_id),
                std::move(serial),
                std::move(model),
                std::move(firmware),
                std::move(tuning_range_hz),
                std::move(sample_rate_ranges_hz),
                std::move(analog_bandwidth_ranges_hz),
                std::move(gain_range_db),
                std::move(gain_modes),
                std::move(sample_formats),
                supports_hardware_timestamps,
                supports_fastlock,
                supports_temperature,
                supports_overflow_counter,
                supports_continuous_iq,
                schema_version,
            };
            validate(result);
            return result;
        }))
        .def_readonly("backend_id", &DeviceCapabilities::backend_id)
        .def_readonly("device_id", &DeviceCapabilities::device_id)
        .def_readonly("serial", &DeviceCapabilities::serial)
        .def_readonly("model", &DeviceCapabilities::model)
        .def_readonly("firmware", &DeviceCapabilities::firmware)
        .def_readonly("tuning_range_hz", &DeviceCapabilities::tuning_range_hz)
        .def_readonly("sample_rate_ranges_hz", &DeviceCapabilities::sample_rate_ranges_hz)
        .def_readonly(
            "analog_bandwidth_ranges_hz",
            &DeviceCapabilities::analog_bandwidth_ranges_hz
        )
        .def_readonly("gain_range_db", &DeviceCapabilities::gain_range_db)
        .def_readonly("gain_modes", &DeviceCapabilities::gain_modes)
        .def_readonly("sample_formats", &DeviceCapabilities::sample_formats)
        .def_readonly(
            "supports_hardware_timestamps",
            &DeviceCapabilities::supports_hardware_timestamps
        )
        .def_readonly("supports_fastlock", &DeviceCapabilities::supports_fastlock)
        .def_readonly("supports_temperature", &DeviceCapabilities::supports_temperature)
        .def_readonly(
            "supports_overflow_counter",
            &DeviceCapabilities::supports_overflow_counter
        )
        .def_readonly("supports_continuous_iq", &DeviceCapabilities::supports_continuous_iq)
        .def_readonly("schema_version", &DeviceCapabilities::schema_version);

    py::class_<EngineMetrics>(module, "EngineMetrics")
        .def(py::init([]() {
            EngineMetrics result;
            validate(result);
            return result;
        }))
        .def_readonly("iq_samples_received", &EngineMetrics::iq_samples_received)
        .def_readonly("iq_samples_dropped", &EngineMetrics::iq_samples_dropped)
        .def_readonly("iq_blocks_received", &EngineMetrics::iq_blocks_received)
        .def_readonly("iq_blocks_dropped", &EngineMetrics::iq_blocks_dropped)
        .def_readonly("fft_frames_computed", &EngineMetrics::fft_frames_computed)
        .def_readonly("fft_frames_dropped", &EngineMetrics::fft_frames_dropped)
        .def_readonly("analytical_fft_rate", &EngineMetrics::analytical_fft_rate)
        .def_readonly(
            "spectrum_snapshots_emitted",
            &EngineMetrics::spectrum_snapshots_emitted
        )
        .def_readonly("waterfall_rows_emitted", &EngineMetrics::waterfall_rows_emitted)
        .def_readonly("persistence_updates", &EngineMetrics::persistence_updates)
        .def_readonly("render_snapshots_applied", &EngineMetrics::render_snapshots_applied)
        .def_readonly("acquisition_queue_depth", &EngineMetrics::acquisition_queue_depth)
        .def_readonly("dsp_queue_depth", &EngineMetrics::dsp_queue_depth)
        .def_readonly("recorder_queue_depth", &EngineMetrics::recorder_queue_depth)
        .def_readonly("cpu_processing_ms", &EngineMetrics::cpu_processing_ms)
        .def_readonly("gpu_processing_ms", &EngineMetrics::gpu_processing_ms)
        .def_readonly("h2d_ms", &EngineMetrics::h2d_ms)
        .def_readonly("d2h_ms", &EngineMetrics::d2h_ms)
        .def_readonly("end_to_end_latency_ms", &EngineMetrics::end_to_end_latency_ms);
}

}  // namespace sdr_core::python

#include "dsp_binding.hpp"

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/events.hpp"
#include "sdr_core/recording_reprocess.hpp"
#include "sdr_core/recording_path.hpp"

#include <complex>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <memory>
#include <vector>

#include <pybind11/numpy.h>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {

namespace {

// Test/feed bridge: wraps a NumPy complex array into a ComplexFloat32Le
// IqBlock (input is cast to float32; the conversion is documented and
// covered by the golden-parity tolerances).
IqBlock block_from_numpy(
    const py::array& samples,
    const double sample_rate_hz,
    const double center_frequency_hz,
    const std::uint64_t first_sample_index
) {
    const auto info = samples.request();
    const bool is_c128 = info.format == py::format_descriptor<std::complex<double>>::format();
    const bool is_c64 = info.format == py::format_descriptor<std::complex<float>>::format();
    if (info.ndim != 1 || (!is_c128 && !is_c64)) {
        throw ConfigurationError("samples must be a 1-D complex64/complex128 array");
    }
    if (info.strides[0] != static_cast<std::ptrdiff_t>(info.itemsize)) {
        throw ConfigurationError("samples must be C-contiguous (no strided views)");
    }
    const auto count = static_cast<std::size_t>(info.shape[0]);
    if (count == 0U || count > 0xFFFFFFFFU) {
        throw ConfigurationError("samples length is out of range");
    }
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(count * 8U);
    for (std::size_t index = 0; index < count; ++index) {
        float re = 0.0F;
        float im = 0.0F;
        if (is_c128) {
            const auto& value = static_cast<const std::complex<double>*>(info.ptr)[index];
            re = static_cast<float>(value.real());
            im = static_cast<float>(value.imag());
        } else {
            const auto& value = static_cast<const std::complex<float>*>(info.ptr)[index];
            re = value.real();
            im = value.imag();
        }
        std::memcpy(bytes->data() + index * 8U, &re, sizeof(re));
        std::memcpy(bytes->data() + index * 8U + 4U, &im, sizeof(im));
    }
    IqBlock block;
    block.source_sequence = 0U;
    block.first_sample_index = first_sample_index;
    block.timestamp_ns = host_monotonic_ns();
    block.center_frequency_hz = center_frequency_hz;
    block.sample_rate_hz = sample_rate_hz;
    block.sample_format = SampleFormat::ComplexFloat32Le;
    block.sample_count = static_cast<std::uint32_t>(count);
    block.flags = QualityFlag::None;
    block.samples = std::move(bytes);
    block.config_generation = 1U;
    return block;
}

}  // namespace

void bind_dsp(py::module_& module) {
    py::class_<DspBackendMetrics>(module, "DspBackendMetrics")
        .def_readonly("fft_frames_computed", &DspBackendMetrics::fft_frames_computed)
        .def_readonly("fft_frames_dropped", &DspBackendMetrics::fft_frames_dropped)
        .def_readonly("samples_processed", &DspBackendMetrics::samples_processed)
        .def_readonly("output_pending", &DspBackendMetrics::output_pending)
        .def_readonly("requested_preference", &DspBackendMetrics::requested_preference)
        .def_readonly("active_backend", &DspBackendMetrics::active_backend)
        .def_readonly("backend_self_test_passed", &DspBackendMetrics::backend_self_test_passed)
        .def_readonly("backend_fallback_count", &DspBackendMetrics::backend_fallback_count)
        .def_readonly("backend_switch_count", &DspBackendMetrics::backend_switch_count)
        .def_readonly("last_backend_error", &DspBackendMetrics::last_backend_error)
        .def_readonly("gpu_processing_ns", &DspBackendMetrics::gpu_processing_ns)
        .def_readonly("h2d_ns", &DspBackendMetrics::h2d_ns)
        .def_readonly("d2h_ns", &DspBackendMetrics::d2h_ns);

    py::class_<BackendInfo>(module, "BackendInfo")
        .def_readonly("kind", &BackendInfo::kind)
        .def_readonly("backend_id", &BackendInfo::backend_id)
        .def_readonly("vendor", &BackendInfo::vendor)
        .def_readonly("device_name", &BackendInfo::device_name)
        .def_readonly("architecture", &BackendInfo::architecture)
        .def_readonly("driver_version", &BackendInfo::driver_version)
        .def_readonly("runtime_version", &BackendInfo::runtime_version)
        .def_readonly("fft_library", &BackendInfo::fft_library)
        .def_readonly("fft_library_version", &BackendInfo::fft_library_version)
        .def_readonly("total_memory_bytes", &BackendInfo::total_memory_bytes)
        .def_readonly("supports_fp64", &BackendInfo::supports_fp64)
        .def_readonly("supports_pinned_host", &BackendInfo::supports_pinned_host)
        .def_readonly("supports_async_copy", &BackendInfo::supports_async_copy)
        .def_readonly("validated", &BackendInfo::validated);

    py::class_<BackendAvailability>(module, "BackendAvailability")
        .def_readonly("compiled", &BackendAvailability::compiled)
        .def_readonly("runtime_present", &BackendAvailability::runtime_present)
        .def_readonly("device_count", &BackendAvailability::device_count)
        .def_readonly("device_supported", &BackendAvailability::device_supported)
        .def_readonly("self_test_passed", &BackendAvailability::self_test_passed)
        .def_readonly("reason_code", &BackendAvailability::reason_code)
        .def_readonly("details", &BackendAvailability::details);

    py::class_<DspBackendSelectionOptions>(module, "DspBackendSelectionOptions")
        .def(py::init([](
                 const ComputeBackendKind preference,
                 const bool allow_runtime_fallback,
                 const int device_id,
                 const std::uint32_t plan_cache_capacity
             ) {
            DspBackendSelectionOptions result;
            result.preference = preference;
            result.allow_runtime_fallback = allow_runtime_fallback;
            result.device_id = device_id;
            result.plan_cache_capacity = plan_cache_capacity;
            validate(result);
            return result;
        }),
            py::arg("preference") = ComputeBackendKind::Auto,
            py::arg("allow_runtime_fallback") = true,
            py::arg("device_id") = -1,
            py::arg("plan_cache_capacity") = 8U
        )
        .def_readonly("preference", &DspBackendSelectionOptions::preference)
        .def_readonly("allow_runtime_fallback", &DspBackendSelectionOptions::allow_runtime_fallback)
        .def_readonly("device_id", &DspBackendSelectionOptions::device_id)
        .def_readonly("plan_cache_capacity", &DspBackendSelectionOptions::plan_cache_capacity);

    py::enum_<NativeIqReprocessState>(module, "NativeIqReprocessState")
        .value("READY", NativeIqReprocessState::Ready)
        .value("RUNNING", NativeIqReprocessState::Running)
        .value("COMPLETED", NativeIqReprocessState::Completed)
        .value("CANCELLED", NativeIqReprocessState::Cancelled)
        .value("FAILED", NativeIqReprocessState::Failed);

    py::class_<NativeIqReprocessProgress>(module, "NativeIqReprocessProgress")
        .def_readonly("state", &NativeIqReprocessProgress::state)
        .def_readonly("total_input_blocks", &NativeIqReprocessProgress::total_input_blocks)
        .def_readonly("processed_input_blocks", &NativeIqReprocessProgress::processed_input_blocks)
        .def_readonly("processed_input_samples", &NativeIqReprocessProgress::processed_input_samples)
        .def_readonly("written_spectrum_frames", &NativeIqReprocessProgress::written_spectrum_frames)
        .def_readonly("input_gap_boundaries", &NativeIqReprocessProgress::input_gap_boundaries)
        .def_readonly("input_gap_samples", &NativeIqReprocessProgress::input_gap_samples)
        .def_readonly("discarded_fft_frames", &NativeIqReprocessProgress::discarded_fft_frames)
        .def_readonly("backend_requested", &NativeIqReprocessProgress::backend_requested)
        .def_readonly("backend_active", &NativeIqReprocessProgress::backend_active)
        .def_readonly("output_uri", &NativeIqReprocessProgress::output_uri)
        .def_readonly("message", &NativeIqReprocessProgress::message);

    py::class_<NativeIqRecordingReprocessor,
               std::shared_ptr<NativeIqRecordingReprocessor>>(
        module, "NativeIqRecordingReprocessor"
    )
        .def(py::init([](
                 const std::string& input_uri,
                 const std::string& output_uri,
                 const DspConfig& dsp,
                 const DspBackendSelectionOptions& selection,
                 const std::uint32_t max_samples_per_push
             ) {
            return std::make_shared<NativeIqRecordingReprocessor>(
                recording_path_from_utf8(input_uri),
                recording_path_from_utf8(output_uri),
                dsp,
                selection,
                max_samples_per_push
            );
        }),
            py::arg("input_uri"),
            py::arg("output_uri"),
            py::arg("dsp"),
            py::arg("selection"),
            py::arg("max_samples_per_push") = 262144U
        )
        .def(
            "process",
            [](NativeIqRecordingReprocessor& value, const std::uint32_t max_blocks) {
                py::gil_scoped_release release;
                return value.process(max_blocks);
            },
            py::arg("max_blocks") = 8U
        )
        .def("request_cancel", &NativeIqRecordingReprocessor::request_cancel)
        .def_property_readonly("progress", &NativeIqRecordingReprocessor::progress);

    module.attr("ANALYTICAL_READY_CONTRACT_VERSION") = 1;
    py::enum_<AnalyticalReadyClock>(module, "AnalyticalReadyClock")
        .value("NativeSteady", AnalyticalReadyClock::NativeSteady);
    py::enum_<AnalyticalReadyClockState>(module, "AnalyticalReadyClockState")
        .value("Monotonic", AnalyticalReadyClockState::Monotonic)
        .value("Regressed", AnalyticalReadyClockState::Regressed);
    py::enum_<AnalyticalReadyEventKind>(module, "AnalyticalReadyEventKind")
        .value("Offered", AnalyticalReadyEventKind::Offered)
        .value("HandedOff", AnalyticalReadyEventKind::HandedOff)
        .value("ProducerSuperseded", AnalyticalReadyEventKind::ProducerSuperseded)
        .value("ProducerCancelled", AnalyticalReadyEventKind::ProducerCancelled)
        .value("OwnerForwarded", AnalyticalReadyEventKind::OwnerForwarded)
        .value("OwnerSuperseded", AnalyticalReadyEventKind::OwnerSuperseded)
        .value("OwnerCoalesced", AnalyticalReadyEventKind::OwnerCoalesced)
        .value("OwnerCancelled", AnalyticalReadyEventKind::OwnerCancelled)
        .value("OwnerCadenceSuppressed", AnalyticalReadyEventKind::OwnerCadenceSuppressed);
    py::class_<AnalyticalReadyRef>(module, "AnalyticalReadyRef")
        .def_readonly("producer_instance_id", &AnalyticalReadyRef::producer_instance_id)
        .def_readonly("offer_sequence", &AnalyticalReadyRef::offer_sequence)
        .def_readonly("config_generation", &AnalyticalReadyRef::config_generation)
        .def_readonly("ready_native_ns", &AnalyticalReadyRef::ready_native_ns)
        .def_readonly("clock", &AnalyticalReadyRef::clock)
        .def_readonly("clock_state", &AnalyticalReadyRef::clock_state);
    py::class_<AnalyticalReadyEvent>(module, "AnalyticalReadyEvent")
        .def_readonly("event_sequence", &AnalyticalReadyEvent::event_sequence)
        .def_readonly("ref", &AnalyticalReadyEvent::ref)
        .def_readonly("kind", &AnalyticalReadyEvent::kind);
    module.attr("OWNER_PRESENTATION_DISPOSITION_CONTRACT_VERSION") = 1;
    module.attr("OWNER_PRESENTATION_RELEASE_CONTRACT_VERSION") = 1;
    py::class_<OwnerPresentationSummary>(module, "OwnerPresentationSummary")
        .def_readonly("supported", &OwnerPresentationSummary::supported)
        .def_readonly("forwarded", &OwnerPresentationSummary::forwarded)
        .def_readonly("superseded", &OwnerPresentationSummary::superseded)
        .def_readonly("coalesced", &OwnerPresentationSummary::coalesced)
        .def_readonly("cancelled", &OwnerPresentationSummary::cancelled)
        .def_readonly("cadence_suppressed", &OwnerPresentationSummary::cadence_suppressed)
        .def_readonly("accounting_failures", &OwnerPresentationSummary::accounting_failures);
    py::class_<AnalyticalReadySummary>(module, "AnalyticalReadySummary")
        .def_readonly("supported", &AnalyticalReadySummary::supported)
        .def_readonly("producer_instance_id", &AnalyticalReadySummary::producer_instance_id)
        .def_readonly("offered", &AnalyticalReadySummary::offered)
        .def_readonly("handed_off", &AnalyticalReadySummary::handed_off)
        .def_readonly("producer_superseded", &AnalyticalReadySummary::producer_superseded)
        .def_readonly("producer_cancelled", &AnalyticalReadySummary::producer_cancelled)
        .def_readonly("outstanding", &AnalyticalReadySummary::outstanding)
        .def_readonly("clock_regressions", &AnalyticalReadySummary::clock_regressions)
        .def_readonly("events_generated", &AnalyticalReadySummary::events_generated)
        .def_readonly("events_drained", &AnalyticalReadySummary::events_drained)
        .def_readonly("events_lost", &AnalyticalReadySummary::events_lost)
        .def_readonly("first_lost_event_sequence", &AnalyticalReadySummary::first_lost_event_sequence)
        .def_readonly("last_lost_event_sequence", &AnalyticalReadySummary::last_lost_event_sequence)
        .def_readonly("event_capacity", &AnalyticalReadySummary::event_capacity)
        .def_readonly("events_pending", &AnalyticalReadySummary::events_pending)
        .def_readonly("event_storage_bytes", &AnalyticalReadySummary::event_storage_bytes)
        .def_readonly("presentation", &AnalyticalReadySummary::presentation)
        .def_property_readonly("owner_disposition_events", [](const AnalyticalReadySummary& s) {
            const auto& p = s.presentation;
            return p.forwarded + p.superseded + p.coalesced + p.cancelled + p.cadence_suppressed;
        });
    module.def("analytical_ready_clock_ns", &analytical_ready_clock_ns);
    module.attr("OWNER_ANALYTICAL_READY_CONTRACT_VERSION") = 2;
    py::class_<AnalyticalReadyDrain>(module, "AnalyticalReadyDrain")
        .def_readonly("events", &AnalyticalReadyDrain::events)
        .def_readonly("summary", &AnalyticalReadyDrain::summary);

    // Bound under the CPU implementation name through the replaceable
    // DspBackend interface (P05 §7).
    py::class_<DspBackend, std::shared_ptr<DspBackend>>(module, "CpuDspBackend")
        .def(py::init([]() {
            return std::shared_ptr<DspBackend>(make_cpu_dsp_backend({}));
        }))
        .def(py::init([](const std::uint32_t capacity) {
            CpuDspOptions options;
            options.analytical_event_capacity = capacity;
            return std::shared_ptr<DspBackend>(make_cpu_dsp_backend(std::move(options)));
        }), py::arg("analytical_event_capacity"))
        .def("configure", &DspBackend::configure, py::arg("config"))
        .def(
            "push_iq",
            [](DspBackend& backend, const IqBlock& block) {
                py::gil_scoped_release release;
                backend.push_iq(block);
            },
            py::arg("block")
        )
        .def(
            "push_samples",
            [](DspBackend& backend,
               const py::array& samples,
               const double sample_rate_hz,
               const double center_frequency_hz,
               const std::uint64_t first_sample_index) {
                auto block = block_from_numpy(
                    samples,
                    sample_rate_hz,
                    center_frequency_hz,
                    first_sample_index
                );
                py::gil_scoped_release release;
                backend.push_iq(block);
            },
            py::arg("samples"),
            py::arg("sample_rate_hz"),
            py::arg("center_frequency_hz"),
            py::arg("first_sample_index") = 0U
        )
        .def(
            "poll_spectrum",
            &DspBackend::poll_spectrum,
            py::arg("max_items") = 0U,
            py::arg("flush_partial_batch") = true
        )
        .def("reset", &DspBackend::reset)
        .def("metrics", &DspBackend::metrics)
        .def("analytical_ready_summary", &DspBackend::analytical_ready_summary)
        .def("drain_analytical_ready_events", &DspBackend::drain_analytical_ready,
             py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def("poll_analytical_ready_events", &DspBackend::poll_analytical_ready_events,
             py::arg("max_items") = 0U)
        .def("info", &DspBackend::info);

    module.def(
        "make_dsp_backend",
        [](const DspBackendSelectionOptions& selection) {
            return std::shared_ptr<DspBackend>(make_dsp_backend(selection, {}));
        },
        py::arg("selection")
    );
    module.def(
        "backend_availability",
        &backend_availability,
        py::arg("kind"),
        py::call_guard<py::gil_scoped_release>()
    );
    module.def(
        "run_backend_self_test",
        &run_backend_self_test,
        py::arg("kind"),
        py::call_guard<py::gil_scoped_release>()
    );
}

}  // namespace sdr_core::python

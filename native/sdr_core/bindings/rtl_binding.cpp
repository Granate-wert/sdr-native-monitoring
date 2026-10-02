#include "rtl_binding.hpp"

#include "sdr_core/errors.hpp"
#include "sdr_rtlsdr/rtl_official_port.hpp"

#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {

void bind_rtl(py::module_& module) {
    module.attr("RTLSDR_RX_CONTROL_CONTRACT_VERSION") = 1;
    module.def("rtl_process_is_quarantined", &sdr_rtlsdr::rtl_process_quarantined);
    py::class_<sdr_rtlsdr::RtlExternalFile>(module, "RtlExternalFile")
        .def(py::init([](std::string path, std::string hash) {
            return sdr_rtlsdr::RtlExternalFile{std::move(path), std::move(hash)};
        }), py::arg("absolute_utf8_path"), py::arg("sha256_hex"))
        .def_readonly("absolute_utf8_path", &sdr_rtlsdr::RtlExternalFile::absolute_utf8_path)
        .def_readonly("sha256_hex", &sdr_rtlsdr::RtlExternalFile::sha256_hex);
    py::class_<sdr_rtlsdr::RtlExternalRuntime>(module, "RtlExternalRuntime")
        .def(py::init([](sdr_rtlsdr::RtlExternalFile library,
                         std::vector<sdr_rtlsdr::RtlExternalFile> dependencies) {
            return sdr_rtlsdr::RtlExternalRuntime{std::move(library), std::move(dependencies)};
        }),
             py::arg("library"), py::arg("dependencies"));
    py::class_<sdr_rtlsdr::RtlObservedCandidate>(module, "RtlObservedCandidate")
        .def_readonly("enumeration_index", &sdr_rtlsdr::RtlObservedCandidate::enumeration_index)
        .def_readonly("manufacturer", &sdr_rtlsdr::RtlObservedCandidate::manufacturer)
        .def_readonly("product", &sdr_rtlsdr::RtlObservedCandidate::product)
        .def_readonly("serial", &sdr_rtlsdr::RtlObservedCandidate::serial)
        .def_readonly("tuner_type", &sdr_rtlsdr::RtlObservedCandidate::tuner_type)
        .def_readonly("direct_sampling", &sdr_rtlsdr::RtlObservedCandidate::direct_sampling)
        .def_readonly("offset_tuning", &sdr_rtlsdr::RtlObservedCandidate::offset_tuning);
    py::class_<sdr_rtlsdr::RtlSessionRoute>(module, "RtlSessionRoute")
        .def(py::init([](std::string manufacturer, std::string product, std::string serial,
                         std::uint32_t tuner_type, std::uint64_t selection_revision) {
            return sdr_rtlsdr::RtlSessionRoute{std::move(manufacturer), std::move(product),
                std::move(serial), tuner_type, selection_revision};
        }),
             py::arg("manufacturer"), py::arg("product"), py::arg("serial"),
             py::arg("tuner_type"), py::arg("selection_revision"))
        .def_readonly("manufacturer", &sdr_rtlsdr::RtlSessionRoute::manufacturer)
        .def_readonly("product", &sdr_rtlsdr::RtlSessionRoute::product)
        .def_readonly("serial", &sdr_rtlsdr::RtlSessionRoute::serial)
        .def_readonly("tuner_type", &sdr_rtlsdr::RtlSessionRoute::tuner_type)
        .def_readonly("selection_revision", &sdr_rtlsdr::RtlSessionRoute::selection_revision);
    py::class_<sdr_rtlsdr::RtlAcquisitionReadback>(module, "RtlAcquisitionReadback")
        .def_readonly("session_epoch", &sdr_rtlsdr::RtlAcquisitionReadback::session_epoch)
        .def_readonly("actual_sample_rate_hz", &sdr_rtlsdr::RtlAcquisitionReadback::actual_sample_rate_hz)
        .def_readonly("actual_center_hz", &sdr_rtlsdr::RtlAcquisitionReadback::actual_center_hz)
        .def_readonly("tuner_gain_readback_known", &sdr_rtlsdr::RtlAcquisitionReadback::tuner_gain_readback_known);
    py::class_<sdr_rtlsdr::RtlLatestFrame>(module, "RtlLatestFrame")
        .def_readonly("frame", &sdr_rtlsdr::RtlLatestFrame::frame)
        .def_readonly("coalesced_frames", &sdr_rtlsdr::RtlLatestFrame::coalesced_frames);
    py::class_<sdr_rtlsdr::RtlStopResult>(module, "RtlStopResult")
        .def_readonly("cancel_requested", &sdr_rtlsdr::RtlStopResult::cancel_requested)
        .def_readonly("cancel_status", &sdr_rtlsdr::RtlStopResult::cancel_status)
        .def_readonly("first_cancel_error_status", &sdr_rtlsdr::RtlStopResult::first_cancel_error_status)
        .def_readonly("reader_joined", &sdr_rtlsdr::RtlStopResult::reader_joined)
        .def_readonly("dsp_joined", &sdr_rtlsdr::RtlStopResult::dsp_joined)
        .def_readonly("close_called", &sdr_rtlsdr::RtlStopResult::close_called)
        .def_readonly("close_status", &sdr_rtlsdr::RtlStopResult::close_status)
        .def("complete", &sdr_rtlsdr::RtlStopResult::complete);
    py::class_<sdr_rtlsdr::RtlMetrics>(module, "RtlMetrics")
        .def_readonly("callbacks", &sdr_rtlsdr::RtlMetrics::callbacks)
        .def_readonly("malformed_callbacks", &sdr_rtlsdr::RtlMetrics::malformed_callbacks)
        .def_readonly("callbacks_after_stop", &sdr_rtlsdr::RtlMetrics::callbacks_after_stop)
        .def_readonly("host_input_blocks_dropped", &sdr_rtlsdr::RtlMetrics::host_input_blocks_dropped)
        .def_readonly("host_input_samples_dropped", &sdr_rtlsdr::RtlMetrics::host_input_samples_dropped)
        .def_readonly("host_loss_cardinality_unknown", &sdr_rtlsdr::RtlMetrics::host_loss_cardinality_unknown)
        .def_readonly("blocks_admitted", &sdr_rtlsdr::RtlMetrics::blocks_admitted)
        .def_readonly("samples_admitted", &sdr_rtlsdr::RtlMetrics::samples_admitted)
        .def_readonly("worker_failures", &sdr_rtlsdr::RtlMetrics::worker_failures)
        .def_readonly("reader_returned", &sdr_rtlsdr::RtlMetrics::reader_returned)
        .def_readonly("reader_return_status", &sdr_rtlsdr::RtlMetrics::reader_return_status)
        .def_readonly("reader_returned_without_stop", &sdr_rtlsdr::RtlMetrics::reader_returned_without_stop)
        .def_readonly("ready_depth", &sdr_rtlsdr::RtlMetrics::ready_depth)
        .def_readonly("ready_high_water", &sdr_rtlsdr::RtlMetrics::ready_high_water)
        .def_readonly("slots_in_use", &sdr_rtlsdr::RtlMetrics::slots_in_use)
        .def_readonly("presentation_frames_superseded", &sdr_rtlsdr::RtlMetrics::presentation_frames_superseded)
        .def_readonly("dsp", &sdr_rtlsdr::RtlMetrics::dsp);
    py::class_<sdr_rtlsdr::RtlRuntimeSession>(module, "RtlRuntimeControl")
        .def("readback", &sdr_rtlsdr::RtlRuntimeSession::readback)
        .def("drain_latest_spectrum_frame", &sdr_rtlsdr::RtlRuntimeSession::drain_latest_spectrum_frame,
             py::call_guard<py::gil_scoped_release>())
        .def("metrics", &sdr_rtlsdr::RtlRuntimeSession::metrics,
             py::call_guard<py::gil_scoped_release>())
        .def("running", &sdr_rtlsdr::RtlRuntimeSession::running)
        .def("cleanup_required", &sdr_rtlsdr::RtlRuntimeSession::cleanup_required)
        .def("stop", [](sdr_rtlsdr::RtlRuntimeSession& owner, std::uint64_t timeout_ms) {
            if (timeout_ms == 0U || timeout_ms > 5000U) {
                throw sdr_core::ConfigurationError("RTL Stop timeout must be 1..5000 ms");
            }
            py::gil_scoped_release release;
            return owner.stop(std::chrono::milliseconds(timeout_ms));
        }, py::arg("timeout_ms"));
    module.def("rtl_observe_single_candidate", [](const sdr_rtlsdr::RtlExternalRuntime& runtime) {
        py::gil_scoped_release release;
        return sdr_rtlsdr::observe_single_rtl_candidate(runtime);
    });
    module.def("rtl_enumerate_candidates", [](const sdr_rtlsdr::RtlExternalRuntime& runtime) {
        py::gil_scoped_release release;
        return sdr_rtlsdr::enumerate_rtl_candidates(runtime);
    });
    module.def("create_rtl_runtime_control", [](
        const sdr_rtlsdr::RtlExternalRuntime& runtime,
        const std::uint32_t center_hz, const std::uint32_t sample_rate_hz,
        const std::uint32_t fft_size, const std::uint32_t hop_size,
        const std::uint32_t slot_count, const std::uint32_t ready_capacity,
        const std::uint32_t dsp_output_capacity, const std::uint32_t presentation_capacity,
        const std::uint64_t configuration_generation, std::string source_id,
        const sdr_core::DetectorType detector, std::string expected_unique_serial,
        std::optional<sdr_rtlsdr::RtlSessionRoute> session_route) {
        sdr_rtlsdr::RtlProfile profile;
        profile.center_hz = center_hz;
        profile.sample_rate_hz = sample_rate_hz;
        profile.fft_size = fft_size;
        profile.hop_size = hop_size;
        profile.slot_count = slot_count;
        profile.ready_capacity = ready_capacity;
        profile.dsp_output_capacity = dsp_output_capacity;
        profile.presentation_capacity = presentation_capacity;
        profile.configuration_generation = configuration_generation;
        profile.source_id = std::move(source_id);
        profile.detector = detector;
        profile.expected_unique_serial = std::move(expected_unique_serial);
        profile.session_route = std::move(session_route);
        profile.official_unbundled_runtime = true;
        sdr_rtlsdr::validate_rtl_profile(profile);
        py::gil_scoped_release release;
        return sdr_rtlsdr::RtlRuntimeSession::start(
            sdr_rtlsdr::make_official_rtl_port(runtime), std::move(profile));
    }, py::arg("runtime"), py::arg("center_hz"), py::arg("sample_rate_hz"),
       py::arg("fft_size"), py::arg("hop_size"), py::arg("slot_count"),
       py::arg("ready_capacity"), py::arg("dsp_output_capacity"),
       py::arg("presentation_capacity"), py::arg("configuration_generation"),
       py::arg("source_id"), py::arg("detector"), py::arg("expected_unique_serial") = "",
       py::arg("session_route") = py::none());
}

}  // namespace sdr_core::python

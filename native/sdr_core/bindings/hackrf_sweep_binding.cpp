#include "hackrf_binding.hpp"

#include "sdr_core/errors.hpp"
#include "sdr_core/sweep_line_assembler.hpp"
#include "sdr_hackrf/hackrf_sweep_runtime_analysis_session.hpp"
#if defined(SDR_CORE_HACKRF_OFFICIAL_COMPILED)
#include "sdr_hackrf/hackrf_official_rx_port.hpp"
#endif

#include <array>
#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>

#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {
namespace {

constexpr std::uint64_t sweep_min_stop_timeout_ms = 1U;
constexpr std::uint64_t sweep_max_stop_timeout_ms = 5'000U;

[[nodiscard]] std::chrono::milliseconds sweep_stop_timeout(const std::uint64_t value) {
    if (value < sweep_min_stop_timeout_ms || value > sweep_max_stop_timeout_ms) {
        throw ConfigurationError("HackRF Sweep stop timeout must be 1..5000 ms");
    }
    return std::chrono::milliseconds(value);
}

[[nodiscard]] py::dict sweep_metrics(
    const sdr_hackrf::HackrfSweepRuntimeAnalysisMetrics& value
) {
    py::dict result;
    result["lifecycle_open"] = value.lifecycle_open;
    result["worker_exited"] = value.worker_exited;
    result["worker_joined"] = value.worker_joined;
    result["worker_failed"] = value.worker_failed;
    result["worker_blocks_processed"] = value.worker_blocks_processed;
    result["progress_superseded"] = value.progress_superseded;
    result["progress_cleared_by_terminal"] = value.progress_cleared_by_terminal;
    result["terminal_superseded"] = value.terminal_superseded;
    result["progress_pending"] = value.progress_pending;
    result["terminal_pending"] = value.terminal_pending;
    result["accepted_blocks"] = value.analysis.accepted_blocks;
    result["suppressed_after_gap"] = value.analysis.suppressed_after_gap;
    result["gap_events"] = value.analysis.gap_events;
    result["completed_lines"] = value.analysis.lines.completed_lines;
    result["gapped_lines"] = value.analysis.lines.gapped_lines;
    result["capacity_evicted_lines"] = value.analysis.lines.capacity_evicted_lines;
    result["pending_lines"] = value.analysis.lines.pending_lines;
    result["callbacks_seen"] = value.source.callbacks_seen;
    result["callback_bytes_seen"] = value.source.callback_bytes_seen;
    result["callback_gate_drops"] = value.source.callback_gate_drops;
    result["invalid_timestamp_callbacks"] = value.source.invalid_timestamp_callbacks;
    result["blocks_queued"] = value.source.blocks_queued;
    result["blocks_popped"] = value.source.blocks_popped;
    result["blocks_abandoned"] = value.source.blocks_abandoned;
    result["ready_full_drops"] = value.source.ready_full_drops;
    result["ready_depth"] = value.source.ready_depth;
    result["ready_high_water"] = value.source.ready_high_water;
    result["callbacks_active"] = value.source.callbacks_active;
    result["accepting_callbacks"] = value.source.accepting_callbacks;
    result["device_overrun_counter_available"] = value.source.device_overrun_counter_available;
    result["sequence"] = py::none();
    if (value.source.sequence) {
        py::dict sequence;
        sequence["transfers_seen"] = value.source.sequence->transfers_seen;
        sequence["transfers_rejected"] = value.source.sequence->transfers_rejected;
        sequence["blocks_seen"] = value.source.sequence->blocks_seen;
        sequence["blocks_admitted"] = value.source.sequence->blocks_admitted;
        sequence["invalid_markers"] = value.source.sequence->invalid_markers;
        sequence["out_of_plan"] = value.source.sequence->out_of_plan;
        sequence["awaiting_first_frequency"] = value.source.sequence->awaiting_first_frequency;
        sequence["out_of_order"] = value.source.sequence->out_of_order;
        sequence["downstream_drops"] = value.source.sequence->downstream_drops;
        sequence["known_skipped_headers"] = value.source.sequence->known_skipped_headers;
        sequence["unlocated_rejections"] = value.source.sequence->unlocated_rejections;
        sequence["scan_epoch"] = value.source.sequence->scan_epoch;
        sequence["continuity_epoch"] = value.source.sequence->continuity_epoch;
        sequence["next_plan_index"] = value.source.sequence->next_plan_index;
        sequence["synchronized"] = value.source.sequence->synchronized;
        sequence["gap_pending"] = value.source.sequence->gap_pending;
        result["sequence"] = std::move(sequence);
    }
    return result;
}

[[nodiscard]] py::dict sweep_stop_result(
    const sdr_hackrf::HackrfSweepRuntimeAnalysisStopResult& value
) {
    py::dict result;
    result["complete"] = value.complete();
    result["clean"] = value.clean();
    result["stop_rx_called"] = value.source.stop_rx_called;
    result["stop_rx_status"] = value.source.stop_rx_status;
    result["callbacks_quiescent"] = value.source.callbacks_quiescent;
    result["abandoned_blocks"] = value.source.abandoned_blocks;
    result["close_called"] = value.source.close_called;
    result["close_status"] = value.source.close_status;
    result["first_close_error"] = value.source.first_close_error;
    result["exit_called"] = value.source.exit_called;
    result["exit_status"] = value.source.exit_status;
    result["first_exit_error"] = value.source.first_exit_error;
    result["worker_joined"] = value.worker_joined;
    result["worker_failed"] = value.worker_failed;
    return result;
}

}  // namespace

void bind_hackrf_sweep(py::module_& module) {
    module.attr("HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION") = 1;
    py::class_<sdr_hackrf::HackrfSweepRuntimeAnalysisSession>(
        module, "HackrfSweepRuntimeAnalysisControl"
    )
        .def("poll_next_publication", [](sdr_hackrf::HackrfSweepRuntimeAnalysisSession& value) {
            sdr_hackrf::HackrfSweepPublication publication;
            {
                py::gil_scoped_release release;
                publication = value.poll_next_publication();
            }
            return std::visit([](auto&& frame) -> py::object {
                using T = std::decay_t<decltype(frame)>;
                if constexpr (std::is_same_v<T, std::monostate>) {
                    return py::none();
                } else {
                    return py::cast(std::forward<decltype(frame)>(frame));
                }
            }, std::move(publication));
        })
        .def("metrics", [](const sdr_hackrf::HackrfSweepRuntimeAnalysisSession& value) {
            sdr_hackrf::HackrfSweepRuntimeAnalysisMetrics metrics;
            {
                py::gil_scoped_release release;
                metrics = value.metrics();
            }
            return sweep_metrics(metrics);
        })
        .def("stop", [](sdr_hackrf::HackrfSweepRuntimeAnalysisSession& value,
                          const std::uint64_t timeout_ms) {
            const auto timeout = sweep_stop_timeout(timeout_ms);
            sdr_hackrf::HackrfSweepRuntimeAnalysisStopResult stopped;
            {
                py::gil_scoped_release release;
                stopped = value.stop(timeout);
            }
            return sweep_stop_result(stopped);
        }, py::arg("timeout_ms"));

#if defined(SDR_CORE_HACKRF_OFFICIAL_COMPILED)
    module.attr("HACKRF_SWEEP_FACTORY_CONTRACT_VERSION") = 1;
    module.def("create_hackrf_sweep_runtime_control",
        [](std::array<std::uint32_t, 4> expected_serial_words,
           std::string source_id,
           const std::uint64_t epoch,
           const std::uint32_t fft_size,
           const std::uint16_t range_start_mhz,
           const std::uint16_t range_stop_mhz,
           const std::uint32_t lna_gain_db,
           const std::uint32_t vga_gain_db) {
            // Fixed numerical policy is deliberately non-configurable at this
            // boundary. Session::start validates the entire plan/FFT/source
            // before the official port is initialized or opens any device.
            sdr_hackrf::HackrfSweepRuntimeAnalysisConfig config;
            auto& analysis = config.analysis;
            if (range_start_mhz < 1U || range_stop_mhz > 6000U ||
                range_stop_mhz <= range_start_mhz ||
                range_stop_mhz - range_start_mhz < 20U) {
                throw ConfigurationError("HackRF Sweep analysis range must fit 1..6000 MHz with >=20 MHz span");
            }
            const auto steps = (range_stop_mhz - range_start_mhz + 19U) / 20U;
            const auto hardware_stop_mhz = static_cast<std::uint16_t>(range_start_mhz + steps * 20U);
            if (static_cast<double>(hardware_stop_mhz) * 1'000'000.0 - 7'500'000.0 > 6'000'000'000.0) {
                throw ConfigurationError("HackRF rounded capture would tune outside the qualified RF envelope");
            }
            analysis.acquisition.sequence.ranges = {{range_start_mhz, hardware_stop_mhz}};
            analysis.analysis_stop_hz = static_cast<double>(range_stop_mhz) * 1'000'000.0;
            analysis.acquisition.sequence.step_width_hz = 20'000'000U;
            analysis.acquisition.sequence.offset_hz = 7'500'000U;
            analysis.acquisition.sequence.style = sdr_hackrf::HackrfSweepStyle::Interleaved;
            analysis.acquisition.sequence.max_transfer_blocks = 16U;
            analysis.acquisition.sample_rate_hz = 20'000'000.0;
            analysis.acquisition.baseband_filter_hz = 15'000'000U;
            analysis.acquisition.lna_gain_db = lna_gain_db;
            analysis.acquisition.vga_gain_db = vga_gain_db;
            analysis.acquisition.ready_capacity = 64U;
            analysis.acquisition.config_generation = epoch;
            analysis.acquisition_epoch = epoch;
            analysis.fft_size = fft_size;
            analysis.window = WindowType::Hann;
            analysis.detector = DetectorType::Sample;
            analysis.unit = SpectrumUnit::DbfsBin;
            analysis.source.source_type = SourceType::LiveIq;
            analysis.source.source_id = std::move(source_id);
            analysis.source.display_name = "HackRF Sweep";
            analysis.source.backend_id = "native.libhackrf.sweep.v1";
            auto port = sdr_hackrf::make_official_hackrf_sweep_port(expected_serial_words);
            py::gil_scoped_release release;
            return sdr_hackrf::HackrfSweepRuntimeAnalysisSession::start(
                std::move(port), std::move(config));
        },
        py::arg("expected_serial_words"), py::arg("source_id"), py::arg("epoch"),
        py::arg("fft_size"), py::arg("range_start_mhz"), py::arg("range_stop_mhz"),
        py::arg("lna_gain_db"), py::arg("vga_gain_db"));
#endif
}

}  // namespace sdr_core::python

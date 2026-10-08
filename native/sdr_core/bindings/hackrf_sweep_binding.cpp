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
#include <mutex>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {
namespace {

constexpr std::uint64_t sweep_min_stop_timeout_ms = 1U;
constexpr std::uint64_t sweep_max_stop_timeout_ms = 5'000U;

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
// Explicit in-process SDK substitute. No vendor handles or USB, and manual
// callback injection so the ORIGINAL service must observe progressive output
// before the fixture supplies the remaining headers.
struct SweepTestState {
    std::mutex mutex;
    sdr_hackrf::HackrfRxBytesCallback callback{};
    void* context{};
    bool running{};
};

class SweepTestPort final : public sdr_hackrf::HackrfSweepRuntimePort {
public:
    explicit SweepTestPort(std::shared_ptr<SweepTestState> state) : state_(std::move(state)) {}
    int initialize_library() noexcept override { return 0; }
    int open_exactly_one_hackrf_one() noexcept override { return 0; }
    std::uint32_t transfer_buffer_size() const noexcept override { return 262'144U; }
    int read_usb_api_version(std::uint16_t& version) noexcept override { version = 0x0109U; return 0; }
    int set_sample_rate(double) noexcept override { return 0; }
    int set_baseband_filter(std::uint32_t) noexcept override { return 0; }
    int set_center_frequency(std::uint64_t) noexcept override { return -1; }
    int set_rf_amplifier(bool value) noexcept override { return value ? -1 : 0; }
    int set_bias_tee(bool value) noexcept override { return value ? -1 : 0; }
    int set_lna_gain(std::uint32_t) noexcept override { return 0; }
    int set_vga_gain(std::uint32_t) noexcept override { return 0; }
    int initialize_sweep(const sdr_hackrf::HackrfSweepSequencePlan&) noexcept override { return 0; }
    int start_rx(sdr_hackrf::HackrfRxBytesCallback, void*) noexcept override { return -1; }
    int start_rx_sweep(sdr_hackrf::HackrfRxBytesCallback callback, void* context) noexcept override {
        const std::lock_guard lock(state_->mutex);
        state_->callback = callback; state_->context = context; state_->running = true; return 0;
    }
    int stop_rx() noexcept override {
        const std::lock_guard lock(state_->mutex);
        state_->running = false;
        return 0;
    }
    int close_device() noexcept override { return 0; }
    int exit_library() noexcept override { return 0; }
private:
    std::shared_ptr<SweepTestState> state_;
};
#endif

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
    // All values belong to the SAME analysis snapshot. One accepted firmware
    // block yields one FFT and two crops; GUI/publication counts are separate.
    result["iq_payload_samples_accepted"] = value.analysis.iq_payload_samples_accepted;
    result["fft_frames_computed"] = value.analysis.dsp.fft_frames_computed;
    result["fft_frames_dropped"] = value.analysis.dsp.fft_frames_dropped;
    result["dsp_samples_processed"] = value.analysis.dsp.samples_processed;
    result["dsp_output_pending"] = value.analysis.dsp.output_pending;
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
    // Additive scalar metric projection, independent of factory/RF contract1.
    module.attr("HACKRF_SWEEP_METRICS_CONTRACT_VERSION") = 1;
    // Additive optional journal contract; not RF/device capability evidence.
    module.attr("HACKRF_LAYER_CREATION_CONTRACT_VERSION") = 1;
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
        .def("drain_sweep_layer_ready_events", &sdr_hackrf::HackrfSweepRuntimeAnalysisSession::drain_sweep_layer_ready_events,
             py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
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

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
    module.def("_make_test_hackrf_sweep_runtime_control", [](std::string source_id,
            std::uint64_t epoch, std::uint32_t fft_size, std::uint16_t range_start_mhz,
            std::uint16_t range_stop_mhz, bool dc_removal_block_mean) {
        if (range_stop_mhz <= range_start_mhz || range_stop_mhz - range_start_mhz > 80U) {
            throw ConfigurationError("MOCK Sweep fixture requires a bounded <=80MHz range");
        }
        sdr_hackrf::HackrfSweepRuntimeAnalysisConfig config;
        auto& a = config.analysis;
        const auto steps = (range_stop_mhz - range_start_mhz + 19U) / 20U;
        a.acquisition.sequence.ranges = {{range_start_mhz,
            static_cast<std::uint16_t>(range_start_mhz + steps * 20U)}};
        a.acquisition.config_generation = a.acquisition_epoch = epoch;
        a.source.source_type = SourceType::LiveIq;
        a.source.source_id = std::move(source_id);
        a.source.display_name = "MOCK Sweep processing owner";
        a.source.backend_id = "native.libhackrf.sweep.v1";
        a.analysis_stop_hz = static_cast<double>(range_stop_mhz) * 1'000'000.0;
        a.fft_size = fft_size;
        a.window = WindowType::Hann; a.detector = DetectorType::Sample;
        a.unit = SpectrumUnit::DbfsBin;
        a.layer_event_capacity = 64U;
        a.dc_removal = dc_removal_block_mean ? DcRemovalMode::BlockMean : DcRemovalMode::Off;
        auto state = std::make_shared<SweepTestState>();
        std::unique_ptr<sdr_hackrf::HackrfSweepRuntimeAnalysisSession> owner;
        {
            py::gil_scoped_release release;
            owner = sdr_hackrf::HackrfSweepRuntimeAnalysisSession::start(
                std::make_unique<SweepTestPort>(state), std::move(config));
        }
        auto emit = py::cpp_function([state, range_start_mhz, steps](std::uint32_t first, std::uint32_t count) {
            const std::lock_guard lock(state->mutex);
            if (!state->running || !count || first + static_cast<std::uint64_t>(count) > steps * 2U) {
                throw ConfigurationError("MOCK Sweep injection requires active bounded plan headers");
            }
            std::vector<std::uint8_t> bytes(static_cast<std::size_t>(count) * sdr_hackrf::hackrf_sweep_block_bytes);
            for (std::uint32_t n = 0U; n < count; ++n) {
                const auto index = first + n;
                const auto base = static_cast<std::size_t>(n) * sdr_hackrf::hackrf_sweep_block_bytes;
                const std::uint64_t frequency = static_cast<std::uint64_t>(range_start_mhz) * 1'000'000ULL
                    + (index / 2U) * 20'000'000ULL + (index % 2U) * 5'000'000ULL;
                bytes[base] = bytes[base + 1U] = 0x7fU;
                for (std::size_t b = 0U; b < 8U; ++b) bytes[base + 2U + b] = static_cast<std::uint8_t>(frequency >> (8U * b));
                for (std::size_t sample = 0U; sample < sdr_hackrf::hackrf_sweep_ci8_bytes / 2U; ++sample) {
                    // Deterministic CI8 tone plus complex DC; all DSP is native.
                    const int tone = sample % 4U == 0U ? 50 : sample % 4U == 2U ? -50 : 0;
                    bytes[base + 10U + 2U * sample] = static_cast<std::uint8_t>(tone + 20);
                    bytes[base + 11U + 2U * sample] = 10U;
                }
            }
            if (state->callback(bytes, 10'000 + first, state->context) != 0) {
                throw DeviceError("MOCK Sweep callback refused bounded transfer");
            }
        });
        return py::make_tuple(py::cast(std::move(owner)), emit);
    }, py::arg("source_id"), py::arg("epoch"), py::arg("fft_size"),
       py::arg("range_start_mhz"), py::arg("range_stop_mhz"),
       py::arg("dc_removal_block_mean").noconvert() = false);
#endif

#if defined(SDR_CORE_HACKRF_OFFICIAL_COMPILED)
    module.attr("HACKRF_SWEEP_FACTORY_CONTRACT_VERSION") = 1;
    module.attr("HACKRF_SWEEP_PROCESSING_FACTORY_CONTRACT_VERSION") = 1;
    module.def("create_hackrf_sweep_runtime_control",
        [](std::array<std::uint32_t, 4> expected_serial_words,
           std::string source_id,
           const std::uint64_t epoch,
           const std::uint32_t fft_size,
           const std::uint16_t range_start_mhz,
           const std::uint16_t range_stop_mhz,
           const std::uint32_t lna_gain_db,
           const std::uint32_t vga_gain_db,
           const std::uint32_t layer_event_capacity,
           const bool dc_removal_block_mean) {
            // Numerical geometry stays pinned; the sole additive policy is
            // explicit default-OFF native BlockMean. Session::start validates
            // the entire plan/FFT/source
            // before the official port is initialized or opens any device.
            sdr_hackrf::HackrfSweepRuntimeAnalysisConfig config;
            auto& analysis = config.analysis;
            if (layer_event_capacity > sdr_core::LayerReadyJournal::max_capacity) {
                throw ConfigurationError("HackRF Sweep layer journal capacity exceeds 4096");
            }
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
            analysis.dc_removal = dc_removal_block_mean ? DcRemovalMode::BlockMean : DcRemovalMode::Off;
            analysis.layer_event_capacity = layer_event_capacity;
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
        py::arg("lna_gain_db"), py::arg("vga_gain_db"), py::arg("layer_event_capacity") = 0U,
        py::kw_only(), py::arg("dc_removal_block_mean").noconvert() = false);
#endif
}

}  // namespace sdr_core::python

#include "hackrf_binding.hpp"

#include "sdr_core/errors.hpp"
#include "sdr_hackrf/hackrf_runtime_dsp_session.hpp"

#include <array>
#include <chrono>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <utility>

#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {
namespace {

constexpr std::uint64_t r11l_min_stop_timeout_ms = 1U;
constexpr std::uint64_t r11l_max_stop_timeout_ms = 5'000U;

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
constexpr std::uint32_t r11l_max_fixture_blocks = 32U;

// Test-only in-process implementation. It has no vendor-library handle and
// uses no device or transport. It exists only so the freshly
// built canonical pybind extension can exercise the real R11-G/I/J/K path.
class R11LTestRuntime final : public sdr_hackrf::HackrfRxRuntimePort {
public:
    explicit R11LTestRuntime(const std::uint32_t blocks) : blocks_(blocks) {
        for (std::size_t index = 0U; index < transfer_.size(); ++index) {
            transfer_[index] = static_cast<std::uint8_t>((index % 31U) + 1U);
        }
    }

    int initialize_library() noexcept override { return 0; }
    int open_exactly_one_hackrf_one() noexcept override { return 0; }
    [[nodiscard]] std::uint32_t transfer_buffer_size() const noexcept override {
        return static_cast<std::uint32_t>(transfer_.size());
    }
    int set_sample_rate(double) noexcept override { return 0; }
    int set_baseband_filter(std::uint32_t) noexcept override { return 0; }
    int set_center_frequency(std::uint64_t) noexcept override { return 0; }
    int set_rf_amplifier(bool) noexcept override { return 0; }
    int set_bias_tee(bool) noexcept override { return 0; }
    int set_lna_gain(std::uint32_t) noexcept override { return 0; }
    int set_vga_gain(std::uint32_t) noexcept override { return 0; }
    int stop_rx() noexcept override { return 0; }
    int close_device() noexcept override { return 0; }
    int exit_library() noexcept override { return 0; }

    int start_rx(
        const sdr_hackrf::HackrfRxBytesCallback callback,
        void* const context
    ) noexcept override {
        if (callback == nullptr || context == nullptr) {
            return -1;
        }
        const auto transfer = std::span<const std::uint8_t>(transfer_);
        for (std::uint32_t index = 0U; index < blocks_; ++index) {
            if (callback(transfer, 1'000 + static_cast<std::int64_t>(index), context) != 0) {
                return -2;
            }
        }
        return 0;
    }

private:
    std::uint32_t blocks_{};
    std::array<std::uint8_t, 512U> transfer_{};
};

[[nodiscard]] std::unique_ptr<sdr_hackrf::HackrfRuntimeDspSession>
make_test_hackrf_runtime_dsp_control(
    const std::uint32_t blocks,
    const std::uint32_t layer_event_capacity,
    const bool dc_removal_block_mean,
    const std::uint32_t analytical_event_capacity,
    const std::uint64_t configuration_generation,
    const std::string& source_id,
    const double center_frequency_hz,
    const double sample_rate_hz,
    const std::uint32_t baseband_filter_hz
) {
    if (blocks == 0U || blocks > r11l_max_fixture_blocks) {
        throw ConfigurationError("HackRF control test fixture blocks must be 1..32");
    }

    sdr_hackrf::HackrfRuntimeDspSessionConfig config;
    config.rx.slot_count = blocks;
    config.rx.ready_capacity = blocks;
    config.rx.config_generation = configuration_generation;
    config.rx.center_frequency_hz = center_frequency_hz;
    config.rx.sample_rate_hz = sample_rate_hz;
    config.rx.baseband_filter_hz = baseband_filter_hz;
    config.processing.dsp.dsp.fft_size = 256U;
    config.processing.dsp.dsp.hop_size = 256U;
    config.processing.dsp.dsp.window = WindowType::Rectangular;
    config.processing.dsp.dsp.detector = DetectorType::Sample;
    config.processing.dsp.dsp.unit = SpectrumUnit::DbfsBin;
    config.processing.dsp.dsp.precision_mode = PrecisionMode::ReferenceF64;
    config.processing.dsp.source.source_type = SourceType::LiveIq;
    config.processing.dsp.source.source_id = source_id;
    config.processing.dsp.source.display_name = "HackRF R11-L test fixture";
    config.processing.dsp.source.backend_id = "native.libhackrf.rx.v1";
    config.processing.dsp.dsp_output_capacity = blocks;
    config.processing.dsp.presentation_capacity = blocks;
    config.processing.dsp.layer_event_capacity = layer_event_capacity;
    config.processing.dsp.analytical_event_capacity = analytical_event_capacity;
    config.processing.dsp.dc_removal = dc_removal_block_mean ? DcRemovalMode::BlockMean : DcRemovalMode::Off;
    if (layer_event_capacity) {
        config.processing.dsp.persistence.enabled = true;
        config.processing.dsp.persistence.mode = PersistenceMode::ExponentialDecay;
        config.processing.dsp.persistence.power_bins = 16U;
    }

    return sdr_hackrf::HackrfRuntimeDspSession::start(
        std::make_unique<R11LTestRuntime>(blocks),
        std::move(config)
    );
}
#endif

[[nodiscard]] std::chrono::milliseconds bounded_stop_timeout(
    const std::uint64_t timeout_ms
) {
    if (timeout_ms < r11l_min_stop_timeout_ms || timeout_ms > r11l_max_stop_timeout_ms) {
        throw ConfigurationError("HackRF control stop timeout must be 1..5000 ms");
    }
    return std::chrono::milliseconds(timeout_ms);
}

}  // namespace

void bind_hackrf(py::module_& module) {
    py::enum_<sdr_hackrf::HackrfAcquisitionDspState>(
        module, "HackrfAcquisitionDspState"
    )
        .value("RUNNING", sdr_hackrf::HackrfAcquisitionDspState::Running)
        .value("STOP_PENDING", sdr_hackrf::HackrfAcquisitionDspState::StopPending)
        .value("STOPPED", sdr_hackrf::HackrfAcquisitionDspState::Stopped)
        .value("FAILED", sdr_hackrf::HackrfAcquisitionDspState::Failed);

    py::class_<sdr_hackrf::HackrfRxIngressMetrics>(module, "HackrfRxIngressMetrics")
        .def_readonly("slot_capacity", &sdr_hackrf::HackrfRxIngressMetrics::slot_capacity)
        .def_readonly("slot_bytes", &sdr_hackrf::HackrfRxIngressMetrics::slot_bytes)
        .def_readonly("slots_in_use", &sdr_hackrf::HackrfRxIngressMetrics::slots_in_use)
        .def_readonly("slots_high_water", &sdr_hackrf::HackrfRxIngressMetrics::slots_high_water)
        .def_readonly("ready_capacity", &sdr_hackrf::HackrfRxIngressMetrics::ready_capacity)
        .def_readonly("ready_depth", &sdr_hackrf::HackrfRxIngressMetrics::ready_depth)
        .def_readonly("ready_high_water", &sdr_hackrf::HackrfRxIngressMetrics::ready_high_water)
        .def_readonly("callbacks_active", &sdr_hackrf::HackrfRxIngressMetrics::callbacks_active)
        .def_readonly("accepting_callbacks", &sdr_hackrf::HackrfRxIngressMetrics::accepting_callbacks)
        .def_readonly("callbacks_total", &sdr_hackrf::HackrfRxIngressMetrics::callbacks_total)
        .def_readonly("callback_bytes_total", &sdr_hackrf::HackrfRxIngressMetrics::callback_bytes_total)
        .def_readonly("blocks_admitted", &sdr_hackrf::HackrfRxIngressMetrics::blocks_admitted)
        .def_readonly("bytes_admitted", &sdr_hackrf::HackrfRxIngressMetrics::bytes_admitted)
        .def_readonly("samples_admitted", &sdr_hackrf::HackrfRxIngressMetrics::samples_admitted)
        .def_readonly("blocks_popped", &sdr_hackrf::HackrfRxIngressMetrics::blocks_popped)
        .def_readonly("leases_released", &sdr_hackrf::HackrfRxIngressMetrics::leases_released)
        .def_readonly("malformed_callbacks", &sdr_hackrf::HackrfRxIngressMetrics::malformed_callbacks)
        .def_readonly("short_callbacks", &sdr_hackrf::HackrfRxIngressMetrics::short_callbacks)
        .def_readonly("oversized_callbacks", &sdr_hackrf::HackrfRxIngressMetrics::oversized_callbacks)
        .def_readonly("callbacks_after_stop", &sdr_hackrf::HackrfRxIngressMetrics::callbacks_after_stop)
        .def_readonly("lock_contention_drops", &sdr_hackrf::HackrfRxIngressMetrics::lock_contention_drops)
        .def_readonly("pool_exhaustion_drops", &sdr_hackrf::HackrfRxIngressMetrics::pool_exhaustion_drops)
        .def_readonly("queue_full_drops", &sdr_hackrf::HackrfRxIngressMetrics::queue_full_drops)
        .def_readonly("abandoned_blocks", &sdr_hackrf::HackrfRxIngressMetrics::abandoned_blocks)
        .def_readonly("dropped_bytes", &sdr_hackrf::HackrfRxIngressMetrics::dropped_bytes)
        .def_readonly("dropped_samples", &sdr_hackrf::HackrfRxIngressMetrics::dropped_samples)
        .def_readonly("loss_events", &sdr_hackrf::HackrfRxIngressMetrics::loss_events)
        .def_readonly(
            "device_overrun_counter_available",
            &sdr_hackrf::HackrfRxIngressMetrics::device_overrun_counter_available
        );

    py::class_<sdr_hackrf::HackrfFixedBandDspMetrics>(
        module, "HackrfFixedBandDspMetrics"
    )
        .def_readonly("iq_blocks_processed", &sdr_hackrf::HackrfFixedBandDspMetrics::iq_blocks_processed)
        .def_readonly("iq_samples_processed", &sdr_hackrf::HackrfFixedBandDspMetrics::iq_samples_processed)
        .def_readonly("stage_timing_available", &sdr_hackrf::HackrfFixedBandDspMetrics::stage_timing_available)
        .def_readonly("locked_push_ns", &sdr_hackrf::HackrfFixedBandDspMetrics::locked_push_ns)
        .def_readonly("dsp_push_poll_ns", &sdr_hackrf::HackrfFixedBandDspMetrics::dsp_push_poll_ns)
        .def_readonly("persistence_call_ns", &sdr_hackrf::HackrfFixedBandDspMetrics::persistence_call_ns)
        .def_readonly("persistence_histogram_update_ns", &sdr_hackrf::HackrfFixedBandDspMetrics::persistence_histogram_update_ns)
        .def_readonly("persistence_snapshot_build_ns", &sdr_hackrf::HackrfFixedBandDspMetrics::persistence_snapshot_build_ns)
        .def_readonly("persistence_snapshot_count", &sdr_hackrf::HackrfFixedBandDspMetrics::persistence_snapshot_count)
        .def_readonly("publication_queue_ns", &sdr_hackrf::HackrfFixedBandDspMetrics::publication_queue_ns)
        .def_readonly(
            "source_sequence_discontinuities",
            &sdr_hackrf::HackrfFixedBandDspMetrics::source_sequence_discontinuities
        )
        .def_readonly(
            "source_sample_index_discontinuities",
            &sdr_hackrf::HackrfFixedBandDspMetrics::source_sample_index_discontinuities
        )
        .def_readonly("source_blocks_missing", &sdr_hackrf::HackrfFixedBandDspMetrics::source_blocks_missing)
        .def_readonly("source_samples_missing", &sdr_hackrf::HackrfFixedBandDspMetrics::source_samples_missing)
        .def_readonly(
            "source_timestamp_regressions",
            &sdr_hackrf::HackrfFixedBandDspMetrics::source_timestamp_regressions
        )
        .def_readonly(
            "source_estimated_timestamp_blocks",
            &sdr_hackrf::HackrfFixedBandDspMetrics::source_estimated_timestamp_blocks
        )
        .def_readonly("dsp", &sdr_hackrf::HackrfFixedBandDspMetrics::dsp)
        .def_readonly("persistence_updates", &sdr_hackrf::HackrfFixedBandDspMetrics::persistence_updates)
        .def_readonly("persistence", &sdr_hackrf::HackrfFixedBandDspMetrics::persistence)
        .def_readonly("presentation", &sdr_hackrf::HackrfFixedBandDspMetrics::presentation);

    py::class_<sdr_hackrf::HackrfAcquisitionDspMetrics>(
        module, "HackrfAcquisitionDspMetrics"
    )
        .def_readonly("state", &sdr_hackrf::HackrfAcquisitionDspMetrics::state)
        .def_readonly("worker_exited", &sdr_hackrf::HackrfAcquisitionDspMetrics::worker_exited)
        .def_readonly("worker_joined", &sdr_hackrf::HackrfAcquisitionDspMetrics::worker_joined)
        .def_readonly("worker_blocks_processed", &sdr_hackrf::HackrfAcquisitionDspMetrics::worker_blocks_processed)
        .def_readonly("worker_failures", &sdr_hackrf::HackrfAcquisitionDspMetrics::worker_failures)
        .def_readonly(
            "worker_abandoned_blocks",
            &sdr_hackrf::HackrfAcquisitionDspMetrics::worker_abandoned_blocks
        )
        .def_readonly("ingress", &sdr_hackrf::HackrfAcquisitionDspMetrics::ingress)
        .def_readonly("dsp", &sdr_hackrf::HackrfAcquisitionDspMetrics::dsp);

    py::class_<sdr_hackrf::HackrfRxQuiesceResult>(module, "HackrfRxQuiesceResult")
        .def_readonly("stop_rx_called", &sdr_hackrf::HackrfRxQuiesceResult::stop_rx_called)
        .def_readonly("stop_rx_status", &sdr_hackrf::HackrfRxQuiesceResult::stop_rx_status)
        .def_readonly("callbacks_quiescent", &sdr_hackrf::HackrfRxQuiesceResult::callbacks_quiescent)
        .def("complete", &sdr_hackrf::HackrfRxQuiesceResult::complete);

    py::class_<sdr_hackrf::HackrfAcquisitionDspStopResult>(
        module, "HackrfAcquisitionDspStopResult"
    )
        .def_readonly(
            "callbacks_quiescent",
            &sdr_hackrf::HackrfAcquisitionDspStopResult::callbacks_quiescent
        )
        .def_readonly("worker_joined", &sdr_hackrf::HackrfAcquisitionDspStopResult::worker_joined)
        .def_readonly("processing_failed", &sdr_hackrf::HackrfAcquisitionDspStopResult::processing_failed)
        .def_readonly(
            "worker_abandoned_blocks",
            &sdr_hackrf::HackrfAcquisitionDspStopResult::worker_abandoned_blocks
        )
        .def("complete", &sdr_hackrf::HackrfAcquisitionDspStopResult::complete);

    py::class_<sdr_hackrf::HackrfRxFinalizeResult>(module, "HackrfRxFinalizeResult")
        .def_readonly("drain_verified", &sdr_hackrf::HackrfRxFinalizeResult::drain_verified)
        .def_readonly("close_called", &sdr_hackrf::HackrfRxFinalizeResult::close_called)
        .def_readonly("close_status", &sdr_hackrf::HackrfRxFinalizeResult::close_status)
        .def_readonly("exit_called", &sdr_hackrf::HackrfRxFinalizeResult::exit_called)
        .def_readonly("exit_status", &sdr_hackrf::HackrfRxFinalizeResult::exit_status)
        .def("complete", &sdr_hackrf::HackrfRxFinalizeResult::complete);

    py::class_<sdr_hackrf::HackrfRuntimeDspStopResult>(
        module, "HackrfRuntimeDspStopResult"
    )
        .def_readonly("source_quiesce", &sdr_hackrf::HackrfRuntimeDspStopResult::source_quiesce)
        .def_readonly("processing", &sdr_hackrf::HackrfRuntimeDspStopResult::processing)
        .def_readonly("source_finalize", &sdr_hackrf::HackrfRuntimeDspStopResult::source_finalize)
        .def("complete", &sdr_hackrf::HackrfRuntimeDspStopResult::complete);

    py::class_<sdr_hackrf::HackrfRuntimeDspMetrics>(module, "HackrfRuntimeDspMetrics")
        .def_readonly("lifecycle_open", &sdr_hackrf::HackrfRuntimeDspMetrics::lifecycle_open)
        .def_readonly("source", &sdr_hackrf::HackrfRuntimeDspMetrics::source)
        .def_readonly("processing", &sdr_hackrf::HackrfRuntimeDspMetrics::processing);

    py::class_<sdr_hackrf::HackrfLatestSpectrumFrameDrain>(
        module, "HackrfLatestSpectrumFrameDrain"
    )
        .def_readonly("frame", &sdr_hackrf::HackrfLatestSpectrumFrameDrain::frame)
        .def_readonly("coalesced_frames", &sdr_hackrf::HackrfLatestSpectrumFrameDrain::coalesced_frames);
    module.attr("HACKRF_UI_BRIDGE_CONTRACT_VERSION") = 1;

    py::class_<sdr_hackrf::HackrfRuntimeDspSession>(module, "HackrfRuntimeDspControl")
        .def("discard_terminal_spectrum_frames", &sdr_hackrf::HackrfRuntimeDspSession::discard_terminal_spectrum_frames,
             py::call_guard<py::gil_scoped_release>())
        .def("drain_analytical_ready_events", &sdr_hackrf::HackrfRuntimeDspSession::drain_analytical_ready_events,
             py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def("drain_density_layer_ready_events", &sdr_hackrf::HackrfRuntimeDspSession::drain_density_layer_ready_events,
             py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def("poll_persistence_snapshots",
            &sdr_hackrf::HackrfRuntimeDspSession::poll_persistence_snapshots,
            py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def(
            "poll_spectrum_frames",
            [](sdr_hackrf::HackrfRuntimeDspSession& value, const std::size_t max_items) {
                py::gil_scoped_release release;
                return value.poll_spectrum_frames(max_items);
            },
            py::arg("max_items") = 0U
        )
        .def("drain_latest_spectrum_frame",
            &sdr_hackrf::HackrfRuntimeDspSession::drain_latest_spectrum_frame,
            py::call_guard<py::gil_scoped_release>())
        .def(
            "metrics",
            [](const sdr_hackrf::HackrfRuntimeDspSession& value) {
                py::gil_scoped_release release;
                return value.metrics();
            }
        )
        .def(
            "stop",
            [](sdr_hackrf::HackrfRuntimeDspSession& value, const std::uint64_t timeout_ms) {
                const auto timeout = bounded_stop_timeout(timeout_ms);
                py::gil_scoped_release release;
                return value.stop(timeout);
            },
            py::arg("timeout_ms")
        );

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
    module.def(
        "_make_test_hackrf_runtime_dsp_control",
        &make_test_hackrf_runtime_dsp_control,
        py::arg("blocks") = 4U, py::arg("layer_event_capacity") = 0U,
        py::kw_only(), py::arg("dc_removal_block_mean").noconvert() = false,
        py::arg("analytical_event_capacity") = 0U,
        py::arg("configuration_generation") = 1U,
        py::arg("source_id") = "hackrf-r11l-test-fixture",
        py::arg("center_frequency_hz") = 100'000'000.0,
        py::arg("sample_rate_hz") = 10'000'000.0,
        py::arg("baseband_filter_hz") = 8'000'000U
    );
#endif

}

}  // namespace sdr_core::python

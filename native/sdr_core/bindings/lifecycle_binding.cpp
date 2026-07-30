#include "lifecycle_binding.hpp"

#include "sdr_core/engine.hpp"
#include "sdr_core/synthetic_source.hpp"

#include <cstddef>
#include <cstdint>

#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {

void bind_lifecycle(py::module_& module) {
    py::enum_<EngineState>(module, "EngineState")
        .value("CREATED", EngineState::Created)
        .value("CONFIGURED", EngineState::Configured)
        .value("RUNNING", EngineState::Running)
        .value("STOPPING", EngineState::Stopping)
        .value("STOPPED", EngineState::Stopped)
        .value("ERROR", EngineState::Error);

    py::enum_<OverflowPolicy>(module, "OverflowPolicy")
        .value("BLOCK", OverflowPolicy::Block)
        .value("DROP_NEWEST", OverflowPolicy::DropNewest)
        .value("DROP_OLDEST", OverflowPolicy::DropOldest)
        .value("LATEST_WINS", OverflowPolicy::LatestWins);

    py::enum_<EventSeverity>(module, "EventSeverity")
        .value("INFO", EventSeverity::Info)
        .value("WARNING", EventSeverity::Warning)
        .value("ERROR", EventSeverity::Error)
        .value("CRITICAL", EventSeverity::Critical);

    py::enum_<QueueId>(module, "QueueId")
        .value("ACQUISITION", QueueId::Acquisition)
        .value("DSP", QueueId::Dsp)
        .value("RECORDER", QueueId::Recorder)
        .value("SNAPSHOT", QueueId::Snapshot)
        .value("EVENT", QueueId::Event)
        .value("SPECTRUM", QueueId::Spectrum);

    py::class_<QueueStats>(module, "QueueStats")
        .def_readonly("capacity", &QueueStats::capacity)
        .def_readonly("depth", &QueueStats::depth)
        .def_readonly("high_water", &QueueStats::high_water)
        .def_readonly("pushed", &QueueStats::pushed)
        .def_readonly("popped", &QueueStats::popped)
        .def_readonly("dropped", &QueueStats::dropped)
        .def_readonly("abandoned", &QueueStats::abandoned)
        .def_readonly("stop_requested", &QueueStats::stop_requested);

    py::class_<PoolStats>(module, "PoolStats")
        .def_readonly("capacity", &PoolStats::capacity)
        .def_readonly("block_size", &PoolStats::block_size)
        .def_readonly("in_use", &PoolStats::in_use)
        .def_readonly("high_water", &PoolStats::high_water)
        .def_readonly("acquired", &PoolStats::acquired)
        .def_readonly("returned", &PoolStats::returned)
        .def_readonly("exhausted", &PoolStats::exhausted)
        .def_readonly("stop_requested", &PoolStats::stop_requested);

    py::class_<DiagnosticEvent>(module, "DiagnosticEvent")
        .def_readonly("severity", &DiagnosticEvent::severity)
        .def_readonly("code", &DiagnosticEvent::code)
        .def_readonly("message", &DiagnosticEvent::message)
        .def_readonly("timestamp_ns", &DiagnosticEvent::timestamp_ns)
        .def_readonly("sequence", &DiagnosticEvent::sequence);

    py::class_<EngineConfig>(module, "EngineConfig")
        .def(py::init([](
                 const std::uint32_t acquisition_queue_capacity,
                 const OverflowPolicy acquisition_overflow,
                 const std::uint32_t dsp_queue_capacity,
                 const OverflowPolicy dsp_overflow,
                 const std::uint32_t snapshot_queue_capacity,
                 const std::uint32_t event_queue_capacity,
                 const std::uint32_t recorder_queue_capacity,
                 const OverflowPolicy recorder_overflow,
                 const bool recorder_stop_on_overflow,
                 const std::uint32_t pool_block_count,
                 const std::uint32_t block_size_samples,
                 const double sample_rate_hz,
                 const double center_frequency_hz,
                 const std::uint32_t blocks_per_second,
                 const bool recorder_enabled,
                 const std::uint64_t max_blocks,
                 const SyntheticScenario scenario,
                 const std::uint64_t seed,
                 const std::uint32_t snapshot_interval_blocks,
                 const DspConfig& dsp,
                 const std::uint32_t spectrum_queue_capacity,
                 const bool dc_removal_block_mean
             ) {
            EngineConfig result;
            result.acquisition_queue_capacity = acquisition_queue_capacity;
            result.acquisition_overflow = acquisition_overflow;
            result.dsp_queue_capacity = dsp_queue_capacity;
            result.dsp_overflow = dsp_overflow;
            result.snapshot_queue_capacity = snapshot_queue_capacity;
            result.event_queue_capacity = event_queue_capacity;
            result.recorder_queue_capacity = recorder_queue_capacity;
            result.recorder_overflow = recorder_overflow;
            result.recorder_stop_on_overflow = recorder_stop_on_overflow;
            result.pool_block_count = pool_block_count;
            result.block_size_samples = block_size_samples;
            result.sample_rate_hz = sample_rate_hz;
            result.center_frequency_hz = center_frequency_hz;
            result.blocks_per_second = blocks_per_second;
            result.recorder_enabled = recorder_enabled;
            result.max_blocks = max_blocks;
            result.scenario = scenario;
            result.seed = seed;
            result.snapshot_interval_blocks = snapshot_interval_blocks;
            result.dsp = dsp;
            result.spectrum_queue_capacity = spectrum_queue_capacity;
            result.dc_removal_block_mean = dc_removal_block_mean;
            validate(result);
            return result;
        }),
            py::arg("acquisition_queue_capacity") = 64U,
            py::arg("acquisition_overflow") = OverflowPolicy::DropNewest,
            py::arg("dsp_queue_capacity") = 64U,
            py::arg("dsp_overflow") = OverflowPolicy::DropNewest,
            py::arg("snapshot_queue_capacity") = 4U,
            py::arg("event_queue_capacity") = 64U,
            py::arg("recorder_queue_capacity") = 8U,
            py::arg("recorder_overflow") = OverflowPolicy::DropNewest,
            py::arg("recorder_stop_on_overflow") = false,
            py::arg("pool_block_count") = 128U,
            py::arg("block_size_samples") = 4096U,
            py::arg("sample_rate_hz") = 1'024'000.0,
            py::arg("center_frequency_hz") = 100'000'000.0,
            py::arg("blocks_per_second") = 0U,
            py::arg("recorder_enabled") = false,
            py::arg("max_blocks") = 0U,
            py::arg("scenario") = SyntheticScenario::BroadbandNoise,
            py::arg("seed") = 0x5344525F50303401ULL,
            py::arg("snapshot_interval_blocks") = 64U,
            py::arg("dsp") = DspConfig{.fft_size = 1024U, .hop_size = 1024U},
            py::arg("spectrum_queue_capacity") = 8U,
            py::arg("dc_removal_block_mean") = false
        )
        .def_readonly("acquisition_queue_capacity", &EngineConfig::acquisition_queue_capacity)
        .def_readonly("acquisition_overflow", &EngineConfig::acquisition_overflow)
        .def_readonly("dsp_queue_capacity", &EngineConfig::dsp_queue_capacity)
        .def_readonly("dsp_overflow", &EngineConfig::dsp_overflow)
        .def_readonly("snapshot_queue_capacity", &EngineConfig::snapshot_queue_capacity)
        .def_readonly("event_queue_capacity", &EngineConfig::event_queue_capacity)
        .def_readonly("recorder_queue_capacity", &EngineConfig::recorder_queue_capacity)
        .def_readonly("recorder_overflow", &EngineConfig::recorder_overflow)
        .def_readonly("recorder_stop_on_overflow", &EngineConfig::recorder_stop_on_overflow)
        .def_readonly("pool_block_count", &EngineConfig::pool_block_count)
        .def_readonly("block_size_samples", &EngineConfig::block_size_samples)
        .def_readonly("sample_rate_hz", &EngineConfig::sample_rate_hz)
        .def_readonly("center_frequency_hz", &EngineConfig::center_frequency_hz)
        .def_readonly("blocks_per_second", &EngineConfig::blocks_per_second)
        .def_readonly("recorder_enabled", &EngineConfig::recorder_enabled)
        .def_readonly("max_blocks", &EngineConfig::max_blocks)
        .def_readonly("scenario", &EngineConfig::scenario)
        .def_readonly("seed", &EngineConfig::seed)
        .def_readonly("snapshot_interval_blocks", &EngineConfig::snapshot_interval_blocks)
        .def_readonly("dsp", &EngineConfig::dsp)
        .def_readonly("spectrum_queue_capacity", &EngineConfig::spectrum_queue_capacity)
        .def_readonly("dc_removal_block_mean", &EngineConfig::dc_removal_block_mean)
        .def_readonly("schema_version", &EngineConfig::schema_version);

    // Coarse-grained engine boundary: configuration, lifecycle, snapshots,
    // events and metrics only. There is intentionally no per-block API.
    py::class_<SyntheticEngine>(module, "SyntheticEngine")
        .def(py::init<>())
        .def("configure", &SyntheticEngine::configure, py::arg("config"))
        .def(
            "start",
            [](SyntheticEngine& engine) {
                py::gil_scoped_release release;
                engine.start();
            }
        )
        .def("request_stop", &SyntheticEngine::request_stop)
        .def(
            "join",
            [](SyntheticEngine& engine) {
                py::gil_scoped_release release;
                engine.join();
            }
        )
        .def(
            "stop",
            [](SyntheticEngine& engine) {
                py::gil_scoped_release release;
                engine.stop();
            }
        )
        .def("state", &SyntheticEngine::state)
        .def("config_generation", &SyntheticEngine::config_generation)
        .def("config", &SyntheticEngine::config)
        .def("metrics", &SyntheticEngine::metrics)
        .def(
            "poll_events",
            &SyntheticEngine::poll_events,
            py::arg("max_items") = 0U
        )
        .def(
            "poll_snapshots",
            &SyntheticEngine::poll_snapshots,
            py::arg("max_items") = 0U
        )
        .def(
            "poll_spectrum_frames",
            &SyntheticEngine::poll_spectrum_frames,
            py::arg("max_items") = 0U
        )
        .def("queue_stats", &SyntheticEngine::queue_stats, py::arg("id"))
        .def("pool_stats", &SyntheticEngine::pool_stats);
}

}  // namespace sdr_core::python

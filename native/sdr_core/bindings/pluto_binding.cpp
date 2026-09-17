#include "pluto_binding.hpp"

#include "sdr_core/recording_writer.hpp"
#include "sdr_core/dual_rx_dsp.hpp"
#include "sdr_core/sweep_statistics.hpp"
#include "sdr_pluto/continuous_sweep_coordinator.hpp"
#include "sdr_pluto/fixed_band_engine.hpp"
#include "sdr_pluto/pluto_backend.hpp"

#include <memory>
#include <optional>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {

namespace {

template <typename T>
py::array_t<T> readonly_shared_vector(
    const std::shared_ptr<const std::vector<T>>& values
) {
    if (!values) {
        return py::array_t<T>();
    }
    auto* holder = new std::shared_ptr<const std::vector<T>>(values);
    py::capsule owner(holder, [](void* pointer) {
        delete static_cast<std::shared_ptr<const std::vector<T>>*>(pointer);
    });
    py::array_t<T> result(
        static_cast<py::ssize_t>(values->size()), values->data(), owner
    );
    result.attr("setflags")(false);
    return result;
}

py::array_t<float> readonly_persistence_image(const sdr_core::PersistenceSnapshot& value) {
    if (!value.density) {
        return py::array_t<float>();
    }
    auto* holder = new std::shared_ptr<const std::vector<float>>(value.density);
    py::capsule owner(holder, [](void* pointer) {
        delete static_cast<std::shared_ptr<const std::vector<float>>*>(pointer);
    });
    const auto rows = static_cast<py::ssize_t>(value.power_bins);
    const auto columns = static_cast<py::ssize_t>(value.frequency_bins);
    py::array_t<float> result(
        {rows, columns},
        {columns * static_cast<py::ssize_t>(sizeof(float)), static_cast<py::ssize_t>(sizeof(float))},
        value.density->data(),
        owner
    );
    result.attr("setflags")(false);
    return result;
}

template <typename T>
py::array_t<T> readonly_sweep_image(const SharedArray<T>& values, std::uint32_t rows) {
    auto flat = readonly_shared_vector(values);
    return flat.attr("reshape")(rows, values->size() / rows).template cast<py::array_t<T>>();
}

}  // namespace

void bind_pluto(py::module_& module) {
    py::class_<sdr_core::DualRxChannelDspConfig>(module, "DualRxChannelDspConfig")
        .def(py::init([](
            const sdr_core::SourceDescriptor& source,
            const sdr_core::DspConfig& dsp
        ) {
            return sdr_core::DualRxChannelDspConfig{.source = source, .dsp = dsp};
        }), py::arg("source"), py::arg("dsp"))
        .def_readonly("source", &sdr_core::DualRxChannelDspConfig::source)
        .def_readonly("dsp", &sdr_core::DualRxChannelDspConfig::dsp);

    py::class_<sdr_core::DualRxDspConfig>(module, "DualRxDspConfig")
        .def(py::init([](
            const sdr_core::DualRxChannelDspConfig& primary,
            const sdr_core::DualRxChannelDspConfig& secondary,
            const bool dc_removal_block_mean,
            const std::uint32_t output_queue_capacity
        ) {
            sdr_core::DualRxDspConfig result{
                .primary = primary,
                .secondary = secondary,
                .dc_removal_block_mean = dc_removal_block_mean,
                .output_queue_capacity = output_queue_capacity,
            };
            sdr_core::validate(result);
            return result;
        }),
            py::arg("primary"), py::arg("secondary"),
            py::arg("dc_removal_block_mean") = false,
            py::arg("output_queue_capacity") = 4U)
        .def_readonly("primary", &sdr_core::DualRxDspConfig::primary)
        .def_readonly("secondary", &sdr_core::DualRxDspConfig::secondary)
        .def_readonly("dc_removal_block_mean", &sdr_core::DualRxDspConfig::dc_removal_block_mean)
        .def_readonly("output_queue_capacity", &sdr_core::DualRxDspConfig::output_queue_capacity);

    py::class_<sdr_core::DualRxSpectrumFrame>(module, "DualRxSpectrumFrame")
        .def_readonly("synchronization_epoch", &sdr_core::DualRxSpectrumFrame::synchronization_epoch)
        .def_readonly("first_sample_index", &sdr_core::DualRxSpectrumFrame::first_sample_index)
        .def_readonly("timestamp_ns", &sdr_core::DualRxSpectrumFrame::timestamp_ns)
        .def_readonly("config_generation", &sdr_core::DualRxSpectrumFrame::config_generation)
        .def_readonly(
            "shared_input_gaps_before", &sdr_core::DualRxSpectrumFrame::shared_input_gaps_before
        )
        .def_readonly("primary", &sdr_core::DualRxSpectrumFrame::primary)
        .def_readonly("secondary", &sdr_core::DualRxSpectrumFrame::secondary);

    py::class_<sdr_core::DualRxDspMetrics>(module, "DualRxDspMetrics")
        .def_readonly("input_epochs_received", &sdr_core::DualRxDspMetrics::input_epochs_received)
        .def_readonly("shared_input_gaps", &sdr_core::DualRxDspMetrics::shared_input_gaps)
        .def_readonly("pairing_mismatches", &sdr_core::DualRxDspMetrics::pairing_mismatches)
        .def_readonly("paired_frames_published", &sdr_core::DualRxDspMetrics::paired_frames_published)
        .def_readonly("paired_frames_superseded", &sdr_core::DualRxDspMetrics::paired_frames_superseded)
        .def_readonly("output_queue", &sdr_core::DualRxDspMetrics::output_queue)
        .def_readonly("primary", &sdr_core::DualRxDspMetrics::primary)
        .def_readonly("secondary", &sdr_core::DualRxDspMetrics::secondary);

    py::class_<sdr_core::LatestDualRxSpectrumFrameDrain>(
        module, "LatestDualRxSpectrumFrameDrain"
    )
        .def_readonly("frame", &sdr_core::LatestDualRxSpectrumFrameDrain::frame)
        .def_readonly(
            "coalesced_frames", &sdr_core::LatestDualRxSpectrumFrameDrain::coalesced_frames
        );

    py::class_<sdr_core::DualRxDspPublisher>(module, "DualRxDspPublisher")
        .def(py::init<>())
        .def("configure", &sdr_core::DualRxDspPublisher::configure, py::arg("config"))
        .def("mark_shared_gap", &sdr_core::DualRxDspPublisher::mark_shared_gap)
        .def("reset", &sdr_core::DualRxDspPublisher::reset)
        .def(
            "poll_spectrum_frames",
            &sdr_core::DualRxDspPublisher::poll_spectrum_frames,
            py::arg("max_items") = 0U
        )
        .def(
            "drain_latest_spectrum_frame",
            &sdr_core::DualRxDspPublisher::drain_latest_spectrum_frame
        )
        .def_property_readonly("metrics", &sdr_core::DualRxDspPublisher::metrics);

    py::class_<sdr_core::NativeRecordingRecoveryScan>(module, "NativeRecordingRecoveryScan")
        .def_readonly("iq_manifest_final", &sdr_core::NativeRecordingRecoveryScan::iq_manifest_final)
        .def_readonly("iq_manifest_partial", &sdr_core::NativeRecordingRecoveryScan::iq_manifest_partial)
        .def_readonly("spectrum_manifest_final", &sdr_core::NativeRecordingRecoveryScan::spectrum_manifest_final)
        .def_readonly("spectrum_manifest_partial", &sdr_core::NativeRecordingRecoveryScan::spectrum_manifest_partial)
        .def_readonly("iq_data_segments", &sdr_core::NativeRecordingRecoveryScan::iq_data_segments)
        .def_readonly("iq_data_bytes", &sdr_core::NativeRecordingRecoveryScan::iq_data_bytes)
        .def_readonly("iq_index_complete_records", &sdr_core::NativeRecordingRecoveryScan::iq_index_complete_records)
        .def_readonly("iq_index_complete_bytes", &sdr_core::NativeRecordingRecoveryScan::iq_index_complete_bytes)
        .def_readonly("iq_index_trailing_bytes", &sdr_core::NativeRecordingRecoveryScan::iq_index_trailing_bytes)
        .def_readonly("iq_gap_complete_records", &sdr_core::NativeRecordingRecoveryScan::iq_gap_complete_records)
        .def_readonly("iq_gap_complete_bytes", &sdr_core::NativeRecordingRecoveryScan::iq_gap_complete_bytes)
        .def_readonly("iq_gap_trailing_bytes", &sdr_core::NativeRecordingRecoveryScan::iq_gap_trailing_bytes)
        .def_readonly("spectrum_binary_header_valid", &sdr_core::NativeRecordingRecoveryScan::spectrum_binary_header_valid)
        .def_readonly("spectrum_complete_records", &sdr_core::NativeRecordingRecoveryScan::spectrum_complete_records)
        .def_readonly("spectrum_complete_bytes", &sdr_core::NativeRecordingRecoveryScan::spectrum_complete_bytes)
        .def_readonly("spectrum_trailing_bytes", &sdr_core::NativeRecordingRecoveryScan::spectrum_trailing_bytes);
    module.def(
        "scan_native_recording_prefix",
        [](const std::string& output_uri) {
            return sdr_core::scan_native_recording_prefix(std::filesystem::path(output_uri));
        },
        py::arg("output_uri")
    );
    py::class_<sdr_core::NativeRecordingOpenInfo>(module, "NativeRecordingOpenInfo")
        .def_readonly("iq_manifest_final", &sdr_core::NativeRecordingOpenInfo::iq_manifest_final)
        .def_readonly("spectrum_manifest_final", &sdr_core::NativeRecordingOpenInfo::spectrum_manifest_final)
        .def_readonly("iq_gap_records", &sdr_core::NativeRecordingOpenInfo::iq_gap_records)
        .def_readonly("spectrum_frame_count", &sdr_core::NativeRecordingOpenInfo::spectrum_frame_count)
        .def_readonly("lifecycle_epochs", &sdr_core::NativeRecordingOpenInfo::lifecycle_epochs)
        .def_readonly("lifecycle_control_gaps", &sdr_core::NativeRecordingOpenInfo::lifecycle_control_gaps)
        .def_readonly(
            "lifecycle_control_gap_duration_ns",
            &sdr_core::NativeRecordingOpenInfo::lifecycle_control_gap_duration_ns
        );
    py::class_<sdr_core::NativeSpectrumReplayEntry>(module, "NativeSpectrumReplayEntry")
        .def_readonly("ordinal", &sdr_core::NativeSpectrumReplayEntry::ordinal)
        .def_readonly("offset", &sdr_core::NativeSpectrumReplayEntry::offset)
        .def_readonly("record_bytes", &sdr_core::NativeSpectrumReplayEntry::record_bytes)
        .def_readonly("frame_sequence", &sdr_core::NativeSpectrumReplayEntry::frame_sequence)
        .def_readonly("timestamp_ns", &sdr_core::NativeSpectrumReplayEntry::timestamp_ns);
    py::class_<sdr_core::NativeSpectrumReplayFrame>(module, "NativeSpectrumReplayFrame")
        .def_property_readonly("source_id", [](const sdr_core::NativeSpectrumReplayFrame& value) {
            return value.source.source_id;
        })
        .def_readonly("frame_sequence", &sdr_core::NativeSpectrumReplayFrame::frame_sequence)
        .def_readonly("timestamp_ns", &sdr_core::NativeSpectrumReplayFrame::timestamp_ns)
        .def_readonly("config_generation", &sdr_core::NativeSpectrumReplayFrame::config_generation)
        .def_property_readonly("unit", [](const sdr_core::NativeSpectrumReplayFrame& value) {
            return std::string(sdr_core::to_wire(value.unit));
        })
        .def_readonly(
            "calibration_profile_id", &sdr_core::NativeSpectrumReplayFrame::calibration_profile_id
        )
        .def_readonly("quality_flags", &sdr_core::NativeSpectrumReplayFrame::quality_flags)
        .def_readonly(
            "dropped_samples_before", &sdr_core::NativeSpectrumReplayFrame::dropped_samples_before
        )
        .def_readonly(
            "dropped_iq_blocks_before", &sdr_core::NativeSpectrumReplayFrame::dropped_iq_blocks_before
        )
        .def_readonly(
            "dropped_fft_frames_before", &sdr_core::NativeSpectrumReplayFrame::dropped_fft_frames_before
        )
        .def_property_readonly("frequencies_hz", [](const sdr_core::NativeSpectrumReplayFrame& value) {
            return readonly_shared_vector(value.frequencies_hz);
        })
        .def_property_readonly("values", [](const sdr_core::NativeSpectrumReplayFrame& value) {
            return readonly_shared_vector(value.values);
        });
    py::class_<sdr_core::NativeSpectrumRecordingReader,
               std::shared_ptr<sdr_core::NativeSpectrumRecordingReader>>(
        module, "NativeSpectrumRecordingReader"
    )
        .def(py::init([](const std::string& output_uri) {
            return std::make_shared<sdr_core::NativeSpectrumRecordingReader>(
                std::filesystem::path(output_uri)
            );
        }), py::arg("output_uri"))
        .def_property_readonly("info", [](const sdr_core::NativeSpectrumRecordingReader& value) {
            return value.info();
        })
        .def_property_readonly("frame_count", &sdr_core::NativeSpectrumRecordingReader::frame_count)
        .def_property_readonly(
            "first_timestamp_ns", &sdr_core::NativeSpectrumRecordingReader::first_timestamp_ns
        )
        .def_property_readonly(
            "last_timestamp_ns", &sdr_core::NativeSpectrumRecordingReader::last_timestamp_ns
        )
        .def_property_readonly(
            "source_size_bytes", &sdr_core::NativeSpectrumRecordingReader::source_size_bytes
        )
        .def("entry_at", &sdr_core::NativeSpectrumRecordingReader::entry_at, py::arg("ordinal"))
        .def("read_frame", &sdr_core::NativeSpectrumRecordingReader::read_frame, py::arg("ordinal"));
    module.def(
        "inspect_final_native_recording",
        [](const std::string& output_uri) {
            return sdr_core::inspect_final_native_recording(std::filesystem::path(output_uri));
        },
        py::arg("output_uri")
    );

    py::class_<sdr_pluto::RuntimeInfo>(module, "PlutoRuntimeInfo")
        .def_readonly("available", &sdr_pluto::RuntimeInfo::available)
        .def_readonly("library_path", &sdr_pluto::RuntimeInfo::library_path)
        .def_readonly("major", &sdr_pluto::RuntimeInfo::major)
        .def_readonly("minor", &sdr_pluto::RuntimeInfo::minor)
        .def_readonly("git_tag", &sdr_pluto::RuntimeInfo::git_tag)
        .def_readonly("backends", &sdr_pluto::RuntimeInfo::backends)
        .def_readonly("supports_kernel_buffer_count", &sdr_pluto::RuntimeInfo::supports_kernel_buffer_count)
        .def_readonly("supports_buffer_blocking_mode", &sdr_pluto::RuntimeInfo::supports_buffer_blocking_mode)
        .def_readonly("supports_buffer_poll_fd", &sdr_pluto::RuntimeInfo::supports_buffer_poll_fd)
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

    py::class_<sdr_pluto::InputScanElement>(module, "PlutoInputScanElement")
        .def_readonly("id", &sdr_pluto::InputScanElement::id)
        .def_readonly("device_channel_index", &sdr_pluto::InputScanElement::device_channel_index)
        .def_readonly("storage_bits", &sdr_pluto::InputScanElement::storage_bits)
        .def_readonly("significant_bits", &sdr_pluto::InputScanElement::significant_bits)
        .def_readonly("shift", &sdr_pluto::InputScanElement::shift)
        .def_readonly("is_signed", &sdr_pluto::InputScanElement::is_signed)
        .def_readonly("is_big_endian", &sdr_pluto::InputScanElement::is_big_endian)
        .def_readonly("repeat", &sdr_pluto::InputScanElement::repeat);

    py::class_<sdr_pluto::ReceiverTopologyProbe>(module, "PlutoReceiverTopologyProbe")
        .def_readonly("context", &sdr_pluto::ReceiverTopologyProbe::context)
        .def_readonly("phy_rx_channel_ids", &sdr_pluto::ReceiverTopologyProbe::phy_rx_channel_ids)
        .def_readonly("input_scan_elements", &sdr_pluto::ReceiverTopologyProbe::input_scan_elements);

    py::enum_<sdr_pluto::ReceiverSelection>(module, "PlutoReceiverSelection")
        .value("RX1", sdr_pluto::ReceiverSelection::Rx1)
        .value("RX2", sdr_pluto::ReceiverSelection::Rx2)
        .value("BOTH", sdr_pluto::ReceiverSelection::Both);

    py::class_<sdr_pluto::ReceiverIqBlockSet>(module, "PlutoReceiverIqBlockSet")
        .def_readonly("selection", &sdr_pluto::ReceiverIqBlockSet::selection)
        .def_readonly("rx1", &sdr_pluto::ReceiverIqBlockSet::rx1)
        .def_readonly("rx2", &sdr_pluto::ReceiverIqBlockSet::rx2);

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
        .def_readonly("refill_wait_ns", &sdr_pluto::StreamMetrics::refill_wait_ns)
        .def_readonly("refill_calls", &sdr_pluto::StreamMetrics::refill_calls)
        .def_readonly("refill_wait_over_nominal_period", &sdr_pluto::StreamMetrics::refill_wait_over_nominal_period)
        .def_readonly("refill_wait_over_two_nominal_periods", &sdr_pluto::StreamMetrics::refill_wait_over_two_nominal_periods)
        .def_readonly("canonicalization_ns", &sdr_pluto::StreamMetrics::canonicalization_ns)
        .def_readonly("inter_refill_gap_ns", &sdr_pluto::StreamMetrics::inter_refill_gap_ns)
        .def_readonly("inter_refill_gap_count", &sdr_pluto::StreamMetrics::inter_refill_gap_count)
        .def_readonly("short_reads", &sdr_pluto::StreamMetrics::short_reads)
        .def_readonly("refill_errors", &sdr_pluto::StreamMetrics::refill_errors)
        .def_readonly("output_pool_exhaustions", &sdr_pluto::StreamMetrics::output_pool_exhaustions)
        .def_readonly("output_blocks_dropped", &sdr_pluto::StreamMetrics::output_blocks_dropped)
        .def_readonly("estimated_dropped_samples", &sdr_pluto::StreamMetrics::estimated_dropped_samples);

    py::class_<sdr_core::PersistenceSnapshot>(module, "PersistenceSnapshot")
        .def_property_readonly("source_id", [](const sdr_core::PersistenceSnapshot& value) {
            return value.source.source_id;
        })
        .def_readonly("config_generation", &sdr_core::PersistenceSnapshot::config_generation)
        .def_readonly("unit", &sdr_core::PersistenceSnapshot::unit)
        .def_readonly("update_sequence", &sdr_core::PersistenceSnapshot::update_sequence)
        .def_readonly("timestamp_ns", &sdr_core::PersistenceSnapshot::timestamp_ns)
        .def_readonly("source_frame_sequence", &sdr_core::PersistenceSnapshot::source_frame_sequence)
        .def_readonly("power_min_db", &sdr_core::PersistenceSnapshot::power_min_db)
        .def_readonly("power_max_db", &sdr_core::PersistenceSnapshot::power_max_db)
        .def_readonly("power_bins", &sdr_core::PersistenceSnapshot::power_bins)
        .def_readonly("frequency_bins", &sdr_core::PersistenceSnapshot::frequency_bins)
        .def_readonly("processed_frames", &sdr_core::PersistenceSnapshot::processed_frames)
        .def_readonly("exponential_decay", &sdr_core::PersistenceSnapshot::exponential_decay)
        .def_readonly("probability_scale", &sdr_core::PersistenceSnapshot::probability_scale)
        .def_readonly("count_scale", &sdr_core::PersistenceSnapshot::count_scale)
        .def_property_readonly("frequencies_hz", [](const sdr_core::PersistenceSnapshot& value) {
            return readonly_shared_vector(value.frequencies_hz);
        })
        .def_property_readonly("density", [](const sdr_core::PersistenceSnapshot& value) {
            return readonly_persistence_image(value);
        });

    py::class_<SweepStatisticsConfig>(module, "SweepStatisticsConfig")
        .def(py::init([](std::uint32_t window_passes, std::uint32_t power_bins,
            double power_min_db, double power_max_db, std::size_t max_payload_bytes,
            std::uint32_t density_columns) {
            return SweepStatisticsConfig{window_passes, power_bins, power_min_db,
                power_max_db, max_payload_bytes, density_columns};
        }), py::arg("window_passes"), py::arg("power_bins"), py::arg("power_min_db"),
            py::arg("power_max_db"), py::arg("max_payload_bytes"), py::arg("density_columns") = 0U)
        .def_readonly("window_passes", &SweepStatisticsConfig::window_passes)
        .def_readonly("power_bins", &SweepStatisticsConfig::power_bins)
        .def_readonly("power_min_db", &SweepStatisticsConfig::power_min_db)
        .def_readonly("power_max_db", &SweepStatisticsConfig::power_max_db)
        .def_readonly("max_payload_bytes", &SweepStatisticsConfig::max_payload_bytes)
        .def_readonly("density_columns", &SweepStatisticsConfig::density_columns)
        .def("required_payload_bytes", [](const SweepStatisticsConfig& value, std::size_t frequency_bins,
            std::size_t retained_snapshot_slots) {
            return SweepStatisticsAccumulator::required_payload_bytes(value, frequency_bins, retained_snapshot_slots);
        }, py::arg("frequency_bins"), py::arg("retained_snapshot_slots") = 1U);

    py::class_<SweepStatisticsSnapshot>(module, "SweepStatisticsSnapshot")
        .def_readonly("source_id", &SweepStatisticsSnapshot::source_id)
        .def_readonly("epoch", &SweepStatisticsSnapshot::epoch)
        .def_readonly("update_sequence", &SweepStatisticsSnapshot::update_sequence)
        .def_readonly("newest_pass_sequence", &SweepStatisticsSnapshot::newest_pass_sequence)
        .def_readonly("unique_passes_seen", &SweepStatisticsSnapshot::unique_passes_seen)
        .def_readonly("retained_passes", &SweepStatisticsSnapshot::retained_passes)
        .def_readonly("power_min_db", &SweepStatisticsSnapshot::power_min_db)
        .def_readonly("power_max_db", &SweepStatisticsSnapshot::power_max_db)
        .def_readonly("power_bins", &SweepStatisticsSnapshot::power_bins)
        .def_property_readonly("unit", [](const SweepStatisticsSnapshot& value) { return std::string(to_wire(value.unit)); })
        .def_property_readonly("frequencies_hz", [](const SweepStatisticsSnapshot& value) { return readonly_shared_vector(value.frequencies_hz); })
        .def_property_readonly("average_db", [](const SweepStatisticsSnapshot& value) { return readonly_shared_vector(value.average_db); })
        .def_property_readonly("observations", [](const SweepStatisticsSnapshot& value) { return readonly_shared_vector(value.observations); })
        .def_property_readonly("density_frequency_edges_hz", [](const SweepStatisticsSnapshot& value) { return readonly_shared_vector(value.density_frequency_edges_hz); })
        .def_property_readonly("density_observations", [](const SweepStatisticsSnapshot& value) { return readonly_shared_vector(value.density_observations); })
        .def_property_readonly("histogram_counts", [](const SweepStatisticsSnapshot& value) { return readonly_sweep_image(value.histogram_counts, value.power_bins); })
        .def_property_readonly("probability", [](const SweepStatisticsSnapshot& value) { return readonly_sweep_image(value.probability, value.power_bins); });

    py::class_<sdr_core::SweepSegmentAcquisition>(module, "SweepSegmentAcquisition")
        .def_readonly("segment_index", &sdr_core::SweepSegmentAcquisition::segment_index)
        .def_readonly("config_generation", &sdr_core::SweepSegmentAcquisition::config_generation)
        .def_readonly("frame_sequence", &sdr_core::SweepSegmentAcquisition::frame_sequence)
        .def_readonly("first_sample_index", &sdr_core::SweepSegmentAcquisition::first_sample_index)
        .def_readonly("timestamp_ns", &sdr_core::SweepSegmentAcquisition::timestamp_ns)
        .def_readonly("sample_rate_hz", &sdr_core::SweepSegmentAcquisition::sample_rate_hz)
        .def_readonly("fft_size", &sdr_core::SweepSegmentAcquisition::fft_size)
        .def_property_readonly("quality_flags", [](const sdr_core::SweepSegmentAcquisition& value) {
            return static_cast<std::uint32_t>(value.quality_flags);
        });

    py::class_<sdr_core::SweepProgressFrame>(module, "SweepProgressFrame")
        .def_property_readonly("statistics", [](const SweepProgressFrame& value) -> py::object {
            return value.statistics ? py::cast(*value.statistics) : py::none();
        })
        .def_property_readonly("source_id", [](const sdr_core::SweepProgressFrame& value) {
            return value.source.source_id;
        })
        .def_readonly("line_sequence", &sdr_core::SweepProgressFrame::line_sequence)
        .def_readonly("epoch", &sdr_core::SweepProgressFrame::epoch)
        .def_readonly("revision", &sdr_core::SweepProgressFrame::revision)
        .def_readonly("segment_acquisition", &sdr_core::SweepProgressFrame::segment_acquisition)
        .def_property_readonly("unit", [](const sdr_core::SweepProgressFrame& value) {
            return std::string(sdr_core::to_wire(value.unit));
        })
        .def_property_readonly("frequencies_hz", [](const sdr_core::SweepProgressFrame& value) {
            return readonly_shared_vector(value.frequencies_hz);
        })
        .def_property_readonly("values", [](const sdr_core::SweepProgressFrame& value) {
            return readonly_shared_vector(value.values);
        })
        .def_property_readonly("quality_flags_per_bin", [](const sdr_core::SweepProgressFrame& value) {
            return readonly_shared_vector(value.quality_flags_per_bin);
        })
        .def_property_readonly("source_segment_indices", [](const sdr_core::SweepProgressFrame& value) {
            return readonly_shared_vector(value.source_segment_indices);
        })
        .def_readonly("pending_segment_indices", &sdr_core::SweepProgressFrame::pending_segment_indices)
        .def_property_readonly("acquired_segment_generations", [](const sdr_core::SweepProgressFrame& value) {
            std::vector<std::pair<std::uint32_t, std::uint64_t>> result;
            for (const auto& item : value.acquired_segments) {
                result.emplace_back(item.segment_index, item.config_generation);
            }
            return result;
        });

    py::class_<sdr_core::SweepLineFrame>(module, "SweepLineFrame")
        .def_property_readonly("statistics", [](const SweepLineFrame& value) -> py::object {
            return value.statistics ? py::cast(*value.statistics) : py::none();
        })
        .def_property_readonly("source_id", [](const sdr_core::SweepLineFrame& value) {
            return value.source.source_id;
        })
        .def_readonly("line_sequence", &sdr_core::SweepLineFrame::line_sequence)
        .def_readonly("epoch", &sdr_core::SweepLineFrame::epoch)
        .def_readonly("completed_ns", &sdr_core::SweepLineFrame::completed_ns)
        .def_property_readonly("state", [](const sdr_core::SweepLineFrame& value) {
            return std::string(sdr_core::to_wire(value.state));
        })
        .def_readonly("start_frequency_hz", &sdr_core::SweepLineFrame::start_frequency_hz)
        .def_readonly("stop_frequency_hz", &sdr_core::SweepLineFrame::stop_frequency_hz)
        .def_readonly("target_spacing_hz", &sdr_core::SweepLineFrame::target_spacing_hz)
        .def_readonly("analysis_window_hz", &sdr_core::SweepLineFrame::analysis_window_hz)
        .def_readonly("analysis_bins_per_usable_window", &sdr_core::SweepLineFrame::analysis_bins_per_usable_window)
        .def_readonly("physical_fft_bin_width_hz", &sdr_core::SweepLineFrame::physical_fft_bin_width_hz)
        .def_readonly("physical_fft_size", &sdr_core::SweepLineFrame::physical_fft_size)
        .def_property_readonly("unit", [](const sdr_core::SweepLineFrame& value) {
            return std::string(sdr_core::to_wire(value.unit));
        })
        .def_property_readonly("frequencies_hz", [](const sdr_core::SweepLineFrame& value) {
            return readonly_shared_vector(value.frequencies_hz);
        })
        .def_property_readonly("values", [](const sdr_core::SweepLineFrame& value) {
            return readonly_shared_vector(value.values);
        })
        .def_property_readonly("quality_flags_per_bin", [](const sdr_core::SweepLineFrame& value) {
            return readonly_shared_vector(value.quality_flags_per_bin);
        })
        .def_property_readonly("source_segment_indices", [](const sdr_core::SweepLineFrame& value) {
            return readonly_shared_vector(value.source_segment_indices);
        })
        .def_readonly("missing_segment_indices", &sdr_core::SweepLineFrame::missing_segment_indices)
        .def_readonly("segment_acquisition", &sdr_core::SweepLineFrame::acquired_segments)
        .def_property_readonly("segment_config_generations", [](const sdr_core::SweepLineFrame& value) {
            std::vector<std::pair<std::uint32_t, std::uint64_t>> result;
            result.reserve(value.segment_generations.size());
            for (const auto& item : value.segment_generations) {
                result.emplace_back(item.segment_index, item.config_generation);
            }
            return result;
        })
        .def_property_readonly("gap_reasons", [](const sdr_core::SweepLineFrame& value) {
            std::vector<std::string> result;
            result.reserve(value.gap_reasons.size());
            for (const auto item : value.gap_reasons) {
                result.emplace_back(sdr_core::to_wire(item));
            }
            return result;
        });

    py::class_<sdr_pluto::ContinuousSweepLineConfig>(module, "ContinuousSweepLineConfig")
        .def(py::init([](
            const bool enabled,
            const std::uint64_t epoch,
            const double display_start_hz,
            const double display_stop_hz,
            const double usable_window_hz,
            const std::uint32_t output_queue_capacity,
            const double line_snapshot_rate_hz,
            const std::uint32_t analysis_bins_per_usable_window
        ) {
            sdr_pluto::ContinuousSweepLineConfig result{
                .enabled = enabled,
                .epoch = epoch,
                .display_start_hz = display_start_hz,
                .display_stop_hz = display_stop_hz,
                .usable_window_hz = usable_window_hz,
                .output_queue_capacity = output_queue_capacity,
                .analysis_bins_per_usable_window = analysis_bins_per_usable_window,
                .line_snapshot_rate_hz = line_snapshot_rate_hz,
            };
            sdr_pluto::validate(result);
            return result;
        }),
            py::arg("enabled") = false,
            py::arg("epoch") = 0U,
            py::arg("display_start_hz") = 0.0,
            py::arg("display_stop_hz") = 0.0,
            py::arg("usable_window_hz") = 0.0,
            py::arg("output_queue_capacity") = 4U,
            py::arg("line_snapshot_rate_hz") = 60.0,
            py::arg("analysis_bins_per_usable_window") = 0U
        )
        .def_readonly("enabled", &sdr_pluto::ContinuousSweepLineConfig::enabled)
        .def_readonly("epoch", &sdr_pluto::ContinuousSweepLineConfig::epoch)
        .def_readonly("display_start_hz", &sdr_pluto::ContinuousSweepLineConfig::display_start_hz)
        .def_readonly("display_stop_hz", &sdr_pluto::ContinuousSweepLineConfig::display_stop_hz)
        .def_readonly("usable_window_hz", &sdr_pluto::ContinuousSweepLineConfig::usable_window_hz)
        .def_readonly("output_queue_capacity", &sdr_pluto::ContinuousSweepLineConfig::output_queue_capacity)
        .def_readonly("analysis_bins_per_usable_window", &sdr_pluto::ContinuousSweepLineConfig::analysis_bins_per_usable_window)
        .def_readonly("line_snapshot_rate_hz", &sdr_pluto::ContinuousSweepLineConfig::line_snapshot_rate_hz);

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
            const PersistenceConfig& persistence,
            const RecordingConfig& recording,
            const std::optional<sdr_pluto::ContinuousSweepLineConfig>& continuous_sweep_line
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
            result.recording = recording;
            result.continuous_sweep_line = continuous_sweep_line;
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
            py::arg("persistence") = PersistenceConfig{},
            py::arg("recording") = RecordingConfig{},
            py::arg("continuous_sweep_line") = std::nullopt
        )
        .def_readonly("device", &sdr_pluto::FixedBandConfig::device)
        .def_readonly("dsp", &sdr_pluto::FixedBandConfig::dsp)
        .def_readonly("persistence", &sdr_pluto::FixedBandConfig::persistence)
        .def_readonly("recording", &sdr_pluto::FixedBandConfig::recording)
        .def_readonly(
            "continuous_sweep_line", &sdr_pluto::FixedBandConfig::continuous_sweep_line
        )
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
        .def_readonly("recorder_queue", &sdr_pluto::FixedBandMetrics::recorder_queue)
        .def_readonly("spectrum_queue", &sdr_pluto::FixedBandMetrics::spectrum_queue)
        .def_readonly("sweep_line_queue", &sdr_pluto::FixedBandMetrics::sweep_line_queue)
        .def_readonly(
            "spectrum_recorder_queue",
            &sdr_pluto::FixedBandMetrics::spectrum_recorder_queue
        )
        .def_readonly("persistence_queue", &sdr_pluto::FixedBandMetrics::persistence_queue)
        .def_readonly(
            "acquisition_queue_blocks_dropped",
            &sdr_pluto::FixedBandMetrics::acquisition_queue_blocks_dropped
        )
        .def_readonly(
            "acquisition_queue_samples_dropped",
            &sdr_pluto::FixedBandMetrics::acquisition_queue_samples_dropped
        )
        .def_readonly("source_sequence_discontinuities", &sdr_pluto::FixedBandMetrics::source_sequence_discontinuities)
        .def_readonly("source_sample_index_discontinuities", &sdr_pluto::FixedBandMetrics::source_sample_index_discontinuities)
        .def_readonly("source_timestamp_regressions", &sdr_pluto::FixedBandMetrics::source_timestamp_regressions)
        .def_readonly("source_estimated_timestamp_blocks", &sdr_pluto::FixedBandMetrics::source_estimated_timestamp_blocks)
        .def_readonly("hardware_overflow_counter_available", &sdr_pluto::FixedBandMetrics::hardware_overflow_counter_available)
        .def_readonly(
            "recorder_queue_blocks_dropped",
            &sdr_pluto::FixedBandMetrics::recorder_queue_blocks_dropped
        )
        .def_readonly(
            "recorder_queue_samples_dropped",
            &sdr_pluto::FixedBandMetrics::recorder_queue_samples_dropped
        )
        .def_readonly(
            "recorder_shutdown_blocks_discarded",
            &sdr_pluto::FixedBandMetrics::recorder_shutdown_blocks_discarded
        )
        .def_readonly(
            "recorder_shutdown_samples_discarded",
            &sdr_pluto::FixedBandMetrics::recorder_shutdown_samples_discarded
        )
        .def_readonly(
            "recorder_writer_blocks_written",
            &sdr_pluto::FixedBandMetrics::recorder_writer_blocks_written
        )
        .def_readonly(
            "recorder_writer_samples_written",
            &sdr_pluto::FixedBandMetrics::recorder_writer_samples_written
        )
        .def_readonly(
            "recorder_writer_bytes_written",
            &sdr_pluto::FixedBandMetrics::recorder_writer_bytes_written
        )
        .def_readonly(
            "recorder_writer_blocks_unavailable",
            &sdr_pluto::FixedBandMetrics::recorder_writer_blocks_unavailable
        )
        .def_readonly(
            "recorder_writer_samples_unavailable",
            &sdr_pluto::FixedBandMetrics::recorder_writer_samples_unavailable
        )
        .def_readonly(
            "recorder_writer_failed",
            &sdr_pluto::FixedBandMetrics::recorder_writer_failed
        )
        .def_readonly(
            "spectrum_recorder_frames_dropped",
            &sdr_pluto::FixedBandMetrics::spectrum_recorder_frames_dropped
        )
        .def_readonly(
            "spectrum_recorder_frames_unavailable",
            &sdr_pluto::FixedBandMetrics::spectrum_recorder_frames_unavailable
        )
        .def_readonly(
            "spectrum_recorder_shutdown_frames_discarded",
            &sdr_pluto::FixedBandMetrics::spectrum_recorder_shutdown_frames_discarded
        )
        .def_readonly(
            "spectrum_writer_frames_written",
            &sdr_pluto::FixedBandMetrics::spectrum_writer_frames_written
        )
        .def_readonly(
            "spectrum_writer_bytes_written",
            &sdr_pluto::FixedBandMetrics::spectrum_writer_bytes_written
        )
        .def_readonly(
            "spectrum_writer_failed",
            &sdr_pluto::FixedBandMetrics::spectrum_writer_failed
        )
        .def_readonly("transient_blocks_discarded", &sdr_pluto::FixedBandMetrics::transient_blocks_discarded)
        .def_readonly("transient_samples_discarded", &sdr_pluto::FixedBandMetrics::transient_samples_discarded)
        .def_readonly("spectrum_snapshots_superseded", &sdr_pluto::FixedBandMetrics::spectrum_snapshots_superseded)
        .def_readonly(
            "sweep_line_snapshots_superseded",
            &sdr_pluto::FixedBandMetrics::sweep_line_snapshots_superseded
        )
        .def_readonly("completed_sweep_lines", &sdr_pluto::FixedBandMetrics::completed_sweep_lines)
        .def_readonly("gapped_sweep_lines", &sdr_pluto::FixedBandMetrics::gapped_sweep_lines)
        .def_readonly(
            "sweep_line_capacity_evicted",
            &sdr_pluto::FixedBandMetrics::sweep_line_capacity_evicted
        )
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

    py::class_<sdr_pluto::LatestSpectrumFrameDrain>(module, "LatestSpectrumFrameDrain")
        .def_readonly("frame", &sdr_pluto::LatestSpectrumFrameDrain::frame)
        .def_readonly(
            "coalesced_frames", &sdr_pluto::LatestSpectrumFrameDrain::coalesced_frames
        );

    py::class_<sdr_pluto::ContinuousSweepSegmentConfig>(module, "ContinuousSweepSegmentConfig")
        .def(py::init([](
            const sdr_pluto::FixedBandConfig& fixed_band,
            const double usable_start_hz,
            const double usable_stop_hz
        ) {
            return sdr_pluto::ContinuousSweepSegmentConfig{
                .fixed_band = fixed_band,
                .usable_start_hz = usable_start_hz,
                .usable_stop_hz = usable_stop_hz,
            };
        }), py::arg("fixed_band"), py::arg("usable_start_hz"), py::arg("usable_stop_hz"))
        .def_readonly("fixed_band", &sdr_pluto::ContinuousSweepSegmentConfig::fixed_band)
        .def_readonly("usable_start_hz", &sdr_pluto::ContinuousSweepSegmentConfig::usable_start_hz)
        .def_readonly("usable_stop_hz", &sdr_pluto::ContinuousSweepSegmentConfig::usable_stop_hz);

    py::class_<sdr_pluto::ContinuousSweepCoordinatorConfig>(module, "ContinuousSweepCoordinatorConfig")
        .def(py::init([](
            const std::uint64_t epoch,
            const double display_start_hz,
            const double display_stop_hz,
            const std::vector<sdr_pluto::ContinuousSweepSegmentConfig>& segments,
            const std::uint32_t output_queue_capacity,
            const std::uint32_t segment_frame_timeout_ms,
            const double usable_window_hz,
            const std::uint32_t analysis_bins_per_usable_window,
            const double line_snapshot_rate_hz,
            const std::optional<SweepStatisticsConfig>& statistics,
            const double statistics_snapshot_rate_hz
        ) {
            sdr_pluto::ContinuousSweepCoordinatorConfig result{
                .epoch = epoch,
                .display_start_hz = display_start_hz,
                .display_stop_hz = display_stop_hz,
                .usable_window_hz = usable_window_hz,
                .analysis_bins_per_usable_window = analysis_bins_per_usable_window,
                .line_snapshot_rate_hz = line_snapshot_rate_hz,
                .output_queue_capacity = output_queue_capacity,
                .segment_frame_timeout_ms = segment_frame_timeout_ms,
                .segments = segments,
                .statistics = statistics,
                .statistics_snapshot_rate_hz = statistics_snapshot_rate_hz,
            };
            sdr_pluto::validate(result);
            return result;
        }),
            py::arg("epoch"), py::arg("display_start_hz"), py::arg("display_stop_hz"),
            py::arg("segments"),
            py::arg("output_queue_capacity") = 4U,
            py::arg("segment_frame_timeout_ms") = 1000U,
            py::arg("usable_window_hz") = 0.0,
            py::arg("analysis_bins_per_usable_window") = 0U,
            py::arg("line_snapshot_rate_hz") = 0.0,
            py::arg("statistics") = py::none(),
            py::arg("statistics_snapshot_rate_hz") = 15.0)
        .def_readonly("statistics", &sdr_pluto::ContinuousSweepCoordinatorConfig::statistics)
        .def_readonly("statistics_snapshot_rate_hz", &sdr_pluto::ContinuousSweepCoordinatorConfig::statistics_snapshot_rate_hz)
        .def_readonly("epoch", &sdr_pluto::ContinuousSweepCoordinatorConfig::epoch)
        .def_readonly("display_start_hz", &sdr_pluto::ContinuousSweepCoordinatorConfig::display_start_hz)
        .def_readonly("display_stop_hz", &sdr_pluto::ContinuousSweepCoordinatorConfig::display_stop_hz)
        .def_readonly("usable_window_hz", &sdr_pluto::ContinuousSweepCoordinatorConfig::usable_window_hz)
        .def_readonly("analysis_bins_per_usable_window", &sdr_pluto::ContinuousSweepCoordinatorConfig::analysis_bins_per_usable_window)
        .def_readonly("line_snapshot_rate_hz", &sdr_pluto::ContinuousSweepCoordinatorConfig::line_snapshot_rate_hz)
        .def_readonly("output_queue_capacity", &sdr_pluto::ContinuousSweepCoordinatorConfig::output_queue_capacity)
        .def_readonly("segment_frame_timeout_ms", &sdr_pluto::ContinuousSweepCoordinatorConfig::segment_frame_timeout_ms)
        .def_readonly("segments", &sdr_pluto::ContinuousSweepCoordinatorConfig::segments);

    py::class_<sdr_pluto::ContinuousSweepCoordinatorMetrics>(module, "ContinuousSweepCoordinatorMetrics")
        .def_readonly("state", &sdr_pluto::ContinuousSweepCoordinatorMetrics::state)
        .def_readonly("has_error", &sdr_pluto::ContinuousSweepCoordinatorMetrics::has_error)
        .def_readonly("output_queue", &sdr_pluto::ContinuousSweepCoordinatorMetrics::output_queue)
        .def_readonly("completed_lines", &sdr_pluto::ContinuousSweepCoordinatorMetrics::completed_lines)
        .def_readonly("gapped_lines", &sdr_pluto::ContinuousSweepCoordinatorMetrics::gapped_lines)
        .def_readonly("line_relay_snapshots_superseded", &sdr_pluto::ContinuousSweepCoordinatorMetrics::line_relay_snapshots_superseded)
        .def_readonly("line_relay_queue_capacity", &sdr_pluto::ContinuousSweepCoordinatorMetrics::line_relay_queue_capacity)
        .def_readonly("line_relay_queue_high_water", &sdr_pluto::ContinuousSweepCoordinatorMetrics::line_relay_queue_high_water)
        .def_readonly("output_snapshots_superseded", &sdr_pluto::ContinuousSweepCoordinatorMetrics::output_snapshots_superseded)
        .def_readonly("segment_reconfigurations", &sdr_pluto::ContinuousSweepCoordinatorMetrics::segment_reconfigurations)
        .def_readonly("segment_frame_timeouts", &sdr_pluto::ContinuousSweepCoordinatorMetrics::segment_frame_timeouts)
        .def_readonly("terminal_control_gaps", &sdr_pluto::ContinuousSweepCoordinatorMetrics::terminal_control_gaps)
        .def_readonly("expected_cancellations", &sdr_pluto::ContinuousSweepCoordinatorMetrics::expected_cancellations)
        .def_readonly("device_iq_samples", &sdr_pluto::ContinuousSweepCoordinatorMetrics::device_iq_samples)
        .def_readonly("device_iq_blocks", &sdr_pluto::ContinuousSweepCoordinatorMetrics::device_iq_blocks)
        .def_readonly("analytical_fft_frames", &sdr_pluto::ContinuousSweepCoordinatorMetrics::analytical_fft_frames)
        .def_readonly("completed_current_generation_fft_frames", &sdr_pluto::ContinuousSweepCoordinatorMetrics::completed_current_generation_fft_frames)
        .def_readonly("completed_line_analysis_geometry_available", &sdr_pluto::ContinuousSweepCoordinatorMetrics::completed_line_analysis_geometry_available)
        .def_readonly("completed_line_analysis_window_hz", &sdr_pluto::ContinuousSweepCoordinatorMetrics::completed_line_analysis_window_hz)
        .def_readonly("completed_line_analysis_bins_per_usable_window", &sdr_pluto::ContinuousSweepCoordinatorMetrics::completed_line_analysis_bins_per_usable_window)
        .def_readonly("completed_line_physical_fft_bin_width_hz", &sdr_pluto::ContinuousSweepCoordinatorMetrics::completed_line_physical_fft_bin_width_hz)
        .def_readonly("completed_line_physical_fft_size", &sdr_pluto::ContinuousSweepCoordinatorMetrics::completed_line_physical_fft_size)
        .def_readonly("completed_line_analysis_geometry_mismatches", &sdr_pluto::ContinuousSweepCoordinatorMetrics::completed_line_analysis_geometry_mismatches)
        .def_readonly("source_short_reads", &sdr_pluto::ContinuousSweepCoordinatorMetrics::source_short_reads)
        .def_readonly("source_refill_errors", &sdr_pluto::ContinuousSweepCoordinatorMetrics::source_refill_errors)
        .def_readonly("source_output_pool_exhaustions", &sdr_pluto::ContinuousSweepCoordinatorMetrics::source_output_pool_exhaustions)
        .def_readonly("source_estimated_dropped_samples", &sdr_pluto::ContinuousSweepCoordinatorMetrics::source_estimated_dropped_samples)
        .def_readonly("acquisition_queue_blocks_dropped", &sdr_pluto::ContinuousSweepCoordinatorMetrics::acquisition_queue_blocks_dropped)
        .def_readonly("acquisition_queue_samples_dropped", &sdr_pluto::ContinuousSweepCoordinatorMetrics::acquisition_queue_samples_dropped)
        .def_readonly("source_sequence_discontinuities", &sdr_pluto::ContinuousSweepCoordinatorMetrics::source_sequence_discontinuities)
        .def_readonly("source_sample_index_discontinuities", &sdr_pluto::ContinuousSweepCoordinatorMetrics::source_sample_index_discontinuities)
        .def_readonly("source_timestamp_regressions", &sdr_pluto::ContinuousSweepCoordinatorMetrics::source_timestamp_regressions)
        .def_readonly("source_estimated_timestamp_blocks", &sdr_pluto::ContinuousSweepCoordinatorMetrics::source_estimated_timestamp_blocks)
        .def_readonly("hardware_overflow_counter_available", &sdr_pluto::ContinuousSweepCoordinatorMetrics::hardware_overflow_counter_available)
        .def_readonly("fft_frames_dropped", &sdr_pluto::ContinuousSweepCoordinatorMetrics::fft_frames_dropped)
        .def_readonly("acquisition_queue_high_water", &sdr_pluto::ContinuousSweepCoordinatorMetrics::acquisition_queue_high_water)
        .def_readonly("spectrum_queue_high_water", &sdr_pluto::ContinuousSweepCoordinatorMetrics::spectrum_queue_high_water);

    py::class_<sdr_pluto::ContinuousSweepCoordinator>(module, "NativeContinuousSweepCoordinator")
        .def(py::init<std::string, std::uint32_t>(), py::arg("uri"), py::arg("timeout_ms") = 3000U, py::call_guard<py::gil_scoped_release>())
        .def("configure", &sdr_pluto::ContinuousSweepCoordinator::configure, py::arg("config"), py::call_guard<py::gil_scoped_release>())
        .def("start", &sdr_pluto::ContinuousSweepCoordinator::start, py::call_guard<py::gil_scoped_release>())
        .def("request_stop", &sdr_pluto::ContinuousSweepCoordinator::request_stop, py::call_guard<py::gil_scoped_release>())
        .def("join", &sdr_pluto::ContinuousSweepCoordinator::join, py::call_guard<py::gil_scoped_release>())
        .def("stop", &sdr_pluto::ContinuousSweepCoordinator::stop, py::call_guard<py::gil_scoped_release>())
        .def("disconnect", &sdr_pluto::ContinuousSweepCoordinator::disconnect, py::call_guard<py::gil_scoped_release>())
        .def("state", &sdr_pluto::ContinuousSweepCoordinator::state, py::call_guard<py::gil_scoped_release>())
        .def("metrics", &sdr_pluto::ContinuousSweepCoordinator::metrics, py::call_guard<py::gil_scoped_release>())
        .def("applied_segments", &sdr_pluto::ContinuousSweepCoordinator::applied_segments, py::call_guard<py::gil_scoped_release>())
        .def("poll_lines", &sdr_pluto::ContinuousSweepCoordinator::poll_lines, py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def("poll_progress", &sdr_pluto::ContinuousSweepCoordinator::poll_progress, py::call_guard<py::gil_scoped_release>())
        .def("discard_lines", &sdr_pluto::ContinuousSweepCoordinator::discard_lines, py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>());

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
        .def(
            "drain_latest_spectrum_frame",
            &sdr_pluto::FixedBandEngine::drain_latest_spectrum_frame,
            py::call_guard<py::gil_scoped_release>()
        )
        .def("poll_sweep_line_frames", &sdr_pluto::FixedBandEngine::poll_sweep_line_frames, py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def("poll_persistence_snapshots", &sdr_pluto::FixedBandEngine::poll_persistence_snapshots, py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>())
        .def("poll_events", &sdr_pluto::FixedBandEngine::poll_events, py::arg("max_items") = 0U, py::call_guard<py::gil_scoped_release>());

    py::class_<sdr_pluto::PlutoDevice>(module, "PlutoDevice")
        .def(py::init<std::string, std::uint32_t>(), py::arg("uri"), py::arg("timeout_ms") = 3000U, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("connected", &sdr_pluto::PlutoDevice::connected, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("streaming", &sdr_pluto::PlutoDevice::streaming, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("uri", &sdr_pluto::PlutoDevice::uri, py::call_guard<py::gil_scoped_release>())
        .def("probe", &sdr_pluto::PlutoDevice::probe, py::call_guard<py::gil_scoped_release>())
        .def("capabilities", &sdr_pluto::PlutoDevice::capabilities, py::call_guard<py::gil_scoped_release>())
        // Preserve the pre-R10-E1 positional Python contract:
        // ``configure(config, pool_blocks)``.  The receiver-aware overload
        // requires an explicit enum, so an existing integer can never be
        // silently reinterpreted as RX2 or BOTH.
        .def(
            "configure",
            [](sdr_pluto::PlutoDevice& device,
               const DeviceConfig& config,
               const std::uint32_t output_pool_blocks) {
                return device.configure(config, output_pool_blocks);
            },
            py::arg("config"),
            py::arg("output_pool_blocks") = 8U,
            py::call_guard<py::gil_scoped_release>()
        )
        .def(
            "configure",
            [](sdr_pluto::PlutoDevice& device,
               const DeviceConfig& config,
               const sdr_pluto::ReceiverSelection selection,
               const std::uint32_t output_pool_blocks) {
                return device.configure(config, selection, output_pool_blocks);
            },
            py::arg("config"),
            py::arg("selection"),
            py::arg("output_pool_blocks") = 8U,
            py::call_guard<py::gil_scoped_release>()
        )
        .def("applied_config", &sdr_pluto::PlutoDevice::applied_config, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("receiver_selection", &sdr_pluto::PlutoDevice::receiver_selection, py::call_guard<py::gil_scoped_release>())
        .def("start_stream", &sdr_pluto::PlutoDevice::start_stream, py::call_guard<py::gil_scoped_release>())
        .def("refill", &sdr_pluto::PlutoDevice::refill, py::call_guard<py::gil_scoped_release>())
        .def("refill_receivers", &sdr_pluto::PlutoDevice::refill_receivers, py::call_guard<py::gil_scoped_release>())
        .def("cancel", &sdr_pluto::PlutoDevice::cancel, py::call_guard<py::gil_scoped_release>())
        .def("stop_stream", &sdr_pluto::PlutoDevice::stop_stream, py::call_guard<py::gil_scoped_release>())
        .def("disconnect", &sdr_pluto::PlutoDevice::disconnect, py::call_guard<py::gil_scoped_release>())
        .def("metrics", &sdr_pluto::PlutoDevice::metrics, py::call_guard<py::gil_scoped_release>());

    module.def("pluto_runtime_info", &sdr_pluto::runtime_info, py::call_guard<py::gil_scoped_release>());
    module.def("scan_pluto_contexts", &sdr_pluto::scan_contexts, py::arg("filter") = "usb,ip", py::call_guard<py::gil_scoped_release>());
    module.def("probe_pluto_context", &sdr_pluto::probe_context, py::arg("uri"), py::arg("timeout_ms") = 3000U, py::call_guard<py::gil_scoped_release>());
    module.def("probe_pluto_receiver_topology", &sdr_pluto::probe_receiver_topology, py::arg("uri"), py::arg("timeout_ms") = 3000U, py::call_guard<py::gil_scoped_release>());
}

}  // namespace sdr_core::python

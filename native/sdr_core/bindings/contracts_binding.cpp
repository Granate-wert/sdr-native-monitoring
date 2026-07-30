#include "contracts_binding.hpp"

#include "sdr_core/capabilities.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/metrics.hpp"
#include "sdr_core/types.hpp"

#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <pybind11/numpy.h>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace sdr_core::python {
namespace {

template <typename T>
py::array immutable_array(const std::shared_ptr<const std::vector<T>>& storage) {
    if (!storage) {
        py::array_t<T> result(static_cast<py::ssize_t>(0));
        result.attr("setflags")(false);
        return result;
    }
    auto* owner = new std::shared_ptr<const std::vector<T>>(storage);
    py::capsule capsule(owner, [](void* pointer) {
        delete static_cast<std::shared_ptr<const std::vector<T>>*>(pointer);
    });
    py::array result(
        py::dtype::of<T>(),
        {static_cast<py::ssize_t>(storage->size())},
        {static_cast<py::ssize_t>(sizeof(T))},
        const_cast<T*>(storage->data()),
        capsule
    );
    result.attr("setflags")(false);
    return result;
}

template <typename Enum>
void add_wire(
    py::dict& target,
    const char* name,
    const std::initializer_list<std::pair<const char*, Enum>>& values
) {
    py::dict entries;
    for (const auto& [entry_name, value] : values) {
        entries[entry_name] = std::string(to_wire(value));
    }
    target[name] = entries;
}

py::dict contract_schema() {
    py::dict enums;
    add_wire<SourceType>(
        enums,
        "SourceType",
        {
            {"DFL_FILE", SourceType::DflFile},
            {"LIVE_IQ", SourceType::LiveIq},
            {"RECORDED_IQ", SourceType::RecordedIq},
            {"LIVE_SCALAR_SWEEP", SourceType::LiveScalarSweep},
            {"RECORDED_SPECTRUM", SourceType::RecordedSpectrum},
            {"SYNTHETIC", SourceType::Synthetic},
        }
    );
    add_wire<SpectrumUnit>(
        enums,
        "SpectrumUnit",
        {
            {"DBFS_BIN", SpectrumUnit::DbfsBin},
            {"DBFS_HZ", SpectrumUnit::DbfsHz},
            {"DBM_BIN", SpectrumUnit::DbmBin},
            {"DBM_HZ", SpectrumUnit::DbmHz},
            {"DBM", SpectrumUnit::Dbm},
        }
    );
    add_wire<SampleFormat>(
        enums,
        "SampleFormat",
        {
            {"COMPLEX_INT8_INTERLEAVED", SampleFormat::ComplexInt8Interleaved},
            {"COMPLEX_INT12_IN_INT16_LE", SampleFormat::ComplexInt12InInt16Le},
            {"COMPLEX_INT16_LE", SampleFormat::ComplexInt16Le},
            {"COMPLEX_FLOAT32_LE", SampleFormat::ComplexFloat32Le},
        }
    );
    add_wire<WindowType>(
        enums,
        "WindowType",
        {
            {"RECTANGULAR", WindowType::Rectangular},
            {"HANN", WindowType::Hann},
            {"BLACKMAN_HARRIS_4TERM", WindowType::BlackmanHarris4Term},
            {"FLAT_TOP", WindowType::FlatTop},
            {"NUTTALL", WindowType::Nuttall},
            {"KAISER", WindowType::Kaiser},
        }
    );
    add_wire<DetectorType>(
        enums,
        "DetectorType",
        {
            {"SAMPLE", DetectorType::Sample},
            {"PEAK", DetectorType::Peak},
            {"NEGATIVE_PEAK", DetectorType::NegativePeak},
            {"RMS", DetectorType::Rms},
            {"AVERAGE_POWER", DetectorType::AveragePower},
        }
    );
    add_wire<GainMode>(
        enums,
        "GainMode",
        {
            {"MANUAL", GainMode::Manual},
            {"SLOW_ATTACK", GainMode::SlowAttack},
            {"FAST_ATTACK", GainMode::FastAttack},
            {"HYBRID", GainMode::Hybrid},
        }
    );
    add_wire<DeviceState>(
        enums,
        "DeviceState",
        {
            {"DISCONNECTED", DeviceState::Disconnected},
            {"CONNECTING", DeviceState::Connecting},
            {"IDLE", DeviceState::Idle},
            {"CONFIGURING", DeviceState::Configuring},
            {"STREAMING", DeviceState::Streaming},
            {"SWEEPING", DeviceState::Sweeping},
            {"RECORDING", DeviceState::Recording},
            {"DEGRADED", DeviceState::Degraded},
            {"ERROR", DeviceState::Error},
            {"STOPPING", DeviceState::Stopping},
            {"SHUTTING_DOWN", DeviceState::ShuttingDown},
        }
    );
    add_wire<CalibrationStatus>(
        enums,
        "CalibrationStatus",
        {
            {"NOT_APPLICABLE", CalibrationStatus::NotApplicable},
            {"UNCALIBRATED", CalibrationStatus::Uncalibrated},
            {"APPLIED", CalibrationStatus::Applied},
            {"INTERPOLATED", CalibrationStatus::Interpolated},
            {"EXTRAPOLATED", CalibrationStatus::Extrapolated},
            {"INVALID", CalibrationStatus::Invalid},
        }
    );
    add_wire<BackendKind>(
        enums,
        "BackendKind",
        {{"CPU", BackendKind::Cpu}, {"CUDA", BackendKind::Cuda}}
    );
    add_wire<PrecisionMode>(
        enums,
        "PrecisionMode",
        {
            {"REFERENCE_F64", PrecisionMode::ReferenceF64},
            {"ACCURATE_F32_F64_ACCUM", PrecisionMode::AccurateF32F64Accum},
            {"FAST_F32", PrecisionMode::FastF32},
        }
    );
    add_wire<PersistenceMode>(
        enums,
        "PersistenceMode",
        {
            {"DISABLED", PersistenceMode::Disabled},
            {"ROLLING_EXACT", PersistenceMode::RollingExact},
            {"EXPONENTIAL_DECAY", PersistenceMode::ExponentialDecay},
        }
    );
    add_wire<EngineState>(
        enums,
        "EngineState",
        {
            {"CREATED", EngineState::Created},
            {"CONFIGURED", EngineState::Configured},
            {"RUNNING", EngineState::Running},
            {"STOPPING", EngineState::Stopping},
            {"STOPPED", EngineState::Stopped},
            {"ERROR", EngineState::Error},
        }
    );
    add_wire<OverflowPolicy>(
        enums,
        "OverflowPolicy",
        {
            {"BLOCK", OverflowPolicy::Block},
            {"DROP_NEWEST", OverflowPolicy::DropNewest},
            {"DROP_OLDEST", OverflowPolicy::DropOldest},
            {"LATEST_WINS", OverflowPolicy::LatestWins},
        }
    );
    add_wire<EventSeverity>(
        enums,
        "EventSeverity",
        {
            {"INFO", EventSeverity::Info},
            {"WARNING", EventSeverity::Warning},
            {"ERROR", EventSeverity::Error},
            {"CRITICAL", EventSeverity::Critical},
        }
    );
    py::dict quality;
    quality["NONE"] = 0U;
    quality["UNCALIBRATED"] = static_cast<std::uint32_t>(QualityFlag::Uncalibrated);
    quality["CALIBRATION_INTERPOLATED"] =
        static_cast<std::uint32_t>(QualityFlag::CalibrationInterpolated);
    quality["CALIBRATION_EXTRAPOLATED"] =
        static_cast<std::uint32_t>(QualityFlag::CalibrationExtrapolated);
    quality["GAIN_MODE_AGC"] = static_cast<std::uint32_t>(QualityFlag::GainModeAgc);
    quality["ADC_OVERLOAD"] = static_cast<std::uint32_t>(QualityFlag::AdcOverload);
    quality["IQ_DROPPED"] = static_cast<std::uint32_t>(QualityFlag::IqDropped);
    quality["FFT_DROPPED"] = static_cast<std::uint32_t>(QualityFlag::FftDropped);
    quality["SETTLING_INCOMPLETE"] =
        static_cast<std::uint32_t>(QualityFlag::SettlingIncomplete);
    quality["EDGE_BIN"] = static_cast<std::uint32_t>(QualityFlag::EdgeBin);
    quality["DC_REMOVED"] = static_cast<std::uint32_t>(QualityFlag::DcRemoved);
    quality["LO_LEAKAGE_REGION"] =
        static_cast<std::uint32_t>(QualityFlag::LoLeakageRegion);
    quality["STITCH_OVERLAP"] = static_cast<std::uint32_t>(QualityFlag::StitchOverlap);
    quality["MISSING_SEGMENT"] = static_cast<std::uint32_t>(QualityFlag::MissingSegment);
    quality["TIMESTAMP_ESTIMATED"] =
        static_cast<std::uint32_t>(QualityFlag::TimestampEstimated);
    quality["CUDA_FALLBACK"] = static_cast<std::uint32_t>(QualityFlag::CudaFallback);
    enums["QualityFlag"] = quality;

    py::dict result;
    result["schema"] = std::string(contract_schema_name);
    result["schema_version"] = contract_schema_version;
    result["enums"] = enums;
    return result;
}

IqBlock make_test_iq_block(const std::uint32_t sample_count, const SampleFormat format) {
    std::size_t width = 0U;
    switch (format) {
    case SampleFormat::ComplexInt8Interleaved:
        width = 2U;
        break;
    case SampleFormat::ComplexInt12InInt16Le:
    case SampleFormat::ComplexInt16Le:
        width = 4U;
        break;
    case SampleFormat::ComplexFloat32Le:
        width = 8U;
        break;
    }
    auto samples = std::make_shared<std::vector<std::uint8_t>>(
        static_cast<std::size_t>(sample_count) * width
    );
    for (std::size_t index = 0; index < samples->size(); ++index) {
        (*samples)[index] = static_cast<std::uint8_t>(index & 0xFFU);
    }
    IqBlock result{
        .source_sequence = 1U,
        .first_sample_index = 0U,
        .timestamp_ns = 1,
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = 2'000'000.0,
        .sample_format = format,
        .sample_count = sample_count,
        .flags = QualityFlag::TimestampEstimated,
        .samples = samples,
        .config_generation = 1U,
    };
    validate(result);
    return result;
}

SpectrumFrame make_test_spectrum_frame(
    const std::uint32_t point_count,
    const std::uint64_t frame_sequence,
    const SpectrumUnit unit,
    const CalibrationStatus calibration_status,
    const std::string& profile_id
) {
    auto frequencies = std::make_shared<std::vector<double>>(point_count);
    auto values = std::make_shared<std::vector<float>>(point_count);
    for (std::uint32_t index = 0; index < point_count; ++index) {
        (*frequencies)[index] = 99'000'000.0 + 1'000.0 * static_cast<double>(index);
        (*values)[index] = -90.0F + static_cast<float>(index % 20U);
    }
    SourceDescriptor source{
        .source_type = SourceType::Synthetic,
        .source_id = "p02-test-source",
        .display_name = "P02 test source",
        .uri = "synthetic:p02",
        .device_serial = "",
        .backend_id = "cpu",
        .schema_version = contract_schema_version,
        .metadata_json = {{"generator", "\"p02\""}},
    };
    SpectrumFrame result{
        .source = std::move(source),
        .frame_sequence = frame_sequence,
        .first_sample_index = frame_sequence * point_count,
        .timestamp_ns = static_cast<std::int64_t>(frame_sequence),
        .config_generation = 1U,
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = 2'000'000.0,
        .analog_bandwidth_hz = 1'500'000.0,
        .fft_bin_width_hz = 1'000.0,
        .enbw_hz = 1'500.0,
        .nominal_rbw_hz = 1'500.0,
        .fft_size = point_count,
        .hop_size = point_count,
        .window = WindowType::Hann,
        .detector = DetectorType::Sample,
        .precision_mode = PrecisionMode::AccurateF32F64Accum,
        .unit = unit,
        .frequencies_hz = frequencies,
        .values = values,
        .calibration_status = calibration_status,
        .calibration_profile_id = profile_id,
        .estimated_uncertainty_db = std::numeric_limits<double>::quiet_NaN(),
        .dropped_samples_before = 0U,
        .dropped_iq_blocks_before = 0U,
        .dropped_fft_frames_before = 0U,
        .quality_flags = unit == SpectrumUnit::DbfsBin || unit == SpectrumUnit::DbfsHz
                             ? QualityFlag::Uncalibrated
                             : QualityFlag::None,
    };
    validate(result);
    return result;
}

}  // namespace

void bind_contracts(py::module_& module) {
    py::enum_<SourceType>(module, "SourceType")
        .value("DFL_FILE", SourceType::DflFile)
        .value("LIVE_IQ", SourceType::LiveIq)
        .value("RECORDED_IQ", SourceType::RecordedIq)
        .value("LIVE_SCALAR_SWEEP", SourceType::LiveScalarSweep)
        .value("RECORDED_SPECTRUM", SourceType::RecordedSpectrum)
        .value("SYNTHETIC", SourceType::Synthetic);
    py::enum_<SpectrumUnit>(module, "SpectrumUnit")
        .value("DBFS_BIN", SpectrumUnit::DbfsBin)
        .value("DBFS_HZ", SpectrumUnit::DbfsHz)
        .value("DBM_BIN", SpectrumUnit::DbmBin)
        .value("DBM_HZ", SpectrumUnit::DbmHz)
        .value("DBM", SpectrumUnit::Dbm);
    py::enum_<SampleFormat>(module, "SampleFormat")
        .value("COMPLEX_INT8_INTERLEAVED", SampleFormat::ComplexInt8Interleaved)
        .value("COMPLEX_INT12_IN_INT16_LE", SampleFormat::ComplexInt12InInt16Le)
        .value("COMPLEX_INT16_LE", SampleFormat::ComplexInt16Le)
        .value("COMPLEX_FLOAT32_LE", SampleFormat::ComplexFloat32Le);
    py::enum_<WindowType>(module, "WindowType")
        .value("RECTANGULAR", WindowType::Rectangular)
        .value("HANN", WindowType::Hann)
        .value("BLACKMAN_HARRIS_4TERM", WindowType::BlackmanHarris4Term)
        .value("FLAT_TOP", WindowType::FlatTop)
        .value("NUTTALL", WindowType::Nuttall)
        .value("KAISER", WindowType::Kaiser);
    py::enum_<DetectorType>(module, "DetectorType")
        .value("SAMPLE", DetectorType::Sample)
        .value("PEAK", DetectorType::Peak)
        .value("NEGATIVE_PEAK", DetectorType::NegativePeak)
        .value("RMS", DetectorType::Rms)
        .value("AVERAGE_POWER", DetectorType::AveragePower);
    py::enum_<GainMode>(module, "GainMode")
        .value("MANUAL", GainMode::Manual)
        .value("SLOW_ATTACK", GainMode::SlowAttack)
        .value("FAST_ATTACK", GainMode::FastAttack)
        .value("HYBRID", GainMode::Hybrid);
    py::enum_<DeviceState>(module, "DeviceState")
        .value("DISCONNECTED", DeviceState::Disconnected)
        .value("CONNECTING", DeviceState::Connecting)
        .value("IDLE", DeviceState::Idle)
        .value("CONFIGURING", DeviceState::Configuring)
        .value("STREAMING", DeviceState::Streaming)
        .value("SWEEPING", DeviceState::Sweeping)
        .value("RECORDING", DeviceState::Recording)
        .value("DEGRADED", DeviceState::Degraded)
        .value("ERROR", DeviceState::Error)
        .value("STOPPING", DeviceState::Stopping)
        .value("SHUTTING_DOWN", DeviceState::ShuttingDown);
    py::enum_<CalibrationStatus>(module, "CalibrationStatus")
        .value("NOT_APPLICABLE", CalibrationStatus::NotApplicable)
        .value("UNCALIBRATED", CalibrationStatus::Uncalibrated)
        .value("APPLIED", CalibrationStatus::Applied)
        .value("INTERPOLATED", CalibrationStatus::Interpolated)
        .value("EXTRAPOLATED", CalibrationStatus::Extrapolated)
        .value("INVALID", CalibrationStatus::Invalid);
    py::enum_<QualityFlag>(module, "QualityFlag", py::arithmetic())
        .value("NONE", QualityFlag::None)
        .value("UNCALIBRATED", QualityFlag::Uncalibrated)
        .value("CALIBRATION_INTERPOLATED", QualityFlag::CalibrationInterpolated)
        .value("CALIBRATION_EXTRAPOLATED", QualityFlag::CalibrationExtrapolated)
        .value("GAIN_MODE_AGC", QualityFlag::GainModeAgc)
        .value("ADC_OVERLOAD", QualityFlag::AdcOverload)
        .value("IQ_DROPPED", QualityFlag::IqDropped)
        .value("FFT_DROPPED", QualityFlag::FftDropped)
        .value("SETTLING_INCOMPLETE", QualityFlag::SettlingIncomplete)
        .value("EDGE_BIN", QualityFlag::EdgeBin)
        .value("DC_REMOVED", QualityFlag::DcRemoved)
        .value("LO_LEAKAGE_REGION", QualityFlag::LoLeakageRegion)
        .value("STITCH_OVERLAP", QualityFlag::StitchOverlap)
        .value("MISSING_SEGMENT", QualityFlag::MissingSegment)
        .value("TIMESTAMP_ESTIMATED", QualityFlag::TimestampEstimated)
        .value("CUDA_FALLBACK", QualityFlag::CudaFallback);
    py::enum_<BackendKind>(module, "BackendKind")
        .value("CPU", BackendKind::Cpu)
        .value("CUDA", BackendKind::Cuda);
    py::enum_<PrecisionMode>(module, "PrecisionMode")
        .value("REFERENCE_F64", PrecisionMode::ReferenceF64)
        .value("ACCURATE_F32_F64_ACCUM", PrecisionMode::AccurateF32F64Accum)
        .value("FAST_F32", PrecisionMode::FastF32);
    py::enum_<PersistenceMode>(module, "PersistenceMode")
        .value("DISABLED", PersistenceMode::Disabled)
        .value("ROLLING_EXACT", PersistenceMode::RollingExact)
        .value("EXPONENTIAL_DECAY", PersistenceMode::ExponentialDecay);

    py::class_<SourceDescriptor>(module, "SourceDescriptor")
        .def(py::init([](
                 const SourceType source_type,
                 std::string source_id,
                 std::string display_name,
                 std::string uri,
                 std::string device_serial,
                 std::string backend_id,
                 const std::uint32_t schema_version,
                 std::map<std::string, std::string> metadata_json
             ) {
            SourceDescriptor result{
                source_type,
                std::move(source_id),
                std::move(display_name),
                std::move(uri),
                std::move(device_serial),
                std::move(backend_id),
                schema_version,
                std::move(metadata_json),
            };
            validate(result);
            return result;
        }))
        .def_readonly("source_type", &SourceDescriptor::source_type)
        .def_readonly("source_id", &SourceDescriptor::source_id)
        .def_readonly("display_name", &SourceDescriptor::display_name)
        .def_readonly("uri", &SourceDescriptor::uri)
        .def_readonly("device_serial", &SourceDescriptor::device_serial)
        .def_readonly("backend_id", &SourceDescriptor::backend_id)
        .def_readonly("schema_version", &SourceDescriptor::schema_version)
        .def_readonly("metadata_json", &SourceDescriptor::metadata_json);

    py::class_<NumericRange>(module, "NumericRange")
        .def(py::init([](const double minimum, const double maximum, std::optional<double> step) {
            NumericRange result{minimum, maximum, step};
            validate(result);
            return result;
        }))
        .def_readonly("minimum", &NumericRange::minimum)
        .def_readonly("maximum", &NumericRange::maximum)
        .def_readonly("step", &NumericRange::step)
        .def("contains", &NumericRange::contains);

    py::class_<DeviceConfig>(module, "DeviceConfig")
        .def(py::init([](
                 std::string source_id,
                 std::string context_uri,
                 const double center_frequency_hz,
                 const double sample_rate_hz,
                 const double analog_bandwidth_hz,
                 const GainMode gain_mode,
                 const double manual_gain_db,
                 const std::uint32_t channel_index,
                 const std::uint32_t buffer_samples,
                 const std::uint32_t schema_version
             ) {
            DeviceConfig result{
                std::move(source_id),
                std::move(context_uri),
                center_frequency_hz,
                sample_rate_hz,
                analog_bandwidth_hz,
                gain_mode,
                manual_gain_db,
                channel_index,
                buffer_samples,
                schema_version,
            };
            validate(result);
            return result;
        }))
        .def_readonly("source_id", &DeviceConfig::source_id)
        .def_readonly("context_uri", &DeviceConfig::context_uri)
        .def_readonly("center_frequency_hz", &DeviceConfig::center_frequency_hz)
        .def_readonly("sample_rate_hz", &DeviceConfig::sample_rate_hz)
        .def_readonly("analog_bandwidth_hz", &DeviceConfig::analog_bandwidth_hz)
        .def_readonly("gain_mode", &DeviceConfig::gain_mode)
        .def_readonly("manual_gain_db", &DeviceConfig::manual_gain_db)
        .def_readonly("channel_index", &DeviceConfig::channel_index)
        .def_readonly("buffer_samples", &DeviceConfig::buffer_samples)
        .def_readonly("schema_version", &DeviceConfig::schema_version);

    py::class_<DspConfig>(module, "DspConfig")
        .def(py::init([](
                 const std::uint32_t fft_size,
                 const std::uint32_t hop_size,
                 const WindowType window,
                 const DetectorType detector,
                 const SpectrumUnit unit,
                 const PrecisionMode precision_mode,
                 const std::uint32_t batch_size,
                 const std::uint32_t averaging_frames,
                 const double kaiser_beta,
                 const CalibrationStatus calibration_status,
                 std::string calibration_profile_id,
                 const std::uint32_t schema_version
             ) {
            DspConfig result{
                fft_size,
                hop_size,
                window,
                detector,
                unit,
                precision_mode,
                batch_size,
                averaging_frames,
                kaiser_beta,
                calibration_status,
                std::move(calibration_profile_id),
                schema_version,
            };
            validate(result);
            return result;
        }))
        .def_readonly("fft_size", &DspConfig::fft_size)
        .def_readonly("hop_size", &DspConfig::hop_size)
        .def_readonly("window", &DspConfig::window)
        .def_readonly("detector", &DspConfig::detector)
        .def_readonly("unit", &DspConfig::unit)
        .def_readonly("precision_mode", &DspConfig::precision_mode)
        .def_readonly("batch_size", &DspConfig::batch_size)
        .def_readonly("averaging_frames", &DspConfig::averaging_frames)
        .def_readonly("kaiser_beta", &DspConfig::kaiser_beta)
        .def_readonly("calibration_status", &DspConfig::calibration_status)
        .def_readonly("calibration_profile_id", &DspConfig::calibration_profile_id)
        .def_readonly("schema_version", &DspConfig::schema_version);

    py::class_<PersistenceConfig>(module, "PersistenceConfig")
        .def(py::init([](
                 const bool enabled,
                 const PersistenceMode mode,
                 const std::uint32_t window_frames,
                 const double half_life_seconds,
                 const double power_min_db,
                 const double power_max_db,
                 const std::uint32_t power_bins,
                 const double snapshot_rate_hz,
                 const std::uint32_t schema_version
             ) {
            PersistenceConfig result{
                enabled,
                mode,
                window_frames,
                half_life_seconds,
                power_min_db,
                power_max_db,
                power_bins,
                snapshot_rate_hz,
                schema_version,
            };
            validate(result);
            return result;
        }))
        .def_readonly("enabled", &PersistenceConfig::enabled)
        .def_readonly("mode", &PersistenceConfig::mode)
        .def_readonly("window_frames", &PersistenceConfig::window_frames)
        .def_readonly("half_life_seconds", &PersistenceConfig::half_life_seconds)
        .def_readonly("power_min_db", &PersistenceConfig::power_min_db)
        .def_readonly("power_max_db", &PersistenceConfig::power_max_db)
        .def_readonly("power_bins", &PersistenceConfig::power_bins)
        .def_readonly("snapshot_rate_hz", &PersistenceConfig::snapshot_rate_hz)
        .def_readonly("schema_version", &PersistenceConfig::schema_version);

    py::class_<SweepConfig>(module, "SweepConfig")
        .def(py::init([](
                 const double start_frequency_hz,
                 const double stop_frequency_hz,
                 const double sample_rate_hz,
                 const double analog_bandwidth_hz,
                 const double overlap_hz,
                 const std::uint32_t fft_size,
                 const std::uint32_t hop_size,
                 const std::uint32_t dwell_frames,
                 const double settling_time_seconds,
                 const std::uint32_t discard_blocks,
                 const std::uint32_t schema_version
             ) {
            SweepConfig result{
                start_frequency_hz,
                stop_frequency_hz,
                sample_rate_hz,
                analog_bandwidth_hz,
                overlap_hz,
                fft_size,
                hop_size,
                dwell_frames,
                settling_time_seconds,
                discard_blocks,
                schema_version,
            };
            validate(result);
            return result;
        }))
        .def_readonly("start_frequency_hz", &SweepConfig::start_frequency_hz)
        .def_readonly("stop_frequency_hz", &SweepConfig::stop_frequency_hz)
        .def_readonly("sample_rate_hz", &SweepConfig::sample_rate_hz)
        .def_readonly("analog_bandwidth_hz", &SweepConfig::analog_bandwidth_hz)
        .def_readonly("overlap_hz", &SweepConfig::overlap_hz)
        .def_readonly("fft_size", &SweepConfig::fft_size)
        .def_readonly("hop_size", &SweepConfig::hop_size)
        .def_readonly("dwell_frames", &SweepConfig::dwell_frames)
        .def_readonly("settling_time_seconds", &SweepConfig::settling_time_seconds)
        .def_readonly("discard_blocks", &SweepConfig::discard_blocks)
        .def_readonly("schema_version", &SweepConfig::schema_version);

    py::class_<RecordingConfig>(module, "RecordingConfig")
        .def(py::init([](
                 const bool enabled,
                 std::string output_uri,
                 const bool record_iq,
                 const bool record_spectrum,
                 const std::uint32_t chunk_samples,
                 const std::uint32_t queue_capacity,
                 const bool stop_on_overflow,
                 const std::uint32_t schema_version
             ) {
            RecordingConfig result{
                enabled,
                std::move(output_uri),
                record_iq,
                record_spectrum,
                chunk_samples,
                queue_capacity,
                stop_on_overflow,
                schema_version,
            };
            validate(result);
            return result;
        }))
        .def_readonly("enabled", &RecordingConfig::enabled)
        .def_readonly("output_uri", &RecordingConfig::output_uri)
        .def_readonly("record_iq", &RecordingConfig::record_iq)
        .def_readonly("record_spectrum", &RecordingConfig::record_spectrum)
        .def_readonly("chunk_samples", &RecordingConfig::chunk_samples)
        .def_readonly("queue_capacity", &RecordingConfig::queue_capacity)
        .def_readonly("stop_on_overflow", &RecordingConfig::stop_on_overflow)
        .def_readonly("schema_version", &RecordingConfig::schema_version);

    py::class_<IqBlock>(module, "IqBlock")
        .def_readonly("source_sequence", &IqBlock::source_sequence)
        .def_readonly("first_sample_index", &IqBlock::first_sample_index)
        .def_readonly("timestamp_ns", &IqBlock::timestamp_ns)
        .def_readonly("center_frequency_hz", &IqBlock::center_frequency_hz)
        .def_readonly("sample_rate_hz", &IqBlock::sample_rate_hz)
        .def_readonly("sample_format", &IqBlock::sample_format)
        .def_readonly("sample_count", &IqBlock::sample_count)
        .def_property_readonly("flags", [](const IqBlock& value) {
            return static_cast<std::uint32_t>(value.flags);
        })
        .def_property_readonly("samples", [](const IqBlock& value) {
            return immutable_array(value.samples);
        })
        .def_readonly("config_generation", &IqBlock::config_generation);

    py::class_<SpectrumFrame>(module, "SpectrumFrame")
        .def_readonly("source", &SpectrumFrame::source)
        .def_readonly("frame_sequence", &SpectrumFrame::frame_sequence)
        .def_readonly("first_sample_index", &SpectrumFrame::first_sample_index)
        .def_readonly("timestamp_ns", &SpectrumFrame::timestamp_ns)
        .def_readonly("config_generation", &SpectrumFrame::config_generation)
        .def_readonly("center_frequency_hz", &SpectrumFrame::center_frequency_hz)
        .def_readonly("sample_rate_hz", &SpectrumFrame::sample_rate_hz)
        .def_readonly("analog_bandwidth_hz", &SpectrumFrame::analog_bandwidth_hz)
        .def_readonly("fft_bin_width_hz", &SpectrumFrame::fft_bin_width_hz)
        .def_readonly("enbw_hz", &SpectrumFrame::enbw_hz)
        .def_readonly("nominal_rbw_hz", &SpectrumFrame::nominal_rbw_hz)
        .def_readonly("fft_size", &SpectrumFrame::fft_size)
        .def_readonly("hop_size", &SpectrumFrame::hop_size)
        .def_readonly("window", &SpectrumFrame::window)
        .def_readonly("detector", &SpectrumFrame::detector)
        .def_readonly("precision_mode", &SpectrumFrame::precision_mode)
        .def_readonly("unit", &SpectrumFrame::unit)
        .def_property_readonly("frequencies_hz", [](const SpectrumFrame& value) {
            return immutable_array(value.frequencies_hz);
        })
        .def_property_readonly("values", [](const SpectrumFrame& value) {
            return immutable_array(value.values);
        })
        .def_readonly("calibration_status", &SpectrumFrame::calibration_status)
        .def_readonly("calibration_profile_id", &SpectrumFrame::calibration_profile_id)
        .def_readonly("estimated_uncertainty_db", &SpectrumFrame::estimated_uncertainty_db)
        .def_readonly("dropped_samples_before", &SpectrumFrame::dropped_samples_before)
        .def_readonly("dropped_iq_blocks_before", &SpectrumFrame::dropped_iq_blocks_before)
        .def_readonly("dropped_fft_frames_before", &SpectrumFrame::dropped_fft_frames_before)
        .def_property_readonly("quality_flags", [](const SpectrumFrame& value) {
            return static_cast<std::uint32_t>(value.quality_flags);
        });

    module.attr("CONTRACT_SCHEMA_NAME") = std::string(contract_schema_name);
    module.attr("CONTRACT_SCHEMA_VERSION") = contract_schema_version;
    module.def("contract_schema", &contract_schema);
    module.def(
        "_make_test_iq_block",
        &make_test_iq_block,
        py::arg("sample_count"),
        py::arg("sample_format") = SampleFormat::ComplexInt16Le
    );
    module.def(
        "_make_test_spectrum_frame",
        &make_test_spectrum_frame,
        py::arg("point_count"),
        py::arg("frame_sequence") = 1U,
        py::arg("unit") = SpectrumUnit::DbfsBin,
        py::arg("calibration_status") = CalibrationStatus::Uncalibrated,
        py::arg("calibration_profile_id") = ""
    );
}

}  // namespace sdr_core::python

#pragma once

#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace sdr_core {

inline constexpr std::string_view contract_schema_name = "sdr-native-contracts";
// Version 2: P04 adds EngineState, OverflowPolicy and EventSeverity wire enums.
// Version 3: P05 restricts DspConfig.fft_size to power-of-two in [256, 262144].
inline constexpr std::uint32_t contract_schema_version = 3;

enum class SourceType : std::uint8_t {
    DflFile,
    LiveIq,
    RecordedIq,
    LiveScalarSweep,
    RecordedSpectrum,
    Synthetic,
};

enum class SpectrumUnit : std::uint8_t {
    DbfsBin,
    DbfsHz,
    DbmBin,
    DbmHz,
    Dbm,
};

enum class SampleFormat : std::uint8_t {
    ComplexInt8Interleaved,
    ComplexInt12InInt16Le,
    ComplexInt16Le,
    ComplexFloat32Le,
};

enum class WindowType : std::uint8_t {
    Rectangular,
    Hann,
    BlackmanHarris4Term,
    FlatTop,
    Nuttall,
    Kaiser,
};

enum class DetectorType : std::uint8_t {
    Sample,
    Peak,
    NegativePeak,
    Rms,
    AveragePower,
};

enum class GainMode : std::uint8_t {
    Manual,
    SlowAttack,
    FastAttack,
    Hybrid,
};

enum class DeviceState : std::uint8_t {
    Disconnected,
    Connecting,
    Idle,
    Configuring,
    Streaming,
    Sweeping,
    Recording,
    Degraded,
    Error,
    Stopping,
    ShuttingDown,
};

enum class CalibrationStatus : std::uint8_t {
    NotApplicable,
    Uncalibrated,
    Applied,
    Interpolated,
    Extrapolated,
    Invalid,
};

enum class QualityFlag : std::uint32_t {
    None = 0,
    Uncalibrated = 1U << 0U,
    CalibrationInterpolated = 1U << 1U,
    CalibrationExtrapolated = 1U << 2U,
    GainModeAgc = 1U << 3U,
    AdcOverload = 1U << 4U,
    IqDropped = 1U << 5U,
    FftDropped = 1U << 6U,
    SettlingIncomplete = 1U << 7U,
    EdgeBin = 1U << 8U,
    DcRemoved = 1U << 9U,
    LoLeakageRegion = 1U << 10U,
    StitchOverlap = 1U << 11U,
    MissingSegment = 1U << 12U,
    TimestampEstimated = 1U << 13U,
    CudaFallback = 1U << 14U,
};

enum class BackendKind : std::uint8_t {
    Cpu,
    Cuda,
};

enum class PrecisionMode : std::uint8_t {
    ReferenceF64,
    AccurateF32F64Accum,
    FastF32,
};

enum class PersistenceMode : std::uint8_t {
    Disabled,
    RollingExact,
    ExponentialDecay,
};

// P04 engine lifecycle state machine. Distinct from DeviceState, which
// describes an acquisition device, not the transport engine.
enum class EngineState : std::uint8_t {
    Created,
    Configured,
    Running,
    Stopping,
    Stopped,
    Error,
};

// P04 bounded queue overflow policy. Capacity never grows at runtime.
enum class OverflowPolicy : std::uint8_t {
    Block,
    DropNewest,
    DropOldest,
    LatestWins,
};

// P04 diagnostic event severity for the bounded event queue.
enum class EventSeverity : std::uint8_t {
    Info,
    Warning,
    Error,
    Critical,
};

[[nodiscard]] constexpr QualityFlag operator|(const QualityFlag left, const QualityFlag right) noexcept {
    return static_cast<QualityFlag>(
        static_cast<std::uint32_t>(left) | static_cast<std::uint32_t>(right)
    );
}

[[nodiscard]] constexpr bool has_flag(const QualityFlag value, const QualityFlag flag) noexcept {
    return (static_cast<std::uint32_t>(value) & static_cast<std::uint32_t>(flag)) != 0U;
}

[[nodiscard]] std::string_view to_wire(SourceType value);
[[nodiscard]] std::string_view to_wire(SpectrumUnit value);
[[nodiscard]] std::string_view to_wire(SampleFormat value);
[[nodiscard]] std::string_view to_wire(WindowType value);
[[nodiscard]] std::string_view to_wire(DetectorType value);
[[nodiscard]] std::string_view to_wire(GainMode value);
[[nodiscard]] std::string_view to_wire(DeviceState value);
[[nodiscard]] std::string_view to_wire(CalibrationStatus value);
[[nodiscard]] std::string_view to_wire(BackendKind value);
[[nodiscard]] std::string_view to_wire(PrecisionMode value);
[[nodiscard]] std::string_view to_wire(PersistenceMode value);
[[nodiscard]] std::string_view to_wire(EngineState value);
[[nodiscard]] std::string_view to_wire(OverflowPolicy value);
[[nodiscard]] std::string_view to_wire(EventSeverity value);

struct SourceDescriptor {
    SourceType source_type{SourceType::Synthetic};
    std::string source_id;
    std::string display_name;
    std::string uri;
    std::string device_serial;
    std::string backend_id;
    std::uint32_t schema_version{contract_schema_version};
    std::map<std::string, std::string> metadata_json;
};

using SharedBuffer = std::shared_ptr<const std::vector<std::uint8_t>>;

template <typename T>
using SharedArray = std::shared_ptr<const std::vector<T>>;

struct IqBlock {
    std::uint64_t source_sequence{};
    std::uint64_t first_sample_index{};
    std::int64_t timestamp_ns{};
    double center_frequency_hz{};
    double sample_rate_hz{};
    SampleFormat sample_format{SampleFormat::ComplexInt16Le};
    std::uint32_t sample_count{};
    QualityFlag flags{QualityFlag::None};
    SharedBuffer samples;
    std::uint64_t config_generation{};
};

struct SpectrumFrame {
    SourceDescriptor source;
    std::uint64_t frame_sequence{};
    std::uint64_t first_sample_index{};
    std::int64_t timestamp_ns{};
    std::uint64_t config_generation{};
    double center_frequency_hz{};
    double sample_rate_hz{};
    double analog_bandwidth_hz{};
    double fft_bin_width_hz{};
    double enbw_hz{};
    double nominal_rbw_hz{};
    std::uint32_t fft_size{};
    std::uint32_t hop_size{};
    WindowType window{WindowType::Hann};
    DetectorType detector{DetectorType::Sample};
    PrecisionMode precision_mode{PrecisionMode::AccurateF32F64Accum};
    SpectrumUnit unit{SpectrumUnit::DbfsBin};
    SharedArray<double> frequencies_hz;
    SharedArray<float> values;
    CalibrationStatus calibration_status{CalibrationStatus::Uncalibrated};
    std::string calibration_profile_id;
    double estimated_uncertainty_db{};
    std::uint64_t dropped_samples_before{};
    std::uint64_t dropped_iq_blocks_before{};
    std::uint64_t dropped_fft_frames_before{};
    QualityFlag quality_flags{QualityFlag::Uncalibrated};
};

struct SweepSegmentMetadata {
    std::uint32_t segment_index{};
    double center_frequency_hz{};
    double actual_start_hz{};
    double actual_stop_hz{};
    QualityFlag quality_flags{QualityFlag::None};
};

struct SweepSpectrumFrame {
    std::uint64_t sweep_id{};
    std::int64_t started_ns{};
    std::int64_t completed_ns{};
    double requested_start_hz{};
    double requested_stop_hz{};
    double actual_start_hz{};
    double actual_stop_hz{};
    double nominal_rbw_hz{};
    SharedArray<double> frequencies_hz;
    SharedArray<float> values;
    SharedArray<std::uint16_t> quality_flags_per_bin;
    std::vector<SweepSegmentMetadata> segments;
};

void validate(const SourceDescriptor& value);
void validate(const IqBlock& value);
void validate(const SpectrumFrame& value);
void validate(const SweepSpectrumFrame& value);
void validate_unit_calibration(
    SpectrumUnit unit,
    CalibrationStatus status,
    const std::string& profile_id
);

}  // namespace sdr_core

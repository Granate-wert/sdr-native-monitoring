#pragma once

#include <cstdint>
#include <limits>
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
// Version 4: P08 adds ComputeBackendKind, BackendErrorCode and the
// vendor-neutral GPU backend model (P08H-00). The Hip enumerator declares the
// future AMD backend contract only; it does not imply an implementation.
// Version 5: P08H-00 freezes generic fallback/discontinuity quality bits.
// R12-C corrects the omitted pybind/Python name for the already-reserved
// BackendDiscontinuity bit; it does not change the wire schema.
inline constexpr std::uint32_t contract_schema_version = 5;

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
    BackendFallback = 1U << 14U,
    // Compatibility wire alias for clients built against the pre-freeze name.
    CudaFallback = BackendFallback,
    BackendDiscontinuity = 1U << 15U,
};

enum class BackendKind : std::uint8_t {
    Cpu,
    Cuda,
};

// P08/P08H-00 vendor-neutral compute backend identifier. Used both as a
// selection preference (Auto allowed) and as the identifier of the actually
// active backend (Auto/Hip are never reported as active). Hip declares the
// future AMD HIP backend contract (P08H branch); it is NOT implemented.
enum class ComputeBackendKind : std::uint8_t {
    Auto,
    Cpu,
    Cuda,
    Hip,
};

// P08/P08H-00 vendor-neutral backend error taxonomy. Vendor-specific errors
// (CUDA today, HIP later) are translated into these stable categories before
// crossing any public boundary.
enum class BackendErrorCode : std::uint8_t {
    None,
    RuntimeNotFound,
    RuntimeIncompatible,
    NoDevice,
    UnsupportedDevice,
    AllocationFailed,
    CopyFailed,
    KernelLaunchFailed,
    FftPlanFailed,
    FftExecutionFailed,
    DeviceLost,
    TimeoutOrTdr,
    NumericalSelfTestFailed,
    Unknown,
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

// R10-D native continuous-sweep publication state.  This is deliberately
// separate from analytical FFT frames: a line is either complete over the
// declared span or carries explicit loss/control-gap evidence.
enum class SweepLineState : std::uint8_t {
    Complete,
    Gap,
};

enum class SweepLineGapReason : std::uint8_t {
    MissingSegment,
    Capacity,
    Cancellation,
    Disconnect,
    Reconfigure,
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
[[nodiscard]] std::string_view to_wire(ComputeBackendKind value);
[[nodiscard]] std::string_view to_wire(BackendErrorCode value);
[[nodiscard]] std::string_view to_wire(PrecisionMode value);
[[nodiscard]] std::string_view to_wire(PersistenceMode value);
[[nodiscard]] std::string_view to_wire(EngineState value);
[[nodiscard]] std::string_view to_wire(OverflowPolicy value);
[[nodiscard]] std::string_view to_wire(EventSeverity value);
[[nodiscard]] std::string_view to_wire(SweepLineState value);
[[nodiscard]] std::string_view to_wire(SweepLineGapReason value);

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
    double estimated_uncertainty_db{std::numeric_limits<double>::quiet_NaN()};
    std::uint64_t dropped_samples_before{};
    std::uint64_t dropped_iq_blocks_before{};
    std::uint64_t dropped_fft_frames_before{};
    QualityFlag quality_flags{QualityFlag::Uncalibrated};
    // Producer-reported detector accumulation count; zero means unavailable
    // for historical deserialized/test frames, never implicit average=1.
    std::uint32_t averaging_frames{};
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

// Definition of one explicitly planned RF segment in an R10-D sweep line.
// The usable bounds exclude receiver edge/DC regions before line assembly;
// they are not inferred or adjusted by the assembler.
struct SweepLineSegmentDefinition {
    std::uint32_t segment_index{};
    std::uint64_t config_generation{};
    double usable_start_hz{};
    double usable_stop_hz{};
};

// One fixed immutable line profile.  `source` and `unit` are part of the
// admission key so a line can never silently mix data from two epochs.
struct SweepLineDefinition {
    SourceDescriptor source;
    std::uint64_t epoch{};
    double start_frequency_hz{};
    double stop_frequency_hz{};
    // Spacing of the emitted reduced SweepLineFrame grid.  This remains the
    // physical FFT spacing for legacy definitions and becomes the analysis
    // spacing only when analysis_bins_per_usable_window is nonzero.
    double target_spacing_hz{};
    // Nonzero only with analysis_bins_per_usable_window. It is the declared
    // post-FFT usable RF width, not the ADC/transport sample rate.
    double analysis_window_hz{};
    // Zero retains the generic legacy spacing contract. A nonzero value is
    // the number of reduced analysis bins in one declared usable RF window;
    // it makes a wideband Sweep's user-selected N independent from the
    // larger power-of-two transform required by the transport sample rate.
    std::uint32_t analysis_bins_per_usable_window{};
    // Zero preserves legacy semantics.  Otherwise this records the source
    // SpectrumFrame spacing and prevents the reduced grid from inventing
    // resolution finer than the physical transform.
    double physical_fft_bin_width_hz{};
    // The full-rate power-of-two transform.  User-selected analysis N is not
    // allowed to be silently substituted for this physical size.
    std::uint32_t physical_fft_size{};
    SpectrumUnit unit{SpectrumUnit::DbfsBin};
    std::uint32_t max_inflight_lines{4U};
    std::vector<SweepLineSegmentDefinition> segments;
};

// Native input to a line assembler: already reduced spectrum data only.
// Raw I/Q is intentionally absent from this contract.
struct SweepLineSegmentFrame {
    std::uint32_t segment_index{};
    SpectrumFrame spectrum;
};

// Immutable reduced result published after the native line is terminal.  A
// gapped result is still a final line: GUI code must not attempt to repair it.
struct SweepSegmentAcquisition {
    std::uint32_t segment_index{};
    std::uint64_t config_generation{};
    std::uint64_t frame_sequence{};
    std::uint64_t first_sample_index{};
    // Retained producer timestamp, NOT a hardware-clock/synchronization claim.
    std::int64_t timestamp_ns{};
    double sample_rate_hz{};
    std::uint32_t fft_size{};
    QualityFlag quality_flags{QualityFlag::None};
};

struct SweepStatisticsSnapshot;

struct SweepLineFrame {
    SourceDescriptor source;
    std::uint64_t line_sequence{};
    std::uint64_t epoch{};
    std::int64_t completed_ns{};
    SweepLineState state{SweepLineState::Gap};
    double start_frequency_hz{};
    double stop_frequency_hz{};
    double target_spacing_hz{};
    // Exact geometry of a reduced 36 MHz-style analysis grid.  All three are
    // zero for legacy physical-FFT grids; otherwise they let every published
    // line prove that its user-visible N was not confused with the larger
    // physical transform used at the configured ADC sample rate.
    double analysis_window_hz{};
    std::uint32_t analysis_bins_per_usable_window{};
    double physical_fft_bin_width_hz{};
    std::uint32_t physical_fft_size{};
    SpectrumUnit unit{SpectrumUnit::DbfsBin};
    SharedArray<double> frequencies_hz;
    SharedArray<float> values;
    SharedArray<std::uint32_t> quality_flags_per_bin;
    SharedArray<std::int32_t> source_segment_indices;
    std::vector<std::uint32_t> missing_segment_indices;
    std::vector<SweepLineSegmentDefinition> segment_generations;
    std::vector<SweepLineGapReason> gap_reasons;
    std::vector<SweepSegmentAcquisition> acquired_segments;
    // Explicitly stamped native statistics; cadence may lag this trace.
    std::shared_ptr<const SweepStatisticsSnapshot> statistics;
    // Last SUCCESSFUL assembler admission in this pass, not current RF tuning.
    // Absent for empty control gaps and producers without this contract.
    std::optional<SweepLineSegmentDefinition> last_admitted_segment;
};

struct SweepLineAssemblyMetrics {
    std::uint64_t completed_lines{};
    std::uint64_t gapped_lines{};
    std::uint64_t capacity_evicted_lines{};
    std::uint32_t pending_lines{};
};

void validate(const SourceDescriptor& value);
void validate(const IqBlock& value);
void validate(const SpectrumFrame& value);
void validate(const SweepSpectrumFrame& value);
void validate(const SweepLineDefinition& value);
void validate(const SweepLineSegmentFrame& value);
void validate(const SweepLineFrame& value);
void validate_unit_calibration(
    SpectrumUnit unit,
    CalibrationStatus status,
    const std::string& profile_id
);

}  // namespace sdr_core

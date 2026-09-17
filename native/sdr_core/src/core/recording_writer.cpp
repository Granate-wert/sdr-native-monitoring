#include "sdr_core/recording_writer.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <charconv>
#include <cmath>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <system_error>
#include <utility>

namespace sdr_core {

namespace {

[[nodiscard]] std::string quote_json(const std::string_view value) {
    std::ostringstream result;
    result << '"';
    for (const unsigned char character : value) {
        switch (character) {
        case '"': result << "\\\""; break;
        case '\\': result << "\\\\"; break;
        case '\b': result << "\\b"; break;
        case '\f': result << "\\f"; break;
        case '\n': result << "\\n"; break;
        case '\r': result << "\\r"; break;
        case '\t': result << "\\t"; break;
        default:
            if (character < 0x20U) {
                result << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                       << static_cast<unsigned int>(character) << std::dec
                       << std::setfill(' ');
            } else {
                result << static_cast<char>(character);
            }
        }
    }
    result << '"';
    return result.str();
}

[[nodiscard]] std::string json_number(const double value) {
    if (!std::isfinite(value)) {
        return "null";
    }
    std::ostringstream result;
    result << std::setprecision(17) << value;
    return result.str();
}

[[nodiscard]] std::size_t bytes_per_sample(const SampleFormat format) {
    switch (format) {
    case SampleFormat::ComplexInt8Interleaved:
        return 2U;
    case SampleFormat::ComplexInt12InInt16Le:
    case SampleFormat::ComplexInt16Le:
        return 4U;
    case SampleFormat::ComplexFloat32Le:
        return 8U;
    }
    throw ConfigurationError("unsupported I/Q sample format for recording");
}

[[nodiscard]] std::string_view sigmf_datatype(const SampleFormat format) {
    switch (format) {
    case SampleFormat::ComplexInt8Interleaved:
        return "ci8_le";
    case SampleFormat::ComplexInt12InInt16Le:
        return "ci12_le";
    case SampleFormat::ComplexInt16Le:
        return "ci16_le";
    case SampleFormat::ComplexFloat32Le:
        return "cf32_le";
    }
    throw ConfigurationError("unsupported I/Q sample format for SigMF metadata");
}

void flush_or_throw(std::ofstream& stream, const char* label) {
    stream.flush();
    if (!stream) {
        throw std::runtime_error(std::string("failed to flush recording ") + label);
    }
}

void rename_no_replace(
    const std::filesystem::path& source,
    const std::filesystem::path& destination
) {
    std::error_code error;
    std::filesystem::rename(source, destination, error);
    if (error) {
        throw std::runtime_error(
            "failed to finalize recording artifact " + destination.string() +
            ": " + error.message()
        );
    }
}

[[nodiscard]] std::filesystem::path normalized_base_path(
    std::filesystem::path path
) {
    auto name = path.filename().string();
    constexpr std::string_view partial_suffix = ".part";
    while (name.size() >= partial_suffix.size() &&
           name.compare(
               name.size() - partial_suffix.size(),
               partial_suffix.size(),
               partial_suffix
           ) == 0) {
        name.resize(name.size() - partial_suffix.size());
    }
    constexpr std::string_view suffixes[] = {
        ".sigmf-meta", ".sigmf-index.jsonl", ".sigmf-gaps.jsonl",
        ".sdr-spectrum.meta", ".sdr-spectrum.bin", ".sdr-spectrum-index.jsonl",
    };
    for (const auto suffix : suffixes) {
        if (name.size() >= suffix.size() &&
            name.compare(name.size() - suffix.size(), suffix.size(), suffix) == 0) {
            return path.parent_path() /
                   name.substr(0U, name.size() - suffix.size());
        }
    }
    return path;
}

constexpr std::array<char, 8U> spectrum_magic{{'S', 'D', 'R', 'S', 'P', 'C', '0', '1'}};
constexpr std::uint64_t spectrum_record_fixed_payload_bytes = 152U;

void write_u32_le(std::ostream& stream, const std::uint32_t value) {
    std::array<char, 4U> bytes{};
    for (std::size_t index = 0U; index < bytes.size(); ++index) {
        bytes[index] = static_cast<char>((value >> (index * 8U)) & 0xffU);
    }
    stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

void write_u64_le(std::ostream& stream, const std::uint64_t value) {
    std::array<char, 8U> bytes{};
    for (std::size_t index = 0U; index < bytes.size(); ++index) {
        bytes[index] = static_cast<char>((value >> (index * 8U)) & 0xffU);
    }
    stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

void write_f32_le(std::ostream& stream, const float value) {
    write_u32_le(stream, std::bit_cast<std::uint32_t>(value));
}

void write_f64_le(std::ostream& stream, const double value) {
    write_u64_le(stream, std::bit_cast<std::uint64_t>(value));
}

[[nodiscard]] bool read_u64_le(std::istream& stream, std::uint64_t& value) {
    std::array<unsigned char, 8U> bytes{};
    stream.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    if (stream.gcount() != static_cast<std::streamsize>(bytes.size())) {
        return false;
    }
    value = 0U;
    for (std::size_t index = 0U; index < bytes.size(); ++index) {
        value |= static_cast<std::uint64_t>(bytes[index]) << (index * 8U);
    }
    return true;
}

[[nodiscard]] bool read_u32_le(std::istream& stream, std::uint32_t& value) {
    std::array<unsigned char, 4U> bytes{};
    stream.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    if (stream.gcount() != static_cast<std::streamsize>(bytes.size())) {
        return false;
    }
    value = 0U;
    for (std::size_t index = 0U; index < bytes.size(); ++index) {
        value |= static_cast<std::uint32_t>(bytes[index]) << (index * 8U);
    }
    return true;
}

[[nodiscard]] bool read_f32_le(std::istream& stream, float& value) {
    std::uint32_t bits{};
    if (!read_u32_le(stream, bits)) {
        return false;
    }
    value = std::bit_cast<float>(bits);
    return true;
}

[[nodiscard]] bool read_f64_le(std::istream& stream, double& value) {
    std::uint64_t bits{};
    if (!read_u64_le(stream, bits)) {
        return false;
    }
    value = std::bit_cast<double>(bits);
    return true;
}

[[nodiscard]] std::string read_file_limited(
    const std::filesystem::path& path,
    const std::uint64_t limit = 1024U * 1024U
) {
    std::error_code error;
    const auto bytes = std::filesystem::file_size(path, error);
    if (error || bytes > limit) {
        throw std::runtime_error("native recording manifest is missing or exceeds its bounded limit");
    }
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("cannot open native recording manifest");
    }
    std::string result(static_cast<std::size_t>(bytes), '\0');
    stream.read(result.data(), static_cast<std::streamsize>(result.size()));
    if (stream.gcount() != static_cast<std::streamsize>(result.size())) {
        throw std::runtime_error("cannot read native recording manifest");
    }
    return result;
}

[[nodiscard]] bool completed_manifest(
    const std::filesystem::path& path,
    const std::string_view recording_type
) {
    if (!std::filesystem::exists(path)) {
        return false;
    }
    const auto content = read_file_limited(path);
    return content.find("\"schema\":\"sdr-native-recording\"") != std::string::npos &&
           content.find("\"recording_type\":\"" + std::string(recording_type) + "\"") !=
               std::string::npos &&
           content.find("\"completed\":true") != std::string::npos;
}

[[nodiscard]] std::size_t json_value_offset(
    const std::string_view line,
    const std::string_view key
) {
    const auto marker = "\"" + std::string(key) + "\":";
    const auto marker_offset = line.find(marker);
    if (marker_offset == std::string::npos) {
        throw std::runtime_error("native spectrum index is missing required field " + std::string(key));
    }
    return marker_offset + marker.size();
}

template <typename Integer>
[[nodiscard]] Integer json_integer(
    const std::string_view line,
    const std::string_view key
) {
    const auto start = json_value_offset(line, key);
    Integer value{};
    const auto* first = line.data() + start;
    const auto* last = line.data() + line.size();
    const auto [end, error] = std::from_chars(first, last, value);
    if (error != std::errc{} || end == first) {
        throw std::runtime_error("native spectrum index has invalid integer field " + std::string(key));
    }
    return value;
}

[[nodiscard]] std::string json_string(
    const std::string_view line,
    const std::string_view key
) {
    auto position = json_value_offset(line, key);
    if (position >= line.size() || line[position] != '"') {
        throw std::runtime_error("native spectrum index has invalid string field " + std::string(key));
    }
    ++position;
    std::string result;
    while (position < line.size()) {
        const char character = line[position++];
        if (character == '"') {
            return result;
        }
        if (character != '\\') {
            result.push_back(character);
            continue;
        }
        if (position >= line.size()) {
            break;
        }
        const char escaped = line[position++];
        switch (escaped) {
        case '"': result.push_back('"'); break;
        case '\\': result.push_back('\\'); break;
        case '/': result.push_back('/'); break;
        case 'b': result.push_back('\b'); break;
        case 'f': result.push_back('\f'); break;
        case 'n': result.push_back('\n'); break;
        case 'r': result.push_back('\r'); break;
        case 't': result.push_back('\t'); break;
        default:
            throw std::runtime_error("native spectrum index uses unsupported JSON escape");
        }
    }
    throw std::runtime_error("native spectrum index has unterminated string field " + std::string(key));
}

[[nodiscard]] bool is_spectrum_index_line(const std::string_view line) {
    return line.starts_with("{\"type\":\"frame\",");
}

struct NewlineScan {
    std::uint64_t complete_records{};
    std::uint64_t complete_bytes{};
    std::uint64_t trailing_bytes{};
};

[[nodiscard]] NewlineScan scan_newline_records(const std::filesystem::path& path) {
    NewlineScan result;
    std::error_code error;
    const auto file_bytes = std::filesystem::file_size(path, error);
    if (error) {
        return result;
    }
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        return result;
    }
    std::uint64_t offset = 0U;
    char character{};
    while (stream.get(character)) {
        ++offset;
        if (character == '\n') {
            ++result.complete_records;
            result.complete_bytes = offset;
        }
    }
    result.trailing_bytes = file_bytes - result.complete_bytes;
    return result;
}

[[nodiscard]] bool has_suffix(const std::string_view value, const std::string_view suffix) {
    return value.size() >= suffix.size() &&
           value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0;
}

}  // namespace

SegmentedIqRecordingWriter::SegmentedIqRecordingWriter(
    RecordingConfig config,
    SourceDescriptor source
) : config_(std::move(config)),
    source_(std::move(source)),
    base_path_(normalized_base_path(config_.output_uri)),
    manifest_path_(base_path_.string() + ".sigmf-meta"),
    index_path_(base_path_.string() + ".sigmf-index.jsonl"),
    gaps_path_(base_path_.string() + ".sigmf-gaps.jsonl") {
    validate(config_);
    validate(source_);
    if (!config_.enabled || !config_.record_iq) {
        throw ConfigurationError("native I/Q writer requires enabled record_iq configuration");
    }
}

SegmentedIqRecordingWriter::~SegmentedIqRecordingWriter() noexcept {
    abort("writer_destroyed_without_finalize");
}

void SegmentedIqRecordingWriter::start() {
    std::lock_guard lock(mutex_);
    if (active_ || finalized_) {
        throw ConfigurationError("native I/Q writer cannot be started twice");
    }
    if (base_path_.empty() || base_path_.filename().empty()) {
        throw ConfigurationError("native I/Q recording output path is invalid");
    }
    std::error_code error;
    if (!base_path_.parent_path().empty()) {
        std::filesystem::create_directories(base_path_.parent_path(), error);
        if (error) {
            throw std::runtime_error(
                "cannot create recording directory: " + error.message()
            );
        }
    }
    const auto manifest_part = part_path(manifest_path_);
    const auto index_part = part_path(index_path_);
    const auto gaps_part = part_path(gaps_path_);
    if (std::filesystem::exists(manifest_path_) ||
        std::filesystem::exists(index_path_) ||
        std::filesystem::exists(gaps_path_) ||
        std::filesystem::exists(manifest_part) ||
        std::filesystem::exists(index_part) ||
        std::filesystem::exists(gaps_part)) {
        throw ConfigurationError("native recording target already exists");
    }
    const auto prefix = base_path_.filename().string() + ".";
    for (const auto& entry : std::filesystem::directory_iterator(base_path_.parent_path())) {
        const auto name = entry.path().filename().string();
        if (name.starts_with(prefix) && name.find(".sigmf-data") != std::string::npos) {
            throw ConfigurationError("native recording target has existing data segment");
        }
    }
    index_.open(index_part, std::ios::binary | std::ios::out | std::ios::trunc);
    gaps_.open(gaps_part, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!index_ || !gaps_) {
        index_.close();
        gaps_.close();
        throw std::runtime_error("cannot open native recording index/gap artifacts");
    }
    active_ = true;
    metrics_.active = true;
    try {
        write_manifest(false, "in_progress");
    } catch (...) {
        active_ = false;
        metrics_.active = false;
        index_.close();
        gaps_.close();
        throw;
    }
}

void SegmentedIqRecordingWriter::write_block(const IqBlock& block) {
    std::lock_guard lock(mutex_);
    require_active();
    if (!block.samples || block.sample_count == 0U ||
        !std::isfinite(block.sample_rate_hz) || block.sample_rate_hz <= 0.0 ||
        !std::isfinite(block.center_frequency_hz)) {
        throw std::runtime_error("native recording received invalid I/Q block metadata");
    }
    const auto width = bytes_per_sample(block.sample_format);
    if (block.sample_count > std::numeric_limits<std::size_t>::max() / width ||
        block.samples->size() != static_cast<std::size_t>(block.sample_count) * width) {
        throw std::runtime_error("native recording I/Q payload size does not match metadata");
    }
    if (has_sample_format_ && block.sample_format != sample_format_) {
        throw std::runtime_error("native recording sample format changed within one manifest");
    }
    if (!has_sample_format_) {
        sample_format_ = block.sample_format;
        has_sample_format_ = true;
    }
    const bool capture_changed =
        metrics_.written_blocks == 0U || sample_rate_hz_ != block.sample_rate_hz ||
        center_frequency_hz_ != block.center_frequency_hz ||
        config_generation_ != block.config_generation;
    sample_rate_hz_ = block.sample_rate_hz;
    center_frequency_hz_ = block.center_frequency_hz;
    config_generation_ = block.config_generation;
    if (capture_changed) {
        write_index_line(
            "{\"type\":\"capture\",\"first_sample_index\":" +
            std::to_string(block.first_sample_index) +
            ",\"timestamp_ns\":" + std::to_string(block.timestamp_ns) +
            ",\"center_frequency_hz\":" + json_number(block.center_frequency_hz) +
            ",\"sample_rate_hz\":" + json_number(block.sample_rate_hz) +
            ",\"config_generation\":" + std::to_string(block.config_generation) + "}"
        );
    }
    if (has_expected_sample_index_) {
        if (block.first_sample_index < expected_sample_index_) {
            throw std::runtime_error("native recording sample indexes overlap or regress");
        }
        if (block.first_sample_index > expected_sample_index_) {
            write_gap(
                expected_sample_index_,
                block.first_sample_index - expected_sample_index_,
                block.timestamp_ns,
                "sample_index_gap"
            );
        }
    }
    const auto segment_capacity = static_cast<std::uint64_t>(config_.chunk_samples);
    const auto remaining_segment_samples = current_segment_samples_ >= segment_capacity
                                               ? 0U
                                               : segment_capacity - current_segment_samples_;
    if (!data_.is_open() ||
        (current_segment_samples_ != 0U &&
         static_cast<std::uint64_t>(block.sample_count) > remaining_segment_samples)) {
        close_segment();
        open_segment(block);
    }
    const auto offset = current_segment_bytes_;
    const auto byte_count = static_cast<std::uint64_t>(block.samples->size());
    data_.write(
        reinterpret_cast<const char*>(block.samples->data()),
        static_cast<std::streamsize>(block.samples->size())
    );
    if (!data_) {
        throw std::runtime_error("native recording data write failed");
    }
    write_index_line(
        "{\"type\":\"block\",\"segment\":" +
        std::to_string(current_segment_) + ",\"offset\":" +
        std::to_string(offset) + ",\"byte_count\":" +
        std::to_string(byte_count) + ",\"sample_count\":" +
        std::to_string(block.sample_count) + ",\"first_sample_index\":" +
        std::to_string(block.first_sample_index) + ",\"timestamp_ns\":" +
        std::to_string(block.timestamp_ns) + ",\"source_sequence\":" +
        std::to_string(block.source_sequence) + ",\"config_generation\":" +
        std::to_string(block.config_generation) + ",\"sample_format\":" +
        quote_json(to_wire(block.sample_format)) + ",\"flags\":" +
        std::to_string(static_cast<std::uint32_t>(block.flags)) + "}"
    );
    current_segment_samples_ += block.sample_count;
    current_segment_bytes_ += byte_count;
    expected_sample_index_ = block.first_sample_index + block.sample_count;
    has_expected_sample_index_ = true;
    ++metrics_.written_blocks;
    metrics_.written_samples += block.sample_count;
    metrics_.written_bytes += byte_count;
    if (metrics_.written_blocks == 1U || metrics_.written_blocks % 16U == 0U) {
        flush_or_throw(data_, "data");
        flush_or_throw(index_, "index");
        flush_or_throw(gaps_, "gaps");
    }
}

void SegmentedIqRecordingWriter::set_recorder_queue_loss(
    const std::uint64_t blocks,
    const std::uint64_t samples
) {
    std::lock_guard lock(mutex_);
    require_active();
    metrics_.recorder_queue_dropped_blocks = blocks;
    metrics_.recorder_queue_dropped_samples = samples;
}

void SegmentedIqRecordingWriter::finalize() {
    std::lock_guard lock(mutex_);
    require_active();
    close_segment();
    flush_or_throw(index_, "index");
    flush_or_throw(gaps_, "gaps");
    index_.close();
    gaps_.close();
    if (index_.bad() || gaps_.bad()) {
        throw std::runtime_error("native recording index/gap close failed");
    }
    write_manifest(true, "");
    for (std::uint64_t segment = 0U; segment < metrics_.segments; ++segment) {
        const auto final_path = data_path(segment);
        rename_no_replace(part_path(final_path), final_path);
    }
    rename_no_replace(part_path(index_path_), index_path_);
    rename_no_replace(part_path(gaps_path_), gaps_path_);
    rename_no_replace(part_path(manifest_path_), manifest_path_);
    active_ = false;
    finalized_ = true;
    metrics_.active = false;
    metrics_.finalized = true;
}

void SegmentedIqRecordingWriter::abort(std::string reason) noexcept {
    try {
        std::lock_guard lock(mutex_);
        if (!active_) {
            return;
        }
        if (data_.is_open()) {
            data_.flush();
            data_.close();
        }
        if (index_.is_open()) {
            index_.flush();
            index_.close();
        }
        if (gaps_.is_open()) {
            gaps_.flush();
            gaps_.close();
        }
        try {
            write_manifest(false, reason);
        } catch (...) {
        }
        active_ = false;
        failed_ = true;
        metrics_.active = false;
        metrics_.failed = true;
    } catch (...) {
    }
}

NativeIqRecordingMetrics SegmentedIqRecordingWriter::metrics() const noexcept {
    try {
        std::lock_guard lock(mutex_);
        return metrics_;
    } catch (...) {
        return {};
    }
}

std::filesystem::path SegmentedIqRecordingWriter::manifest_path() const {
    return manifest_path_;
}

std::filesystem::path SegmentedIqRecordingWriter::data_path(
    const std::uint64_t segment
) const {
    std::ostringstream name;
    name << base_path_.string() << '.' << std::setw(6) << std::setfill('0')
         << segment << ".sigmf-data";
    return name.str();
}

std::filesystem::path SegmentedIqRecordingWriter::part_path(
    const std::filesystem::path& path
) const {
    return path.string() + ".part";
}

void SegmentedIqRecordingWriter::open_segment(const IqBlock& first_block) {
    const auto path = part_path(data_path(current_segment_));
    data_.open(path, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!data_) {
        throw std::runtime_error("cannot open native recording data segment");
    }
    current_segment_samples_ = 0U;
    current_segment_bytes_ = 0U;
    write_index_line(
        "{\"type\":\"segment_start\",\"segment\":" +
        std::to_string(current_segment_) + ",\"file\":" +
        quote_json(data_path(current_segment_).filename().string()) +
        ",\"first_sample_index\":" + std::to_string(first_block.first_sample_index) +
        ",\"timestamp_ns\":" + std::to_string(first_block.timestamp_ns) + "}"
    );
}

void SegmentedIqRecordingWriter::close_segment() {
    if (!data_.is_open()) {
        return;
    }
    flush_or_throw(data_, "data");
    data_.close();
    if (data_.bad()) {
        throw std::runtime_error("native recording data segment close failed");
    }
    write_index_line(
        "{\"type\":\"segment_end\",\"segment\":" +
        std::to_string(current_segment_) + ",\"sample_count\":" +
        std::to_string(current_segment_samples_) + ",\"byte_count\":" +
        std::to_string(current_segment_bytes_) + "}"
    );
    ++metrics_.segments;
    ++current_segment_;
    current_segment_samples_ = 0U;
    current_segment_bytes_ = 0U;
}

void SegmentedIqRecordingWriter::write_gap(
    const std::uint64_t first_sample_index,
    const std::uint64_t sample_count,
    const std::int64_t timestamp_ns,
    const std::string_view reason
) {
    gaps_ << "{\"schema\":\"sdr-native-recording\",\"schema_version\":2,"
          << "\"stream\":\"iq\",\"reason\":" << quote_json(reason)
          << ",\"first_sample_index\":" << first_sample_index
          << ",\"sample_count\":" << sample_count
          << ",\"timestamp_ns\":" << timestamp_ns << "}\n";
    if (!gaps_) {
        throw std::runtime_error("native recording gap write failed");
    }
    ++metrics_.gaps;
    metrics_.gap_samples += sample_count;
}

void SegmentedIqRecordingWriter::write_manifest(
    const bool completed,
    const std::string_view abort_reason
) {
    const auto part = part_path(manifest_path_);
    const auto temporary = part.string() + ".tmp";
    std::ofstream manifest(temporary, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!manifest) {
        throw std::runtime_error("cannot write native recording manifest");
    }
    manifest << "{\"schema\":\"sdr-native-recording\",\"schema_version\":2,"
             << "\"recording_type\":\"native_iq_segmented\","
             << "\"completed\":" << (completed ? "true" : "false") << ','
             << "\"abort_reason\":" << quote_json(abort_reason) << ','
             << "\"sigmf\":{\"global\":{\"core:datatype\":"
             << (has_sample_format_ ? quote_json(sigmf_datatype(sample_format_)) : "null")
             << ",\"core:sample_rate\":" << json_number(sample_rate_hz_)
             << ",\"core:frequency\":" << json_number(center_frequency_hz_)
             << ",\"core:sample_count\":" << metrics_.written_samples
             << ",\"core:version\":\"1.0.0\"}},"
             << "\"sdr\":{\"source\":{\"source_type\":"
             << quote_json(to_wire(source_.source_type)) << ",\"source_id\":"
             << quote_json(source_.source_id) << ",\"display_name\":"
             << quote_json(source_.display_name) << ",\"uri\":"
             << quote_json(source_.uri) << ",\"backend_id\":"
             << quote_json(source_.backend_id) << ",\"schema_version\":"
             << source_.schema_version << "},\"index_file\":"
             << quote_json(index_path_.filename().string()) << ",\"gaps_file\":"
             << quote_json(gaps_path_.filename().string()) << ",\"segment_count\":"
             << metrics_.segments << ",\"chunk_samples\":" << config_.chunk_samples
             << ",\"written_blocks\":" << metrics_.written_blocks
             << ",\"written_samples\":" << metrics_.written_samples
             << ",\"written_bytes\":" << metrics_.written_bytes
             << ",\"gap_count\":" << metrics_.gaps
             << ",\"gap_samples\":" << metrics_.gap_samples
             << ",\"recorder_queue_dropped_blocks\":"
             << metrics_.recorder_queue_dropped_blocks
             << ",\"recorder_queue_dropped_samples\":"
             << metrics_.recorder_queue_dropped_samples
             << ",\"config_generation\":" << config_generation_ << "}}\n";
    flush_or_throw(manifest, "manifest");
    manifest.close();
    if (manifest.bad()) {
        throw std::runtime_error("native recording manifest close failed");
    }
    std::error_code error;
    std::filesystem::remove(part, error);
    error.clear();
    std::filesystem::rename(temporary, part, error);
    if (error) {
        throw std::runtime_error("cannot publish native recording manifest part: " + error.message());
    }
}

void SegmentedIqRecordingWriter::write_index_line(const std::string& line) {
    index_ << line << '\n';
    if (!index_) {
        throw std::runtime_error("native recording index write failed");
    }
}

void SegmentedIqRecordingWriter::require_active() const {
    if (!active_ || finalized_) {
        throw ConfigurationError("native I/Q writer is not active");
    }
}

SpectrumFrameRecordingWriter::SpectrumFrameRecordingWriter(
    RecordingConfig config,
    SourceDescriptor source
) : config_(std::move(config)),
    source_(std::move(source)),
    base_path_(normalized_base_path(config_.output_uri)),
    manifest_path_(base_path_.string() + ".sdr-spectrum.meta"),
    data_path_(base_path_.string() + ".sdr-spectrum.bin"),
    index_path_(base_path_.string() + ".sdr-spectrum-index.jsonl") {
    validate(config_);
    validate(source_);
    if (!config_.enabled || !config_.record_spectrum) {
        throw ConfigurationError("native spectrum writer requires enabled record_spectrum configuration");
    }
}

SpectrumFrameRecordingWriter::~SpectrumFrameRecordingWriter() noexcept {
    abort("writer_destroyed_without_finalize");
}

void SpectrumFrameRecordingWriter::start() {
    std::lock_guard lock(mutex_);
    if (active_ || finalized_) {
        throw ConfigurationError("native spectrum writer cannot be started twice");
    }
    if (base_path_.empty() || base_path_.filename().empty()) {
        throw ConfigurationError("native spectrum recording output path is invalid");
    }
    std::error_code error;
    if (!base_path_.parent_path().empty()) {
        std::filesystem::create_directories(base_path_.parent_path(), error);
        if (error) {
            throw std::runtime_error(
                "cannot create spectrum recording directory: " + error.message()
            );
        }
    }
    const auto manifest_part = part_path(manifest_path_);
    const auto data_part = part_path(data_path_);
    const auto index_part = part_path(index_path_);
    if (std::filesystem::exists(manifest_path_) ||
        std::filesystem::exists(data_path_) ||
        std::filesystem::exists(index_path_) ||
        std::filesystem::exists(manifest_part) ||
        std::filesystem::exists(data_part) ||
        std::filesystem::exists(index_part)) {
        throw ConfigurationError("native spectrum recording target already exists");
    }
    data_.open(data_part, std::ios::binary | std::ios::out | std::ios::trunc);
    index_.open(index_part, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!data_ || !index_) {
        data_.close();
        index_.close();
        throw std::runtime_error("cannot open native spectrum recording artifacts");
    }
    data_.write(spectrum_magic.data(), static_cast<std::streamsize>(spectrum_magic.size()));
    if (!data_) {
        data_.close();
        index_.close();
        throw std::runtime_error("cannot write native spectrum recording header");
    }
    data_offset_ = spectrum_magic.size();
    active_ = true;
    metrics_.active = true;
    try {
        write_manifest(false, "in_progress");
    } catch (...) {
        active_ = false;
        metrics_.active = false;
        data_.close();
        index_.close();
        throw;
    }
}

void SpectrumFrameRecordingWriter::write_frame(const SpectrumFrame& frame) {
    std::lock_guard lock(mutex_);
    require_active();
    validate(frame);
    const auto bin_count = static_cast<std::uint64_t>(frame.values->size());
    if (bin_count == 0U || frame.frequencies_hz->size() != frame.values->size() ||
        bin_count > (std::numeric_limits<std::uint64_t>::max() -
                     spectrum_record_fixed_payload_bytes) / 12U) {
        throw std::runtime_error("native spectrum recording frame shape is invalid");
    }
    const auto payload_bytes = spectrum_record_fixed_payload_bytes + bin_count * 12U;
    const auto record_bytes = payload_bytes + 8U;
    const auto offset = data_offset_;
    write_u64_le(data_, payload_bytes);
    write_u32_le(data_, 1U);
    write_u32_le(data_, static_cast<std::uint32_t>(bin_count));
    write_u64_le(data_, frame.frame_sequence);
    write_u64_le(data_, frame.first_sample_index);
    write_u64_le(data_, static_cast<std::uint64_t>(frame.timestamp_ns));
    write_u64_le(data_, frame.config_generation);
    write_f64_le(data_, frame.center_frequency_hz);
    write_f64_le(data_, frame.sample_rate_hz);
    write_f64_le(data_, frame.analog_bandwidth_hz);
    write_f64_le(data_, frame.fft_bin_width_hz);
    write_f64_le(data_, frame.enbw_hz);
    write_f64_le(data_, frame.nominal_rbw_hz);
    write_u32_le(data_, frame.fft_size);
    write_u32_le(data_, frame.hop_size);
    write_u32_le(data_, static_cast<std::uint32_t>(frame.window));
    write_u32_le(data_, static_cast<std::uint32_t>(frame.detector));
    write_u32_le(data_, static_cast<std::uint32_t>(frame.precision_mode));
    write_u32_le(data_, static_cast<std::uint32_t>(frame.unit));
    write_u32_le(data_, static_cast<std::uint32_t>(frame.calibration_status));
    write_u32_le(data_, static_cast<std::uint32_t>(frame.quality_flags));
    write_f64_le(data_, frame.estimated_uncertainty_db);
    write_u64_le(data_, frame.dropped_samples_before);
    write_u64_le(data_, frame.dropped_iq_blocks_before);
    write_u64_le(data_, frame.dropped_fft_frames_before);
    for (const double frequency_hz : *frame.frequencies_hz) {
        write_f64_le(data_, frequency_hz);
    }
    for (const float value : *frame.values) {
        write_f32_le(data_, value);
    }
    if (!data_) {
        throw std::runtime_error("native spectrum recording data write failed");
    }
    write_index_line(
        "{\"type\":\"frame\",\"offset\":" + std::to_string(offset) +
        ",\"record_bytes\":" + std::to_string(record_bytes) +
        ",\"frame_sequence\":" + std::to_string(frame.frame_sequence) +
        ",\"first_sample_index\":" + std::to_string(frame.first_sample_index) +
        ",\"timestamp_ns\":" + std::to_string(frame.timestamp_ns) +
        ",\"source_id\":" + quote_json(frame.source.source_id) +
        ",\"config_generation\":" + std::to_string(frame.config_generation) +
        ",\"bin_count\":" + std::to_string(bin_count) +
        ",\"center_frequency_hz\":" + json_number(frame.center_frequency_hz) +
        ",\"sample_rate_hz\":" + json_number(frame.sample_rate_hz) +
        ",\"analog_bandwidth_hz\":" + json_number(frame.analog_bandwidth_hz) +
        ",\"fft_bin_width_hz\":" + json_number(frame.fft_bin_width_hz) +
        ",\"enbw_hz\":" + json_number(frame.enbw_hz) +
        ",\"nominal_rbw_hz\":" + json_number(frame.nominal_rbw_hz) +
        ",\"fft_size\":" + std::to_string(frame.fft_size) +
        ",\"hop_size\":" + std::to_string(frame.hop_size) +
        ",\"window\":" + quote_json(to_wire(frame.window)) +
        ",\"detector\":" + quote_json(to_wire(frame.detector)) +
        ",\"precision\":" + quote_json(to_wire(frame.precision_mode)) +
        ",\"unit\":" + quote_json(to_wire(frame.unit)) +
        ",\"calibration_status\":" + quote_json(to_wire(frame.calibration_status)) +
        ",\"calibration_profile_id\":" + quote_json(frame.calibration_profile_id) +
        ",\"quality_flags\":" +
        std::to_string(static_cast<std::uint32_t>(frame.quality_flags)) + "}"
    );
    data_offset_ += record_bytes;
    ++metrics_.written_frames;
    metrics_.written_bytes += record_bytes;
    if (metrics_.written_frames == 1U || metrics_.written_frames % 16U == 0U) {
        flush_or_throw(data_, "spectrum data");
        flush_or_throw(index_, "spectrum index");
    }
}

void SpectrumFrameRecordingWriter::set_recorder_queue_loss(const std::uint64_t frames) {
    std::lock_guard lock(mutex_);
    require_active();
    metrics_.recorder_queue_dropped_frames = frames;
}

void SpectrumFrameRecordingWriter::finalize() {
    std::lock_guard lock(mutex_);
    require_active();
    flush_or_throw(data_, "spectrum data");
    flush_or_throw(index_, "spectrum index");
    data_.close();
    index_.close();
    if (data_.bad() || index_.bad()) {
        throw std::runtime_error("native spectrum recording close failed");
    }
    write_manifest(true, "");
    rename_no_replace(part_path(data_path_), data_path_);
    rename_no_replace(part_path(index_path_), index_path_);
    rename_no_replace(part_path(manifest_path_), manifest_path_);
    active_ = false;
    finalized_ = true;
    metrics_.active = false;
    metrics_.finalized = true;
}

void SpectrumFrameRecordingWriter::abort(std::string reason) noexcept {
    try {
        std::lock_guard lock(mutex_);
        if (!active_) {
            return;
        }
        if (data_.is_open()) {
            data_.flush();
            data_.close();
        }
        if (index_.is_open()) {
            index_.flush();
            index_.close();
        }
        try {
            write_manifest(false, reason);
        } catch (...) {
        }
        active_ = false;
        failed_ = true;
        metrics_.active = false;
        metrics_.failed = true;
    } catch (...) {
    }
}

NativeSpectrumRecordingMetrics SpectrumFrameRecordingWriter::metrics() const noexcept {
    try {
        std::lock_guard lock(mutex_);
        return metrics_;
    } catch (...) {
        return {};
    }
}

std::filesystem::path SpectrumFrameRecordingWriter::manifest_path() const {
    return manifest_path_;
}

std::filesystem::path SpectrumFrameRecordingWriter::part_path(
    const std::filesystem::path& path
) const {
    return path.string() + ".part";
}

void SpectrumFrameRecordingWriter::write_manifest(
    const bool completed,
    const std::string_view abort_reason
) {
    const auto part = part_path(manifest_path_);
    const auto temporary = part.string() + ".tmp";
    std::ofstream manifest(temporary, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!manifest) {
        throw std::runtime_error("cannot write native spectrum recording manifest");
    }
    manifest << "{\"schema\":\"sdr-native-recording\",\"schema_version\":2,"
             << "\"recording_type\":\"native_spectrum_frames\","
             << "\"completed\":" << (completed ? "true" : "false") << ','
             << "\"abort_reason\":" << quote_json(abort_reason) << ','
             << "\"sdr\":{\"source\":{\"source_type\":"
             << quote_json(to_wire(source_.source_type)) << ",\"source_id\":"
             << quote_json(source_.source_id) << ",\"display_name\":"
             << quote_json(source_.display_name) << ",\"uri\":"
             << quote_json(source_.uri) << ",\"backend_id\":"
             << quote_json(source_.backend_id) << ",\"schema_version\":"
             << source_.schema_version << "},\"spectrum_file\":"
             << quote_json(data_path_.filename().string()) << ",\"index_file\":"
             << quote_json(index_path_.filename().string()) << ",\"written_frames\":"
             << metrics_.written_frames << ",\"written_bytes\":"
             << metrics_.written_bytes << ",\"recorder_queue_dropped_frames\":"
             << metrics_.recorder_queue_dropped_frames << "}}\n";
    flush_or_throw(manifest, "spectrum manifest");
    manifest.close();
    if (manifest.bad()) {
        throw std::runtime_error("native spectrum recording manifest close failed");
    }
    std::error_code error;
    std::filesystem::remove(part, error);
    error.clear();
    std::filesystem::rename(temporary, part, error);
    if (error) {
        throw std::runtime_error(
            "cannot publish native spectrum recording manifest part: " + error.message()
        );
    }
}

void SpectrumFrameRecordingWriter::write_index_line(const std::string& line) {
    index_ << line << '\n';
    if (!index_) {
        throw std::runtime_error("native spectrum recording index write failed");
    }
}

void SpectrumFrameRecordingWriter::require_active() const {
    if (!active_ || finalized_) {
        throw ConfigurationError("native spectrum writer is not active");
    }
}

NativeRecordingRecoveryScan scan_native_recording_prefix(
    const std::filesystem::path& output_uri
) {
    NativeRecordingRecoveryScan result;
    const auto base = normalized_base_path(output_uri);
    const auto iq_manifest = std::filesystem::path(base.string() + ".sigmf-meta");
    const auto spectrum_manifest =
        std::filesystem::path(base.string() + ".sdr-spectrum.meta");
    const auto iq_index = std::filesystem::path(base.string() + ".sigmf-index.jsonl");
    const auto iq_gaps = std::filesystem::path(base.string() + ".sigmf-gaps.jsonl");
    const auto spectrum_data = std::filesystem::path(base.string() + ".sdr-spectrum.bin");
    result.iq_manifest_final = std::filesystem::exists(iq_manifest);
    result.iq_manifest_partial = std::filesystem::exists(iq_manifest.string() + ".part");
    result.spectrum_manifest_final = std::filesystem::exists(spectrum_manifest);
    result.spectrum_manifest_partial =
        std::filesystem::exists(spectrum_manifest.string() + ".part");

    const auto select_artifact = [](const std::filesystem::path& final_path) {
        const auto partial = std::filesystem::path(final_path.string() + ".part");
        return std::filesystem::exists(partial) ? partial : final_path;
    };
    const auto index_scan = scan_newline_records(select_artifact(iq_index));
    result.iq_index_complete_records = index_scan.complete_records;
    result.iq_index_complete_bytes = index_scan.complete_bytes;
    result.iq_index_trailing_bytes = index_scan.trailing_bytes;
    const auto gap_scan = scan_newline_records(select_artifact(iq_gaps));
    result.iq_gap_complete_records = gap_scan.complete_records;
    result.iq_gap_complete_bytes = gap_scan.complete_bytes;
    result.iq_gap_trailing_bytes = gap_scan.trailing_bytes;

    const auto parent = base.parent_path().empty() ? std::filesystem::path(".")
                                                    : base.parent_path();
    const auto prefix = base.filename().string() + ".";
    std::error_code directory_error;
    for (std::filesystem::directory_iterator iterator(parent, directory_error), end;
         !directory_error && iterator != end;
         iterator.increment(directory_error)) {
        const auto name = iterator->path().filename().string();
        if (!name.starts_with(prefix) ||
            (!has_suffix(name, ".sigmf-data") &&
             !has_suffix(name, ".sigmf-data.part"))) {
            continue;
        }
        std::error_code size_error;
        const auto size = std::filesystem::file_size(iterator->path(), size_error);
        if (!size_error) {
            ++result.iq_data_segments;
            result.iq_data_bytes += size;
        }
    }

    const auto selected_spectrum = select_artifact(spectrum_data);
    std::error_code spectrum_size_error;
    const auto spectrum_size = std::filesystem::file_size(selected_spectrum, spectrum_size_error);
    if (spectrum_size_error || spectrum_size < spectrum_magic.size()) {
        return result;
    }
    std::ifstream spectrum(selected_spectrum, std::ios::binary);
    std::array<char, spectrum_magic.size()> header{};
    spectrum.read(header.data(), static_cast<std::streamsize>(header.size()));
    if (!spectrum || header != spectrum_magic) {
        result.spectrum_trailing_bytes = spectrum_size;
        return result;
    }
    result.spectrum_binary_header_valid = true;
    std::uint64_t offset = spectrum_magic.size();
    while (offset <= spectrum_size - spectrum_magic.size()) {
        std::uint64_t payload_bytes{};
        if (!read_u64_le(spectrum, payload_bytes) ||
            payload_bytes < spectrum_record_fixed_payload_bytes ||
            payload_bytes > spectrum_size - offset - 8U) {
            break;
        }
        if (payload_bytes >
            static_cast<std::uint64_t>(std::numeric_limits<std::streamoff>::max())) {
            break;
        }
        spectrum.seekg(static_cast<std::streamoff>(payload_bytes), std::ios::cur);
        if (!spectrum) {
            break;
        }
        offset += 8U + payload_bytes;
        ++result.spectrum_complete_records;
    }
    result.spectrum_complete_bytes = offset;
    result.spectrum_trailing_bytes = spectrum_size - offset;
    return result;
}

NativeRecordingOpenInfo inspect_final_native_recording(
    const std::filesystem::path& output_uri
) {
    const auto base = normalized_base_path(output_uri);
    NativeRecordingOpenInfo result;
    const auto iq_manifest = std::filesystem::path(base.string() + ".sigmf-meta");
    const auto spectrum_manifest = std::filesystem::path(base.string() + ".sdr-spectrum.meta");
    result.iq_manifest_final = completed_manifest(iq_manifest, "native_iq_segmented");
    result.spectrum_manifest_final =
        completed_manifest(spectrum_manifest, "native_spectrum_frames");
    if (result.iq_manifest_final) {
        result.iq_gap_records = scan_newline_records(
            std::filesystem::path(base.string() + ".sigmf-gaps.jsonl")
        ).complete_records;
    }
    if (result.spectrum_manifest_final) {
        result.spectrum_frame_count = scan_native_recording_prefix(base).spectrum_complete_records;
    }

    const auto lifecycle_path = std::filesystem::path(base.string() + ".sdr-lifecycle.jsonl");
    if (!std::filesystem::exists(lifecycle_path)) {
        return result;
    }
    std::ifstream lifecycle(lifecycle_path, std::ios::binary);
    if (!lifecycle) {
        throw std::runtime_error("cannot open finalized native recording lifecycle sidecar");
    }
    std::string line;
    while (std::getline(lifecycle, line)) {
        if (line.size() > 64U * 1024U) {
            throw std::runtime_error("native recording lifecycle line exceeds bounded limit");
        }
        if (line.find("\"type\":\"epoch_start\"") != std::string::npos) {
            ++result.lifecycle_epochs;
        }
        if (line.find("\"gap_scope\":\"live_control_transaction\"") == std::string::npos) {
            continue;
        }
        ++result.lifecycle_control_gaps;
        result.lifecycle_control_gap_duration_ns += json_integer<std::uint64_t>(
            line, "gap_duration_ns"
        );
    }
    if (!lifecycle.eof()) {
        throw std::runtime_error("cannot read finalized native recording lifecycle sidecar");
    }
    return result;
}

NativeSpectrumRecordingReader::NativeSpectrumRecordingReader(
    const std::filesystem::path& output_uri
) : base_path_(normalized_base_path(output_uri)),
    data_path_(base_path_.string() + ".sdr-spectrum.bin"),
    index_path_(base_path_.string() + ".sdr-spectrum-index.jsonl"),
    info_(inspect_final_native_recording(base_path_)) {
    if (!info_.spectrum_manifest_final) {
        throw ConfigurationError(
            "native spectrum replay requires a completed .sdr-spectrum.meta manifest; partial captures are scan-only"
        );
    }
    std::error_code data_error;
    const auto data_bytes = std::filesystem::file_size(data_path_, data_error);
    if (data_error || data_bytes < spectrum_magic.size()) {
        throw std::runtime_error("completed native spectrum recording data is missing or truncated");
    }
    std::ifstream data(data_path_, std::ios::binary);
    std::array<char, spectrum_magic.size()> header{};
    data.read(header.data(), static_cast<std::streamsize>(header.size()));
    if (!data || header != spectrum_magic) {
        throw std::runtime_error("completed native spectrum recording has an invalid header");
    }

    std::ifstream count_stream(index_path_, std::ios::binary);
    if (!count_stream) {
        throw std::runtime_error("completed native spectrum recording index is missing");
    }
    std::string line;
    while (std::getline(count_stream, line)) {
        if (line.empty() || line.size() > 64U * 1024U || !is_spectrum_index_line(line)) {
            throw std::runtime_error("completed native spectrum recording index has an invalid record");
        }
        ++frame_count_;
    }
    if (!count_stream.eof()) {
        throw std::runtime_error("cannot read completed native spectrum recording index");
    }
    info_.spectrum_frame_count = frame_count_;
    constexpr std::uint64_t maximum_sparse_entries = 4096U;
    sparse_stride_ = std::max<std::uint64_t>(
        1U, (frame_count_ + maximum_sparse_entries - 1U) / maximum_sparse_entries
    );

    std::ifstream sparse_stream(index_path_, std::ios::binary);
    if (!sparse_stream) {
        throw std::runtime_error("cannot reopen completed native spectrum recording index");
    }
    std::uint64_t ordinal = 0U;
    while (true) {
        const auto stream_offset = sparse_stream.tellg();
        if (!std::getline(sparse_stream, line)) {
            break;
        }
        if (ordinal % sparse_stride_ == 0U) {
            if (stream_offset < 0) {
                throw std::runtime_error("native spectrum index offset is invalid");
            }
            sparse_index_.push_back(SparseIndexEntry{
                ordinal, static_cast<std::uint64_t>(stream_offset)
            });
        }
        const auto timestamp = json_integer<std::int64_t>(line, "timestamp_ns");
        if (ordinal == 0U) {
            first_timestamp_ns_ = timestamp;
        }
        last_timestamp_ns_ = timestamp;
        ++ordinal;
    }
    if (!sparse_stream.eof() || ordinal != frame_count_ ||
        sparse_index_.size() > maximum_sparse_entries) {
        throw std::runtime_error("native spectrum sparse index construction failed");
    }

    std::error_code index_error;
    const auto index_bytes = std::filesystem::file_size(index_path_, index_error);
    if (index_error || data_bytes > std::numeric_limits<std::uint64_t>::max() - index_bytes) {
        throw std::runtime_error("native spectrum recording size is invalid");
    }
    source_size_bytes_ = data_bytes + index_bytes;
}

const NativeRecordingOpenInfo& NativeSpectrumRecordingReader::info() const noexcept {
    return info_;
}

std::uint64_t NativeSpectrumRecordingReader::frame_count() const noexcept {
    return frame_count_;
}

std::int64_t NativeSpectrumRecordingReader::first_timestamp_ns() const noexcept {
    return first_timestamp_ns_;
}

std::int64_t NativeSpectrumRecordingReader::last_timestamp_ns() const noexcept {
    return last_timestamp_ns_;
}

std::uint64_t NativeSpectrumRecordingReader::source_size_bytes() const noexcept {
    return source_size_bytes_;
}

std::string NativeSpectrumRecordingReader::index_line_at(const std::uint64_t ordinal) const {
    if (ordinal >= frame_count_) {
        throw std::out_of_range("native spectrum replay ordinal is outside the final index");
    }
    const auto sparse_position = ordinal / sparse_stride_;
    if (sparse_position >= sparse_index_.size()) {
        throw std::runtime_error("native spectrum sparse index has no seek anchor");
    }
    const auto& anchor = sparse_index_[static_cast<std::size_t>(sparse_position)];
    std::ifstream index(index_path_, std::ios::binary);
    if (!index) {
        throw std::runtime_error("cannot open completed native spectrum recording index");
    }
    index.seekg(static_cast<std::streamoff>(anchor.offset), std::ios::beg);
    if (!index) {
        throw std::runtime_error("cannot seek completed native spectrum recording index");
    }
    std::string line;
    for (std::uint64_t current = anchor.ordinal; current <= ordinal; ++current) {
        if (!std::getline(index, line) || line.empty() || line.size() > 64U * 1024U ||
            !is_spectrum_index_line(line)) {
            throw std::runtime_error("native spectrum recording index contains an invalid seek record");
        }
    }
    return line;
}

NativeSpectrumReplayEntry NativeSpectrumRecordingReader::entry_at(
    const std::uint64_t ordinal
) const {
    const auto line = index_line_at(ordinal);
    return NativeSpectrumReplayEntry{
        ordinal,
        json_integer<std::uint64_t>(line, "offset"),
        json_integer<std::uint64_t>(line, "record_bytes"),
        json_integer<std::uint64_t>(line, "frame_sequence"),
        json_integer<std::int64_t>(line, "timestamp_ns"),
    };
}

NativeSpectrumReplayFrame NativeSpectrumRecordingReader::read_frame(
    const std::uint64_t ordinal
) const {
    const auto line = index_line_at(ordinal);
    const auto offset = json_integer<std::uint64_t>(line, "offset");
    const auto record_bytes = json_integer<std::uint64_t>(line, "record_bytes");
    std::error_code size_error;
    const auto data_bytes = std::filesystem::file_size(data_path_, size_error);
    if (size_error || offset < spectrum_magic.size() || record_bytes < 8U ||
        offset > data_bytes || record_bytes > data_bytes - offset) {
        throw std::runtime_error("native spectrum replay index points outside final data");
    }
    std::ifstream data(data_path_, std::ios::binary);
    data.seekg(static_cast<std::streamoff>(offset), std::ios::beg);
    std::uint64_t payload_bytes{};
    std::uint32_t version{};
    std::uint32_t bin_count{};
    if (!data || !read_u64_le(data, payload_bytes) || !read_u32_le(data, version) ||
        !read_u32_le(data, bin_count) || version != 1U ||
        payload_bytes != record_bytes - 8U || payload_bytes < spectrum_record_fixed_payload_bytes ||
        bin_count == 0U || bin_count > 262144U ||
        payload_bytes != spectrum_record_fixed_payload_bytes + static_cast<std::uint64_t>(bin_count) * 12U) {
        throw std::runtime_error("native spectrum replay record has an invalid bounded shape");
    }

    NativeSpectrumReplayFrame result;
    result.source.source_type = SourceType::RecordedSpectrum;
    result.source.source_id = json_string(line, "source_id");
    result.source.display_name = result.source.source_id;
    result.source.backend_id = "native-recording-reader";
    result.calibration_profile_id = json_string(line, "calibration_profile_id");
    std::uint32_t window{};
    std::uint32_t detector{};
    std::uint32_t precision{};
    std::uint32_t raw_unit{};
    std::uint32_t raw_calibration{};
    std::uint64_t raw_timestamp{};
    double uncertainty{};
    if (!read_u64_le(data, result.frame_sequence) ||
        !read_u64_le(data, result.first_sample_index) ||
        !read_u64_le(data, raw_timestamp) ||
        !read_u64_le(data, result.config_generation) ||
        !read_f64_le(data, result.center_frequency_hz) ||
        !read_f64_le(data, result.sample_rate_hz) ||
        !read_f64_le(data, result.analog_bandwidth_hz) ||
        !read_f64_le(data, result.fft_bin_width_hz) ||
        !read_f64_le(data, result.enbw_hz) ||
        !read_f64_le(data, result.nominal_rbw_hz) ||
        !read_u32_le(data, result.fft_size) || !read_u32_le(data, result.hop_size) ||
        !read_u32_le(data, window) || !read_u32_le(data, detector) || !read_u32_le(data, precision) ||
        !read_u32_le(data, raw_unit) || !read_u32_le(data, raw_calibration) ||
        !read_u32_le(data, result.quality_flags) || !read_f64_le(data, uncertainty) ||
        !read_u64_le(data, result.dropped_samples_before) ||
        !read_u64_le(data, result.dropped_iq_blocks_before) ||
        !read_u64_le(data, result.dropped_fft_frames_before) || raw_unit > 4U || raw_calibration > 5U) {
        throw std::runtime_error("native spectrum replay record is truncated or has invalid metadata");
    }
    result.timestamp_ns = static_cast<std::int64_t>(raw_timestamp);
    result.unit = static_cast<SpectrumUnit>(raw_unit);
    result.calibration_status = static_cast<CalibrationStatus>(raw_calibration);
    auto frequencies = std::make_shared<std::vector<double>>(bin_count);
    auto values = std::make_shared<std::vector<float>>(bin_count);
    for (auto& frequency : *frequencies) {
        if (!read_f64_le(data, frequency)) {
            throw std::runtime_error("native spectrum replay frequency array is truncated");
        }
    }
    for (auto& value : *values) {
        if (!read_f32_le(data, value)) {
            throw std::runtime_error("native spectrum replay value array is truncated");
        }
    }
    if (!data || result.frame_sequence != json_integer<std::uint64_t>(line, "frame_sequence") ||
        result.timestamp_ns != json_integer<std::int64_t>(line, "timestamp_ns") ||
        result.config_generation != json_integer<std::uint64_t>(line, "config_generation")) {
        throw std::runtime_error("native spectrum replay data/index provenance mismatch");
    }
    result.frequencies_hz = std::move(frequencies);
    result.values = std::move(values);
    return result;
}

}  // namespace sdr_core

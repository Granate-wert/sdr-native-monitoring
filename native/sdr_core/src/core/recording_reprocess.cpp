#include "sdr_core/recording_reprocess.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace sdr_core {
namespace {

constexpr std::uint64_t max_reprocess_block_bytes = 64U * 1024U * 1024U;

[[nodiscard]] std::filesystem::path normalized_base_path(const std::filesystem::path& output_uri) {
    const auto value = output_uri.string();
    static constexpr std::string_view suffixes[] = {
        ".sigmf-meta", ".sigmf-index.jsonl", ".sigmf-gaps.jsonl",
        ".sdr-spectrum.meta", ".sdr-spectrum.bin", ".sdr-spectrum-index.jsonl",
    };
    for (const auto suffix : suffixes) {
        if (value.size() >= suffix.size() &&
            value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0) {
            return std::filesystem::path(value.substr(0U, value.size() - suffix.size()));
        }
    }
    return output_uri;
}

[[nodiscard]] std::size_t json_value_offset(const std::string_view line, const std::string_view key) {
    const std::string needle = "\"" + std::string(key) + "\":";
    const auto found = line.find(needle);
    if (found == std::string_view::npos) {
        throw std::runtime_error("native I/Q index is missing field " + std::string(key));
    }
    return found + needle.size();
}

template <typename Integer>
[[nodiscard]] Integer json_integer(const std::string_view line, const std::string_view key) {
    const auto start = json_value_offset(line, key);
    Integer value{};
    const auto* first = line.data() + start;
    const auto* last = line.data() + line.size();
    const auto [end, error] = std::from_chars(first, last, value);
    if (error != std::errc{} || end == first) {
        throw std::runtime_error("native I/Q index has invalid integer field " + std::string(key));
    }
    return value;
}

[[nodiscard]] double json_number(const std::string_view line, const std::string_view key) {
    const auto start = json_value_offset(line, key);
    const auto* first = line.data() + start;
    const auto* last = line.data() + line.size();
    double value{};
    const auto [end, error] = std::from_chars(first, last, value);
    if (error != std::errc{} || end == first || !std::isfinite(value)) {
        throw std::runtime_error("native I/Q index has invalid number field " + std::string(key));
    }
    return value;
}

[[nodiscard]] std::string json_string(const std::string_view line, const std::string_view key) {
    auto position = json_value_offset(line, key);
    if (position >= line.size() || line[position] != '"') {
        throw std::runtime_error("native I/Q metadata has invalid string field " + std::string(key));
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
            throw std::runtime_error("native I/Q metadata uses unsupported JSON escape");
        }
    }
    throw std::runtime_error("native I/Q metadata has unterminated string field " + std::string(key));
}

[[nodiscard]] bool is_index_type(const std::string_view line, const std::string_view type) {
    return line.starts_with("{\"type\":\"") &&
           line.substr(9U).starts_with(type) &&
           line.size() > 9U + type.size() && line[9U + type.size()] == '"';
}

[[nodiscard]] SampleFormat sample_format_from_wire(const std::string_view value) {
    if (value == "ci8") {
        return SampleFormat::ComplexInt8Interleaved;
    }
    if (value == "ci12_le") {
        return SampleFormat::ComplexInt12InInt16Le;
    }
    if (value == "ci16_le") {
        return SampleFormat::ComplexInt16Le;
    }
    if (value == "cf32_le") {
        return SampleFormat::ComplexFloat32Le;
    }
    throw std::runtime_error("native I/Q index has unsupported sample format " + std::string(value));
}

[[nodiscard]] std::uint32_t bytes_per_sample_for_reprocess(const SampleFormat format) {
    switch (format) {
    case SampleFormat::ComplexInt8Interleaved:
        return 2U;
    case SampleFormat::ComplexInt12InInt16Le:
    case SampleFormat::ComplexInt16Le:
        return 4U;
    case SampleFormat::ComplexFloat32Le:
        return 8U;
    }
    throw std::runtime_error("native I/Q index has an unknown sample format");
}

[[nodiscard]] std::filesystem::path data_path(
    const std::filesystem::path& base,
    const std::uint64_t segment
) {
    std::ostringstream name;
    name << base.string() << '.' << std::setw(6) << std::setfill('0') << segment
         << ".sigmf-data";
    return name.str();
}

[[nodiscard]] SourceDescriptor source_from_manifest(const std::filesystem::path& manifest_path) {
    std::ifstream manifest(manifest_path, std::ios::binary);
    if (!manifest) {
        throw std::runtime_error("cannot open final native I/Q manifest");
    }
    const std::string text{
        std::istreambuf_iterator<char>(manifest), std::istreambuf_iterator<char>()
    };
    if (text.find("\"completed\":true") == std::string::npos ||
        text.find("\"recording_type\":\"native_iq_segmented\"") == std::string::npos) {
        throw ConfigurationError("native I/Q reprocess requires a completed native_iq_segmented manifest");
    }
    SourceDescriptor source;
    source.source_type = SourceType::RecordedIq;
    source.source_id = json_string(text, "source_id");
    source.display_name = json_string(text, "display_name");
    source.uri = json_string(text, "uri");
    source.backend_id = json_string(text, "backend_id");
    source.schema_version = contract_schema_version;
    validate(source);
    return source;
}

[[nodiscard]] std::uint32_t output_capacity_for(const DspConfig& dsp) {
    if (dsp.batch_size > std::numeric_limits<std::uint32_t>::max() - 2U) {
        throw ConfigurationError("DSP batch_size is too large for bounded replay output capacity");
    }
    return std::max(8U, dsp.batch_size + 2U);
}

[[nodiscard]] std::int64_t timestamp_offset_ns(
    const std::uint64_t offset_samples,
    const double sample_rate_hz
) {
    const auto value = static_cast<long double>(offset_samples) * 1.0e9L /
                       static_cast<long double>(sample_rate_hz);
    if (value > static_cast<long double>(std::numeric_limits<std::int64_t>::max())) {
        throw std::runtime_error("native I/Q chunk timestamp offset overflows int64");
    }
    return static_cast<std::int64_t>(std::llround(value));
}

}  // namespace

NativeIqRecordingReprocessor::NativeIqRecordingReprocessor(
    std::filesystem::path input_uri,
    std::filesystem::path output_uri,
    DspConfig dsp,
    DspBackendSelectionOptions selection,
    const std::uint32_t max_samples_per_push
) : input_base_path_(normalized_base_path(input_uri)),
    output_base_path_(normalized_base_path(output_uri)),
    dsp_(std::move(dsp)),
    selection_(std::move(selection)),
    max_samples_per_push_(max_samples_per_push) {
    validate(dsp_);
    validate(selection_);
    if (selection_.preference == ComputeBackendKind::Auto ||
        selection_.preference == ComputeBackendKind::Hip) {
        throw ConfigurationError("native I/Q reprocess requires an explicit CPU or CUDA backend");
    }
    if (max_samples_per_push_ == 0U) {
        throw ConfigurationError("native I/Q reprocess max_samples_per_push must be positive");
    }
    // DspBackend emits/pends FFT output only when it is polled.  Limit one
    // push to one hop so a single physical input block cannot evict hundreds
    // of computed SpectrumFrames from its bounded output deque before this
    // reprocessor drains it.  The caller's larger value remains an upper
    // admission request, never permission for an implicit output drop.
    max_samples_per_push_ = std::min(max_samples_per_push_, dsp_.hop_size);
    if (input_base_path_ == output_base_path_) {
        throw ConfigurationError("native I/Q reprocess output must not overwrite its input capture");
    }
    const auto admission = inspect_final_native_recording(input_base_path_);
    if (!admission.iq_manifest_final) {
        throw ConfigurationError("native I/Q reprocess accepts only a completed I/Q manifest");
    }
    source_ = source_from_manifest(input_base_path_.string() + ".sigmf-meta");
    index_.open(input_base_path_.string() + ".sigmf-index.jsonl", std::ios::binary);
    if (!index_) {
        throw std::runtime_error("cannot open final native I/Q index");
    }
    std::string line;
    while (std::getline(index_, line)) {
        if (is_index_type(line, "block")) {
            ++progress_.total_input_blocks;
        }
    }
    if (!index_.eof()) {
        throw std::runtime_error("cannot scan final native I/Q index");
    }
    index_.clear();
    index_.seekg(0);
    if (progress_.total_input_blocks == 0U) {
        throw ConfigurationError("completed native I/Q capture contains no I/Q blocks");
    }

    DspOptions options;
    options.source = source_;
    options.output_capacity = output_capacity_for(dsp_);
    backend_ = make_dsp_backend(selection_, std::move(options));
    backend_->configure(dsp_);

    RecordingConfig recording;
    recording.enabled = true;
    recording.output_uri = output_base_path_.string();
    recording.record_spectrum = true;
    recording.chunk_samples = 1'048'576U;
    recording.queue_capacity = 1U;
    recording.stop_on_overflow = false;
    recording.schema_version = contract_schema_version;
    writer_ = std::make_unique<SpectrumFrameRecordingWriter>(std::move(recording), source_);
    writer_->start();

    progress_.backend_requested = selection_.preference;
    progress_.backend_active = backend_->metrics().active_backend;
    progress_.output_uri = output_base_path_.string();
}

NativeIqRecordingReprocessor::~NativeIqRecordingReprocessor() noexcept {
    if (progress_.state == NativeIqReprocessState::Ready ||
        progress_.state == NativeIqReprocessState::Running) {
        finish_cancelled();
    }
}

bool NativeIqRecordingReprocessor::process(const std::uint32_t max_blocks) {
    if (max_blocks == 0U) {
        throw ConfigurationError("native I/Q reprocess process max_blocks must be positive");
    }
    if (progress_.state == NativeIqReprocessState::Completed ||
        progress_.state == NativeIqReprocessState::Cancelled ||
        progress_.state == NativeIqReprocessState::Failed) {
        return true;
    }
    progress_.state = NativeIqReprocessState::Running;
    try {
        std::uint32_t processed_this_call = 0U;
        std::uint32_t scanned_this_call = 0U;
        if (max_blocks > (std::numeric_limits<std::uint32_t>::max() - 4U) / 4U) {
            throw ConfigurationError("native I/Q reprocess process max_blocks is too large");
        }
        const auto max_lines = max_blocks * 4U + 4U;
        std::string line;
        while (processed_this_call < max_blocks && scanned_this_call < max_lines &&
               std::getline(index_, line)) {
            ++scanned_this_call;
            if (cancel_requested_.load(std::memory_order_relaxed)) {
                finish_cancelled();
                return true;
            }
            if (is_index_type(line, "capture")) {
                capture_.center_frequency_hz = json_number(line, "center_frequency_hz");
                capture_.sample_rate_hz = json_number(line, "sample_rate_hz");
                capture_.config_generation = json_integer<std::uint64_t>(line, "config_generation");
                if (capture_.sample_rate_hz <= 0.0) {
                    throw std::runtime_error("native I/Q capture metadata has non-positive sample rate");
                }
                capture_.valid = true;
                continue;
            }
            if (!is_index_type(line, "block")) {
                continue;
            }
            process_block(line);
            if (progress_.state == NativeIqReprocessState::Cancelled) {
                return true;
            }
            ++processed_this_call;
        }
        if (index_.bad()) {
            throw std::runtime_error("cannot read final native I/Q index");
        }
        if (cancel_requested_.load(std::memory_order_relaxed)) {
            finish_cancelled();
            return true;
        }
        if (index_.eof()) {
            finish_completed();
            return true;
        }
        if (processed_this_call == 0U) {
            throw std::runtime_error("native I/Q index has an excessive non-block record run");
        }
        return false;
    } catch (const std::exception& error) {
        fail(error.what());
        throw;
    }
}

void NativeIqRecordingReprocessor::request_cancel() noexcept {
    cancel_requested_.store(true, std::memory_order_relaxed);
}

NativeIqReprocessProgress NativeIqRecordingReprocessor::progress() const {
    auto result = progress_;
    if (backend_) {
        const auto metrics = backend_->metrics();
        result.backend_active = metrics.active_backend;
        result.discarded_fft_frames = metrics.fft_frames_dropped;
    }
    if (writer_) {
        result.written_spectrum_frames = writer_->metrics().written_frames;
    }
    return result;
}

void NativeIqRecordingReprocessor::process_block(const std::string& index_line) {
    if (!capture_.valid) {
        throw std::runtime_error("native I/Q block appears before capture metadata");
    }
    const auto segment = json_integer<std::uint64_t>(index_line, "segment");
    const auto offset = json_integer<std::uint64_t>(index_line, "offset");
    const auto byte_count = json_integer<std::uint64_t>(index_line, "byte_count");
    const auto sample_count = json_integer<std::uint32_t>(index_line, "sample_count");
    const auto first_sample_index = json_integer<std::uint64_t>(index_line, "first_sample_index");
    const auto timestamp_ns = json_integer<std::int64_t>(index_line, "timestamp_ns");
    const auto source_sequence = json_integer<std::uint64_t>(index_line, "source_sequence");
    const auto config_generation = json_integer<std::uint64_t>(index_line, "config_generation");
    const auto flags = json_integer<std::uint32_t>(index_line, "flags");
    const auto sample_format = sample_format_from_wire(json_string(index_line, "sample_format"));
    const auto width = static_cast<std::uint64_t>(bytes_per_sample_for_reprocess(sample_format));
    if (sample_count == 0U || sample_count > std::numeric_limits<std::uint64_t>::max() / width ||
        byte_count != static_cast<std::uint64_t>(sample_count) * width ||
        byte_count > max_reprocess_block_bytes ||
        config_generation != capture_.config_generation ||
        first_sample_index > std::numeric_limits<std::uint64_t>::max() - sample_count ||
        offset > static_cast<std::uint64_t>(std::numeric_limits<std::streamoff>::max())) {
        throw std::runtime_error("native I/Q block metadata is inconsistent with its final index");
    }

    std::ifstream data(data_path(input_base_path_, segment), std::ios::binary);
    if (!data) {
        throw std::runtime_error("native I/Q data segment is missing");
    }
    data.seekg(static_cast<std::streamoff>(offset));
    if (!data) {
        throw std::runtime_error("native I/Q data segment seek failed");
    }
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(byte_count));
    data.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    if (data.gcount() != static_cast<std::streamsize>(bytes.size())) {
        throw std::runtime_error("native I/Q data segment is truncated");
    }

    if (has_expected_next_sample_index_ && first_sample_index < expected_next_sample_index_) {
        throw std::runtime_error("native I/Q index sample indexes overlap or regress");
    }
    if (has_expected_next_sample_index_ && first_sample_index != expected_next_sample_index_) {
        ++progress_.input_gap_boundaries;
        if (first_sample_index > expected_next_sample_index_) {
            progress_.input_gap_samples += first_sample_index - expected_next_sample_index_;
        }
    }

    std::uint32_t chunk_offset = 0U;
    while (chunk_offset < sample_count) {
        if (cancel_requested_.load(std::memory_order_relaxed)) {
            finish_cancelled();
            return;
        }
        const auto chunk_samples = std::min(max_samples_per_push_, sample_count - chunk_offset);
        const auto begin = static_cast<std::size_t>(chunk_offset) * static_cast<std::size_t>(width);
        const auto end = begin + static_cast<std::size_t>(chunk_samples) * static_cast<std::size_t>(width);
        auto chunk = std::vector<std::uint8_t>(
            bytes.begin() + static_cast<std::ptrdiff_t>(begin),
            bytes.begin() + static_cast<std::ptrdiff_t>(end)
        );
        IqBlock block;
        block.source_sequence = source_sequence;
        block.first_sample_index = first_sample_index + chunk_offset;
        const auto offset_ns = timestamp_offset_ns(chunk_offset, capture_.sample_rate_hz);
        if ((offset_ns > 0 && timestamp_ns > std::numeric_limits<std::int64_t>::max() - offset_ns) ||
            (offset_ns < 0 && timestamp_ns < std::numeric_limits<std::int64_t>::min() - offset_ns)) {
            throw std::runtime_error("native I/Q chunk timestamp overflows int64");
        }
        block.timestamp_ns = timestamp_ns + offset_ns;
        block.center_frequency_hz = capture_.center_frequency_hz;
        block.sample_rate_hz = capture_.sample_rate_hz;
        block.sample_format = sample_format;
        block.sample_count = chunk_samples;
        block.flags = static_cast<QualityFlag>(flags);
        block.samples = std::make_shared<const std::vector<std::uint8_t>>(std::move(chunk));
        block.config_generation = config_generation;
        backend_->push_iq(block);
        flush_backend_output(false);
        chunk_offset += chunk_samples;
    }
    progress_.processed_input_samples += sample_count;
    expected_next_sample_index_ = first_sample_index + sample_count;
    has_expected_next_sample_index_ = true;
    ++progress_.processed_input_blocks;
}

void NativeIqRecordingReprocessor::flush_backend_output(const bool flush_partial_batch) {
    const auto frames = backend_->poll_spectrum(0U, flush_partial_batch);
    for (const auto& frame : frames) {
        writer_->write_frame(frame);
    }
}

void NativeIqRecordingReprocessor::finish_completed() {
    if (progress_.state == NativeIqReprocessState::Completed) {
        return;
    }
    flush_backend_output(true);
    writer_->finalize();
    index_.close();
    progress_.state = NativeIqReprocessState::Completed;
    progress_.message.clear();
}

void NativeIqRecordingReprocessor::finish_cancelled() noexcept {
    if (progress_.state == NativeIqReprocessState::Completed ||
        progress_.state == NativeIqReprocessState::Cancelled ||
        progress_.state == NativeIqReprocessState::Failed) {
        return;
    }
    try {
        if (writer_) {
            writer_->abort("reprocess_cancelled");
        }
        if (index_.is_open()) {
            index_.close();
        }
    } catch (...) {
    }
    progress_.state = NativeIqReprocessState::Cancelled;
    progress_.message = "reprocess cancelled; output remains partial and scan-only";
}

void NativeIqRecordingReprocessor::fail(std::string message) noexcept {
    if (progress_.state == NativeIqReprocessState::Completed ||
        progress_.state == NativeIqReprocessState::Failed) {
        return;
    }
    try {
        if (writer_) {
            writer_->abort("reprocess_failed");
        }
        if (index_.is_open()) {
            index_.close();
        }
    } catch (...) {
    }
    progress_.state = NativeIqReprocessState::Failed;
    progress_.message = std::move(message);
}

}  // namespace sdr_core

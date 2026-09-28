#include "sdr_hackrf/hackrf_sweep_block.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void fill_block(const std::span<std::uint8_t> bytes, const std::uint64_t frequency_hz) {
    expect(bytes.size() == sdr_hackrf::hackrf_sweep_block_bytes, "fixture block size");
    std::fill(bytes.begin(), bytes.end(), static_cast<std::uint8_t>(0x80U));
    bytes[0] = 0x7fU;
    bytes[1] = 0x7fU;
    for (std::size_t index = 0U; index < 8U; ++index) {
        bytes[index + 2U] = static_cast<std::uint8_t>(frequency_hz >> (8U * index));
    }
    bytes[sdr_hackrf::hackrf_sweep_header_bytes] = 0x00U;
    bytes.back() = 0xffU;
}

void exact_header_and_borrowed_ci8() {
    std::vector<std::uint8_t> buffer(sdr_hackrf::hackrf_sweep_block_bytes);
    constexpr std::uint64_t frequency_hz = 2'420'000'001U;
    fill_block(buffer, frequency_hz);
    const auto parsed = sdr_hackrf::parse_hackrf_sweep_block(buffer);
    expect(parsed.syntax_valid(), "valid firmware header must parse");
    expect(parsed.block.reported_tuned_frequency_hz == frequency_hz,
           "eight-byte frequency must be decoded little-endian");
    expect(parsed.block.interleaved_ci8.size() == sdr_hackrf::hackrf_sweep_ci8_bytes,
           "CI8 payload must exclude the entire ten-byte header");
    expect(parsed.block.interleaved_ci8.data() == buffer.data() + sdr_hackrf::hackrf_sweep_header_bytes,
           "parser must borrow without copying or shifting samples");
    expect(parsed.block.interleaved_ci8.front() == 0x00U &&
           parsed.block.interleaved_ci8.back() == 0xffU,
           "raw CI8 signed-byte bit patterns must be preserved");
}

void malformed_blocks_fail_closed() {
    std::vector<std::uint8_t> buffer(sdr_hackrf::hackrf_sweep_block_bytes + 1U);
    fill_block(std::span<std::uint8_t>(buffer).first(sdr_hackrf::hackrf_sweep_block_bytes), 100'000'000U);
    for (const auto bytes : {
            std::span<const std::uint8_t>{},
            std::span<const std::uint8_t>(buffer).first(buffer.size() - 2U),
            std::span<const std::uint8_t>(buffer),
         }) {
        const auto parsed = sdr_hackrf::parse_hackrf_sweep_block(bytes);
        expect(parsed.status == sdr_hackrf::HackrfSweepBlockStatus::InvalidSize &&
               parsed.block.interleaved_ci8.empty(), "truncated/oversized block must not publish IQ");
    }
    buffer[0] = 0x00U;
    auto parsed = sdr_hackrf::parse_hackrf_sweep_block(
        std::span<const std::uint8_t>(buffer).first(sdr_hackrf::hackrf_sweep_block_bytes));
    expect(parsed.status == sdr_hackrf::HackrfSweepBlockStatus::InvalidMarker &&
           parsed.block.interleaved_ci8.empty(), "bad first marker must not publish IQ");
    buffer[0] = 0x7fU;
    buffer[1] = 0x00U;
    parsed = sdr_hackrf::parse_hackrf_sweep_block(
        std::span<const std::uint8_t>(buffer).first(sdr_hackrf::hackrf_sweep_block_bytes));
    expect(parsed.status == sdr_hackrf::HackrfSweepBlockStatus::InvalidMarker &&
           parsed.block.interleaved_ci8.empty(), "bad second marker must not publish IQ");
}

void frequency_is_decoded_without_premature_admission() {
    std::vector<std::uint8_t> buffer(sdr_hackrf::hackrf_sweep_block_bytes);
    for (const std::uint64_t frequency_hz :
         std::array<std::uint64_t, 7>{
             0U, 999'999U, 1'000'000U, 6'000'000'000U,
             7'250'000'000U, 7'250'000'001U,
             std::numeric_limits<std::uint64_t>::max(),
         }) {
        fill_block(buffer, frequency_hz);
        const auto parsed = sdr_hackrf::parse_hackrf_sweep_block(buffer);
        expect(parsed.syntax_valid() &&
               parsed.block.reported_tuned_frequency_hz == frequency_hz,
               "syntax must retain raw header; coordinator decides plan admission");
    }
}

void aligned_transfer_keeps_each_reported_frequency() {
    constexpr std::size_t blocks = 16U;  // SDK hackrf_sweep transfer example.
    std::vector<std::uint8_t> transfer(blocks * sdr_hackrf::hackrf_sweep_block_bytes);
    for (std::size_t index = 0U; index < blocks; ++index) {
        fill_block(std::span<std::uint8_t>(transfer).subspan(
                       index * sdr_hackrf::hackrf_sweep_block_bytes,
                       sdr_hackrf::hackrf_sweep_block_bytes),
                   100'000'000U + index * 20'000'000U);
    }
    transfer[7U * sdr_hackrf::hackrf_sweep_block_bytes] = 0U;
    std::size_t accepted = 0U;
    for (std::size_t index = 0U; index < blocks; ++index) {
        const auto parsed = sdr_hackrf::parse_hackrf_sweep_block(
            std::span<const std::uint8_t>(transfer).subspan(
                index * sdr_hackrf::hackrf_sweep_block_bytes,
                sdr_hackrf::hackrf_sweep_block_bytes));
        if (index == 7U) {
            expect(!parsed.syntax_valid(), "corrupt block must not inherit previous frequency");
        } else {
            expect(parsed.syntax_valid(), "later aligned block must not inherit corruption");
            expect(parsed.block.reported_tuned_frequency_hz == 100'000'000U + index * 20'000'000U,
                   "each block must keep its own reported frequency");
            ++accepted;
        }
    }
    expect(accepted == blocks - 1U, "one corrupt block must not shift subsequent boundaries");
}

}  // namespace

int main() {
    try {
        exact_header_and_borrowed_ci8();
        malformed_blocks_fail_closed();
        frequency_is_decoded_without_premature_admission();
        aligned_transfer_keeps_each_reported_frequency();
        std::cout << "HackRF sweep block syntax PASS\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

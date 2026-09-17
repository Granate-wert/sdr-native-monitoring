#include "sdr_pluto/pluto_backend.hpp"
#include "sdr_core/dual_rx_dsp.hpp"
#include "sdr_core/errors.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <limits>
#include <string>
#include <thread>
#include <vector>

namespace {
sdr_core::DeviceConfig config(const std::string& uri, const double rate = 3'000'000.0,
                              const double bandwidth = 1'500'000.0) {
    return {
        .source_id = "p06-mock",
        .context_uri = uri,
        .center_frequency_hz = 2'450'000'000.0,
        .sample_rate_hz = rate,
        .analog_bandwidth_hz = bandwidth,
        .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = 20.0,
        .channel_index = 0U,
        .buffer_samples = 1024U,
        .schema_version = sdr_core::contract_schema_version,
    };
}

struct MockHooks final {
    using void_fn = void (*)();
    using int_fn = int (*)();
    HMODULE module{};
    void_fn reset{};
    int_fn entered{};
    void_fn release{};
    int_fn destroyed{};

    MockHooks() {
        const char* path = std::getenv("LIBIIO_DLL_PATH");
        if (path == nullptr) throw std::runtime_error("LIBIIO_DLL_PATH is missing");
        module = LoadLibraryA(path);
        if (module == nullptr) throw std::runtime_error("mock libiio load failed");
        reset = reinterpret_cast<void_fn>(GetProcAddress(module, "mock_iio_reset_cancel_race"));
        entered = reinterpret_cast<int_fn>(GetProcAddress(module, "mock_iio_cancel_entered"));
        release = reinterpret_cast<void_fn>(GetProcAddress(module, "mock_iio_release_cancel"));
        destroyed = reinterpret_cast<int_fn>(GetProcAddress(module, "mock_iio_destroyed_during_cancel"));
        if (reset == nullptr || entered == nullptr || release == nullptr || destroyed == nullptr) {
            throw std::runtime_error("mock cancel-race hooks are missing");
        }
    }
    ~MockHooks() { if (module != nullptr) FreeLibrary(module); }
};
}

int main() {
    try {
        const auto runtime = sdr_pluto::runtime_info();
        if (!runtime.available || runtime.major != 0U || runtime.minor != 26U) return 1;
        if (!runtime.supports_kernel_buffer_count || !runtime.supports_buffer_blocking_mode || !runtime.supports_buffer_poll_fd) return 13;
        const auto contexts = sdr_pluto::scan_contexts();
        if (contexts.size() != 1U || contexts.front().uri != "usb:mock") return 2;
        const auto probe = sdr_pluto::probe_context("usb:mock");
        if (probe.phy_device_id.empty() || probe.rx_stream_device_id != "iio:device2") return 3;
        const auto single_topology = sdr_pluto::probe_receiver_topology("usb:mock");
        if (single_topology.phy_rx_channel_ids.size() != 1U || single_topology.input_scan_elements.size() != 2U ||
            single_topology.input_scan_elements.front().id != "voltage0" ||
            single_topology.input_scan_elements.front().storage_bits != 16U ||
            single_topology.input_scan_elements.front().significant_bits != 12U) return 32;
        // The test executable may be launched with a topology environment from
        // an outer harness.  E0's single-layout assertion must be independent
        // of that ambient state.
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "");
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "1");
        const auto dual_topology = sdr_pluto::probe_receiver_topology("usb:mock");
        if (dual_topology.input_scan_elements.size() != 4U ||
            dual_topology.input_scan_elements[2].id != "voltage2" ||
            dual_topology.input_scan_elements[3].id != "voltage3") return 33;

        // E1: RX1/RX2/BOTH are one-buffer selections.  The mock emits four
        // distinct CI12 lanes only when BOTH is selected, so the check proves
        // data deinterleaving and shared source metadata without a real radio.
        sdr_pluto::PlutoDevice dual_device("usb:mock");
        const auto dual_config = config("usb:mock");
        static_cast<void>(dual_device.configure(
            dual_config, sdr_pluto::ReceiverSelection::Both, 4U
        ));
        if (dual_device.receiver_selection() != sdr_pluto::ReceiverSelection::Both ||
            dual_device.applied_config().sample_layout.stride_bytes != 8) return 34;
        dual_device.start_stream();
        auto dual_blocks = dual_device.refill_receivers();
        if (!dual_blocks.rx1.has_value() || !dual_blocks.rx2.has_value() ||
            dual_blocks.selection != sdr_pluto::ReceiverSelection::Both) return 35;
        const auto& dual_rx1 = *dual_blocks.rx1;
        const auto& dual_rx2 = *dual_blocks.rx2;
        if (dual_rx1.source_sequence != dual_rx2.source_sequence ||
            dual_rx1.first_sample_index != dual_rx2.first_sample_index ||
            dual_rx1.timestamp_ns != dual_rx2.timestamp_ns ||
            dual_rx1.config_generation != dual_rx2.config_generation ||
            dual_rx1.samples->size() != 4096U || dual_rx2.samples->size() != 4096U ||
            (*dual_rx1.samples)[0] != 0x00U || (*dual_rx1.samples)[1] != 0xF8U ||
            (*dual_rx1.samples)[2] != 0xFFU || (*dual_rx1.samples)[3] != 0x07U ||
            (*dual_rx2.samples)[0] != 0xE8U || (*dual_rx2.samples)[1] != 0x03U ||
            (*dual_rx2.samples)[2] != 0x18U || (*dual_rx2.samples)[3] != 0xFCU) return 36;
        // E2 consumes the two canonical blocks only in native code and emits
        // bounded paired spectra.  This connects the E1 one-buffer demux to
        // the CPU publication contract without claiming a physical RX2 path.
        const sdr_core::DspConfig dual_dsp{
            .fft_size = 256U,
            .hop_size = 256U,
            .window = sdr_core::WindowType::Hann,
            .detector = sdr_core::DetectorType::Sample,
            .unit = sdr_core::SpectrumUnit::DbfsBin,
            .precision_mode = sdr_core::PrecisionMode::ReferenceF64,
            .batch_size = 1U,
            .averaging_frames = 1U,
            .schema_version = sdr_core::contract_schema_version,
        };
        const auto dual_source = [](const std::string& source_id) {
            return sdr_core::SourceDescriptor{
                .source_type = sdr_core::SourceType::LiveIq,
                .source_id = source_id,
                .display_name = source_id,
                .uri = "usb:mock",
                .backend_id = "pluto-libiio",
                .schema_version = sdr_core::contract_schema_version,
            };
        };
        sdr_core::DualRxDspPublisher dual_publisher;
        dual_publisher.configure({
            .primary = {.source = dual_source("p06-mock-rx1"), .dsp = dual_dsp},
            .secondary = {.source = dual_source("p06-mock-rx2"), .dsp = dual_dsp},
            .output_queue_capacity = 8U,
        });
        dual_publisher.push(dual_rx1, dual_rx2);
        const auto paired_spectra = dual_publisher.poll_spectrum_frames(0U);
        if (paired_spectra.size() != 4U ||
            paired_spectra.front().primary.source.source_id != "p06-mock-rx1" ||
            paired_spectra.front().secondary.source.source_id != "p06-mock-rx2" ||
            paired_spectra.front().first_sample_index != dual_rx1.first_sample_index ||
            paired_spectra.front().timestamp_ns != dual_rx1.timestamp_ns ||
            paired_spectra.front().config_generation != dual_rx1.config_generation) return 44;
        // The legacy single-RX API must fail before it refills a dual stream;
        // otherwise it would silently consume and discard one shared epoch.
        bool legacy_refill_rejected = false;
        try { static_cast<void>(dual_device.refill()); }
        catch (const std::runtime_error&) { legacy_refill_rejected = true; }
        if (!legacy_refill_rejected) return 41;
        const auto dual_first_sequence = dual_rx1.source_sequence;
        dual_blocks = {};
        const auto next_dual_blocks = dual_device.refill_receivers();
        if (!next_dual_blocks.rx1.has_value() || !next_dual_blocks.rx2.has_value() ||
            next_dual_blocks.rx1->source_sequence != dual_first_sequence + 1U ||
            next_dual_blocks.rx2->source_sequence != next_dual_blocks.rx1->source_sequence) return 42;
        dual_device.stop_stream();
        dual_device.disconnect();

        // BOTH needs two retained pool slots per source epoch. If a later
        // source epoch cannot acquire both, accounting must state two output
        // blocks dropped, not the single-RX count.
        sdr_pluto::PlutoDevice dual_pool_device("usb:mock");
        auto dual_pool_config = config("usb:mock");
        dual_pool_config.buffer_samples = 32U;
        static_cast<void>(dual_pool_device.configure(
            dual_pool_config, sdr_pluto::ReceiverSelection::Both, 2U
        ));
        dual_pool_device.start_stream();
        const auto retained_dual = dual_pool_device.refill_receivers();
        bool dual_pool_exhausted = false;
        try { static_cast<void>(dual_pool_device.refill_receivers()); }
        catch (const std::runtime_error&) { dual_pool_exhausted = true; }
        const auto dual_pool_metrics = dual_pool_device.metrics();
        if (!retained_dual.rx1.has_value() || !retained_dual.rx2.has_value() ||
            !dual_pool_exhausted || dual_pool_metrics.blocks_received != 2U ||
            dual_pool_metrics.samples_received != 64U ||
            dual_pool_metrics.output_pool_exhaustions != 1U ||
            dual_pool_metrics.output_blocks_dropped != 2U ||
            dual_pool_metrics.estimated_dropped_samples != 32U) return 43;
        dual_pool_device.stop_stream();
        dual_pool_device.disconnect();

        sdr_pluto::PlutoDevice rx2_device("usb:mock");
        static_cast<void>(rx2_device.configure(
            dual_config, sdr_pluto::ReceiverSelection::Rx2, 4U
        ));
        if (rx2_device.applied_config().sample_layout.stride_bytes != 4) return 38;
        rx2_device.start_stream();
        const auto rx2_blocks = rx2_device.refill_receivers();
        if (rx2_blocks.rx1.has_value() || !rx2_blocks.rx2.has_value() ||
            rx2_blocks.rx2->samples->size() != 4096U ||
            (*rx2_blocks.rx2->samples)[0] != 0xE8U ||
            (*rx2_blocks.rx2->samples)[1] != 0x03U) return 39;
        rx2_device.stop_stream();
        rx2_device.disconnect();
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "");

        // An AD9364/single-layout device must fail closed before stream start
        // when RX2/BOTH is requested; legacy RX1 remains unchanged below.
        sdr_pluto::PlutoDevice single_layout("usb:mock");
        bool rx2_rejected = false;
        try {
            static_cast<void>(single_layout.configure(
                config("usb:mock"), sdr_pluto::ReceiverSelection::Rx2, 4U
            ));
        } catch (const sdr_core::ConfigurationError&) {
            rx2_rejected = true;
        }
        single_layout.disconnect();
        if (!rx2_rejected) return 37;

        // Rejecting a not-observed RX2 selection is fail-closed but must not
        // silently stop an already running legacy RX1 stream.
        sdr_pluto::PlutoDevice legacy_device("usb:mock");
        static_cast<void>(legacy_device.configure(config("usb:mock")));
        legacy_device.start_stream();
        bool rejected_while_running = false;
        try {
            static_cast<void>(legacy_device.configure(
                config("usb:mock"), sdr_pluto::ReceiverSelection::Both, 4U
            ));
        } catch (const sdr_core::ConfigurationError&) {
            rejected_while_running = true;
        }
        if (!rejected_while_running || !legacy_device.streaming() ||
            legacy_device.refill().sample_count != 1024U) return 40;
        legacy_device.stop_stream();
        legacy_device.disconnect();

        // Exact-name candidate without the required control attribute must fall back structurally.
        _putenv_s("SDR_MOCK_LIBIIO_EXACT_WRONG", "1");
        sdr_pluto::PlutoDevice fallback_device("usb:mock");
        static_cast<void>(fallback_device.configure(config("usb:mock")));
        fallback_device.disconnect();
        _putenv_s("SDR_MOCK_LIBIIO_EXACT_WRONG", "");

        // A custom AD9363 firmware profile must be admitted from IIO discovery and
        // applied readback, not rejected because the product model has an official
        // lower-bandwidth datasheet identity. Exact legacy names are absent here.
        _putenv_s("SDR_MOCK_LIBIIO_AD9363_EXTENDED", "1");
        sdr_pluto::PlutoDevice extended_ad9363("usb:mock");
        const auto extended_capabilities = extended_ad9363.capabilities();
        if (extended_capabilities.model.find("AD9363") == std::string::npos ||
            extended_capabilities.tuning_range_hz.maximum != 6'000'000'000.0 ||
            extended_capabilities.sample_rate_ranges_hz.front().maximum != 61'440'000.0 ||
            extended_capabilities.analog_bandwidth_ranges_hz.front().maximum != 56'000'000.0) return 25;
        const auto extended_applied = extended_ad9363.configure(config("usb:mock", 61'440'000.0, 36'000'000.0));
        if (extended_applied.sample_rate_hz != 61'440'000.0 || extended_applied.analog_bandwidth_hz != 36'000'000.0) return 26;
        extended_ad9363.disconnect();
        _putenv_s("SDR_MOCK_LIBIIO_AD9363_EXTENDED", "");

        sdr_pluto::PlutoDevice device("usb:mock");
        const auto capabilities = device.capabilities();
        if (capabilities.tuning_range_hz.minimum != 70'000'000.0 ||
            capabilities.sample_rate_ranges_hz.front().minimum != 2'083'333.0 ||
            capabilities.analog_bandwidth_ranges_hz.front().minimum != 200'000.0) return 4;
        const auto applied = device.configure(config("usb:mock"));
        if (applied.center_frequency_hz != 2'450'000'000.0 || applied.sample_rate_hz != 3'000'000.0 ||
            applied.analog_bandwidth_hz != 1'500'000.0 || applied.sample_layout.significant_bits != 12U) return 5;
        device.start_stream();
        // A controlled mock delay makes the nominal-period threshold contract
        // deterministic rather than dependent on Windows timer granularity.
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "2");
        // The packed le:S12/16 fast path must preserve canonical signed int16 output even
        // if the unused high nibble in the source storage is not sign-extended.
        _putenv_s("SDR_MOCK_LIBIIO_NON_SIGN_EXTENDED_S12", "1");
        const auto first = device.refill();
        _putenv_s("SDR_MOCK_LIBIIO_NON_SIGN_EXTENDED_S12", "");
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
        const auto second = device.refill();
        if (first.sample_format != sdr_core::SampleFormat::ComplexInt12InInt16Le || first.sample_count != 1024U ||
            first.samples->size() != 4096U || second.source_sequence != first.source_sequence + 1U ||
            second.first_sample_index != first.first_sample_index + first.sample_count) return 6;
        if ((*first.samples)[0] != 0x00U || (*first.samples)[1] != 0xF8U ||
            (*first.samples)[2] != 0xFFU || (*first.samples)[3] != 0x07U) return 29;
        _putenv_s("SDR_MOCK_LIBIIO_SHORT_READ", "1");
        const auto short_block = device.refill();
        _putenv_s("SDR_MOCK_LIBIIO_SHORT_READ", "");
        if (short_block.sample_count != 1023U || short_block.samples->size() != 4092U ||
            !sdr_core::has_flag(short_block.flags, sdr_core::QualityFlag::IqDropped)) return 7;
        // Reported channel extent ends one byte into the last Q sample: never read beyond it.
        _putenv_s("SDR_MOCK_LIBIIO_TRUNCATED_END", "1");
        const auto truncated_block = device.refill();
        _putenv_s("SDR_MOCK_LIBIIO_TRUNCATED_END", "");
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "");
        if (truncated_block.sample_count != 1023U || truncated_block.samples->size() != 4092U ||
            !sdr_core::has_flag(truncated_block.flags, sdr_core::QualityFlag::IqDropped)) return 22;
        const auto metrics = device.metrics();
        if (metrics.blocks_received != 4U || metrics.samples_received != 4094U || metrics.refill_errors != 0U ||
            metrics.short_reads != 2U || metrics.estimated_dropped_samples != 2U ||
            metrics.refill_wait_ns == 0U || metrics.refill_calls != 4U ||
            metrics.refill_wait_over_nominal_period != 4U ||
            metrics.refill_wait_over_two_nominal_periods != 4U ||
            metrics.canonicalization_ns == 0U || metrics.inter_refill_gap_count != 3U ||
            metrics.inter_refill_gap_ns == 0U) return 8;
        device.stop_stream();
        if (device.streaming()) return 8;
        device.disconnect();
        if (device.connected()) return 9;

        sdr_pluto::PlutoDevice pool_device("usb:mock");
        auto pool_config = config("usb:mock");
        pool_config.buffer_samples = 32U;
        static_cast<void>(pool_device.configure(pool_config));
        pool_device.start_stream();
        std::vector<sdr_core::IqBlock> retained;
        for (std::uint32_t index = 0U; index < 8U; ++index) {
            retained.push_back(pool_device.refill());
        }
        bool pool_exhausted = false;
        try { static_cast<void>(pool_device.refill()); }
        catch (const std::runtime_error&) { pool_exhausted = true; }
        const auto exhausted_metrics = pool_device.metrics();
        if (!pool_exhausted || exhausted_metrics.blocks_received != 9U ||
            exhausted_metrics.samples_received != 288U ||
            exhausted_metrics.output_pool_exhaustions != 1U ||
            exhausted_metrics.output_blocks_dropped != 1U ||
            exhausted_metrics.estimated_dropped_samples != 32U) return 20;
        retained.clear();
        const auto after_gap = pool_device.refill();
        if (after_gap.source_sequence != 9U || after_gap.first_sample_index != 288U) return 21;
        pool_device.stop_stream();
        pool_device.disconnect();

        sdr_pluto::PlutoDevice reconnected("usb:mock");
        bool rejected = false;
        try { static_cast<void>(reconnected.configure(config("usb:mock", 1'000'000.0))); }
        catch (const sdr_core::ConfigurationError&) { rejected = true; }
        if (!rejected) return 10;
        auto oversized = config("usb:mock");
        oversized.buffer_samples = std::numeric_limits<std::uint32_t>::max();
        bool oversized_rejected = false;
        try { static_cast<void>(reconnected.configure(oversized)); }
        catch (const sdr_core::ConfigurationError&) { oversized_rejected = true; }
        if (!oversized_rejected) return 11;
        const auto stable = reconnected.configure(config("usb:mock"));

        // Required mode readback may neither disappear nor silently mismatch.
        auto agc = config("usb:mock");
        agc.gain_mode = sdr_core::GainMode::SlowAttack;
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_READ_FAIL", "1");
        bool readback_failed = false;
        try { static_cast<void>(reconnected.configure(agc)); }
        catch (const std::exception&) { readback_failed = true; }
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_READ_FAIL", "");
        if (!readback_failed || reconnected.applied_config().config_generation != stable.config_generation) return 23;
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_MISMATCH", "1");
        bool mismatch_failed = false;
        try { static_cast<void>(reconnected.configure(agc)); }
        catch (const std::exception&) { mismatch_failed = true; }
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_MISMATCH", "");
        if (!mismatch_failed || reconnected.applied_config().config_generation != stable.config_generation) return 24;

        // P06 output contract is strictly signed int12-in-int16, never mislabeled int16.
        _putenv_s("SDR_MOCK_LIBIIO_FORMAT_16", "1");
        bool int16_rejected = false;
        try { static_cast<void>(reconnected.configure(config("usb:mock"))); }
        catch (const std::exception&) { int16_rejected = true; }
        _putenv_s("SDR_MOCK_LIBIIO_FORMAT_16", "");
        if (!int16_rejected) return 25;
        _putenv_s("SDR_MOCK_LIBIIO_INVALID_SHIFT", "1");
        bool shift_rejected = false;
        try { static_cast<void>(reconnected.configure(config("usb:mock"))); }
        catch (const std::exception&) { shift_rejected = true; }
        _putenv_s("SDR_MOCK_LIBIIO_INVALID_SHIFT", "");
        if (!shift_rejected) return 26;
        _putenv_s("SDR_MOCK_LIBIIO_OVERFLOW_SHIFT", "1");
        bool overflow_shift_rejected = false;
        try { static_cast<void>(reconnected.configure(config("usb:mock"))); }
        catch (const std::exception&) { overflow_shift_rejected = true; }
        _putenv_s("SDR_MOCK_LIBIIO_OVERFLOW_SHIFT", "");
        if (!overflow_shift_rejected) return 31;

        auto failing = config("usb:mock", 4'000'000.0);
        failing.manual_gain_db = 100.0;
        bool rolled_back = false;
        try { static_cast<void>(reconnected.configure(failing)); }
        catch (const sdr_core::ConfigurationError&) {
            const auto still_applied = reconnected.applied_config();
            rolled_back = still_applied.config_generation == stable.config_generation &&
                          still_applied.sample_rate_hz == stable.sample_rate_hz;
        }
        if (!rolled_back) return 11;

        // If any rollback write/readback fails, published configuration is invalidated.
        _putenv_s("SDR_MOCK_LIBIIO_ROLLBACK_FAIL", "1");
        bool rollback_invalidated = false;
        try { static_cast<void>(reconnected.configure(failing)); }
        catch (const sdr_core::ConfigurationError&) {
            try { static_cast<void>(reconnected.applied_config()); }
            catch (const sdr_core::ConfigurationError&) { rollback_invalidated = true; }
        }
        _putenv_s("SDR_MOCK_LIBIIO_ROLLBACK_FAIL", "");
        if (!rollback_invalidated) return 27;
        bool start_rejected = false;
        try { reconnected.start_stream(); }
        catch (const sdr_core::ConfigurationError&) { start_rejected = true; }
        if (!start_rejected) return 28;
        static_cast<void>(reconnected.configure(config("usb:mock")));
        reconnected.start_stream();
        reconnected.cancel();
        bool canceled = false;
        try { static_cast<void>(reconnected.refill()); }
        catch (const std::runtime_error&) { canceled = true; }
        if (!canceled || reconnected.metrics().refill_errors != 1U) return 11;
        reconnected.stop_stream();

        // cancel() and stop_stream() synchronize iio_buffer lifetime, not only pointer value.
        MockHooks hooks;
        sdr_pluto::PlutoDevice race_device("usb:mock");
        static_cast<void>(race_device.configure(config("usb:mock")));
        race_device.start_stream();
        _putenv_s("SDR_MOCK_LIBIIO_BLOCK_CANCEL", "1");
        hooks.reset();
        std::thread cancel_thread([&] { race_device.cancel(); });
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (hooks.entered() == 0 && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
        if (hooks.entered() == 0) { hooks.release(); cancel_thread.join(); return 29; }
        std::thread stop_thread([&] { race_device.stop_stream(); });
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        const bool destroyed_early = hooks.destroyed() != 0;
        hooks.release();
        cancel_thread.join();
        stop_thread.join();
        _putenv_s("SDR_MOCK_LIBIIO_BLOCK_CANCEL", "");
        if (destroyed_early || hooks.destroyed() != 0) return 30;
        race_device.disconnect();

        bool invalid_uri = false;
        try { sdr_pluto::PlutoDevice invalid("serial:mock"); }
        catch (const std::invalid_argument&) { invalid_uri = true; }
        if (!invalid_uri) return 12;
        std::cout << "P06 mock libiio backend passed" << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 100;
    }
}

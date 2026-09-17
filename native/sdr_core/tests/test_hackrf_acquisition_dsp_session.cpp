#include "sdr_hackrf/hackrf_acquisition_dsp_session.hpp"

#include "sdr_core/errors.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

using namespace std::chrono_literals;

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

sdr_core::SourceDescriptor source() {
    sdr_core::SourceDescriptor value;
    value.source_type = sdr_core::SourceType::LiveIq;
    value.source_id = "hackrf-fake-session-0";
    value.display_name = "HackRF fake session";
    value.backend_id = "native.libhackrf.rx.v1";
    return value;
}

sdr_hackrf::HackrfAcquisitionDspSessionConfig session_config() {
    sdr_hackrf::HackrfAcquisitionDspSessionConfig value;
    value.dsp.dsp.fft_size = 256U;
    value.dsp.dsp.hop_size = 256U;
    value.dsp.dsp.window = sdr_core::WindowType::Rectangular;
    value.dsp.dsp.detector = sdr_core::DetectorType::Sample;
    value.dsp.dsp.unit = sdr_core::SpectrumUnit::DbfsBin;
    value.dsp.dsp.precision_mode = sdr_core::PrecisionMode::ReferenceF64;
    value.dsp.source = source();
    value.dsp.dsp_output_capacity = 8U;
    value.dsp.presentation_capacity = 4U;
    return value;
}

std::shared_ptr<sdr_hackrf::HackrfRxIngress> ingress(
    const std::uint32_t slot_bytes,
    const std::uint32_t slot_count = 4U,
    const std::uint32_t ready_capacity = 3U
) {
    return std::make_shared<sdr_hackrf::HackrfRxIngress>(
        sdr_hackrf::HackrfRxIngressConfig{
            .slot_count = slot_count,
            .slot_bytes = slot_bytes,
            .ready_capacity = ready_capacity,
            .center_frequency_hz = 50'000'000.0,
            .sample_rate_hz = 256'000.0,
            .config_generation = 12U,
        }
    );
}

std::vector<std::uint8_t> constant_ci8(const std::uint32_t samples) {
    std::vector<std::uint8_t> result(static_cast<std::size_t>(samples) * 2U);
    const std::int8_t i = 64;
    const std::int8_t q = 0;
    for (std::uint32_t index = 0U; index < samples; ++index) {
        std::memcpy(result.data() + index * 2U, &i, 1U);
        std::memcpy(result.data() + index * 2U + 1U, &q, 1U);
    }
    return result;
}

template <typename Predicate>
bool wait_until(Predicate&& predicate, const std::chrono::milliseconds timeout = 1s) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) {
            return true;
        }
        std::this_thread::sleep_for(1ms);
    }
    return predicate();
}

void test_invalid_config_fails_before_worker_start() {
    bool rejected = false;
    try {
        auto session = sdr_hackrf::HackrfAcquisitionDspSession::start(
            {},
            session_config()
        );
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "null ingress was accepted");

    auto source_ingress = ingress(512U);
    auto invalid = session_config();
    invalid.dsp.source.uri = "usb:forbidden";
    rejected = false;
    try {
        auto session = sdr_hackrf::HackrfAcquisitionDspSession::start(
            source_ingress,
            invalid
        );
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "route-bearing DSP config was accepted");
    expect(source_ingress->metrics().blocks_popped == 0U, "invalid config started a worker");
}

void test_worker_drains_real_ingress_into_cpu_dsp() {
    auto source_ingress = ingress(512U);
    auto session = sdr_hackrf::HackrfAcquisitionDspSession::start(
        source_ingress,
        session_config()
    );
    const auto block = constant_ci8(256U);
    expect(
        source_ingress->admit_callback(block, 1'000'000) ==
            sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "fake callback was not admitted"
    );
    expect(wait_until([&] { return session->metrics().worker_blocks_processed == 1U; }),
           "worker did not process the admitted block");
    const auto frames = session->poll_spectrum_frames();
    expect(frames.size() == 1U, "worker did not publish one spectrum frame");
    expect(frames.front().source.source_id == "hackrf-fake-session-0", "source mismatch");
    expect(frames.front().config_generation == 12U, "generation mismatch");

    const auto stopped = session->stop(1s);
    expect(stopped.complete(), "clean worker stop did not complete");
    const auto metrics = session->metrics();
    expect(metrics.state == sdr_hackrf::HackrfAcquisitionDspState::Stopped,
           "clean worker did not reach Stopped");
    expect(metrics.worker_exited && metrics.worker_joined, "worker was not joined");
    expect(metrics.ingress.ready_depth == 0U, "clean stop left a ready lease");
}

void test_real_ready_ring_backpressure_retains_exact_gap() {
    auto source_ingress = ingress(256U, 2U, 1U);
    const auto half = constant_ci8(128U);
    expect(
        source_ingress->admit_callback(half, 100) ==
            sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "first prefilled block was not admitted"
    );
    expect(
        source_ingress->admit_callback(half, 200) ==
            sdr_hackrf::HackrfRxAdmissionResult::QueueFull,
        "full real ready ring did not reject the callback"
    );

    auto session = sdr_hackrf::HackrfAcquisitionDspSession::start(
        source_ingress,
        session_config()
    );
    expect(wait_until([&] { return session->metrics().worker_blocks_processed == 1U; }),
           "prefilled block was not drained");
    expect(
        source_ingress->admit_callback(half, 300) ==
            sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "post-gap block was not admitted"
    );
    expect(wait_until([&] { return session->metrics().worker_blocks_processed == 2U; }),
           "post-gap block was not drained");
    expect(
        source_ingress->admit_callback(half, 400) ==
            sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "second post-gap block was not admitted"
    );
    expect(wait_until([&] { return session->metrics().worker_blocks_processed == 3U; }),
           "second post-gap block was not drained");

    const auto frames = session->poll_spectrum_frames();
    expect(frames.size() == 1U, "fresh post-gap samples did not produce one frame");
    expect(frames.front().first_sample_index == 256U, "post-gap FFT did not rebase");
    expect(frames.front().dropped_iq_blocks_before == 1U, "missing block count mismatch");
    expect(frames.front().dropped_samples_before == 128U, "missing sample count mismatch");
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::IqDropped),
        "real ingress queue loss was hidden"
    );
    const auto metrics = session->metrics();
    expect(metrics.ingress.queue_full_drops == 1U, "queue-full loss count mismatch");
    expect(metrics.dsp.source_blocks_missing == 1U, "DSP missing-block count mismatch");
    expect(metrics.dsp.source_samples_missing == 128U, "DSP missing-sample count mismatch");
    expect(session->stop(1s).complete(), "backpressure session did not stop");
}

void test_stop_drains_all_admitted_leases_and_is_idempotent() {
    auto source_ingress = ingress(512U, 4U, 4U);
    const auto block = constant_ci8(256U);
    for (std::int64_t index = 0; index < 4; ++index) {
        expect(
            source_ingress->admit_callback(block, 1'000 + index) ==
                sdr_hackrf::HackrfRxAdmissionResult::Admitted,
            "prefill for drain test failed"
        );
    }
    auto session = sdr_hackrf::HackrfAcquisitionDspSession::start(
        source_ingress,
        session_config()
    );
    const auto first = session->stop(1s);
    const auto second = session->stop(1s);
    expect(first.complete() && second.complete(), "idempotent stop did not complete");
    const auto metrics = session->metrics();
    expect(metrics.worker_blocks_processed == 4U, "stop did not drain all admitted leases");
    expect(metrics.worker_abandoned_blocks == 0U, "clean drain abandoned a worker block");
    expect(metrics.ingress.blocks_popped == 4U, "ingress pop count mismatch after drain");
    expect(metrics.ingress.ready_depth == 0U, "drain left a pending ready block");
}

void test_callback_timeout_fails_closed_then_joins_after_release() {
    auto source_ingress = ingress(512U);
    auto session = sdr_hackrf::HackrfAcquisitionDspSession::start(
        source_ingress,
        session_config()
    );
    std::atomic<bool> entered{};
    std::atomic<bool> release{};
    std::thread held([&] {
        source_ingress->test_hold_active_callback(entered, release);
    });
    expect(wait_until([&] { return entered.load(std::memory_order_acquire); }),
           "test callback did not become active");

    const auto incomplete = session->stop(2ms);
    expect(!incomplete.complete(), "held callback did not make stop incomplete");
    expect(!incomplete.callbacks_quiescent && !incomplete.worker_joined,
           "timeout incorrectly joined the worker");
    expect(!session->metrics().worker_joined, "timed-out stop detached or joined the worker");

    release.store(true, std::memory_order_release);
    held.join();
    const auto complete = session->stop(1s);
    expect(complete.complete(), "second stop did not complete after callback release");
    expect(session->metrics().worker_joined, "released callback did not allow join");
}

}  // namespace

int main() {
    try {
        test_invalid_config_fails_before_worker_start();
        test_worker_drains_real_ingress_into_cpu_dsp();
        test_real_ready_ring_backpressure_retains_exact_gap();
        test_stop_drains_all_admitted_leases_and_is_idempotent();
        test_callback_timeout_fails_closed_then_joins_after_release();
        std::cout << "R11-J HackRF acquisition/DSP session OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#include "sdr_rtlsdr/rtl_runtime.hpp"

#include "sdr_core/errors.hpp"

#include <array>
#include <atomic>
#ifdef NDEBUG
#undef NDEBUG  // Contract checks must execute in the focused Release build.
#endif
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <memory>
#include <string>
#include <thread>

using namespace std::chrono_literals;

namespace {

struct Observed {
    std::atomic<int> opens{};
    std::atomic<int> closes{};
    std::atomic<int> cancels{};
    std::atomic<int> reads{};
    std::atomic<bool> release_read{};
};

enum class Fault { None, Open, RateReadback, RateReadbackCloseFault,
                   ReaderMinusNine, IgnoreCancel };

class MockRtlPort final : public sdr_rtlsdr::RtlRuntimePort {
public:
    explicit MockRtlPort(std::shared_ptr<Observed> observed, bool delayed_reader = false,
                         bool close_fault = false, bool malformed_first = false,
                         bool short_first = false, Fault fault = Fault::None,
                         bool empty_first = false)
        : observed_(std::move(observed)), delayed_reader_(delayed_reader),
          close_fault_(close_fault), malformed_first_(malformed_first),
          short_first_(short_first), fault_(fault), empty_first_(empty_first) {}

    int open_exact_unique_serial(const std::string& expected) noexcept override {
        observed_->opens.fetch_add(1);
        return expected == "RTL-UNIQUE-42" && fault_ != Fault::Open ? 0 : -7;
    }
    int open_selected_session_route(const sdr_rtlsdr::RtlSessionRoute& expected) noexcept override {
        observed_->opens.fetch_add(1);
        return expected.manufacturer == "Mock" && expected.product == "RTL tuner" &&
            expected.serial == "00000001" && expected.tuner_type == 5U &&
            expected.selection_revision == 3U && fault_ != Fault::Open ? 0 : -7;
    }
    int set_sample_rate(const std::uint32_t value) noexcept override {
        rate_ = value;
        return 0;
    }
    std::uint32_t get_sample_rate() noexcept override {
        return rate_ - (fault_ == Fault::RateReadback ||
                        fault_ == Fault::RateReadbackCloseFault ? 1U : 0U);
    }
    int set_center_frequency(const std::uint32_t value) noexcept override {
        center_ = value;
        return 0;
    }
    std::uint32_t get_center_frequency() noexcept override { return center_; }
    int set_automatic_tuner_gain() noexcept override { return 0; }
    int reset_buffer() noexcept override { return 0; }
    int verify_normal_tuner_mode() noexcept override { return 0; }

    int read_async(const sdr_rtlsdr::RtlBytesCallback callback, void* context,
                   const std::uint32_t buffer_bytes) noexcept override {
        observed_->reads.fetch_add(1);
        if (delayed_reader_) std::this_thread::sleep_for(12ms);
        active_.store(true);
        if (fault_ == Fault::ReaderMinusNine) return -9;
        if (buffer_bytes != 32'768U) return -1;
        if (empty_first_) callback({}, context);
        if (malformed_first_) {
            const std::array<std::uint8_t, 3> odd{0U, 128U, 255U};
            callback(odd, context);
        }
        std::array<std::uint8_t, 32'768> values{};
        for (std::size_t index = 0; index < values.size(); index += 2U) {
            values[index] = (index % 16U == 0U) ? 255U : 128U;
            values[index + 1U] = 128U;
        }
        if (short_first_) callback(std::span(values.data(), values.size() / 2U), context);
        callback(values, context);
        while (!(fault_ == Fault::IgnoreCancel ? observed_->release_read.load() : cancel_.load())) {
            std::this_thread::sleep_for(1ms);
        }
        active_.store(false);
        return 0;
    }
    int cancel_async() noexcept override {
        observed_->cancels.fetch_add(1);
        if (!active_.load()) return -2;  // actual librtlsdr pending-state behavior
        if (fault_ != Fault::IgnoreCancel) cancel_.store(true);
        return 0;
    }
    int close() noexcept override {
        observed_->closes.fetch_add(1);
        return close_fault_ || fault_ == Fault::RateReadbackCloseFault ? -1 : 0;
    }

private:
    std::shared_ptr<Observed> observed_;
    bool delayed_reader_{};
    bool close_fault_{};
    bool malformed_first_{};
    bool short_first_{};
    Fault fault_{Fault::None};
    bool empty_first_{};
    std::atomic<bool> active_{};
    std::atomic<bool> cancel_{};
    std::uint32_t rate_{};
    std::uint32_t center_{};
};

sdr_rtlsdr::RtlProfile profile() {
    sdr_rtlsdr::RtlProfile value;
    value.center_hz = 100'000'000U;
    value.sample_rate_hz = 2'400'000U;
    value.expected_unique_serial = "RTL-UNIQUE-42";
    return value;
}

void check_inert_validation() {
    auto invalid_gain = profile();
    invalid_gain.manual_tuner_gain_tenth_db = 1001;
    try {
        sdr_rtlsdr::validate_rtl_profile(invalid_gain);
        assert(false);
    } catch (const sdr_core::ConfigurationError&) {}
    invalid_gain.manual_tuner_gain_tenth_db = -1001;
    try {
        sdr_rtlsdr::validate_rtl_profile(invalid_gain);
        assert(false);
    } catch (const sdr_core::ConfigurationError&) {}
    auto request = profile();
    request.expected_unique_serial = "00000001";
    try {
        sdr_rtlsdr::validate_rtl_profile(request);
        assert(false);
    } catch (const sdr_core::ConfigurationError&) {}
    request = profile();
    request.dsp_output_capacity = 4U;  // final presentation cap is independent.
    try {
        sdr_rtlsdr::validate_rtl_profile(request);
        assert(false);
    } catch (const sdr_core::ConfigurationError&) {}
    request = profile();
    request.sample_rate_hz = 3'200'000U;  // no above-2.4M loss claim.
    try {
        sdr_rtlsdr::validate_rtl_profile(request);
        assert(false);
    } catch (const sdr_core::ConfigurationError&) {}
    request = profile();
    request.expected_unique_serial.clear();
    request.session_route = sdr_rtlsdr::RtlSessionRoute{"Mock", "RTL tuner", "00000001", 5U, 3U};
    sdr_rtlsdr::validate_rtl_profile(request);
    request.session_route->tuner_type = 0U;
    try {
        sdr_rtlsdr::validate_rtl_profile(request);
        assert(false);
    } catch (const sdr_core::ConfigurationError&) {}
}

void check_manual_gain_refusal_before_rx() {
    for (const int gain : {-1001, 1001, 0, 144}) {
        auto observed = std::make_shared<Observed>();
        auto request = profile();
        request.manual_tuner_gain_tenth_db = gain;
        const bool invalid = gain < -1000 || gain > 1000;
        bool refused{};
        try {
            static_cast<void>(sdr_rtlsdr::RtlRuntimeSession::start(
                std::make_unique<MockRtlPort>(observed), request));
        } catch (const sdr_core::ConfigurationError&) { assert(invalid); refused = true;
        } catch (const sdr_core::DeviceError&) { assert(!invalid); refused = true; }
        assert(refused);
        assert(observed->opens.load() == (invalid ? 0 : 1));
        assert(observed->closes.load() == (invalid ? 0 : 1));
        assert(observed->reads.load() == 0 && observed->cancels.load() == 0);
        assert(!sdr_rtlsdr::rtl_process_quarantined());
    }
}

void check_callback_dsp_and_stop(const bool delayed, const bool malformed,
                                 const bool short_first = false, const bool empty_first = false) {
    auto observed = std::make_shared<Observed>();
    auto session = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(observed, delayed, false, malformed, short_first,
                                      Fault::None, empty_first), [&] {
            auto request = profile(); request.analytical_event_capacity = 128U; return request;
        }());
    if (!delayed) {
        for (int trial = 0; trial < 200 && session->metrics().dsp.fft_frames_computed == 0U; ++trial) {
            std::this_thread::sleep_for(2ms);
        }
        const auto metrics = session->metrics();
        assert(metrics.blocks_admitted == (short_first ? 2U : 1U));
        assert(metrics.samples_admitted == (short_first ? 24'576U : 16'384U));
        assert(metrics.malformed_callbacks == (malformed || empty_first ? 1U : 0U));
        assert(metrics.host_input_blocks_dropped == (malformed || empty_first ? 1U : 0U));
        assert(metrics.dsp.fft_frames_computed > 0U);
        assert(metrics.dsp.fft_frames_dropped == 0U);
        const auto newest = session->drain_latest_spectrum_frame();
        assert(newest.frame);
        assert(newest.frame->source.backend_id == "native.rtlsdr.injected.cpu.v1");
        assert(newest.frame->unit == sdr_core::SpectrumUnit::DbfsBin);
        assert(newest.frame->values && newest.frame->values->size() == 4096U);
        const auto expected_dc = 20.0 * std::log10(127.0 / (8.0 * 128.0));
        assert(std::abs((*newest.frame->values)[2048U] - expected_dc) < 0.1);
        assert(sdr_core::has_flag(newest.frame->quality_flags, sdr_core::QualityFlag::Uncalibrated));
        assert(sdr_core::has_flag(newest.frame->quality_flags, sdr_core::QualityFlag::TimestampEstimated));
        assert(metrics.presentation_frames_superseded > 0U);  // delivery, not FFT loss
        const auto journal = session->drain_analytical_ready_events(0U);
        assert(journal.summary.supported && journal.summary.offered > 0U &&
               journal.summary.handed_off == journal.summary.offered &&
               journal.summary.events_lost == 0U && !journal.events.empty());
        const auto& p = journal.summary.presentation;
        assert(p.supported && p.forwarded == 1U && p.accounting_failures == 0U);
        assert(p.coalesced == newest.coalesced_frames);
        assert(journal.summary.handed_off == p.forwarded + p.superseded + p.coalesced);
    }
    const auto stopped = session->stop(2000ms);
    assert(stopped.complete());
    const auto final_journal = session->drain_analytical_ready_events(0U).summary;
    assert(final_journal.outstanding == 0U &&
           final_journal.events_generated == final_journal.events_drained +
               final_journal.events_pending + final_journal.events_lost);
    assert(!session->cleanup_required());
    assert(observed->opens.load() == 1);
    assert(observed->reads.load() == 1);
    assert(observed->closes.load() == 1);
    assert(observed->cancels.load() >= 1);
}

void check_odd_callback_fails_closed() {
    auto observed = std::make_shared<Observed>();
    auto session = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(observed, false, false, true), profile());
    for (int trial = 0; trial < 200 && session->metrics().malformed_callbacks == 0U; ++trial) {
        std::this_thread::sleep_for(1ms);
    }
    const auto metrics = session->metrics();
    assert(metrics.malformed_callbacks == 1U);
    assert(metrics.host_input_blocks_dropped == 1U);
    assert(metrics.host_input_samples_dropped == 0U);  // byte alignment lost; count unknown
    assert(metrics.host_loss_cardinality_unknown);
    assert(metrics.blocks_admitted == 0U);
    assert(metrics.worker_failures == 1U);
    assert(!session->running());
    assert(!session->drain_latest_spectrum_frame().frame);
    assert(session->stop(2000ms).complete());
}

void check_pre_rx_failure_releases_owner(const Fault fault) {
    auto observed = std::make_shared<Observed>();
    try {
        static_cast<void>(sdr_rtlsdr::RtlRuntimeSession::start(
            std::make_unique<MockRtlPort>(observed, false, false, false, false, fault), profile()));
        assert(false);
    } catch (const sdr_core::DeviceError&) {}
    assert(observed->reads.load() == 0);
    assert(observed->closes.load() == (fault == Fault::Open ? 0 : 1));
    check_callback_dsp_and_stop(false, false);  // a clean new owner can start
}

void check_reader_error_first_cause() {
    auto observed = std::make_shared<Observed>();
    auto session = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(observed, false, false, false, false,
                                      Fault::ReaderMinusNine), profile());
    for (int trial = 0; trial < 200 && !session->metrics().reader_returned_without_stop; ++trial) {
        std::this_thread::sleep_for(1ms);
    }
    const auto metrics = session->metrics();
    assert(metrics.reader_returned);
    assert(metrics.reader_returned_without_stop);
    assert(metrics.reader_return_status == -9);
    assert(metrics.worker_failures == 1U);
    const auto stopped = session->stop(2000ms);
    assert(stopped.complete());
    assert(observed->closes.load() == 1);
}

void check_concurrent_observers_during_stop() {
    auto observed = std::make_shared<Observed>();
    auto session = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(observed), profile());
    const auto readback = session->readback();
    assert(readback.session_epoch > 0U);
    assert(readback.actual_sample_rate_hz == 2'400'000U);
    assert(readback.actual_center_hz == 100'000'000U);
    assert(!readback.tuner_gain_readback_known);
    std::atomic<bool> observe{true};
    std::thread watcher([&] {
        while (observe.load(std::memory_order_acquire)) {
            static_cast<void>(session->running());
            static_cast<void>(session->cleanup_required());
            static_cast<void>(session->metrics());
            std::this_thread::yield();
        }
    });
    const auto stopped = session->stop(2000ms);
    observe.store(false, std::memory_order_release);
    watcher.join();
    assert(stopped.complete());
    assert(!session->running());
    assert(!session->cleanup_required());
}

void check_selected_session_route() {
    auto observed = std::make_shared<Observed>();
    auto request = profile();
    request.expected_unique_serial.clear();
    request.session_route = sdr_rtlsdr::RtlSessionRoute{"Mock", "RTL tuner", "00000001", 5U, 3U};
    auto session = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(observed), request);
    assert(session->readback().session_epoch > 0U);
    assert(session->stop(2000ms).complete());
    assert(observed->opens.load() == 1);
}

void check_stopped_object_does_not_hold_or_erase_new_lease() {
    auto first_observed = std::make_shared<Observed>();
    auto first = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(first_observed), profile());
    assert(first->stop(2000ms).complete());
    assert(!first->cleanup_required());
    auto second_observed = std::make_shared<Observed>();
    auto second = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(second_observed), profile());
    first.reset();
    try {
        static_cast<void>(sdr_rtlsdr::RtlRuntimeSession::start(
            std::make_unique<MockRtlPort>(second_observed), profile()));
        assert(false);
    } catch (const sdr_core::DeviceError&) {}
    assert(second->stop(2000ms).complete());
    assert(first_observed->closes.load() == 1);
    assert(second_observed->closes.load() == 1);
}

void check_cancel_timeout_child() {
    auto observed = std::make_shared<Observed>();
    auto session = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(observed, false, false, false, false,
                                      Fault::IgnoreCancel), profile());
    for (int trial = 0; trial < 200 && observed->reads.load() == 0; ++trial) {
        std::this_thread::sleep_for(1ms);
    }
    assert(observed->reads.load() == 1);
    const auto first = session->stop(20ms);
    assert(!first.complete());
    assert(!first.reader_joined && !first.close_called);
    bool refused = false;
    try { static_cast<void>(session->discard_terminal_spectrum_frames()); }
    catch (const sdr_core::ConfigurationError&) { refused = true; }
    assert(refused);
    assert(observed->closes.load() == 0);
    try {
        static_cast<void>(sdr_rtlsdr::RtlRuntimeSession::start(
            std::make_unique<MockRtlPort>(observed), profile()));
        assert(false);
    } catch (const sdr_core::DeviceError&) {}
    observed->release_read.store(true);
    const auto recovered = session->stop(2000ms);
    assert(recovered.complete());
    assert(observed->closes.load() == 1);
}

void check_setup_close_fault_child() {
    auto observed = std::make_shared<Observed>();
    try {
        static_cast<void>(sdr_rtlsdr::RtlRuntimeSession::start(
            std::make_unique<MockRtlPort>(observed, false, false, false, false,
                                          Fault::RateReadbackCloseFault), profile()));
        assert(false);
    } catch (const sdr_core::DeviceError& error) {
        const std::string cause = error.what();
        assert(cause.find("configuration/readback failed before RX") != std::string::npos);
        assert(cause.find("native status=-101") != std::string::npos);
        assert(cause.find("close status=-1") != std::string::npos);
        assert(cause.find("owner quarantined") != std::string::npos);
    }
    assert(observed->reads.load() == 0);
    assert(observed->closes.load() == 1);  // destructor never retries ambiguous close
    try {
        static_cast<void>(sdr_rtlsdr::RtlRuntimeSession::start(
            std::make_unique<MockRtlPort>(observed), profile()));
        assert(false);
    } catch (const sdr_core::DeviceError&) {}
}

void check_ambiguous_close_child() {
    auto observed = std::make_shared<Observed>();
    auto session = sdr_rtlsdr::RtlRuntimeSession::start(
        std::make_unique<MockRtlPort>(observed, false, true), profile());
    const auto result = session->stop(2000ms);
    assert(!result.complete());
    assert(result.reader_joined && result.dsp_joined && result.close_called);
    assert(result.close_status == -1);
    bool refused = false;
    try { static_cast<void>(session->discard_terminal_spectrum_frames()); }
    catch (const sdr_core::ConfigurationError&) { refused = true; }
    assert(refused);
    assert(observed->closes.load() == 1);
    session.reset();  // bounded process-lifetime quarantine, never a second close
    assert(observed->closes.load() == 1);
    try {
        static_cast<void>(sdr_rtlsdr::RtlRuntimeSession::start(
            std::make_unique<MockRtlPort>(observed), profile()));
        assert(false);
    } catch (const sdr_core::DeviceError&) {}
}

void check_terminal_discard_and_post_stop_drain() {
    for (const bool discard : {false, true}) {
        auto observed = std::make_shared<Observed>();
        auto request = profile(); request.analytical_event_capacity = 64U;
        auto session = sdr_rtlsdr::RtlRuntimeSession::start(std::make_unique<MockRtlPort>(observed), request);
        bool refused = false;
        try { static_cast<void>(session->discard_terminal_spectrum_frames()); }
        catch (const sdr_core::ConfigurationError&) { refused = true; }
        assert(refused);
        for (int trial = 0; trial < 200 && session->metrics().dsp.fft_frames_computed == 0U; ++trial)
            std::this_thread::sleep_for(2ms);
        assert(session->stop(2000ms).complete());
        if (discard) {
            const auto n = session->discard_terminal_spectrum_frames();
            assert(n > 0U && session->discard_terminal_spectrum_frames() == 0U);
            assert(!session->drain_latest_spectrum_frame().frame);
            const auto s = session->drain_analytical_ready_events(0U).summary;
            assert(s.presentation.cancelled == n && s.presentation.accounting_failures == 0U &&
                s.handed_off == s.presentation.cancelled + s.presentation.superseded);
        } else {
            assert(session->drain_latest_spectrum_frame().frame);
            assert(session->discard_terminal_spectrum_frames() == 0U);
            assert(session->drain_analytical_ready_events(0U).summary.presentation.cancelled == 0U);
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--close-fault") {
        check_ambiguous_close_child();
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--cancel-timeout") {
        check_cancel_timeout_child();
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--setup-close-fault") {
        check_setup_close_fault_child();
        return 0;
    }
    check_inert_validation();
    check_terminal_discard_and_post_stop_drain();
    check_manual_gain_refusal_before_rx();
    check_pre_rx_failure_releases_owner(Fault::Open);
    check_pre_rx_failure_releases_owner(Fault::RateReadback);
    check_callback_dsp_and_stop(false, false);
    check_odd_callback_fails_closed();
    check_callback_dsp_and_stop(false, false, false, true);  // zero-length is malformed, no freeze
    check_callback_dsp_and_stop(false, false, true);  // shorter even callback then full buffer
    check_callback_dsp_and_stop(true, false);  // immediate Stop before read enters
    check_reader_error_first_cause();
    check_concurrent_observers_during_stop();
    check_selected_session_route();
    check_stopped_object_does_not_hold_or_erase_new_lease();
    const std::string command = std::string("\"") + argv[0] + "\" --close-fault";
    assert(std::system(command.c_str()) == 0);
    const std::string timeout_command = std::string("\"") + argv[0] + "\" --cancel-timeout";
    assert(std::system(timeout_command.c_str()) == 0);
    const std::string setup_command = std::string("\"") + argv[0] + "\" --setup-close-fault";
    assert(std::system(setup_command.c_str()) == 0);
    return 0;
}

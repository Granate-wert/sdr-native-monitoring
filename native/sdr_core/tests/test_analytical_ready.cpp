#include "sdr_core/analytical_ready.hpp"
#include "sdr_core/errors.hpp"

#include <iostream>
#include <atomic>
#include <thread>
#include <stdexcept>

namespace {
using namespace sdr_core;
void expect(bool ok, const char* message) {
    if (!ok) { throw std::runtime_error(message); }
}
template<class Function> void refuses(Function function) {
    bool rejected = false;
    try { function(); } catch (const ConfigurationError&) { rejected = true; }
    expect(rejected, "invalid journal operation must refuse");
}
void conservation(const AnalyticalReadySummary& s) {
    expect(s.offered == s.handed_off + s.producer_superseded +
           s.producer_cancelled + s.outstanding, "producer conservation failed");
    expect(s.events_generated == s.events_drained + s.events_pending +
           s.events_lost, "event conservation failed");
}
std::int64_t fake_time = 100;
std::int64_t fake_clock() noexcept { return fake_time; }

void test_three_offers_latest_only_and_retirement_guards() {
    AnalyticalReadyJournal journal(12U);
    const auto a = journal.offer(7U), b = journal.offer(7U), c = journal.offer(8U);
    expect(a.offer_sequence == 1U && b.offer_sequence == 2U && c.offer_sequence == 3U,
           "offer IDs are not monotonic");
    refuses([&] { journal.retire(b, AnalyticalReadyEventKind::HandedOff); });
    journal.retire(a, AnalyticalReadyEventKind::ProducerSuperseded);
    refuses([&] { journal.retire(a, AnalyticalReadyEventKind::ProducerCancelled); });
    journal.retire(b, AnalyticalReadyEventKind::ProducerSuperseded);
    journal.retire(c, AnalyticalReadyEventKind::HandedOff);
    refuses([&] { journal.retire(c, AnalyticalReadyEventKind::HandedOff); });
    const auto events = journal.poll_events(0U);
    expect(events.size() == 6U && events[3].ref == a && events[4].ref == b &&
           events[5].ref == c, "original identities were lost at retirement");
    const auto s = journal.summary();
    expect(s.offered == 3U && s.producer_superseded == 2U && s.handed_off == 1U &&
           s.outstanding == 0U && s.events_lost == 0U, "latest-only counters wrong");
    conservation(s);
}
void test_ring_wrap_overflow_preserves_loss_and_original_evidence() {
    AnalyticalReadyJournal journal(2U);
    const auto a = journal.offer(1U);
    journal.retire(a, AnalyticalReadyEventKind::HandedOff);
    const auto b = journal.offer(1U);
    auto s = journal.summary();
    expect(s.events_lost == 1U && s.first_lost_event_sequence == 3U &&
           s.last_lost_event_sequence == 3U, "overflow is not explicit");
    auto first = journal.poll_events(1U);
    expect(first.size() == 1U && first[0].ref == a && first[0].event_sequence == 1U,
           "full ring silently evicted old evidence");
    journal.retire(b, AnalyticalReadyEventKind::ProducerCancelled);
    auto rest = journal.poll_events(0U);
    expect(rest.size() == 2U && rest[0].event_sequence == 2U &&
           rest[1].event_sequence == 4U, "ring wrap reordered events");
    const auto bytes = journal.summary().event_storage_bytes;
    for (int index = 0; index < 10000; ++index) {
        auto ref = journal.offer(2U);
        journal.retire(ref, AnalyticalReadyEventKind::HandedOff);
    }
    s = journal.summary();
    expect(s.events_pending == 2U && s.event_storage_bytes == bytes &&
           s.events_lost > 10000U, "journal memory is not bounded");
    conservation(s);
}
void test_disabled_journal_not_false_complete_evidence() {
    AnalyticalReadyJournal journal;
    const auto a = journal.offer(1U);
    journal.retire(a, AnalyticalReadyEventKind::ProducerCancelled);
    const auto s = journal.summary();
    expect(s.supported && s.event_capacity == 0U && s.event_storage_bytes == 0U &&
           s.events_lost == 2U && journal.poll_events(0U).empty(),
           "summary-only mode fabricated detailed evidence");
    conservation(s);
    refuses([] { AnalyticalReadyJournal excessive(4097U); });
}
void test_clock_regression_never_clipped_or_recovered_silently() {
    fake_time = 100;
    AnalyticalReadyJournal journal(8U, &fake_clock);
    const auto a = journal.offer(1U);
    fake_time = -5;
    const auto b = journal.offer(1U);
    fake_time = 101;
    const auto c = journal.offer(1U);
    expect(a.clock_state == AnalyticalReadyClockState::Monotonic &&
           b.ready_native_ns == -5 && b.clock_state == AnalyticalReadyClockState::Regressed &&
           c.clock_state == AnalyticalReadyClockState::Regressed &&
           journal.summary().clock_regressions == 1U, "regressing clock fabricated latency");
    journal.retire(a, AnalyticalReadyEventKind::ProducerCancelled);
    journal.retire(b, AnalyticalReadyEventKind::ProducerCancelled);
    journal.retire(c, AnalyticalReadyEventKind::ProducerCancelled);
    conservation(journal.summary());
}
void test_independent_instances_and_foreign_refusal() {
    AnalyticalReadyJournal a(4U), b(4U);
    const auto left = a.offer(1U), right = b.offer(1U);
    expect(left.producer_instance_id != right.producer_instance_id &&
           left.offer_sequence == right.offer_sequence, "independent producer IDs alias");
    refuses([&] { a.retire(right, AnalyticalReadyEventKind::HandedOff); });
    refuses([&] { a.retire(left, AnalyticalReadyEventKind::Offered); });
    a.retire(left, AnalyticalReadyEventKind::ProducerCancelled);
    b.retire(right, AnalyticalReadyEventKind::ProducerCancelled);
    conservation(a.summary()); conservation(b.summary());
}
void test_concurrent_owner_drain_and_budget() {
    refuses([] { static_cast<void>(analytical_ready_reserved_bytes(4097U)); });
    expect(analytical_ready_reserved_bytes(128U) == 256U * sizeof(AnalyticalReadyEvent),
           "journal plus drain reservation missing");
    AnalyticalReadyJournal journal(128U);
    refuses([&] { static_cast<void>(journal.drain(4097U)); });
    std::atomic<bool> done{};
    std::jthread producer([&] {
        for (int index = 0; index < 10000; ++index) {
            const auto ref = journal.offer(7U);
            journal.retire(ref, AnalyticalReadyEventKind::HandedOff);
        }
        done.store(true);
    });
    std::uint64_t count{}, last_sequence{};
    do {
        const auto batch = journal.drain(32U);
        conservation(batch.summary);
        for (const auto& event : batch.events) {
            expect(event.event_sequence > last_sequence, "concurrent drain duplicates or reorders evidence");
            last_sequence = event.event_sequence;
            ++count;
        }
    } while (!done.load() || journal.summary().events_pending != 0U);
    producer.join();
    const auto terminal = journal.drain(0U).summary;
    conservation(terminal);
    expect(terminal.offered == 10000U && terminal.handed_off == 10000U &&
           terminal.outstanding == 0U && count + terminal.events_lost == 20000U,
           "concurrent drain loses unaccounted offers or terminal dispositions");
}
}  // namespace
int main() {
    try {
        test_three_offers_latest_only_and_retirement_guards();
        test_ring_wrap_overflow_preserves_loss_and_original_evidence();
        test_disabled_journal_not_false_complete_evidence();
        test_clock_regression_never_clipped_or_recovered_silently();
        test_independent_instances_and_foreign_refusal();
        test_concurrent_owner_drain_and_budget();
        std::cout << "analytical-ready journal: 6 cases OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}

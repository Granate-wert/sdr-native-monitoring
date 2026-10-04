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
    const auto& p = s.presentation;
    expect(s.events_generated == 2U * s.offered - s.outstanding +
        p.forwarded + p.superseded + p.coalesced + p.cancelled + p.cadence_suppressed,
        "owner event generation conservation failed");
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
    expect(analytical_ready_reserved_bytes(128U) == 256U * sizeof(AnalyticalReadyEvent) + sizeof(OwnerPresentationSummary),
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
void test_actual_owner_scalar_decisions_share_atomic_summary() {
    AnalyticalReadyJournal journal(2U), foreign;
    expect(!journal.summary().presentation.supported, "standalone DSP fabricates owner coverage");
    journal.enable_owner_presentation();
    const auto a = journal.offer(1), b = journal.offer(1), c = journal.offer(2);
    journal.retire(a, AnalyticalReadyEventKind::HandedOff);
    journal.retire(b, AnalyticalReadyEventKind::HandedOff);
    journal.retire(c, AnalyticalReadyEventKind::HandedOff);
    // LatestWins evicts its back: actual dispositions need NOT follow FIFO.
    journal.record_owner_presentation(b, OwnerPresentationDisposition::Superseded);
    journal.record_owner_presentation(a, OwnerPresentationDisposition::Coalesced);
    journal.record_owner_presentation(c, OwnerPresentationDisposition::Forwarded);
    const auto s = journal.drain(0).summary;
    conservation(s);
    expect(s.presentation.forwarded == 1 && s.presentation.coalesced == 1 &&
        s.presentation.superseded == 1 && s.presentation.accounting_failures == 0 &&
        s.events_generated == 9 && s.events_lost == 7,
        "owner counters/events lost exact dispositions or declared ring loss");
    const auto wrong = foreign.offer(1);
    journal.record_owner_presentation(wrong, OwnerPresentationDisposition::Cancelled);
    journal.record_owner_presentation(c, OwnerPresentationDisposition::Forwarded);
    expect(journal.summary().presentation.accounting_failures == 2 &&
        journal.summary().presentation.forwarded == 1, "invalid/excess owner decisions alter outcomes");
}
void test_original_owner_refs_all_dispositions_and_invalid_kind() {
    AnalyticalReadyJournal journal(32U);
    journal.enable_owner_presentation();
    const OwnerPresentationDisposition decisions[] = {
        OwnerPresentationDisposition::Forwarded, OwnerPresentationDisposition::Superseded,
        OwnerPresentationDisposition::Coalesced, OwnerPresentationDisposition::Cancelled,
        OwnerPresentationDisposition::CadenceSuppressed};
    const AnalyticalReadyEventKind expected[] = {
        AnalyticalReadyEventKind::OwnerForwarded, AnalyticalReadyEventKind::OwnerSuperseded,
        AnalyticalReadyEventKind::OwnerCoalesced, AnalyticalReadyEventKind::OwnerCancelled,
        AnalyticalReadyEventKind::OwnerCadenceSuppressed};
    std::vector<AnalyticalReadyRef> refs;
    for (int i = 0; i < 5; ++i) {
        const auto ref = journal.offer(7);
        refs.push_back(ref);
        journal.retire(ref, AnalyticalReadyEventKind::HandedOff);
    }
    journal.record_owner_presentation(refs[0], static_cast<OwnerPresentationDisposition>(255));
    expect(journal.summary().events_generated == 10 &&
        journal.summary().presentation.accounting_failures == 1,
        "invalid owner disposition appended an event");
    for (int i = 4; i >= 0; --i) journal.record_owner_presentation(refs[i], decisions[i]);
    const auto batch = journal.drain(0);
    expect(batch.events.size() == 15 && batch.summary.events_lost == 0,
        "original owner decisions missing");
    for (int i = 0; i < 5; ++i) {
        expect(batch.events[10 + i].ref == refs[4 - i] &&
            batch.events[10 + i].kind == expected[4 - i],
            "nonFIFO owner terminal changed original receipt");
    }
    conservation(batch.summary);
}
void test_owner_events_zero_capacity_and_concurrent_drain() {
    AnalyticalReadyJournal disabled;
    disabled.enable_owner_presentation();
    const auto ref = disabled.offer(7);
    disabled.retire(ref, AnalyticalReadyEventKind::HandedOff);
    disabled.record_owner_presentation(ref, OwnerPresentationDisposition::Cancelled);
    expect(disabled.summary().events_lost == 3 && disabled.summary().events_pending == 0,
        "disabled owner events fake complete IDs");
    conservation(disabled.summary());
    AnalyticalReadyJournal journal(128U);
    journal.enable_owner_presentation();
    const auto bytes = journal.summary().event_storage_bytes;
    std::atomic<bool> done{};
    std::jthread producer([&] {
        for (int i = 0; i < 10000; ++i) {
            const auto current = journal.offer(9);
            journal.retire(current, AnalyticalReadyEventKind::HandedOff);
            journal.record_owner_presentation(current, OwnerPresentationDisposition::Forwarded);
        }
        done.store(true);
    });
    std::uint64_t last{}, count{};
    do {
        const auto batch = journal.drain(32U);
        conservation(batch.summary);
        for (const auto& event : batch.events) {
            expect(event.event_sequence > last, "owner concurrent drain reordered or duplicated");
            last = event.event_sequence;
            ++count;
        }
    } while (!done.load() || journal.summary().events_pending);
    producer.join();
    const auto end = journal.summary();
    expect(count + end.events_lost == 30000 && end.presentation.forwarded == 10000 &&
        end.event_storage_bytes == bytes, "owner event drain changed memory or lost accounting");
    conservation(end);
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
        test_actual_owner_scalar_decisions_share_atomic_summary();
        test_original_owner_refs_all_dispositions_and_invalid_kind();
        test_owner_events_zero_capacity_and_concurrent_drain();
        std::cout << "analytical-ready journal: 9 cases OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}

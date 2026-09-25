"""Non-hardware guards for the opt-in physical UI C-stderr Stop probe."""

from __future__ import annotations

import unittest

from scripts.probe_app05_ui_stop_stdio import (
    child_provenance_ok,
    classify_ui_stderr,
    ui_run_complete,
)

REQUIRED = ("measurement_started", "measurement_ended_before_stop", "stop_click_before",
            "stop_click_after", "stop_acknowledged", "qt_event_loop_returned", "main_returning")
CHILD = {"result": "pass", "workers_after_close": [],
         "allocation_budget": {"reserved_bytes": 0}}


def trace(*, read_at: str | None = "stop_click_after", warning: str | None = None,
          omit_stage: str | None = None, flush_return: int = 0) -> str:
    lines = []
    for stage in REQUIRED:
        if stage == omit_stage:
            continue
        lines.append(f"APP05_STAGE 100 {stage}")
        for library in ("msvcrt", "ucrtbase"):
            lines.append(f"APP05_CFLUSH_BEGIN {stage} {library}")
            if stage == read_at and library == "msvcrt":
                lines.extend(("ERROR: READ LINE: -9", "ERROR: READ INTEGER: -9"))
            lines.append(f"APP05_CFLUSH_RETURN {stage} {library} {flush_return}")
    if warning is not None:
        lines.append(warning)
    return "\n".join(lines)


class UiStopStdioTests(unittest.TestCase):
    def test_expected_cancellation_is_post_click_but_not_release_acceptance(self):
        classified = classify_ui_stderr(trace())
        self.assertTrue(ui_run_complete(0, CHILD, classified))
        self.assertEqual([row["stage"] for row in classified["read_errors"]],
                         ["stop_click_after", "stop_click_after"])
        self.assertTrue(all(not row["observed_by_preclick_flush"] for row in classified["read_errors"]))
        self.assertTrue(all(not row["origin_after_click_proven"] for row in classified["read_errors"]))

    def test_read_error_before_click_fails_even_when_child_passes(self):
        classified = classify_ui_stderr(trace(read_at="measurement_ended_before_stop"))
        self.assertFalse(ui_run_complete(0, CHILD, classified))
        self.assertTrue(all(row["observed_by_preclick_flush"] for row in classified["read_errors"]))

    def test_missing_bracket_or_nonzero_flush_cannot_pass(self):
        self.assertFalse(ui_run_complete(0, CHILD, classify_ui_stderr(trace(omit_stage="stop_click_before"))))
        self.assertFalse(ui_run_complete(0, CHILD, classify_ui_stderr(trace(flush_return=-1))))
        malformed = trace().replace("APP05_CFLUSH_RETURN stop_click_after msvcrt 0",
                                    "APP05_CFLUSH_RETURN stop_click_before msvcrt 0")
        self.assertFalse(ui_run_complete(0, CHILD, classify_ui_stderr(malformed)))

    def test_qt_warning_and_unexpected_native_error_are_not_hidden(self):
        warning = classify_ui_stderr(trace(warning="RuntimeError: QGraphicsTextItem already deleted"))
        self.assertFalse(ui_run_complete(0, CHILD, warning))
        error = classify_ui_stderr(trace(warning="ERROR: unexpected transport failure"))
        self.assertFalse(ui_run_complete(0, CHILD, error))
        unclassified = classify_ui_stderr(trace(warning="unclassified Qt stderr"))
        self.assertEqual(unclassified["unclassified_stderr"], ["unclassified Qt stderr"])
        self.assertFalse(ui_run_complete(0, CHILD, unclassified))

    def test_no_read_error_is_allowed_but_cleanup_is_required(self):
        diagnostic = classify_ui_stderr(trace(read_at=None))
        self.assertTrue(ui_run_complete(0, CHILD, diagnostic))
        self.assertFalse(ui_run_complete(1, CHILD, diagnostic))
        self.assertFalse(ui_run_complete(0, dict(CHILD, workers_after_close=["rx"]), diagnostic))
        self.assertFalse(ui_run_complete(0, dict(CHILD, allocation_budget={"reserved_bytes": 1}), diagnostic))

    def test_duplicate_or_out_of_order_stage_cannot_pass(self):
        duplicate = trace(read_at=None) + "\nAPP05_STAGE 101 stop_acknowledged"
        self.assertFalse(ui_run_complete(0, CHILD, classify_ui_stderr(duplicate)))
        switched = trace(read_at=None).replace("APP05_STAGE 100 stop_click_after",
                                                 "APP05_STAGE 100 stop_click_after_repeat")
        self.assertFalse(ui_run_complete(0, CHILD, classify_ui_stderr(switched)))

    def test_transition_gap_is_indeterminate_not_proven_after_click(self):
        # A native write between the last pre-click flush and click could first
        # appear inside the post-click flush. The classifier must not date it.
        classified = classify_ui_stderr(trace(read_at="stop_click_after"))
        self.assertTrue(ui_run_complete(0, CHILD, classified))
        self.assertTrue(all(not row["origin_after_click_proven"] for row in classified["read_errors"]))
        before_click_marker = trace(read_at=None).replace(
            "APP05_CFLUSH_BEGIN stop_click_before msvcrt",
            "ERROR: READ LINE: -9\nAPP05_CFLUSH_BEGIN stop_click_before msvcrt")
        self.assertFalse(ui_run_complete(0, CHILD, classify_ui_stderr(before_click_marker)))

    def test_provenance_rejects_dirty_mismatch_and_missing_native(self):
        parent = dict(source_commit="a" * 40, benchmark_sha256="b" * 64,
                      tracked_dirty_paths=[])
        child = dict(source_commit="a" * 40, script_sha256="b" * 64,
                     tracked_dirty_paths=[], uri="usb:1.2.3", requested=dict(
                         c_stdio_bracket=True, render_mode="visual", center_mhz=2450.0,
                         sample_rate_msps=3.0, fft=16384, warmup_s=1.0, measurement_s=4.0),
                     runtime=dict(native_binary=dict(sha256="c" * 64)))
        kwargs = dict(uri="usb:1.2.3", render_mode="visual", center_mhz=2450.0,
                      sample_rate_msps=3.0, fft=16384, warmup_s=1.0,
                      measurement_s=4.0, expected_native_sha256="c" * 64)
        self.assertTrue(child_provenance_ok(child, parent, **kwargs))
        self.assertFalse(child_provenance_ok(child, dict(parent, tracked_dirty_paths=["file"]), **kwargs))
        self.assertFalse(child_provenance_ok(dict(child, source_commit="d" * 40), parent, **kwargs))
        self.assertFalse(child_provenance_ok(dict(child, runtime={}), parent, **kwargs))
        self.assertFalse(child_provenance_ok(dict(child, requested=dict(child["requested"], fft=1024)),
                                             parent, **kwargs))
        self.assertFalse(child_provenance_ok(child, parent,
                                             **dict(kwargs, expected_native_sha256="e" * 64)))


if __name__ == "__main__":
    unittest.main()

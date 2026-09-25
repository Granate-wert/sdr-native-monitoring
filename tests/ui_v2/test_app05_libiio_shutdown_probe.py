"""Hardware-independent checks for the APP-05 subprocess teardown observer."""

import unittest

from scripts.probe_app05_libiio_shutdown import child_complete, classify_stderr


class LibiioShutdownProbeTests(unittest.TestCase):
    def test_read_errors_keep_the_last_flushed_stage(self):
        diagnostic = classify_stderr(
            "APP05_NATIVE_STAGE 100 child_begin\n"
            "APP05_NATIVE_STAGE 200 join_return\n"
            "ERROR: READ LINE: -9\n"
            "APP05_NATIVE_STAGE 300 child_return\n"
            "ERROR: READ INTEGER: -9\n"
        )
        self.assertEqual([row["stage"] for row in diagnostic["stages"]],
                         ["child_begin", "join_return", "child_return"])
        self.assertEqual([row["after_stage"] for row in diagnostic["read_errors"]],
                         ["join_return", "child_return"])

    def test_malformed_marker_is_not_silently_counted_as_stage(self):
        diagnostic = classify_stderr(
            "APP05_NATIVE_STAGE bad join_return\nERROR: READ LINE: -9\n")
        self.assertEqual(diagnostic["stages"], [])
        self.assertTrue(diagnostic["read_errors"][0]["malformed_marker"])
        self.assertIsNone(diagnostic["read_errors"][1]["after_stage"])

    def test_stream_requires_ordered_shutdown_and_no_native_failure(self):
        stages = ["engine_started", "request_stop_return", "join_return",
                  "engine_disconnect_return", "child_return"]
        result = dict(error=None, connected_after_disconnect=False,
                      state_after_join="EngineState.STOPPED",
                      streaming_after_join=False, native_has_error=False,
                      diagnostic_events_lost=0, rx_blocks=10, rx_samples=40960,
                      native_event_codes=["fixed_band_started", "fixed_band_stopped"])
        self.assertTrue(child_complete("stream", 0, result, stages))
        self.assertFalse(child_complete("stream", 0, result, list(reversed(stages))))
        self.assertFalse(child_complete("stream", 1, result, stages))
        self.assertFalse(child_complete("stream", 0, result | {"native_has_error": True}, stages))
        self.assertFalse(child_complete("stream", 0, result | {"rx_blocks": 0}, stages))
        self.assertFalse(child_complete("stream", 0, result | {"rx_samples": 0}, stages))
        self.assertFalse(child_complete("stream", 0, result | {
            "native_event_codes": ["acquisition_failure"]}, stages))

    def test_probe_and_configured_require_disconnect(self):
        self.assertTrue(child_complete(
            "probe", 0, dict(error=None, connected_after_disconnect=False),
            ["device_created", "device_disconnect_return", "child_return"]))
        self.assertFalse(child_complete(
            "configured", 0, dict(error=None, connected_after_disconnect=True),
            ["engine_configured", "engine_disconnect_return", "child_return"]))


if __name__ == "__main__":
    unittest.main()

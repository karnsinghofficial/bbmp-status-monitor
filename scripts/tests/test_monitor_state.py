"""
Tests for the monitor.py state machine: consecutive-failure confirmation,
consecutive-success recovery confirmation, degraded detection, and incident
open/close bookkeeping. Uses synthetic CheckResult objects - no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from monitor import (  # noqa: E402
    apply_transition, default_service_state, record_incident_open,
    record_incident_close, STATUS_OPERATIONAL, STATUS_DOWN, STATUS_DEGRADED,
    STATUS_UNKNOWN,
)
from checker import CheckResult  # noqa: E402
from utils import iso, utc_now  # noqa: E402
from datetime import timedelta


SETTINGS = {
    "fail_threshold": 2,
    "success_threshold": 2,
    "degraded_threshold": 2,
    "repeat_notify_minutes": 720,
}


def ok_result(rt=100):
    return CheckResult(ok=True, keyword_ok=None, http_status=200, response_time_ms=rt, error_type="")


def down_result(err="CONNECTION_ERROR", msg="refused"):
    return CheckResult(ok=False, keyword_ok=None, http_status=None, response_time_ms=None,
                        error_type=err, error_message=msg)


def degraded_result():
    return CheckResult(ok=True, keyword_ok=False, http_status=200, response_time_ms=120, error_type="")


class TestStateMachine(unittest.TestCase):
    def _now(self, offset_seconds=0):
        return iso(utc_now() + timedelta(seconds=offset_seconds))

    def test_single_failure_does_not_trigger_down(self):
        state = default_service_state()
        t = apply_transition("svc", "Test Svc", state, down_result(), SETTINGS, self._now())
        self.assertIsNone(t, "a single failed check must NOT be reported as DOWN")
        self.assertEqual(state["status"], STATUS_UNKNOWN)
        self.assertEqual(state["consecutive_fail"], 1)

    def test_two_consecutive_failures_trigger_down(self):
        state = default_service_state()
        apply_transition("svc", "Test Svc", state, down_result(), SETTINGS, self._now(0))
        t = apply_transition("svc", "Test Svc", state, down_result(), SETTINGS, self._now(300))
        self.assertIsNotNone(t)
        self.assertEqual(t["type"], "DOWN")
        self.assertEqual(state["status"], STATUS_DOWN)
        self.assertIsNotNone(state["current_outage_start"])

    def test_recovery_requires_consecutive_successes(self):
        state = default_service_state()
        apply_transition("svc", "Test Svc", state, down_result(), SETTINGS, self._now(0))
        apply_transition("svc", "Test Svc", state, down_result(), SETTINGS, self._now(300))
        self.assertEqual(state["status"], STATUS_DOWN)

        # one success while down: not yet recovered (success_threshold=2)
        t1 = apply_transition("svc", "Test Svc", state, ok_result(), SETTINGS, self._now(600))
        self.assertIsNone(t1)
        self.assertEqual(state["status"], STATUS_DOWN, "must stay DOWN until success_threshold reached")

        # second consecutive success: now recovered
        t2 = apply_transition("svc", "Test Svc", state, ok_result(), SETTINGS, self._now(900))
        self.assertIsNotNone(t2)
        self.assertEqual(t2["type"], "RECOVERED")
        self.assertEqual(state["status"], STATUS_OPERATIONAL)
        self.assertIsNone(state["current_outage_start"])
        self.assertIsNotNone(t2["downtime_seconds"])

    def test_flapping_single_success_between_failures_does_not_recover(self):
        state = default_service_state()
        apply_transition("svc", "Test Svc", state, down_result(), SETTINGS, self._now(0))
        apply_transition("svc", "Test Svc", state, down_result(), SETTINGS, self._now(300))
        self.assertEqual(state["status"], STATUS_DOWN)

        apply_transition("svc", "Test Svc", state, ok_result(), SETTINGS, self._now(600))  # 1st success
        t = apply_transition("svc", "Test Svc", state, down_result(), SETTINGS, self._now(900))  # fails again
        self.assertIsNone(t)
        self.assertEqual(state["status"], STATUS_DOWN, "still down - success streak was broken")
        self.assertEqual(state["consecutive_success"], 0)

    def test_degraded_after_threshold_keyword_mismatches(self):
        state = default_service_state()
        # first make it operational
        apply_transition("svc", "Test Svc", state, ok_result(), SETTINGS, self._now(0))
        state["status"] = STATUS_OPERATIONAL

        t1 = apply_transition("svc", "Test Svc", state, degraded_result(), SETTINGS, self._now(300))
        self.assertIsNone(t1, "one keyword mismatch should not immediately flag degraded")
        t2 = apply_transition("svc", "Test Svc", state, degraded_result(), SETTINGS, self._now(600))
        self.assertIsNotNone(t2)
        self.assertEqual(t2["type"], "DEGRADED")
        self.assertEqual(state["status"], STATUS_DEGRADED)

    def test_incident_open_and_close_bookkeeping(self):
        incidents = []
        record_incident_open(incidents, "svc", "Test Svc", "2026-01-01T00:00:00Z", "CONNECTION_ERROR: refused")
        self.assertEqual(len(incidents), 1)
        self.assertIsNone(incidents[0]["resolved_at"])

        record_incident_close(incidents, "svc", "2026-01-01T00:35:00Z")
        self.assertIsNotNone(incidents[0]["resolved_at"])
        self.assertEqual(incidents[0]["duration_seconds"], 35 * 60)

    def test_unknown_becomes_operational_on_first_success(self):
        state = default_service_state()
        self.assertEqual(state["status"], STATUS_UNKNOWN)
        t = apply_transition("svc", "Test Svc", state, ok_result(), SETTINGS, self._now())
        self.assertEqual(state["status"], STATUS_OPERATIONAL)


if __name__ == "__main__":
    unittest.main()

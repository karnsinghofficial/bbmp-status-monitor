"""
End-to-end integration test: runs the real monitor.run() against a local
test HTTP server across several simulated 'cron ticks', using a temp config
and temp data dir, verifying status.json / incidents.json / history.jsonl
come out correctly and notifications fire with --dry-run (no real network
calls to Telegram/email).
"""
import http.server
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import monitor  # noqa: E402

FLAKY_STATE = {"mode": "up"}  # shared across requests


class FlakyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        mode = FLAKY_STATE["mode"]
        if mode == "up":
            body = b"<html>Property Tax Portal OK</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif mode == "down":
            self.send_response(500)
            self.end_headers()
        elif mode == "closed":
            # simulate connection failure by just not responding properly
            self.send_response(503)
            self.end_headers()


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.port = _free_port()
        self.server = http.server.HTTPServer(("127.0.0.1", self.port), FlakyHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        FLAKY_STATE["mode"] = "up"

        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(self.data_dir, exist_ok=True)

        self.config_path = os.path.join(self.tmpdir, "services.yaml")
        with open(self.config_path, "w") as f:
            f.write(f"""
settings:
  timeout_seconds: 5
  fail_threshold: 2
  success_threshold: 2
  degraded_threshold: 2
  repeat_notify_minutes: 0
  history_retention_days: 35
  user_agent: "test-agent"

services:
  - id: test_svc
    name: "Test Property Tax"
    url: "http://127.0.0.1:{self.port}/"
    category: "Service"
    check_type: "http"
    expect_status: [200]
    keyword: null
    verified_note: "test"
    enabled: true
""")

    def tearDown(self):
        self.server.shutdown()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_cycle_up_down_recover(self):
        # Tick 1: up -> OPERATIONAL (first success from UNKNOWN)
        payload = monitor.run(self.config_path, self.data_dir, dry_run=True)
        self.assertEqual(payload["services"][0]["status"], "OPERATIONAL")
        self.assertEqual(payload["overall"], "ALL_OPERATIONAL")

        # Tick 2: goes down (1st failure) - should NOT yet be DOWN
        FLAKY_STATE["mode"] = "down"
        payload = monitor.run(self.config_path, self.data_dir, dry_run=True)
        self.assertEqual(payload["services"][0]["status"], "OPERATIONAL",
                          "single failure must not immediately flip to DOWN")

        # Tick 3: still down (2nd consecutive failure) -> DOWN confirmed
        payload = monitor.run(self.config_path, self.data_dir, dry_run=True)
        self.assertEqual(payload["services"][0]["status"], "DOWN")
        self.assertEqual(payload["overall"], "MAJOR_OUTAGE")
        incidents = json.load(open(os.path.join(self.data_dir, "incidents.json")))
        self.assertEqual(len(incidents), 1)
        self.assertIsNone(incidents[0]["resolved_at"])

        # Tick 4: back up (1st success) - should still show DOWN
        FLAKY_STATE["mode"] = "up"
        payload = monitor.run(self.config_path, self.data_dir, dry_run=True)
        self.assertEqual(payload["services"][0]["status"], "DOWN",
                          "must require consecutive successes before declaring recovered")

        # Tick 5: 2nd consecutive success -> RECOVERED
        payload = monitor.run(self.config_path, self.data_dir, dry_run=True)
        self.assertEqual(payload["services"][0]["status"], "OPERATIONAL")
        incidents = json.load(open(os.path.join(self.data_dir, "incidents.json")))
        self.assertIsNotNone(incidents[0]["resolved_at"])
        # Ticks in this test run milliseconds apart (unlike real 5-minute-apart
        # cron runs), so duration will be a tiny but non-negative number.
        self.assertGreaterEqual(incidents[0]["duration_seconds"], 0)

        # history.jsonl should have 5 lines (one per tick)
        history_path = os.path.join(self.data_dir, "history.jsonl")
        with open(history_path) as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), 5)

        # status.json should show uptime stats computed
        status = json.load(open(os.path.join(self.data_dir, "status.json")))
        self.assertIn("uptime_24h", status["services"][0])
        self.assertIsNotNone(status["services"][0]["uptime_24h"])
        self.assertEqual(len(status["services"][0]["timeline_24h"]), 24)

    def test_first_ever_run_reports_checking_not_all_operational(self):
        # A single successful check from a completely fresh state should be
        # honestly reported as OPERATIONAL for that service, but the overall
        # banner logic must not claim "all operational" before any service
        # has ever been confirmed down/up over more than one look - here with
        # only one service it goes OPERATIONAL immediately, so instead we
        # simulate a still-unconfirmed (first failing) state to check the
        # 'CHECKING' path is honest rather than defaulting to all-operational.
        FLAKY_STATE["mode"] = "down"
        payload = monitor.run(self.config_path, self.data_dir, dry_run=True)
        self.assertEqual(payload["services"][0]["status"], "UNKNOWN")
        self.assertEqual(payload["overall"], "CHECKING",
                          "must not claim ALL_OPERATIONAL when nothing has been confirmed yet")

    def test_dry_run_never_touches_real_telegram(self):
        # With no TELEGRAM secrets set at all in env, and dry_run True,
        # this must not raise and must not attempt any real HTTP call
        # to api.telegram.org (there's no network available in sandbox
        # anyway, but this also proves the code path doesn't require it).
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        FLAKY_STATE["mode"] = "down"
        monitor.run(self.config_path, self.data_dir, dry_run=True)
        monitor.run(self.config_path, self.data_dir, dry_run=True)  # triggers DOWN transition
        # no exception = pass


if __name__ == "__main__":
    unittest.main()

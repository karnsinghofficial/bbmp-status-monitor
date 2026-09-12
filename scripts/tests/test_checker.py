"""
Tests for checker.py using a local HTTP server (no real network / no real
BBMP endpoints needed). Run with:

    python -m pytest scripts/tests/ -v

or directly:

    python scripts/tests/test_checker.py
"""
import http.server
import socket
import threading
import time
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from checker import check_service  # noqa: E402


class SlowOrFailingHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence test server logs

    def do_GET(self):
        if self.path == "/ok":
            body = b"<html><body>Property Tax Portal is up and running</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/wrong-keyword":
            body = b"<html><body>Something else entirely</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/notfound":
            self.send_response(404)
            self.end_headers()
        elif self.path == "/slow":
            time.sleep(3)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"finally")
        elif self.path == "/servererror":
            self.send_response(500)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestChecker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server = http.server.HTTPServer(("127.0.0.1", cls.port), SlowOrFailingHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_success_no_keyword(self):
        r = check_service(f"{self.base}/ok", [200], None, timeout_seconds=5, user_agent="test")
        self.assertTrue(r.ok)
        self.assertEqual(r.http_status, 200)
        self.assertIsNone(r.keyword_ok)
        self.assertIsNotNone(r.response_time_ms)
        self.assertEqual(r.error_type, "")

    def test_success_with_matching_keyword(self):
        r = check_service(f"{self.base}/ok", [200], "Property Tax", timeout_seconds=5, user_agent="test")
        self.assertTrue(r.ok)
        self.assertTrue(r.keyword_ok)

    def test_success_but_keyword_missing_is_degraded_signal(self):
        r = check_service(f"{self.base}/wrong-keyword", [200], "Property Tax", timeout_seconds=5, user_agent="test")
        self.assertTrue(r.ok)  # server reachable
        self.assertFalse(r.keyword_ok)  # but content check fails -> caller marks DEGRADED

    def test_http_error_status(self):
        r = check_service(f"{self.base}/notfound", [200], None, timeout_seconds=5, user_agent="test")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_type, "HTTP_ERROR")
        self.assertEqual(r.http_status, 404)

    def test_http_5xx_error_status(self):
        r = check_service(f"{self.base}/servererror", [200], None, timeout_seconds=5, user_agent="test")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_type, "HTTP_ERROR")
        self.assertEqual(r.http_status, 500)

    def test_timeout(self):
        r = check_service(f"{self.base}/slow", [200], None, timeout_seconds=1, user_agent="test")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_type, "TIMEOUT")

    def test_connection_refused(self):
        # nothing listening on this port
        closed_port = _free_port()
        r = check_service(f"http://127.0.0.1:{closed_port}/", [200], None, timeout_seconds=3, user_agent="test")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_type, "CONNECTION_ERROR")

    def test_dns_failure(self):
        r = check_service(
            "http://this-domain-should-not-exist-bbmp-monitor-test.invalid/",
            [200], None, timeout_seconds=5, user_agent="test",
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.error_type, "DNS_ERROR")


if __name__ == "__main__":
    unittest.main()

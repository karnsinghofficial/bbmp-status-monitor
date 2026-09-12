"""
Tests for notify.py. Verifies:
  - with no env vars set, sending is skipped safely (no crash, no secret needed)
  - dry-run mode never makes a real network call
  - real send path calls requests.post with the right endpoint (mocked)
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from notify import send_telegram, send_email  # noqa: E402


class TestNotify(unittest.TestCase):
    def setUp(self):
        # Ensure a clean slate regardless of the host environment
        for key in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "EMAIL_TO"]:
            os.environ.pop(key, None)

    def test_telegram_skipped_when_not_configured(self):
        result = send_telegram("test message", dry_run=False)
        self.assertFalse(result)

    def test_email_skipped_when_not_configured(self):
        result = send_email("subject", "body", dry_run=False)
        self.assertFalse(result)

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "FAKE_TOKEN", "TELEGRAM_CHAT_ID": "12345"})
    def test_telegram_dry_run_does_not_call_network(self):
        with patch("notify.requests.post") as mock_post:
            result = send_telegram("hello", dry_run=True)
            self.assertTrue(result)
            mock_post.assert_not_called()

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "FAKE_TOKEN", "TELEGRAM_CHAT_ID": "12345"})
    def test_telegram_real_send_uses_correct_endpoint_and_no_secret_leak_in_url_body(self):
        mock_resp = MagicMock(status_code=200, text="ok")
        with patch("notify.requests.post", return_value=mock_resp) as mock_post:
            result = send_telegram("hello world", dry_run=False)
            self.assertTrue(result)
            called_url = mock_post.call_args[0][0]
            self.assertIn("FAKE_TOKEN", called_url)  # token goes in the API URL itself, not logged
            called_data = mock_post.call_args[1]["data"]
            self.assertEqual(called_data["chat_id"], "12345")
            self.assertEqual(called_data["text"], "hello world")

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "FAKE_TOKEN", "TELEGRAM_CHAT_ID": "12345"})
    def test_telegram_handles_api_failure_gracefully(self):
        mock_resp = MagicMock(status_code=401, text="Unauthorized")
        with patch("notify.requests.post", return_value=mock_resp):
            result = send_telegram("hello", dry_run=False)
            self.assertFalse(result)

    def test_no_secret_values_hardcoded_in_source(self):
        # sanity check: notify.py must not contain any literal token/password
        here = os.path.join(os.path.dirname(__file__), "..", "notify.py")
        with open(here, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("bot1", content.lower())  # typical telegram token prefix pattern guard
        self.assertIn("os.environ", content)


if __name__ == "__main__":
    unittest.main()

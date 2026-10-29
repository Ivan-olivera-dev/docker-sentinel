import unittest
from unittest.mock import MagicMock, patch
import email
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if "docker" not in sys.modules:
    docker_stub = MagicMock()
    docker_stub.errors.NotFound = Exception
    docker_stub.errors.APIError = Exception
    sys.modules["docker"] = docker_stub

import main
import notifier


class DummyContainer:
    def __init__(self, labels=None):
        self.labels = labels or {}


class TestDockerSentinel(unittest.TestCase):
    def setUp(self):
        main.crash_tracker.clear()
        main.tripped_containers.clear()

    def test_is_monitored_opt_in_label(self):
        self.assertTrue(main.is_monitored(DummyContainer({"sentinel.enable": "true"})))
        self.assertTrue(main.is_monitored(DummyContainer({"sentinel.enable": "TRUE"})))
        self.assertFalse(main.is_monitored(DummyContainer({"sentinel.enable": "false"})))
        self.assertFalse(main.is_monitored(DummyContainer({})))

    def test_should_ignore_clean_exit_zero_by_default(self):
        container = DummyContainer({"sentinel.enable": "true"})
        self.assertFalse(main.should_handle_exit(container, "0"))
        self.assertTrue(main.should_handle_exit(container, "1"))
        self.assertTrue(main.should_handle_exit(container, "137"))

    def test_should_allow_exit_zero_when_label_enabled(self):
        container = DummyContainer({
            "sentinel.enable": "true",
            "sentinel.restart_on_zero": "true",
        })
        self.assertTrue(main.should_handle_exit(container, "0"))

    def test_crash_loop_rate_limiter_and_anti_spam_circuit_breaker(self):
        # Default MAX_RESTARTS = 3 within window: first 3 crashes are allowed
        for _ in range(3):
            is_loop, should_alert = main.evaluate_crash_state("api-service", max_restarts=3, window_sec=300)
            self.assertFalse(is_loop)
            self.assertTrue(should_alert)

        # 4th crash trips the circuit breaker and triggers critical alert once
        is_loop, should_alert = main.evaluate_crash_state("api-service", max_restarts=3, window_sec=300)
        self.assertTrue(is_loop)
        self.assertTrue(should_alert)

        # 5th crash inside the same window stays in loop but suppresses duplicate critical emails
        is_loop, should_alert = main.evaluate_crash_state("api-service", max_restarts=3, window_sec=300)
        self.assertTrue(is_loop)
        self.assertFalse(should_alert)

    @patch("notifier.smtplib.SMTP")
    def test_notifier_sanitizes_headers_and_html_and_uses_timeout(self, mock_smtp):
        smtp_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = smtp_instance

        with patch.multiple(
            notifier,
            SMTP_USER="alerts@example.com",
            SMTP_PASSWORD="secret",
            ALERT_EMAIL_TO="ops@example.com",
            SMTP_SERVER="smtp.example.com",
            SMTP_PORT=587,
            SMTP_TIMEOUT=15,
        ):
            sent = notifier.send_alert_email("bad\n<script>name</script>", "137", is_critical=True)
            self.assertTrue(sent)
            mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=15)
            smtp_instance.sendmail.assert_called_once()
            raw_msg = smtp_instance.sendmail.call_args[0][2]
            parsed = email.message_from_string(raw_msg)
            html_body = parsed.get_payload()[0].get_payload(decode=True).decode("utf-8")
            self.assertNotIn("<script>", html_body)
            self.assertIn("&lt;script&gt;name&lt;/script&gt;", html_body)


if __name__ == "__main__":
    unittest.main()

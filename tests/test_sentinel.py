import email
import threading
import unittest
from datetime import UTC, datetime
from email.message import Message
from unittest.mock import MagicMock, patch

import docker.errors

from docker_sentinel.breaker import CrashBreaker
from docker_sentinel.config import ContainerPolicy, Settings, SmtpSettings
from docker_sentinel.monitor import Monitor, is_monitored, should_handle_exit
from docker_sentinel.notifier import build_alert_message, send_alert_email

SMTP = SmtpSettings(
    server="smtp.example.com",
    port=587,
    timeout=15,
    user="alerts@example.com",
    password="secret",
    recipient="ops@example.com",
)


class DummyContainer:
    def __init__(self, labels=None):
        self.labels = labels or {}
        self.restart = MagicMock()


def send_and_parse(container_name, exit_code, is_critical):
    """Send an alert through a mocked SMTP server and return the parsed message."""
    with patch("docker_sentinel.notifier.smtplib.SMTP") as mock_smtp:
        smtp_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = smtp_instance
        sent = send_alert_email(container_name, exit_code, is_critical, smtp_settings=SMTP)
    raw_msg = smtp_instance.sendmail.call_args[0][2]
    return sent, mock_smtp, email.message_from_string(raw_msg)


def part_text(msg: Message, content_type: str) -> str:
    for part in msg.walk():
        if part.get_content_type() == content_type:
            payload = part.get_payload(decode=True)
            assert isinstance(payload, bytes)
            return payload.decode(part.get_content_charset() or "utf-8")
    raise AssertionError(f"no {content_type} part")


class TestDockerSentinel(unittest.TestCase):
    def test_is_monitored_opt_in_label(self):
        self.assertTrue(is_monitored(DummyContainer({"sentinel.enable": "true"})))
        self.assertTrue(is_monitored(DummyContainer({"sentinel.enable": "TRUE"})))
        self.assertFalse(is_monitored(DummyContainer({"sentinel.enable": "false"})))
        self.assertFalse(is_monitored(DummyContainer({})))

    def test_should_ignore_clean_exit_zero_by_default(self):
        container = DummyContainer({"sentinel.enable": "true"})
        settings = Settings()
        self.assertFalse(should_handle_exit(container, "0", settings))
        self.assertTrue(should_handle_exit(container, "1", settings))
        self.assertTrue(should_handle_exit(container, "137", settings))

    def test_should_allow_exit_zero_when_label_enabled(self):
        container = DummyContainer({"sentinel.enable": "true", "sentinel.restart_on_zero": "true"})
        self.assertTrue(should_handle_exit(container, "0", Settings()))

    def test_crash_loop_rate_limiter_and_anti_spam_circuit_breaker(self):
        breaker = CrashBreaker()
        # The first 3 crashes inside the window are allowed.
        for _ in range(3):
            is_loop, should_alert = breaker.record_crash("api-service", 3, 300)
            self.assertFalse(is_loop)
            self.assertTrue(should_alert)

        # The 4th crash trips the breaker and asks for exactly one critical alert.
        is_loop, should_alert = breaker.record_crash("api-service", 3, 300)
        self.assertTrue(is_loop)
        self.assertTrue(should_alert)

        # Later crashes inside the same window stay in the loop without another alert.
        is_loop, should_alert = breaker.record_crash("api-service", 3, 300)
        self.assertTrue(is_loop)
        self.assertFalse(should_alert)

    def test_notifier_sanitizes_headers_and_html_and_uses_timeout(self):
        sent, mock_smtp, parsed = send_and_parse("bad\n<script>name</script>", "137", True)
        self.assertTrue(sent)
        mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=15)
        self.assertNotIn("\n", parsed["Subject"])
        html_body = part_text(parsed, "text/html")
        self.assertNotIn("<script>", html_body)
        self.assertIn("&lt;script&gt;name&lt;/script&gt;", html_body)

    def test_notifier_skips_when_credentials_missing(self):
        with patch("docker_sentinel.notifier.smtplib.SMTP") as mock_smtp:
            self.assertFalse(send_alert_email("api", "1", smtp_settings=SmtpSettings()))
        mock_smtp.assert_not_called()


class FakeContainers:
    def __init__(self, containers):
        self._containers = containers

    def get(self, container_id):
        if container_id not in self._containers:
            raise docker.errors.NotFound(f"No such container: {container_id}")
        return self._containers[container_id]


class FakeClient:
    """Minimal stand-in for ``docker.DockerClient`` used at the monitor boundary."""

    def __init__(self, containers=None, events=()):
        self.containers = FakeContainers(containers or {})
        self._events = list(events)

    def ping(self):
        return True

    def events(self, filters=None, decode=False):
        return iter(self._events)


def die_event(container_id, name, exit_code):
    return {
        "id": container_id,
        "Actor": {"ID": container_id, "Attributes": {"name": name, "exitCode": str(exit_code)}},
    }


def make_monitor(settings=None):
    notify = MagicMock()
    monitor = Monitor(settings or Settings(), notify=notify, restart_delay=0, retry_delay=0)
    return monitor, notify


class TestMonitor(unittest.TestCase):
    def test_restarts_opted_in_container_and_sends_recovery_alert(self):
        monitor, notify = make_monitor()
        app = DummyContainer({"sentinel.enable": "true"})
        client = FakeClient({"c1": app})
        monitor.handle_event(client, die_event("c1", "app", 1))
        app.restart.assert_called_once_with()
        notify.assert_called_once_with("app", "1", False)

    def test_ignores_unlabelled_missing_and_clean_exit_containers(self):
        monitor, notify = make_monitor()
        plain = DummyContainer({})
        clean = DummyContainer({"sentinel.enable": "true"})
        client = FakeClient({"plain": plain, "clean": clean})
        monitor.handle_event(client, die_event("plain", "plain", 1))
        monitor.handle_event(client, die_event("gone", "gone", 1))
        monitor.handle_event(client, die_event("clean", "clean", 0))
        monitor.handle_event(client, {"Actor": {"Attributes": {"name": "no-id"}}})
        plain.restart.assert_not_called()
        clean.restart.assert_not_called()
        notify.assert_not_called()

    def test_crash_loop_sends_a_single_critical_alert(self):
        monitor, notify = make_monitor()
        app = DummyContainer({"sentinel.enable": "true", "sentinel.max_restarts": "2"})
        client = FakeClient({"c1": app})
        for _ in range(5):
            monitor.handle_event(client, die_event("c1", "app", 137))
        self.assertEqual(app.restart.call_count, 2)
        critical = [c for c in notify.call_args_list if c.args[2] is True]
        self.assertEqual(critical, [unittest.mock.call("app", "137", True)])


class TestSettings(unittest.TestCase):
    def test_defaults_when_environment_is_empty(self):
        self.assertEqual(Settings.from_env({}), Settings(3, 300, True, "INFO"))

    def test_reads_environment_values(self):
        env = {
            "SENTINEL_MAX_RESTARTS": "5",
            "SENTINEL_TIME_WINDOW_SEC": "60",
            "SENTINEL_IGNORE_ZERO_EXIT": "False",
            "LOG_LEVEL": "debug",
        }
        self.assertEqual(Settings.from_env(env), Settings(5, 60, False, "DEBUG"))

    def test_invalid_environment_value_is_rejected(self):
        with self.assertRaises(ValueError):
            Settings.from_env({"SENTINEL_MAX_RESTARTS": "three"})

    def test_container_labels_override_global_limits(self):
        settings = Settings(max_restarts=3, time_window_sec=300)
        labels = {
            "sentinel.enable": "true",
            "sentinel.restart_on_zero": "TRUE",
            "sentinel.max_restarts": "7",
            "sentinel.window_sec": "120",
        }
        self.assertEqual(
            ContainerPolicy.from_labels(labels, settings),
            ContainerPolicy(enabled=True, restart_on_zero=True, max_restarts=7, window_sec=120),
        )

    def test_missing_labels_use_global_settings(self):
        settings = Settings(max_restarts=4, time_window_sec=90)
        self.assertEqual(
            ContainerPolicy.from_labels({}, settings),
            ContainerPolicy(enabled=False, restart_on_zero=False, max_restarts=4, window_sec=90),
        )

    def test_invalid_label_value_falls_back_to_default_for_that_label_only(self):
        settings = Settings(max_restarts=3, time_window_sec=300)
        bad_max = ContainerPolicy.from_labels(
            {"sentinel.max_restarts": "lots", "sentinel.window_sec": "60"}, settings
        )
        bad_window = ContainerPolicy.from_labels(
            {"sentinel.max_restarts": "5", "sentinel.window_sec": "5m"}, settings
        )
        self.assertEqual((bad_max.max_restarts, bad_max.window_sec), (3, 60))
        self.assertEqual((bad_window.max_restarts, bad_window.window_sec), (5, 300))


class TestEmailContent(unittest.TestCase):
    NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def build(self, is_critical):
        return build_alert_message(
            "api-service", "137", is_critical, "alerts@example.com", "ops@example.com", now=self.NOW
        )

    def test_message_has_plain_text_and_html_alternatives(self):
        for is_critical in (False, True):
            with self.subTest(is_critical=is_critical):
                msg = self.build(is_critical)
                self.assertEqual(msg.get_content_type(), "multipart/alternative")
                types = [p.get_content_type() for p in msg.get_payload()]
                self.assertEqual(types, ["text/plain", "text/html"])

    def test_plain_text_part_carries_the_same_facts(self):
        for is_critical in (False, True):
            with self.subTest(is_critical=is_critical):
                msg = self.build(is_critical)
                text = part_text(msg, "text/plain")
                for fact in ("api-service", "137", "2026-01-02 03:04:05 UTC"):
                    self.assertIn(fact, text)
                self.assertNotIn("<", text)

    def test_plain_text_part_strips_newlines_from_container_name(self):
        _, _, parsed = send_and_parse("bad\n<script>name</script>", "137", True)
        text = part_text(parsed, "text/plain")
        self.assertIn("bad<script>name</script>", text)

    def test_subjects_have_no_emoji(self):
        for is_critical in (False, True):
            with self.subTest(is_critical=is_critical):
                subject = str(self.build(is_critical)["Subject"])
                self.assertTrue(subject.isascii(), subject)
                self.assertIn("api-service", subject)

    def test_bodies_have_no_emoji(self):
        for is_critical in (False, True):
            with self.subTest(is_critical=is_critical):
                msg = self.build(is_critical)
                for content_type in ("text/plain", "text/html"):
                    body = part_text(msg, content_type)
                    self.assertTrue(body.isascii(), f"{content_type}: non-ASCII content")


class TestLogMessages(unittest.TestCase):
    def assert_plain(self, records):
        self.assertTrue(records)
        for record in records:
            message = record.getMessage()
            self.assertTrue(message.isascii(), f"non-ASCII log message: {message!r}")

    def test_monitor_log_messages_have_no_emoji(self):
        monitor, _ = make_monitor()
        healthy = DummyContainer({"sentinel.enable": "true", "sentinel.max_restarts": "1"})
        broken = DummyContainer({"sentinel.enable": "true"})
        broken.restart.side_effect = RuntimeError("boom")
        client = FakeClient({"ok": healthy, "bad": broken})
        with self.assertLogs("docker_sentinel", level="DEBUG") as captured:
            monitor.handle_event(client, die_event("ok", "ok", 0))  # clean exit ignored
            for _ in range(3):  # restart, crash loop, suppressed duplicate
                monitor.handle_event(client, die_event("ok", "ok", 1))
            monitor.handle_event(client, die_event("bad", "bad", 1))  # restart failure
        levels = {r.levelname for r in captured.records}
        self.assertTrue({"INFO", "WARNING", "ERROR", "CRITICAL"} <= levels, levels)
        self.assert_plain(captured.records)

    def test_run_loop_log_messages_have_no_emoji(self):
        stop = threading.Event()
        calls = []

        def factory():
            calls.append(1)
            if len(calls) == 1:
                raise docker.errors.APIError("daemon unavailable")
            if len(calls) == 2:
                raise RuntimeError("unexpected")
            stop.set()
            return FakeClient()

        monitor = Monitor(
            Settings(),
            notify=MagicMock(),
            client_factory=factory,
            shutdown_event=stop,
            restart_delay=0,
            retry_delay=0,
        )
        with self.assertLogs("docker_sentinel", level="DEBUG") as captured:
            monitor.run()
        self.assert_plain(captured.records)

    def test_notifier_log_messages_have_no_emoji(self):
        with self.assertLogs("docker_sentinel", level="DEBUG") as captured:
            send_and_parse("api", "1", False)
            send_alert_email("api", "1", smtp_settings=SmtpSettings())
            with patch("docker_sentinel.notifier.smtplib.SMTP", side_effect=OSError("refused")):
                self.assertFalse(send_alert_email("api", "1", smtp_settings=SMTP))
        self.assertEqual(len(captured.records), 3)
        self.assert_plain(captured.records)


if __name__ == "__main__":
    unittest.main()

"""A webhook that cannot be reached must not feed its own failures back to itself.

WebhookHandler sits on the root logger and logged its delivery failures with
logging.error(), which the root logger handed straight back to it. With the
internet down, every failed send logged an error that was sent (and failed)
again, about every 10 seconds, until the recursion limit or the connection
came back. Each round also reached the Unraid handler, so one outage filled the
Unraid notifications, and requests' error text carried the webhook token.
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

if 'fcntl' not in sys.modules:
    sys.modules['fcntl'] = MagicMock()

from core import logging_config  # noqa: E402
from core.logging_config import SUMMARY, WebhookHandler  # noqa: E402

TOKEN = "fake-token-for-tests"
URL = f"https://discord.com/api/webhooks/123456/{TOKEN}"
DNS_ERROR = requests.ConnectionError(
    "HTTPSConnectionPool(host='discord.com', port=443): Max retries exceeded with url: "
    f"/api/webhooks/123456/{TOKEN} (Caused by NameResolutionError(\"Failed to resolve 'discord.com'\"))"
)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=None):
        return [r.getMessage() for r in self.records if level is None or r.levelno == level]


@pytest.fixture
def setup():
    """Root logger with the webhook handler and a capture handler, like a real run."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers = []
    root.setLevel(logging.DEBUG)
    handler = WebhookHandler(URL, enabled_levels=["summary", "error", "warning"])
    handler.setLevel(logging.DEBUG)
    capture = _Capture()
    root.addHandler(handler)
    root.addHandler(capture)
    logging_config.reset_warning_error_flag()
    clock = [1000.0]
    with patch.object(logging_config.time, "monotonic", side_effect=lambda: clock[0]):
        yield handler, capture, clock
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging_config.reset_warning_error_flag()


def _ok():
    return MagicMock(status_code=204)


class TestNoFeedbackLoop:

    def test_failed_send_is_logged_once_and_not_resent(self, setup):
        handler, capture, _ = setup
        with patch.object(logging_config.requests, "post", side_effect=DNS_ERROR) as post:
            logging.error("Failed to resolve 'plex.tv'")
        assert post.call_count == 1
        failures = [m for m in capture.messages(logging.ERROR) if m.startswith("Webhook request failed")]
        assert len(failures) == 1

    def test_http_error_is_not_resent(self, setup):
        _, capture, _ = setup
        with patch.object(logging_config.requests, "post", return_value=MagicMock(status_code=429)) as post:
            logging.warning("Something recoverable")
        assert post.call_count == 1
        assert any("HTTP 429" in m for m in capture.messages(logging.ERROR))

    def test_logged_failure_hides_the_token(self, setup):
        _, capture, _ = setup
        with patch.object(logging_config.requests, "post", side_effect=DNS_ERROR):
            logging.error("boom")
        failure = next(m for m in capture.messages(logging.ERROR) if m.startswith("Webhook request failed"))
        assert TOKEN not in failure
        assert "123456" not in failure
        assert "discord.com" in failure          # still says where it was going
        assert "NameResolutionError" in failure  # and why it failed


class TestPauseAfterFailure:

    def test_messages_during_the_pause_are_not_attempted(self, setup):
        handler, _, clock = setup
        with patch.object(logging_config.requests, "post", side_effect=DNS_ERROR) as post:
            logging.error("first")
            clock[0] += 60
            for i in range(20):
                logging.error(f"more {i}")
        assert post.call_count == 1

    def test_outage_logs_one_error_even_across_retries(self, setup):
        handler, capture, clock = setup
        with patch.object(logging_config.requests, "post", side_effect=DNS_ERROR) as post:
            for _ in range(4):
                logging.error("still down")
                clock[0] += handler.PAUSE_AFTER_FAILURE_SECONDS + 1
        assert post.call_count == 4
        errors = [m for m in capture.messages(logging.ERROR) if m.startswith("Webhook request failed")]
        assert len(errors) == 1
        assert "paused for 5 minutes" in errors[0]

    def test_recovery_resumes_and_reports_what_was_skipped(self, setup):
        handler, capture, clock = setup
        with patch.object(logging_config.requests, "post", side_effect=DNS_ERROR):
            logging.error("down")
            logging.error("skipped 1")
            logging.warning("skipped 2")
        clock[0] += handler.PAUSE_AFTER_FAILURE_SECONDS + 1
        with patch.object(logging_config.requests, "post", return_value=_ok()) as post:
            logging.error("back")
            logging.error("and again")
        assert post.call_count == 2
        assert any("reachable again (2 message(s) not sent" in m for m in capture.messages(logging.INFO))

    def test_summary_is_attempted_during_the_pause(self, setup):
        handler, _, _ = setup
        with patch.object(logging_config.requests, "post", side_effect=DNS_ERROR):
            logging.error("down")
        with patch.object(logging_config.requests, "post", return_value=_ok()) as post:
            logging.log(SUMMARY, "Run complete")
        assert post.call_count == 1

    def test_working_webhook_sends_every_message(self, setup):
        _, capture, _ = setup
        with patch.object(logging_config.requests, "post", return_value=_ok()) as post:
            logging.error("one")
            logging.warning("two")
            logging.log(SUMMARY, "three")
        assert post.call_count == 3
        assert not any("Webhook" in m for m in capture.messages())


class TestRedact:

    def test_query_and_credentials_are_hidden_for_generic_webhooks(self):
        handler = WebhookHandler("https://user:secretpw@hooks.example.com/notify/abc?token=xyz")
        text = handler._redact(
            "failed for https://user:secretpw@hooks.example.com/notify/abc?token=xyz "
            "and url: /notify/abc?token=xyz")
        assert "secretpw" not in text
        assert "xyz" not in text
        assert "/notify/abc" not in text
        assert "hooks.example.com" in text

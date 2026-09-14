import smtplib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.app.events import notifications as notifications_module
from backend.app.events.models import EventType
from backend.app.events.notifications import (
    NotificationError,
    ResendNotificationProvider,
    SMTPNotificationProvider,
    render_event_email,
)


def _provider(**overrides):
    defaults = dict(
        host="smtp.example.com",
        port=587,
        username="owner@example.com",
        password="super-secret-app-password",
        from_email="owner@example.com",
        to_email="owner@example.com",
        use_tls=True,
    )
    defaults.update(overrides)
    return SMTPNotificationProvider(**defaults)


# ---------------------------------------------------------------------------
# Credential safety: an auth failure must never leak the password
# ---------------------------------------------------------------------------


def test_smtp_auth_failure_never_leaks_the_password():
    provider = _provider(password="super-secret-app-password")

    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
        mock_smtp.return_value = instance

        with pytest.raises(NotificationError) as exc_info:
            provider.send("subject", "body")

    assert "super-secret-app-password" not in str(exc_info.value)


def test_smtp_generic_failure_message_never_includes_raw_exception_text_with_password():
    provider = _provider(password="super-secret-app-password")

    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.send_message.side_effect = smtplib.SMTPServerDisconnected(
            "connection lost while sending super-secret-app-password"
        )
        mock_smtp.return_value = instance

        with pytest.raises(NotificationError) as exc_info:
            provider.send("subject", "body")

    # The provider's own message only ever names the exception class —
    # never the underlying exception's str(), which in this contrived case
    # would have contained the password.
    assert "super-secret-app-password" not in str(exc_info.value)


def test_successful_send_calls_login_and_send_message():
    provider = _provider()

    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        mock_smtp.return_value = instance

        provider.send("Test Subject", "Test body")

    instance.login.assert_called_once_with("owner@example.com", "super-secret-app-password")
    instance.send_message.assert_called_once()
    instance.starttls.assert_called_once()


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------


def test_render_event_email_includes_payload_fields():
    payload = {
        "conversation_id": "conv-1",
        "name": "Ahmed",
        "business_name": "Smile Dental",
        "email": "ahmed@example.com",
        "lead_score": 80,
        "lead_level": "high",
    }
    subject, body = render_event_email(EventType.DEMO_REQUESTED.value, payload, datetime.now(timezone.utc))

    assert "Demo" in subject
    assert "Ahmed" in body
    assert "Smile Dental" in body
    assert "ahmed@example.com" in body
    assert "conv-1" in body
    assert "80" in body


def test_render_event_email_never_claims_demo_was_sent():
    payload = {"conversation_id": "conv-1"}
    _, body = render_event_email(EventType.DEMO_REQUESTED.value, payload, datetime.now(timezone.utc))
    assert "sent" not in body.lower() or "no demo has been created or sent" in body.lower()


def test_render_event_email_never_claims_human_has_responded():
    payload = {"conversation_id": "conv-1"}
    _, body = render_event_email(EventType.HUMAN_HANDOFF_REQUESTED.value, payload, datetime.now(timezone.utc))
    assert "responded" not in body.lower()


def test_render_event_email_omits_unknown_and_empty_fields():
    payload = {"conversation_id": "conv-1", "business_type": "unknown", "email": None, "name": ""}
    _, body = render_event_email(EventType.LEAD_CREATED.value, payload, datetime.now(timezone.utc))
    assert "Business type:" not in body
    assert "Email:" not in body
    assert "Name:" not in body


def test_render_event_email_subject_is_fixed_regardless_of_visitor_content():
    # Subject lines never embed visitor-controlled text (no header-
    # injection surface) — see notifications.py module docstring.
    payload = {"name": "Ahmed\r\nBcc: attacker@example.com"}
    subject, _ = render_event_email(EventType.HIGH_VALUE_LEAD.value, payload, datetime.now(timezone.utc))
    assert "\r" not in subject
    assert "\n" not in subject
    assert subject == "High-Value Lead — Brilyx"


# ---------------------------------------------------------------------------
# Resend provider (HTTPS API) — mirrors the OllamaProvider test style
# (monkeypatch notifications_module.httpx.post), since ResendNotificationProvider
# calls httpx.post the same way.
# ---------------------------------------------------------------------------


class FakeHTTPResponse:
    def __init__(self, status_code=200, text="{}"):
        self.status_code = status_code
        self.text = text


def _resend_provider(**overrides):
    defaults = dict(
        api_key="re_super_secret_key_123",
        from_email="notifications@brilyx.com",
        to_email="brilyx.0@gmail.com",
    )
    defaults.update(overrides)
    return ResendNotificationProvider(**defaults)


def test_resend_successful_send_posts_expected_request(monkeypatch):
    provider = _resend_provider()
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeHTTPResponse(status_code=200)

    monkeypatch.setattr(notifications_module.httpx, "post", fake_post)

    provider.send("Demo Requested — Brilyx", "A visitor requested a demo.")

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_super_secret_key_123"
    assert captured["json"]["from"] == "notifications@brilyx.com"
    assert captured["json"]["to"] == ["brilyx.0@gmail.com"]
    assert captured["json"]["subject"] == "Demo Requested — Brilyx"
    assert captured["json"]["text"] == "A visitor requested a demo."


def test_resend_only_reports_success_after_a_2xx_status(monkeypatch):
    # 200 and 201 are both used by Resend's API for a successful send —
    # neither should raise.
    for status_code in (200, 201):
        provider = _resend_provider()
        monkeypatch.setattr(
            notifications_module.httpx, "post", lambda *a, **k: FakeHTTPResponse(status_code=status_code)
        )
        provider.send("subject", "body")  # must not raise


def test_resend_non_2xx_status_raises_notification_error(monkeypatch):
    provider = _resend_provider()
    monkeypatch.setattr(
        notifications_module.httpx,
        "post",
        lambda *a, **k: FakeHTTPResponse(status_code=422, text='{"message": "Invalid `from` field"}'),
    )

    with pytest.raises(NotificationError) as exc_info:
        provider.send("subject", "body")

    assert "422" in str(exc_info.value)


def test_resend_transport_error_raises_notification_error(monkeypatch):
    provider = _resend_provider()

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(notifications_module.httpx, "post", fake_post)

    with pytest.raises(NotificationError):
        provider.send("subject", "body")


def test_resend_api_key_never_appears_in_notification_error_on_failure(monkeypatch):
    secret_key = "re_super_secret_key_123"
    provider = _resend_provider(api_key=secret_key)
    monkeypatch.setattr(
        notifications_module.httpx,
        "post",
        lambda *a, **k: FakeHTTPResponse(status_code=401, text=f'{{"message": "Invalid API key {secret_key}"}}'),
    )

    with pytest.raises(NotificationError) as exc_info:
        provider.send("subject", "body")

    # The provider's own message only ever names the status code — it
    # never reads response.text, so even a response body that happened to
    # echo the key back (contrived here) can never reach the exception,
    # a stored BusinessEvent.error_message, or a log line.
    assert secret_key not in str(exc_info.value)


def test_resend_api_key_never_appears_in_transport_error_message(monkeypatch):
    secret_key = "re_super_secret_key_123"
    provider = _resend_provider(api_key=secret_key)

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError(f"connection refused for key Bearer {secret_key}")

    monkeypatch.setattr(notifications_module.httpx, "post", fake_post)

    with pytest.raises(NotificationError) as exc_info:
        provider.send("subject", "body")

    assert secret_key not in str(exc_info.value)

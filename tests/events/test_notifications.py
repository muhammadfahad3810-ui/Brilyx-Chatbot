import smtplib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.events.models import EventType
from backend.app.events.notifications import NotificationError, SMTPNotificationProvider, render_event_email


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

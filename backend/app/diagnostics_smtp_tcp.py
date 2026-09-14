"""Temporary, opt-in SMTP TCP connectivity diagnostic.

Added to answer one narrow question during Render deployment
troubleshooting: can this container establish a bare TCP connection to
SMTP_HOST:SMTP_PORT at all? It exists entirely outside the real
notification path (backend/app/events/notifications.py) — no TLS, no
STARTTLS, no login, no message content, no smtplib. SMTP_USERNAME and
SMTP_PASSWORD are never read by this module, so there is no code path
here that could ever log a credential.

Off by default (SMTP_TCP_DIAGNOSTIC_ENABLED=false) and safe to leave in
place, but intended to be deleted once the connectivity question it was
added to diagnose is resolved — see the Render deployment conversation
this came from.

Logs at WARNING level deliberately: this app configures no root/handler
logging (see backend/app/events/service.py's logger for the same
constraint), so only WARNING-and-above records are guaranteed to reach
Render's log stream via Python's default `logging.lastResort` handler.
"""

import logging
import socket

from backend.app.config import Settings, settings

logger = logging.getLogger("brilyx.diagnostics")

CONNECT_TIMEOUT_SECONDS = 10


def run_smtp_tcp_diagnostic(config: Settings = settings) -> None:
    """Attempt one bare TCP connect to config.SMTP_HOST:config.SMTP_PORT and log the outcome.

    No-op unless explicitly enabled. Never raises — a diagnostic must
    never be able to break application startup.
    """
    if not config.SMTP_TCP_DIAGNOSTIC_ENABLED:
        return

    if not config.SMTP_HOST:
        logger.warning("SMTP_TCP_DIAGNOSTIC: SKIPPED (SMTP_HOST not set)")
        return

    host = config.SMTP_HOST
    port = config.SMTP_PORT

    try:
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS)
        sock.close()
    except OSError as exc:
        logger.warning(
            "SMTP_TCP_DIAGNOSTIC: FAILED host=%s port=%s exception=%s errno=%s message=%s",
            host,
            port,
            exc.__class__.__name__,
            exc.errno,
            exc.strerror or str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never crash startup
        logger.warning(
            "SMTP_TCP_DIAGNOSTIC: FAILED host=%s port=%s exception=%s (unexpected type) message=%s",
            host,
            port,
            exc.__class__.__name__,
            str(exc),
        )
    else:
        logger.warning("SMTP_TCP_DIAGNOSTIC: SUCCESS host=%s port=%s", host, port)

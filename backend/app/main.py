from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app import database
from backend.app.config import settings, validate_production_config
from backend.app.diagnostics_smtp_tcp import run_smtp_tcp_diagnostic
from backend.app.routers import chat, conversations, health
from backend.app.security_headers import MaxBodySizeMiddleware, SecurityHeadersMiddleware

# Phase 9: fail fast on an obviously unsafe production configuration
# (e.g. wildcard/empty CORS origins) rather than starting up and silently
# running unsafely. No-op in development — see config.py for the exact
# checks. Raises RuntimeError, which is the correct behavior at import
# time: an unsafe production deployment should not start.
validate_production_config(settings)


class UTF8JSONResponse(JSONResponse):
    """JSONResponse with an explicit charset in its Content-Type header.

    The response body was always correctly encoded UTF-8 (Starlette's
    JSONResponse always encodes to UTF-8) — this changes only the
    declared `Content-Type` header from `application/json` to
    `application/json; charset=utf-8`. Some HTTP clients (notably Windows
    PowerShell 5.1's `Invoke-RestMethod`) do not assume UTF-8 when no
    charset is declared and silently misdecode multi-byte characters
    (em dashes, curly quotes) into mojibake — verified by reproducing the
    exact symptom with `Invoke-RestMethod` against this API, and
    confirming an explicit charset resolves it. RFC 8259 doesn't require
    this (JSON is UTF-8 by default), but declaring it explicitly is
    harmless and standard practice for compatibility with such clients.
    """

    media_type = "application/json; charset=utf-8"


app = FastAPI(title=settings.APP_NAME, default_response_class=UTF8JSONResponse)

database.init_db()

# Temporary, opt-in diagnostic (see backend/app/diagnostics_smtp_tcp.py) —
# no-op unless SMTP_TCP_DIAGNOSTIC_ENABLED=true is explicitly set.
run_smtp_tcp_diagnostic()

# Phase 9: applied in reverse order of intent (Starlette wraps middleware
# in the order added, outermost added = outermost executed) — body-size
# rejection should happen before anything else does real work, and
# security headers should be added to every response including error
# responses from the other middleware/handlers below.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)

# Phase 7: the browser widget calls this API from a different origin than
# the host page it's embedded in. Explicit allow-list only — never "*" —
# and no credentials (the widget authenticates nothing; conversation_id
# travels in the JSON body, not a cookie). See docs/frontend-widget.md and
# docs/security.md "CORS" for the production expectations.
#
# X-Conversation-Token (Phase 9) must be explicitly allowed here: it's a
# custom header, so the browser sends a CORS preflight (OPTIONS) before
# the real request, and without it listed here that preflight fails and
# the browser blocks the request entirely with a generic "Failed to
# fetch" — no HTTP response, no server-side log, easy to miss. Caught via
# a real cross-origin browser test during this phase, not just review.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Conversation-Token"],
)

app.include_router(health.router)
app.include_router(conversations.router)
app.include_router(chat.router)

"""Response security headers and a request body-size guard.

Both implemented as small Starlette `BaseHTTPMiddleware` subclasses using
only what FastAPI/Starlette already ships — no new dependency.

**Why no Content-Security-Policy header here:** this API only ever
returns JSON. A CSP header governs how a *document* (HTML) may load and
execute scripts/styles/frames; it has no effect on a JSON response, which
browsers never execute as a page. The actual HTML document — and
therefore the thing that needs a CSP — is www.brilyx.com itself (or
whatever host page embeds the Phase 7 widget), which this backend does
not serve and has no authority over. Claiming a CSP header here would
misrepresent what's actually protected; see docs/security.md "Security
Headers" for the full boundary explanation.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds a small set of headers that are safe for a JSON-only API.

    Deliberately excludes CSP (see module docstring) and anything that
    assumes this response will be rendered as a web page.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Defense in depth only — a JSON API response can't be framed as a
        # clickjackable page anyway, but this costs nothing and blocks any
        # accidental future HTML/error-page response from being embedded.
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Rejects requests whose declared `Content-Length` exceeds `max_bytes`.

    **Known limitation** (documented, not hidden): this only checks the
    `Content-Length` header. A client using chunked transfer encoding
    (no `Content-Length`) or lying about a smaller size than it actually
    sends bypasses this check — a full fix requires capping bytes while
    streaming the body, which Starlette's body-parsing for JSON already
    does internally before Pydantic validation ever runs, so this
    middleware is a cheap, early, best-effort guard against the common
    case (an oversized `Content-Length` from a naive flood script), not a
    complete mitigation. A reverse proxy / ASGI server body-size limit
    (Phase 10 deployment concern) is the complete fix.
    """

    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > self._max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body is too large."},
                )
        return await call_next(request)

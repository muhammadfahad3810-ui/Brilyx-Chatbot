# Deployment Handoff Guide

**This is a handoff document, not a deployment.** Nothing in this file has
been run against Brilyx's actual hosting environment. It exists so
whoever deploys this application (a developer with access to Brilyx
hosting) knows exactly what the application needs, how it's configured,
and what they must verify themselves before going live. Where this
document can't confirm something about the target hosting environment, it
says so explicitly rather than assuming.

## 1. What This Application Is

The Brilyx AI website chatbot: a FastAPI backend (conversation
management, deterministic business intelligence, lead qualification/
capture, business-event notifications) plus a dependency-free,
Shadow-DOM embeddable JavaScript widget (`frontend/widget/brilyx-chatbot.js`)
meant to be included on www.brilyx.com via a single `<script>` tag. The
backend talks to a local/self-hosted Ollama instance for AI responses —
there is no cloud AI dependency and no paid AI service.

## 2. Runtime Requirements

- **Python 3.11** (`pyproject.toml`: `requires-python = ">=3.11"`).
- **FastAPI** + **Uvicorn** (ASGI server) — both already in
  `pyproject.toml`'s dependencies; no separate install needed beyond
  `pip install -e .` (or `-e ".[dev]"` to also get `pytest`).
- **Ollama**, reachable from wherever the backend process runs, with the
  `qwen2.5:3b-instruct-q4_K_M` model already pulled. See section 5 — this
  is the single biggest unknown for hosting and must be verified, not
  assumed.
- **SQLite** — bundled with Python, no separate database server.
- Hardware requirements are **not specified here** because they were
  never measured against Brilyx's actual hosting environment — this
  document does not invent numbers. Ollama running a 3B-parameter
  quantized model is the most resource-intensive part of the stack; size
  the host accordingly based on Ollama's own published guidance for that
  model, not a number guessed here.

## 3. Environment Variables

All configuration is read from environment variables (or a `.env` file in
the working directory) via `backend/app/config.py`. **Never** commit real
values — `.env.example` documents names and safe placeholders only.

| Variable | Required | Purpose | Example/Placeholder |
|---|---|---|---|
| `APP_NAME` | No | Display name in FastAPI's OpenAPI metadata | `Brilyx Chatbot` |
| `ENVIRONMENT` | **Yes, for production** | Set to `production` to activate startup config validation (see section 7 of `docs/security.md`) | `production` |
| `DATABASE_URL` | No | SQLAlchemy connection string | `sqlite:///./data/brilyx.db` |
| `AI_PROVIDER` | No | Which AI backend to use | `ollama` |
| `OLLAMA_BASE_URL` | **Yes, if Ollama isn't on localhost** | Where the backend reaches Ollama | `http://localhost:11434` |
| `OLLAMA_MODEL` | No | Model name Ollama must have pulled | `qwen2.5:3b-instruct-q4_K_M` |
| `OLLAMA_TIMEOUT_SECONDS` | No | Per-request timeout to Ollama | `60` |
| `CHAT_HISTORY_MAX_MESSAGES` | No | How many prior messages are replayed to the model | `20` |
| `CHAT_MAX_MESSAGE_LENGTH` | No | Max visitor message length | `4000` |
| `CORS_ALLOWED_ORIGINS` | **Yes** | Comma-separated browser origins allowed to call this API | `https://www.brilyx.com` |
| `SMTP_HOST` | No (optional feature) | Enables owner email notifications when set | `smtp.gmail.com` |
| `SMTP_PORT` | No | SMTP port | `587` |
| `SMTP_USERNAME` | No | SMTP auth username | *(leave blank unless using SMTP)* |
| `SMTP_PASSWORD` | No | SMTP auth password / app password | *(leave blank unless using SMTP — never a real value in any committed file)* |
| `SMTP_FROM_EMAIL` | No | "From" address for notification emails | *(leave blank unless using SMTP)* |
| `SMTP_USE_TLS` | No | Whether to use STARTTLS | `true` |
| `OWNER_NOTIFICATION_EMAIL` | No (required only if SMTP is used) | Where owner notifications are sent | *(leave blank unless using SMTP)* |
| `NOTIFY_ON_LEAD_CREATED` | No | Email on every new lead, not just high-value/demo/handoff | `false` |
| `CHAT_RATE_LIMIT_MAX_REQUESTS` / `CHAT_RATE_LIMIT_WINDOW_SECONDS` | No | `/api/chat` rate limit | `20` / `60` |
| `CONVERSATION_CREATE_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS` | No | `POST /api/conversations` rate limit | `10` / `60` |
| `MAX_REQUEST_BODY_BYTES` | No | Request body size cap | `32768` |

Copy `.env.example` to `.env` and fill in only what differs from the
defaults. The application starts and runs correctly with every SMTP field
left blank — email notifications are simply skipped (see
`docs/business-actions.md`).

## 4. Start Command

The existing, actual entrypoint — no new one was invented for this
document:

```bash
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

(`--reload` is a development convenience flag; omit it in production.)
`backend/app/main.py` is the ASGI app object (`app`); how the hosting
environment supervises this process (systemd, a process manager, a
platform-specific "web process" convention, etc.) is entirely up to
Brilyx hosting's own conventions — this document does not assume one.

## 5. Ollama

**This is the most important thing to verify before deploying — do not
assume it will work.** The backend calls Ollama over plain HTTP at
whatever `OLLAMA_BASE_URL` is configured to (default
`http://localhost:11434`, meaning "the same machine the backend process
is running on," not any other meaning).

Before deploying, whoever has access to Brilyx hosting must confirm:

1. **Can Ollama actually run there?** Shared/managed hosting platforms
   frequently cannot run arbitrary long-lived background services or GPU
   workloads — this document makes no claim that Brilyx's hosting
   supports this. If it doesn't, the alternative is pointing
   `OLLAMA_BASE_URL` at a separately-hosted, reachable Ollama instance
   (e.g. on a VM you control) — but that instance must be network-
   reachable from the backend and is a real infrastructure decision, not
   something this document sets up.
2. **Is the model already pulled** (`ollama pull qwen2.5:3b-instruct-q4_K_M`)
   on whatever machine runs Ollama? The application does not — and must
   not — download the model automatically at startup (see
   `docs/ai-engine.md`); a missing model results in the same controlled
   `503` the app already returns for any Ollama failure, not a crash.
3. **Is the port reachable** from the backend process to wherever Ollama
   actually runs, accounting for any firewall/network policy on that
   hosting platform?

## 6. Database

Unchanged, single-file **SQLite** — `backend/app/database.py` creates
`data/brilyx.db` (relative to the working directory the process is
started from) automatically on first run via
`Base.metadata.create_all()`, which only ever adds missing tables, never
drops or alters existing ones.

**Persistence consideration for hosting:** if the hosting platform uses
ephemeral/stateless containers or filesystems that reset between
deploys/restarts, `data/brilyx.db` — and therefore all conversations,
leads, and business events — would be lost on every restart. Whoever
deploys this must ensure the directory containing `data/brilyx.db` is on
persistent storage appropriate to that hosting platform. This document
does not migrate to PostgreSQL or any other database — that would be a
real architectural change, out of scope here, and explicitly not
requested.

## 7. CORS

`CORS_ALLOWED_ORIGINS` **must** be set to the real production origin(s)
serving the widget — based on the current project context, that's:

```
CORS_ALLOWED_ORIGINS=https://www.brilyx.com
```

Add any other legitimate origin that will actually embed the widget
(comma-separated). **Never** set this to `*` — `backend/app/config.py::validate_production_config()`
already refuses to start with `ENVIRONMENT=production` if
`CORS_ALLOWED_ORIGINS` is empty, wildcarded, or still points at a local
dev origin (`127.0.0.1`/`localhost`). Set `ENVIRONMENT=production` so
this check actually runs.

## 8. SMTP (Optional)

Owner email notifications (high-value lead, demo request, human handoff —
see `docs/business-actions.md`) are **entirely optional**. Leaving
`SMTP_HOST` and `OWNER_NOTIFICATION_EMAIL` blank keeps the application
fully functional; notifications are just skipped and recorded as such
internally.

If notifications are wanted, the required variables are `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`,
`SMTP_USE_TLS`, and `OWNER_NOTIFICATION_EMAIL` (see the table in section
3). **No credential is provided or invented by this document.**

If Gmail's SMTP is used (already documented in the existing code's
`.env.example` comments): `SMTP_PASSWORD` must be a Google **App
Password**, not the account's normal password — Gmail rejects
plain-password SMTP login. This is an existing, already-documented
requirement, not new guidance invented for this handoff.

## 9. Health Check

```
GET /health
```

Returns `200 {"status": "ok"}` and requires no configuration, no
database, and no Ollama connection — suitable for whatever uptime/health
check mechanism the hosting platform provides.

## 10. Reverse Proxy / HTTPS

**Not implemented here, and intentionally so.** This application serves
plain HTTP on whatever port it's started with. TLS termination, a domain-
appropriate reverse proxy, and HTTPS certificate management are hosting-
environment concerns that must be provided by whoever operates Brilyx
hosting, using whatever mechanism is standard there (a platform load
balancer, nginx, Caddy, etc.). This document does not choose one.

## 11. Frontend Widget

`frontend/widget/brilyx-chatbot.js` is a single-file, dependency-free,
Shadow-DOM script meant to be embedded on the actual Brilyx website via:

```html
<script
  src="https://<wherever-this-file-is-hosted>/brilyx-chatbot.js"
  data-api-base-url="https://<the-deployed-backend-url>"
></script>
```

**The backend API URL is fully configurable, not hardcoded** — it's read
from the `data-api-base-url` attribute on the script tag at load time
(`backend/app/config.py` is irrelevant here; this is purely a frontend
JS read, `frontend/widget/brilyx-chatbot.js` line ~33). Its default
(`http://127.0.0.1:8000`) only applies if the attribute is omitted, which
should never happen in production — whoever embeds the widget on
www.brilyx.com must set `data-api-base-url` to wherever this backend is
actually deployed. See `docs/frontend-widget.md` for the full
configuration reference (title/subtitle/max-message-length/dev-mode are
also configurable the same way).

`frontend/widget/demo.html` is a **local development harness only** — it
simulates a hostile host page to prove Shadow DOM isolation, and is not
meant to be deployed or copied to production. The actual widget file to
host is `brilyx-chatbot.js` alone.

**This backend and the widget script do not need to share an origin** —
that's the entire reason `CORS_ALLOWED_ORIGINS` (section 7) exists. The
widget file itself can be hosted anywhere (a CDN, a static file host, the
same server as the backend, etc.) as long as `data-api-base-url` points
at the backend. What matters for CORS is the origin of the **page that
embeds the `<script>` tag** — i.e. `https://www.brilyx.com`, since that's
the origin the browser attaches to the API request — not wherever the
script file itself happens to be served from.

## What This Document Does NOT Do

- Does not deploy anything.
- Does not provision a server, container, or cloud resource.
- Does not configure DNS, HTTPS certificates, or a reverse proxy.
- Does not verify Ollama actually works on Brilyx's hosting — that is an
  explicit prerequisite for whoever deploys this to check themselves.
- Does not migrate the database to PostgreSQL or any other engine.
- Does not set up CI/CD.

See `docs/security.md` for the full security posture and its honestly-
documented limitations (single-process rate limiting, etc.) — anyone
deploying this publicly should read that file too.

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
| `DATABASE_URL` | No | SQLAlchemy connection string. Defaults to local SQLite; **production deployments (e.g. Render) should set this to a managed PostgreSQL connection string** instead — see `docs/deployment-render.md` "Database". A bare `postgres://` scheme is automatically rewritten to `postgresql://` (`backend/app/database.py::normalize_database_url`), so either form works. | `sqlite:///./data/brilyx.db` (dev) / `postgresql://user:pass@host:5432/dbname` (production) |
| `AI_PROVIDER` | No | `ollama` (default, free/local), `gemini` (cloud, paid), or `groq` (cloud, paid) — see section 5 | `ollama` |
| `OLLAMA_BASE_URL` | **Yes, if using `AI_PROVIDER=ollama` and it isn't on localhost** | Where the backend reaches Ollama | `http://localhost:11434` |
| `OLLAMA_MODEL` | No | Model name Ollama must have pulled | `qwen2.5:3b-instruct-q4_K_M` |
| `OLLAMA_TIMEOUT_SECONDS` | No | Per-request timeout to Ollama | `60` |
| `GEMINI_API_KEY` | **Yes, if using `AI_PROVIDER=gemini`** | Real Google Gemini API key — never committed | *(set in your hosting platform's env var UI only)* |
| `GEMINI_MODEL` | No | Gemini model name | `gemini-2.0-flash` |
| `GEMINI_TIMEOUT_SECONDS` | No | Per-request timeout to Gemini | `60` |
| `GROQ_API_KEY` | **Yes, if using `AI_PROVIDER=groq`** | Real Groq API key — never committed | *(set in your hosting platform's env var UI only)* |
| `GROQ_MODEL` | No | Groq model name for the main chat reply | `openai/gpt-oss-120b` |
| `GROQ_TIMEOUT_SECONDS` | No | Per-request timeout to Groq | `60` |
| `GROQ_MAX_TOKENS` | No | Caps the main chat reply's output length — required to stay under Groq's tokens-per-minute limit (see `docs/ai-engine.md` "Groq tokens-per-minute (TPM) limit") | `800` |
| `GROQ_EXTRACTION_MODEL` | No | Separate, smaller Groq model used only for structured lead-extraction, so it doesn't compete with the main chat call's TPM budget | `openai/gpt-oss-20b` |
| `GROQ_EXTRACTION_MAX_TOKENS` | No | Caps the extraction call's output — must stay well above the extraction model's own hidden reasoning-token usage (see `docs/ai-engine.md`) | `1200` |
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

## 5. AI Provider: Ollama (default), Gemini, or Groq

**This is one of the most important things to verify before deploying —
do not assume Ollama will work in your hosting environment.** The
application supports three AI providers, selected via `AI_PROVIDER` (see
`docs/ai-engine.md` "Provider Selection: Ollama vs. Gemini vs. Groq" for
the full reference) — pick whichever fits your hosting situation; none of
them requires changing any other part of the application.

### Option A — Ollama (`AI_PROVIDER=ollama`, the default)

The backend calls Ollama over plain HTTP at whatever `OLLAMA_BASE_URL` is
configured to (default `http://localhost:11434`, meaning "the same
machine the backend process is running on," not any other meaning).

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

### Option B — Gemini (`AI_PROVIDER=gemini`)

If your hosting environment can't run or reach Ollama, set
`AI_PROVIDER=gemini` and `GEMINI_API_KEY` (a real Google Gemini API key —
**never** committed to source control; set it directly in your hosting
platform's environment variable settings) instead. This is a paid cloud
API (unlike free/local Ollama) but removes the "where do I run Ollama"
problem entirely — there's nothing else to host or make reachable.
`GEMINI_MODEL` defaults to `gemini-2.0-flash` and can be overridden if
needed. See `docs/ai-engine.md` for the full configuration reference.

### Option C — Groq (`AI_PROVIDER=groq`)

Another cloud alternative if your hosting environment can't run or reach
Ollama: set `AI_PROVIDER=groq` and `GROQ_API_KEY` (a real Groq API key —
**never** committed to source control; set it directly in your hosting
platform's environment variable settings). Like Gemini, this is a paid
cloud API but removes the "where do I run Ollama" problem entirely.

**`GROQ_MODEL` must be set explicitly in production** —
`backend/app/config.py`'s own code-level default is stale (it references
a model no longer available on Groq's current catalog for this account).
The current, verified production configuration is:

```
GROQ_MODEL=openai/gpt-oss-120b
GROQ_MAX_TOKENS=800
GROQ_EXTRACTION_MODEL=openai/gpt-oss-20b
GROQ_EXTRACTION_MAX_TOKENS=1200
```

See `docs/ai-engine.md` "Groq tokens-per-minute (TPM) limit" for the full
reasoning behind each of these four values (model choice, the 413/TPM fix,
and the separate extraction-model routing).

## 6. Database

Two supported backends, selected purely by `DATABASE_URL` — no other
configuration or code change needed either way, since the entire data
layer (`backend/app/models.py`, and every query in
`backend/app/services/`, `qualification/`, `leads/`, `events/`) uses only
portable SQLAlchemy ORM constructs (no raw SQL, no SQLite-only column
types or syntax).

- **SQLite** (local development default) — single-file
  `data/brilyx.db`, created automatically on first run via
  `Base.metadata.create_all()` (adds missing tables only, never drops or
  alters existing ones).
- **PostgreSQL** (recommended for production/hosting with ephemeral
  container filesystems, e.g. Render) — set `DATABASE_URL` to the
  provider's connection string. `backend/app/database.py::normalize_database_url()`
  automatically rewrites a legacy `postgres://` scheme to `postgresql://`
  (required for SQLAlchemy 2.x), so either scheme works. The engine adds
  `pool_pre_ping=True` and `pool_recycle=300` on the Postgres path only,
  to tolerate a managed provider silently closing idle connections.

**Persistence consideration for hosting:** if the hosting platform uses
ephemeral/stateless containers or filesystems that reset between
deploys/restarts, **either** point `DATABASE_URL` at a managed PostgreSQL
instance (state then lives outside the container entirely — see
`docs/deployment-render.md` "Database"), **or**, if staying on SQLite,
ensure the directory containing `data/brilyx.db` is on persistent storage
appropriate to that hosting platform. Migrations beyond `create_all()`
(e.g. Alembic) are intentionally not introduced yet — see
`docs/deployment-render.md` "Database" for the reasoning and what would
trigger adding one.

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

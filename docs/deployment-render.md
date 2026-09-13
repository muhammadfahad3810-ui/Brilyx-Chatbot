# Render Free — Manual Deployment Guide

**This is a manual, human-driven deployment guide, not an automated
script.** No Render service was created by preparing this document, and
nothing here was deployed. It exists so you (the person with a GitHub
account and Render access) can deploy this exact, unmodified application
to a Render Free Web Service yourself.

**Read `docs/deployment.md` and `docs/security.md` first** — this
document is Render-specific and assumes the general architecture,
environment variables, and security posture described there. It does not
repeat everything from those files.

## Before You Start — What Render Free Can and Cannot Do Here

- ✅ Render Free **can** host this FastAPI backend as a Web Service.
- ❌ Render Free **cannot** host the Ollama model
  (`qwen2.5:3b-instruct-q4_K_M`) inside the same free web service — Ollama
  needs to run its own long-lived process with real CPU/RAM for
  inference, which is not what a Render Free web service is for. **Ollama
  must be a separate, externally-reachable service** — see "Ollama" below.
- 🆕 **An alternative that avoids the Ollama-hosting problem entirely:**
  set `AI_PROVIDER=gemini` with a real `GEMINI_API_KEY`, or
  `AI_PROVIDER=groq` with a real `GROQ_API_KEY` (see `docs/ai-engine.md`
  "Provider Selection: Ollama vs. Gemini vs. Groq"). Both are cloud APIs
  (not free like Ollama) reachable directly from Render with no separate
  Ollama hosting needed at all — this removes the Ollama half of the
  "test/demo only" conclusion below, though it still does not fix Render
  Free's SQLite persistence limitation.
- ⚠️ Render Free's filesystem is **not durable production storage** for
  the SQLite database — see "Database" below.
- 🆕 **PostgreSQL removes the storage limitation entirely:** set
  `DATABASE_URL` to a managed PostgreSQL connection string (Render's own
  managed Postgres, or any other provider) instead of the default SQLite
  path. State then lives outside the web service's container filesystem
  altogether, so it survives redeploys/restarts regardless of which
  Render tier is used. See "Database" below.
- **Conclusion:** a **Render Free** web service using the default SQLite
  path, on its own, gives you a **test/demo deployment** only — useful for
  demoing the widget and verifying the deployed code actually runs, not a
  durable production system. **A paid Render Web Service with
  `DATABASE_URL` pointed at a managed PostgreSQL database (the currently
  approved production configuration) is a genuine production deployment**
  — no separate persistent disk is needed for the database in that case.

## 1. What This Application Is

The Brilyx AI website chatbot backend (FastAPI + SQLAlchemy + SQLite,
talking to Ollama for AI responses) plus a separately-hosted, dependency-
free JavaScript widget. See `docs/deployment.md` section 1 for the full
description — unchanged here.

## 2. Create a Render Account / Connect GitHub

*(Generic wording used below — Render's exact UI labels may change over
time; if a label doesn't match exactly what you see, look for the
closest equivalent. **NEEDS MANUAL VERIFICATION** against Render's
current dashboard.)*

1. Create a Render account (or sign in) at Render's website.
2. Connect your GitHub account to Render if you haven't already, and
   grant it access to this repository (`Brilyx-Chatbot`, already public
   and already pushed to `main`).
3. From the Render dashboard, choose to create a **new Web Service**.
4. Select the `Brilyx-Chatbot` repository.
5. Select the `main` branch.

## 3. Web Service Configuration

### Environment / Runtime

Choose **Python** as the environment/runtime. Render should auto-detect
this from `pyproject.toml`, but if it asks explicitly, choose Python.

**Python version:** this repository includes a `.python-version` file
pinning `3.11`. If Render offers an explicit `PYTHON_VERSION` environment
variable or a version-selection field in its dashboard, set it to
`3.11` (e.g. `3.11.9` if a full patch version is required) to be certain
— **NEEDS MANUAL VERIFICATION** that Render's current build system
actually reads `.python-version` automatically; setting `PYTHON_VERSION`
explicitly is the more certain path if available.

### Build Command

```
pip install --upgrade pip && pip install .
```

This installs the application and its declared runtime dependencies
(FastAPI, Uvicorn, SQLAlchemy, Pydantic-Settings, httpx — see
`pyproject.toml`) using the project's existing, already-working
`pip install .` packaging. `pytest` (a `dev`-only dependency) is not
needed at runtime and is intentionally not installed by this command.

### Start Command

```
uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT
```

- `backend.app.main:app` is the actual, existing ASGI app object — no new
  entrypoint was created for Render.
- `--host 0.0.0.0` is required — `127.0.0.1`/`localhost` would make the
  service unreachable from outside its own container.
- `--port $PORT` **must** use Render's injected `$PORT` environment
  variable, never a hardcoded port — Render decides which port your
  service is actually reachable on. This exact command was verified
  locally against this codebase with a dynamic `$PORT` value before
  writing this document (see the Phase 10 final report).

### Health Check Path

```
/health
```

The existing `GET /health` endpoint (unchanged, no new endpoint added)
returns `200 {"status": "ok"}` with no dependency on the database or
Ollama — safe for Render to poll to determine whether the service is up.

## 4. Required Environment Variables

Set these in Render's environment variable settings for the service:

| Variable | Value for Render | Why |
|---|---|---|
| `ENVIRONMENT` | `production` | Activates the existing startup safety check (`validate_production_config`) that refuses to start with unsafe CORS — see `docs/security.md`. |
| `CORS_ALLOWED_ORIGINS` | `https://www.brilyx.com` (add `https://brilyx.com` too, comma-separated, only if that apex domain will *also* genuinely embed the widget — do not add origins that aren't confirmed) | Must be the real origin(s) of the page embedding the widget. Never `*`. |
| `DATABASE_URL` | The connection string Render (or your Postgres provider) gives you for the database instance, e.g. `postgresql://user:pass@host:5432/dbname` | **Required for real production use** — without it the app falls back to SQLite on the web service's own filesystem, which is not durable storage on Render. A bare `postgres://` scheme is automatically normalized to `postgresql://` — see "Database" below. |
| **One of** `OLLAMA_BASE_URL`, `AI_PROVIDER`+`GEMINI_API_KEY`, or `AI_PROVIDER`+`GROQ_API_KEY` | Pick one AI provider path (see below) | `/api/chat` needs a working AI provider; `/health` works without any of them. |

**Choose one AI provider path:**
- **Ollama path** (default, `AI_PROVIDER=ollama`): set `OLLAMA_BASE_URL`
  to the reachable HTTP(S) URL of your external Ollama instance — see
  "Ollama" below. This is **not** optional for `/api/chat` to work on this
  path, though the service will still start and `/health` will still pass
  without it.
- **Gemini path** (`AI_PROVIDER=gemini`): set `GEMINI_API_KEY` to a real
  Google Gemini API key (never committed — set it directly in Render's
  environment variable UI) and, optionally, `GEMINI_MODEL` if you don't
  want the default (`gemini-2.0-flash`). This avoids needing any external
  Ollama hosting at all — see `docs/ai-engine.md`. If `AI_PROVIDER=gemini`
  is set without a key, the service will still start, but `/api/chat`
  will return the same controlled `503` it already returns for any AI
  provider failure.
- **Groq path** (`AI_PROVIDER=groq`): set `GROQ_API_KEY` to a real Groq
  API key (never committed — set it directly in Render's environment
  variable UI). **`GROQ_MODEL` must be set explicitly** —
  `backend/app/config.py`'s own code-level default is stale (it names a
  model no longer available on Groq's current catalog for this account).
  The current, verified production configuration is:
  ```
  GROQ_MODEL=openai/gpt-oss-120b
  GROQ_MAX_TOKENS=800
  GROQ_EXTRACTION_MODEL=openai/gpt-oss-20b
  GROQ_EXTRACTION_MAX_TOKENS=1200
  ```
  See `docs/ai-engine.md` "Groq tokens-per-minute (TPM) limit" for the
  full reasoning behind each value. Like the Gemini path, this avoids
  needing any external Ollama hosting at all. If `AI_PROVIDER=groq` is set
  without a key, the service will still start, but `/api/chat` will
  return the same controlled `503` it already returns for any AI provider
  failure.

## 5. Optional Environment Variables

Everything else has a safe default and does not need to be set unless you
want non-default behavior:

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct-q4_K_M` | Must match a model actually pulled on your external Ollama instance. |
| `OLLAMA_TIMEOUT_SECONDS` | `60` | Per-request timeout to Ollama. |
| `CHAT_HISTORY_MAX_MESSAGES` | `20` | Unchanged business behavior. |
| `CHAT_MAX_MESSAGE_LENGTH` | `4000` | Unchanged business behavior. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_EMAIL` / `SMTP_USE_TLS` / `OWNER_NOTIFICATION_EMAIL` | blank | Owner email notifications — entirely optional; the app runs correctly with these unset (see `docs/business-actions.md`). **Never** put real credentials in Render's dashboard notes/logs where they might be screen-shared; use Render's environment variable UI, which is the correct place for them. |
| `NOTIFY_ON_LEAD_CREATED` | `false` | Unchanged business behavior. |
| `CHAT_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS`, `CONVERSATION_CREATE_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS`, `MAX_REQUEST_BODY_BYTES` | Phase 9 defaults | Unchanged security behavior — see `docs/security.md`. |

## 6. Ollama (External Dependency — Critical)

**Render Free cannot run Ollama for you.** `OLLAMA_BASE_URL` must point
to an Ollama HTTP endpoint reachable from wherever Render actually runs
your web service — this could be:

- A separate machine/VM you already have Ollama running on (e.g. your own
  development machine, if it's reachable over the internet — not
  generally recommended for anything beyond a quick demo), or
- Another host you provision specifically to run Ollama.

**Local development is unaffected and unchanged:**
```
OLLAMA_BASE_URL=http://127.0.0.1:11434
```
remains the correct default for running this application on your own
machine, exactly as before.

**For Render:**
```
OLLAMA_BASE_URL=<your reachable external Ollama endpoint>
```

Requirements for that external endpoint:
- It must have the exact model pulled: `ollama pull qwen2.5:3b-instruct-q4_K_M`.
- It must be reachable over HTTP/HTTPS from Render's network — this
  document does not verify that for you; test it (see "Testing" below)
  after deployment.
- **This codebase's `OllamaProvider` sends no authentication header to
  Ollama.** If your external Ollama endpoint needs to be protected (it
  should be, if it's reachable from the public internet — Ollama itself
  has no built-in auth), that protection must come from network-level
  controls (a firewall allow-list, a VPN, a reverse proxy in front of it)
  that you set up separately. Adding credential support to
  `OllamaProvider` itself was out of scope for this phase (no code change
  was made here) — **NEEDS MANUAL VERIFICATION / DECISION** on your part
  for how you'll secure that endpoint.

**Do not assume Render Free itself can run this model** — it was never
claimed to, and this document does not claim it does.

## 7. Database

**Recommended for production: managed PostgreSQL.** Set `DATABASE_URL` to
your Postgres provider's connection string (Render's own managed
PostgreSQL, or any other provider reachable from your Render web
service), e.g.:

```
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

- No code change is required to support this: the entire data layer
  (`backend/app/models.py` and every query in `backend/app/services/`,
  `qualification/`, `leads/`, `events/`) uses only portable SQLAlchemy ORM
  constructs — no raw SQL, no SQLite-only types or syntax.
- A bare `postgres://` scheme (some providers, historically including
  Render, issue URLs this way) is automatically rewritten to
  `postgresql://` by `backend/app/database.py::normalize_database_url()`
  — SQLAlchemy 2.x's Postgres dialect does not recognize `postgres://` on
  its own. Either scheme works as `DATABASE_URL`.
- The engine is created with `pool_pre_ping=True` and `pool_recycle=300`
  on the Postgres path (not applied to SQLite), so a managed provider
  silently closing idle connections doesn't surface as request failures.
- Tables are still created via `Base.metadata.create_all()` on startup —
  additive only, never drops or alters existing tables. This is
  intentionally **not** using Alembic or any other migration tool yet:
  for the initial cutover onto a fresh Postgres database this is
  sufficient and is the smallest safe approach. Introduce a real
  migration tool at the point the schema needs to change *after*
  Postgres already holds real production data — `create_all()` alone is
  not safe for altering an existing column/table at that point.
- With state living in managed Postgres, a **paid Render Web Service no
  longer needs a persistent disk for the database** — this is what makes
  a paid Render Web Service + managed Postgres a genuine production
  deployment rather than a test/demo (see "Before You Start" above).

**SQLite remains fully supported** (local development, or a quick Render
Free test/demo) — `data/brilyx.db`, created automatically, additive-only
schema (see `docs/deployment.md` section 6). On Render Free specifically,
the container filesystem is not guaranteed to survive a redeploy/restart
(**NEEDS MANUAL VERIFICATION** against Render's current free-tier
behavior), so:

> **Every conversation, lead, and business event stored in SQLite on
> Render Free may be lost at any redeploy or restart.** Do not treat data
> in a SQLite-on-Render-Free deployment as durable — use PostgreSQL (or a
> paid persistent disk, `sqlite:////<mount-path>/brilyx.db`) for anything
> that needs to survive a restart.

## 8. CORS

Set `CORS_ALLOWED_ORIGINS=https://www.brilyx.com` (see section 4). The
existing `validate_production_config()` check (Phase 9, unchanged) will
refuse to start the service if `ENVIRONMENT=production` and this is left
wildcarded, empty, or still pointing at a local dev origin — this is a
safety feature, not a bug, if you see the service fail to start with a
config error.

## 9. Health Check Testing

Once deployed, Render will give you a URL like
`https://<your-service-name>.onrender.com`. Test:

```bash
curl https://<your-service-name>.onrender.com/health
```

Expected: `200 {"status": "ok"}`.

## 10. Testing `POST /api/chat`

```bash
curl -X POST https://<your-service-name>.onrender.com/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi"}'
```

If `OLLAMA_BASE_URL` isn't reachable, expect the existing, already-tested
controlled `503` response (`"Brilyx AI is temporarily unavailable..."`) —
not a crash, not a raw error. This is the same behavior as local
development when Ollama isn't running; nothing Render-specific changes
this.

## 11. Inspecting Render Logs

Render provides a log stream/viewer in its dashboard for the deployed
service (exact location/label **NEEDS MANUAL VERIFICATION** in the
current Render UI). Use it to check startup errors (e.g. a
`validate_production_config` failure, which will show a clear
`RuntimeError` message, not a secret) and per-request errors. Per
`docs/security.md`'s logging policy, application-level logs never contain
visitor PII, SMTP credentials, or full request/response bodies — only
`event_type`/`event_id`/`conversation_id`/status where relevant.

## 12. Expected Cold-Start Behavior on Render Free

Render Free services typically **sleep after a period of inactivity** and
take some amount of time to wake up on the next incoming request — this
means the first request after a period of no traffic may be noticeably
slower (or may need a retry) than subsequent ones. **NEEDS MANUAL
VERIFICATION** for the exact current sleep/wake timing on Render's
current free tier. This document does **not** add any keep-alive request,
cron job, or uptime-monitoring service to mask this — that would require
either paid infrastructure or an external scheduler, both out of scope
for this phase, and masking it isn't something the application layer
should silently do anyway.

## 13. Security Notes for This Deployment

Everything in `docs/security.md` is unchanged and still applies:

- Rate limiting, security headers, conversation-token access control
  (`X-Conversation-Token`), body-size limits, sanitized errors, and
  PII-minimized logging are all preserved exactly as implemented in
  Phase 9 — nothing here weakens them.
- The CORS `allow_headers` list already includes `X-Conversation-Token`
  (needed for the browser preflight on the token-gated endpoints) —
  unchanged, already correct.
- `X-Conversation-Token` access control does not depend on Render in any
  way — it's a pure application-layer check comparing a header to a
  database value, unaffected by hosting platform.
- **Reminder from `docs/security.md`:** rate limiting is single-process,
  in-memory — it resets if Render restarts/redeploys the service, and if
  Render ever runs more than one instance of this service (unlikely on
  the Free tier, but worth stating), each instance would have its own
  independent limit. This was already a documented limitation before
  Render was introduced; it is not a new Render-specific gap.
- Render likely places its own reverse proxy in front of your service and
  terminates TLS there — meaning your service itself still only ever
  speaks plain HTTP on `$PORT`, which is correct and expected; Render
  handles the public HTTPS layer. **NEEDS MANUAL VERIFICATION** that this
  matches Render's actual current architecture, but this is standard for
  platforms like Render.

## 14. Frontend Widget Configuration for This Deployment

The widget (`frontend/widget/brilyx-chatbot.js`) is **not** hosted by
Render in this guide — Render here hosts only the backend API. To point
a widget instance at your Render deployment (for testing — this is *not*
the same as installing it on www.brilyx.com, which is a separate,
later step):

```html
<script
  src="YOUR_WIDGET_URL"
  data-api-base-url="https://<your-service-name>.onrender.com"
></script>
```

- `YOUR_WIDGET_URL` is wherever you choose to serve `brilyx-chatbot.js`
  from (it could be served from the same Render service as a static
  file, a separate static host, or simply opened locally for a quick
  test) — this document does not choose that for you.
- `data-api-base-url` is the only value that needs to change to point at
  the Render deployment — this attribute was already fully configurable
  before this phase (Phase 7); nothing was changed here to support this.
- **This is not the same as embedding the widget on www.brilyx.com** —
  that integration is a separate, later step your team will do once a
  deployment (Render test or Brilyx production) is actually ready to be
  linked from the real site.

## 15. Summary: Is This Production?

**Depends on the configuration chosen:**

- A **Render Free** web service using the default SQLite path is a
  **test/demo deployment** only — treat anything stored there as
  disposable.
- A **paid Render Web Service** with `DATABASE_URL` pointed at a managed
  **PostgreSQL** database, `AI_PROVIDER=groq` configured with the current
  production values (see `docs/ai-engine.md`), and `CORS_ALLOWED_ORIGINS`
  pointed at the real production domain(s) — **the currently approved
  configuration** — is a genuine production deployment. Data durability no
  longer depends on the web service's own container filesystem at all
  once Postgres is in place.

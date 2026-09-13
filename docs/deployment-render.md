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
- ⚠️ Render Free's filesystem is **not durable production storage** for
  the SQLite database — see "Database / SQLite Persistence" below.
- **Conclusion:** deploying this to Render Free, on its own, gives you a
  **test/demo deployment** — a real public backend you and others can hit
  over HTTPS, useful for demoing the widget and verifying the deployed
  code actually runs — not a durable production system. Production, per
  the existing project plan, is expected to move to your friend's Brilyx
  hosting once that's ready.

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
| `OLLAMA_BASE_URL` | The reachable HTTP(S) URL of your external Ollama instance (e.g. `https://your-ollama-host.example.com`) | See "Ollama" below — this is **not** optional for `/api/chat` to work, though the service will still start and `/health` will still pass without it. |

## 5. Optional Environment Variables

Everything else has a safe default and does not need to be set unless you
want non-default behavior:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/brilyx.db` | See "Database" below — override only if using a Render persistent disk at a different mount path. |
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

## 7. Database / SQLite Persistence Limitation

Unchanged SQLite architecture — `data/brilyx.db`, created automatically,
additive-only schema (see `docs/deployment.md` section 6).

**Render Free specifically:** Render's free web services generally do not
provide a persistent disk — the filesystem a free service's container
runs on is not guaranteed to survive a redeploy, restart, or the service
being spun down/back up. **NEEDS MANUAL VERIFICATION** against Render's
current free-tier documentation for the exact current behavior, but the
safe assumption for this deployment is:

> **Every conversation, lead, and business event stored in SQLite on
> Render Free may be lost at any redeploy or restart.** Do not treat data
> in this deployment as durable. This is exactly why this deployment is a
> test/demo, not production.

If Render offers a persistent disk add-on (typically a paid feature) and
you attach one, set `DATABASE_URL` to a `sqlite:////<mount-path>/brilyx.db`
pointing at that disk's mount path — the application does not need any
code change to support this (verified: `DATABASE_URL` is fully
env-overridable).

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

**No.** This Render Free deployment is a **test/demo deployment** of the
real, unmodified application. It becomes something closer to production
only if:

1. A durable, persistent database location is provided (a paid Render
   disk, or moving to your friend's Brilyx hosting), **and**
2. A reliably-available external Ollama endpoint is provisioned and
   secured, **and**
3. `CORS_ALLOWED_ORIGINS` is pointed at the real production domain(s).

Until then, treat anything stored during a Render Free test deployment as
disposable.

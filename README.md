# Brilyx-Chatbot

Backend foundation for the Brilyx AI Chatbot — an AI sales, support, and lead
qualification assistant intended for deployment on Brilyx.com.

## Phase 0 scope

This phase builds only the project foundation:

- A FastAPI application with a `GET /health` endpoint.
- Configuration via `pydantic-settings` (app name, environment, database URL).
- A SQLAlchemy + SQLite database connection/session foundation (no business
  tables yet).
- A pytest test suite covering the health endpoint.

## Phase 1 scope

Adds the structured business knowledge base under `knowledge/` (company,
services, pricing, industries, FAQ, sales rules, demo policy) plus
`docs/knowledge-base.md` explaining how it's structured and maintained.

## Phase 2 scope

Adds a local, free-first AI engine on top of the knowledge base:

- A provider-agnostic AI abstraction (`backend/app/ai/`) with a local
  **Ollama** provider (`qwen2.5:3b-instruct-q4_K_M`).
- A centralized Brilyx system prompt with anti-invention and
  prompt-injection-resistant rules.
- A deterministic knowledge loader (plain Markdown into the prompt — no
  embeddings/vector DB/RAG yet).
- `POST /api/chat` for testing the assistant.

See `docs/ai-engine.md` for full details, including how Ollama failures are
handled.

## Phase 3 scope

Turns the stateless `/api/chat` endpoint into a conversational session
system backed by SQLite:

- `Conversation` and `Message` models (`backend/app/models.py`).
- `POST /api/conversations`, `GET /api/conversations/{id}`,
  `POST /api/conversations/{id}/close`.
- `POST /api/chat` now takes `{"conversation_id": "...", "message": "..."}`
  (`conversation_id` is optional — omitting it auto-creates a conversation),
  persists both sides of the exchange, and replays a configurable amount of
  recent history to the AI on each turn.

See `docs/conversations.md` for the full conversation lifecycle, API
examples, and the history-limiting strategy.

## Phase 4 scope

Adds a structured conversation-intelligence layer (`backend/app/intelligence/`)
that turns visitor messages into a validated, persisted `ConversationState`
per conversation — business type, intent, requested service/package,
country/currency, problem summary, requirements, demo/human-handoff flags —
using deterministic rules first and a validated AI extraction pass only
when the rules aren't confident. This state is fed back into the AI's
response prompt as a clearly labeled, non-instructional context block.

- `GET /api/conversations/{id}/state` — development-oriented inspection of
  the structured state.

See `docs/conversation-intelligence.md` for the full schema, rules, merge
behavior, and security boundaries.

Not included yet: the frontend chat widget, lead capture/scoring, CRM,
WhatsApp integration, demo backend, authentication, payments, or production
deployment. These are planned for later phases.

## Later phases

Phases 5–9 (deterministic lead qualification, natural lead capture, the
frontend chat widget, business events/owner notifications, and security/
production hardening) are documented in `docs/lead-qualification.md`,
`docs/lead-capture.md`, `docs/frontend-widget.md`, `docs/business-actions.md`,
and **`docs/security.md`** respectively. `docs/security.md` in particular
covers the threat model, rate limiting, conversation access control, CORS,
and known limitations for anyone deploying this publicly.

## Requirements

- **Python 3.11**
- **[Ollama](https://ollama.com)**, running and reachable, with the
  `qwen2.5:3b-instruct-q4_K_M` model pulled (needed for `/api/chat`;
  `/health` works without it)
- **Git**

The backend (FastAPI + SQLAlchemy + SQLite) and frontend widget (plain
HTML/CSS/JS) are cross-platform — this project has been developed on
Windows, but nothing in the stack is Windows-specific. Commands below are
given for both PowerShell (Windows) and a POSIX shell (macOS/Linux).

## Setup

### 0. Clone the repository

```bash
git clone <repository-url>
cd Brilyx-Chatbot
```

### 1. Create and activate a Python 3.11 virtual environment

PowerShell (Windows):
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

POSIX shell (macOS/Linux):
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -e ".[dev]"
```

### 3. Configure environment variables

PowerShell:
```powershell
copy .env.example .env
```

POSIX shell:
```bash
cp .env.example .env
```

Adjust values in `.env` as needed — defaults work out of the box for local
development. **Never commit `.env`** (already git-ignored); see
`docs/security.md` and `docs/deployment.md` for what each variable does in
production.

### 4. Make sure Ollama has the required model

```bash
ollama pull qwen2.5:3b-instruct-q4_K_M
```

The model itself is never stored in this repository — it's downloaded and
managed entirely by your local Ollama installation.

## Running the API

```bash
python -m uvicorn backend.app.main:app --reload
```

Then check the health endpoint:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status": "ok"}
```

With Ollama running locally, you can also test the conversation flow:

```bash
curl -X POST http://127.0.0.1:8000/api/conversations
curl -X POST http://127.0.0.1:8000/api/chat -H "Content-Type: application/json" -d "{\"conversation_id\": \"<id from above>\", \"message\": \"What services do you offer?\"}"
```

## Running tests

```bash
pytest -q
```

Expected: all tests pass (330+ as of Phase 9; exact count grows with each
phase — see the relevant `docs/*.md` for the count at that phase).

## Frontend widget (local development)

`frontend/widget/brilyx-chatbot.js` is a single-file, dependency-free,
Shadow-DOM embeddable widget — see `docs/frontend-widget.md` for the full
embedding/configuration guide. `frontend/widget/demo.html` is a **local
development harness only** (it simulates a host page with deliberately
hostile CSS to prove Shadow DOM isolation) — it is not meant to be
deployed. To try the widget locally, serve `frontend/widget/` with any
static file server (e.g. `python -m http.server 5500` from inside that
directory) and open `demo.html`; the backend's `CORS_ALLOWED_ORIGINS` must
include whatever origin you serve it from.

## Project structure

```
backend/app/                  FastAPI application entrypoint, config, database, models
backend/app/routers/          API routes: chat, conversations, health
backend/app/ai/               AI provider abstraction, Ollama provider, prompts, orchestrator
backend/app/services/         Conversation/message persistence logic
backend/app/intelligence/     Deterministic rules + AI extraction + state merging (Phase 4)
backend/app/qualification/    Deterministic lead-scoring rules (Phase 5)
backend/app/leads/            Natural lead-capture rules (Phase 6)
backend/app/events/           Business events + owner notifications (Phase 8)
backend/app/rate_limit.py     In-memory rate limiting (Phase 9)
backend/app/security_headers.py  Response security headers + body-size guard (Phase 9)
frontend/widget/              Embeddable chat widget (brilyx-chatbot.js) + local dev harness (demo.html)
knowledge/                    Approved business knowledge base (Markdown)
docs/                         Project documentation (one file per phase, plus security.md/deployment.md)
tests/                        Pytest test suite
data/                         Local SQLite database (git-ignored)
```

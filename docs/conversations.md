# Phase 3 — Conversation & Session Management

## Overview

Phase 3 turns the Phase 2 stateless `/api/chat` endpoint into a proper
conversational session system backed by SQLite. A visitor can now start a
conversation, send multiple messages to it, and the backend maintains and
replays the relevant history to the AI on each turn — without any
embeddings, vector database, or RAG.

```
API / database layer            (backend/app/routers/chat.py, conversations.py)
  ↓ retrieves conversation + recent message history
AIOrchestrator.chat(message, history=[...])   (unchanged from Phase 2)
  ↓
AIProvider → OllamaProvider → local Ollama → qwen2.5:3b-instruct-q4_K_M
```

The AI layer (`backend/app/ai/`) is untouched and remains unaware of
SQLAlchemy or any database concept — it only ever receives a `message`
string and a plain `list[ChatMessage]` history. All conversation/database
logic lives in `backend/app/services/conversation_service.py` and the
routers.

## Data Model

- **Conversation**: `id` (UUID4, primary key — the public `conversation_id`),
  `session_id` (UUID4, unique + indexed), `status` (`active`/`closed`),
  `created_at`, `updated_at` (UTC).
- **Message**: `id` (internal autoincrement, never exposed), `conversation_id`
  (indexed foreign key), `role` (`user`/`assistant`), `content`, `created_at`
  (UTC).

`conversation_id` and `session_id` are deliberately separate UUID4 fields —
`conversation_id` identifies the specific conversation resource (used in
URLs like `GET /api/conversations/{conversation_id}`), while `session_id`
is a distinct opaque identifier returned alongside it. In Phase 3 each
conversation gets its own fresh `session_id`; no database integer ID is
ever exposed publicly.

**Phase 9 update:** `session_id` is now also the conversation's access
token (see `docs/security.md` "Conversation Access Control") — callers
must present it via the `X-Conversation-Token` header to read a
conversation's detail/state/qualification/lead or to close it.
`POST /api/chat` continues to accept `conversation_id` alone, unchanged
from the description below.

Tables are created via `Base.metadata.create_all()` (`database.init_db()`,
called once at app startup) — this only creates tables that don't already
exist, so the existing Phase 0 database and any data in it are left alone.
No Alembic migration tool was introduced; it wasn't needed for an additive,
early-stage schema change like this.

**Known SQLite limitation:** timestamps are always stored and generated as
UTC (`datetime.now(timezone.utc)`), but SQLite's driver drops the `tzinfo`
attribute on read-back — retrieved `created_at`/`updated_at` values are
naive `datetime` objects whose *value* is UTC. This is a standard SQLite
behavior and doesn't affect correctness, only introspection.

## API Endpoints

### `POST /api/conversations`

Creates a new, empty, active conversation.

Response (`201`):
```json
{
  "conversation_id": "036ac49b-e467-43f3-8b2f-744d19013e5a",
  "session_id": "36836e6d-63dd-4672-b82c-8d66025264dd",
  "status": "active"
}
```

### `POST /api/chat`

Request:
```json
{
  "conversation_id": "036ac49b-e467-43f3-8b2f-744d19013e5a",
  "message": "What is your Business package price in Pakistan?"
}
```

Response:
```json
{
  "conversation_id": "036ac49b-e467-43f3-8b2f-744d19013e5a",
  "session_id": "36836e6d-63dd-4672-b82c-8d66025264dd",
  "response": "...",
  "provider": "ollama",
  "model": "qwen2.5:3b-instruct-q4_K_M"
}
```

**`conversation_id` is optional.** If omitted, the backend automatically
creates a new conversation and returns its ID in the response. This was
chosen over requiring a separate `POST /api/conversations` call first,
because it keeps single-shot usage (e.g. a quick test, or a simple client
that doesn't want to manage session setup) as simple as Phase 2's endpoint
was, while still fully supporting persistent multi-turn sessions for any
client that stores and re-sends the returned `conversation_id`. There is no
`history` field in the request anymore — history is entirely
server-managed via `conversation_id`, which is what makes the endpoint
safe against a client injecting fabricated prior "assistant" turns.

**Phase 9:** the response now also always includes `session_id` (see
`docs/security.md`), even when this call auto-created the conversation —
a caller needs it to access that conversation's read/state/qualification/
lead/close endpoints afterward.

Validation:
- Empty or whitespace-only `message` → `422`.
- `message` longer than `CHAT_MAX_MESSAGE_LENGTH` → `422`.
- Unknown `conversation_id` → `404`.
- `conversation_id` referencing a closed conversation → `409` (Ollama is
  **not** called in this case).
- AI provider failure (unreachable/timeout/bad response) → `503` with
  `"Brilyx AI is temporarily unavailable. Please try again shortly."` — no
  stack trace, internal URL, file path, or exception detail is ever
  returned. **If the AI call fails, the user's message is still saved (for
  history/diagnostics), but no assistant message is saved** — there is
  never a fabricated assistant reply in the conversation.
- A database failure returns a generic `500` with no SQL or internal detail
  leaked.

### `GET /api/conversations/{conversation_id}`

Returns the conversation and its full message history, oldest first.

```json
{
  "conversation_id": "...",
  "session_id": "...",
  "status": "active",
  "messages": [
    {"role": "user", "content": "...", "created_at": "..."},
    {"role": "assistant", "content": "...", "created_at": "..."}
  ]
}
```

Unknown `conversation_id` → `404`. No raw SQLAlchemy model or internal
database field (e.g. the message's own autoincrement ID) is exposed.

### `POST /api/conversations/{conversation_id}/close`

Marks a conversation `closed`. Idempotent — closing an already-closed
conversation just returns its current (closed) status rather than erroring.
Unknown `conversation_id` → `404`.

## Message Persistence & History Flow

For every `POST /api/chat` call:

1. Pydantic validates the request (non-blank, within length limit).
2. The conversation is resolved (existing, by ID — or auto-created).
3. The conversation's status is checked; closed → `409`, stop immediately.
4. The most recent messages already in the conversation are fetched
   (bounded by `CHAT_HISTORY_MAX_MESSAGES`, oldest-first).
5. The new user message is persisted.
6. The orchestrator is called with `message=<new message>` and
   `history=<messages fetched in step 4>`.
7. On success, the assistant's reply is persisted and returned.
8. On AI failure, nothing further is persisted, and a controlled `503` is
   returned.

History is fetched *before* the current user message is saved, specifically
so the history list passed to the orchestrator never needs to filter out
the message currently being sent — `AIOrchestrator.chat()` already appends
`message` as the final turn itself. This keeps the "don't duplicate the
current message" requirement structurally guaranteed rather than something
that has to be remembered at each call site.

The system prompt and full business knowledge base are added by
`AIOrchestrator` itself (unchanged from Phase 2) — this layer only ever
supplies `message` and prior `history`; it never touches or trims the
system prompt.

## History Limiting

`CHAT_HISTORY_MAX_MESSAGES` (default `20`) caps how many prior messages are
replayed to the model on each turn — configured via `.env` /
`backend/app/config.py`. This exists because Phase 2 discovered that this
3B local model can lose track of instructions when the context grows too
large; an ever-growing, unbounded history would eventually crowd out (or at
minimum, dilute the model's attention on) the system prompt and knowledge
base within the `num_ctx=8192` window. Phase 3 keeps this simple — a
recent-message cutoff, no summarization — the full transcript remains
available via `GET /api/conversations/{id}` regardless of what's sent to
the model on any given turn.

## Configuration

Added to `backend/app/config.py` / `.env.example`:

```
CHAT_HISTORY_MAX_MESSAGES=20
CHAT_MAX_MESSAGE_LENGTH=4000
```

## What Phase 3 Does Not Include

- No lead capture, lead scoring, or CRM logic.
- No WhatsApp integration, email notifications, or demo backend.
- No frontend/chat widget.
- No authentication — this remains an internal development API.
- No payments or deployment configuration.
- No embeddings, vector database, RAG, or LangChain.
- No Redis, Celery, message queues, WebSockets, or background workers —
  SQLite and synchronous request/response are sufficient at this stage.
- No conversation summarization — only a simple recent-message limit.

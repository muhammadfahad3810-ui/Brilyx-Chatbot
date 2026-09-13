# Phase 5 — Lead Qualification & Scoring

## Purpose

Phase 5 adds a deterministic scoring layer on top of Phase 4's
`ConversationState`: for any conversation, it answers "how qualified is
this visitor based on what they've told us so far?" as a `0-100` score, a
`low`/`medium`/`high` level, and a list of human-readable reasons.

**This is qualification, not lead capture.** There is no contact-detail
storage, no CRM, no email/WhatsApp outreach, and no automated follow-up
here — see docs/conversation-intelligence.md for the same distinction made
about Phase 4. Phase 5 only *scores* what Phase 4 already understood.

```
ConversationState (Phase 4, backend/app/models.py)
  + the conversation's own visitor messages (Phase 3, backend/app/models.py)
  ↓
calculate_qualification(state, user_messages)   (backend/app/qualification/rules.py)
  — pure, deterministic, no LLM call
  ↓
LeadQualificationResult { score, level, reasons }  (backend/app/qualification/models.py)
  ↓
Cached in LeadQualification (backend/app/models.py, one-to-one with Conversation)
  ↓
GET /api/conversations/{id}/qualification   (internal/debug only — never in chat replies)
```

## Why Ollama never touches the score

The scoring arithmetic is plain Python (`+15`, `+20`, ... , `min(100, max(0,
score))`). Ollama continues to do exactly what it did in Phase 4 —
conversational replies and best-effort field extraction — and is never
asked to compute, adjust, or validate a score. This keeps the number
reproducible, auditable, and immune to prompt injection or model drift.

## Scoring Model

| Signal | Weight | Source |
|---|---|---|
| Business identified | +15 | `state.business_type != "unknown"` or a non-empty `business_name` |
| Clear business problem | +20 | a concrete `state.problem_summary` (not vague/too short) or a meaningful stated requirement |
| Specific service identified | +15 | `state.requested_service != "unknown"` |
| Pricing / budget interest | +10 | `state.requested_package` set, or a visitor message matches pricing language |
| Demo requested | +20 | `state.demo_requested is True` |
| Email provided | +10 | a validated `state.email` — **0 today**, see below |
| WhatsApp contact provided | +10 | a validated `state.whatsapp_number`/`whatsapp_contact` — **0 today**, see below |

```
score = business + problem + service + pricing + demo + email + whatsapp
score = min(100, max(0, score))

level = LOW      if score in 0-29
        MEDIUM   if score in 30-59
        HIGH     if score in 60-100
```

Implemented in `backend/app/qualification/rules.py`:
`has_business_identified`, `has_clear_problem`, `has_specific_service`,
`has_pricing_interest`, `has_demo_requested`, `has_email_provided`,
`has_whatsapp_contact_provided`, `level_for_score`, `_clamp`, and the
top-level `calculate_qualification(state, user_messages)`.

## Determinism & No Double Counting

`calculate_qualification(state, user_messages)` is a **pure function**:
the same inputs always produce the same score, level, and reasons — no
randomness, no hidden counters, no incremental `+=` bookkeeping anywhere.

This is also what prevents double counting. Each signal is a boolean
computed fresh from the *current* state on every call — e.g.
`has_business_identified` is `True` for as long as `business_type` stays
`dental_clinic`, no matter how many times the visitor repeats "dental
clinic" across turns, because it evaluates the current value, not "was
this ever mentioned." Re-scoring after every turn (rather than
incrementing a running total) is what makes this safe: see
`tests/test_qualification.py::test_business_type_set_once_does_not_compound_across_recalculations`
and `tests/test_qualification_api.py::test_repeating_the_same_signal_does_not_inflate_the_score`.

## Signal Notes & Mandatory Distinctions

- **Bare "business"** does not set `business_type` (Phase 4 already
  guards this — see docs/conversation-intelligence.md) so it cannot
  trigger the business-identified signal either.
- **Vague problem statements** ("I need help.", "Tell me about AI.", "I
  want something.") never score the problem signal — `has_clear_problem`
  rejects known-vague phrases and anything shorter than
  `MIN_PROBLEM_SUMMARY_LENGTH` (12 chars).
- **WhatsApp AI Agent (service) vs. WhatsApp contact (data)** — asking for
  a WhatsApp AI agent sets `requested_service` and earns the +15 service
  signal, but never implies a captured contact number. The two signals
  are computed by entirely separate functions
  (`has_specific_service` vs. `has_whatsapp_contact_provided`).
- **Business Automation vs. Business pricing tier** — `requested_service
  == "business_automation"` earns +15 alone; it never implies
  `requested_package == "business"`, preserving the exact Phase 4.1 fix
  for this ambiguity.
- **Pricing interest is visitor-driven only.** `has_pricing_interest` is
  only ever given the visitor's own message text (see
  `qualification/service.py::_user_message_texts`, which filters
  `role == "user"`) or the already-sticky `requested_package` field —
  never the assistant's replies. If the assistant mentions a price
  unprompted, that alone never awards the signal.

## Email / WhatsApp Contact Fields

`ConversationState` has no `email` or `whatsapp_number`/`whatsapp_contact`
column yet — that's Phase 6 (lead capture) territory, explicitly out of
scope here. `has_email_provided` / `has_whatsapp_contact_provided` read
these via `getattr(state, "<field>", None)`, so:

- Today, both always return `False` (0 points) for every real
  conversation, because the attribute doesn't exist.
- The moment Phase 6 adds a real `email`/contact field to
  `ConversationState`, these signals activate automatically — no change
  needed in the qualification module.

This is exercised directly in tests via a `SimpleNamespace` stub standing
in for a "future" state that already has those fields
(`tests/test_qualification.py::_future_state`), without adding the fields
to the real Phase 4 model now.

## Persistence

A new table, `lead_qualifications` (one-to-one with `conversations`, via
`LeadQualification` in `backend/app/models.py`), caches the last computed
`score`/`level`/`reasons` for analytics/debugging convenience. It is
**never the source of truth** — `run_qualification()` recomputes it fresh
from `ConversationState` + message history after every chat turn, and the
debug GET endpoint (`get_qualification_or_default()`) also recomputes live
rather than trusting the cache, so a stale or missing row can never produce
a wrong answer.

This is purely additive: it's a brand-new table created by
`Base.metadata.create_all()` on startup, so it appears alongside existing
data without altering `conversations`, `messages`, or `conversation_states`
— no migration tool needed, no risk to the existing database (verified: 5
pre-existing `conversation_states` rows were untouched after adding this
table to an existing `data/brilyx.db`).

## Integration with the Chat Flow

```
POST /api/chat
  ↓ resolve conversation, load history, save user message   (Phase 3)
  ↓ extract intelligence, merge state, persist ConversationState   (Phase 4)
  ↓ calculate_qualification(state, user_messages) → persist LeadQualification   (Phase 5, new)
  ↓ build context, generate AI response, save assistant message   (unchanged)
```

`backend/app/routers/chat.py` calls `run_qualification(db, conversation,
state)` immediately after `run_intelligence()`, inside the same
`try/except SQLAlchemyError` block — a genuine database failure surfaces
as the existing controlled `500`, but a scoring-logic failure inside
`run_qualification` is swallowed internally (mirrors
`intelligence.service.run_intelligence`) so it can never break message
delivery.

## API

### `GET /api/conversations/{conversation_id}/qualification`

Internal/debug endpoint, same convention as
`GET /api/conversations/{id}/state`. Unknown `conversation_id` → `404`.

```json
{
  "conversation_id": "036ac49b-e467-43f3-8b2f-744d19013e5a",
  "score": 60,
  "level": "high",
  "reasons": [
    "Business identified (+15)",
    "Specific service identified (+15)",
    "Pricing interest (+10)"
  ]
}
```

**The score is never exposed through `POST /api/chat` or the assistant's
reply.** The visitor is never told "your lead score is X" or "you are a
high-quality lead," and no internal weight or rule is ever surfaced in
conversation — this is verified by
`tests/test_qualification_api.py::test_chat_response_never_exposes_score_or_level`.

## Security & Validation

- The score is never accepted as client input anywhere — there is no
  request field for it on any endpoint, so it cannot be manipulated by a
  caller.
- Every signal function reads its inputs via `getattr(state, "<field>",
  default)`, so a missing field, an unrecognized enum string, or a bare
  stub object never raises — it just scores 0 for that signal.
- A failure inside `calculate_qualification` during a chat turn is caught
  by `run_qualification` and never propagates to break message delivery.

## Performance

Scoring is plain in-memory Python over already-loaded data (the current
`ConversationState` row and the conversation's own messages) — no
additional LLM call, no external API, no new dependency.

## What Phase 5 Does Not Include

- No lead capture, contact storage, or CRM.
- No email notifications or WhatsApp outreach.
- No frontend lead forms.
- No automated follow-ups.
- No deployment changes.
- The score/level/reasons are never shown to visitors in the chat
  response — internal business intelligence only.

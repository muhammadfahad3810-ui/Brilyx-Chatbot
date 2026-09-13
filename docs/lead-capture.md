# Phase 6 — Natural Lead Capture & Lead Storage

## Purpose

Phase 6 converts a qualified/interested visitor into a structured `Lead`
record — a business snapshot the sales side of Brilyx can eventually act
on. It does **not** send anything, notify anyone, or integrate with any
external system. It only captures and stores.

The conversational principle stays exactly as before:

```
HELP FIRST → UNDERSTAND → RECOMMEND → NEXT STEP → CAPTURE LEAD NATURALLY
```

The assistant keeps answering questions first. Contact information is
never demanded up front, never forced via a rigid form, and never invented
— it's only recorded when the visitor actually states it, in whatever
order they choose to give it.

```
Visitor message (POST /api/chat)
  ↓
ConversationState (Phase 4)            — business/intent understanding
  ↓
LeadQualification (Phase 5)            — deterministic 0-100 score
  ↓
extract_contact_fields(message)        (backend/app/leads/rules.py) — deterministic, no LLM
  ↓
Lead (backend/app/models.py)           — one row per conversation, created/updated
  ↓
GET /api/conversations/{id}/lead       — internal/debug only, never in chat replies
```

## Relationship to ConversationState and Phase 5

- **ConversationState remains the only source of business/intelligence
  truth.** `Lead` never re-derives `business_type`, `business_name`,
  `country`, `currency`, `requested_service`, `requested_package`,
  `problem_summary`, or `requirements` — it copies them verbatim from the
  conversation's `ConversationState` on every turn
  (`leads/service.py::_sync_lead_from_state`). If Phase 4 hasn't
  established a fact, `Lead` doesn't either.
- **Phase 5 remains the only source of scoring truth.** `lead_score` and
  `lead_level` are copied directly from that turn's persisted
  `LeadQualification` row — never recomputed independently, never
  accepted from a client, never touched by Ollama.
- **Phase 6 adds exactly one new thing ConversationState doesn't have:
  actual visitor contact information** (`name`, `email`, `whatsapp`,
  `website`). These live only on `Lead`, extracted by a dedicated
  deterministic module (`backend/app/leads/rules.py`) that never touches
  `ConversationState`'s schema (see "Why contact fields aren't on
  ConversationState" below).

## The `Lead` Model

One row per `Conversation` at most (`conversation_id` is unique, not the
primary key — a conversation may have no lead at all).

| Field | Source |
|---|---|
| `id`, `conversation_id`, `created_at`, `updated_at` | generated / FK |
| `name` | visitor message, deterministic extraction |
| `business_name`, `business_type`, `country`, `preferred_currency` | copied from `ConversationState` |
| `email`, `whatsapp`, `website` | visitor message, deterministic extraction |
| `requested_service`, `problem_summary`, `requirements`, `estimated_package` | copied from `ConversationState` |
| `lead_score`, `lead_level` | copied from Phase 5 `LeadQualification` |
| `lead_status` | deterministic rule (see below) |
| `source` | always `"website_chatbot"` |
| `notes` | short, deterministic, system-generated strings only |

`lead_status` uses exactly these values: `new`, `contacted`,
`demo_requested`, `demo_sent`, `qualified`, `converted`, `lost`,
`do_not_contact`. Phase 6 only ever *sets* `new`, `qualified`, or
`demo_requested` — see below.

## Contact Extraction (`backend/app/leads/rules.py`)

All deterministic, regex-based, no LLM call (this is a stated Phase 6
performance requirement). Every function is pure and only ever called with
**visitor** message text.

- **Email** — a conservative `local@domain.tld` regex. Any syntactically
  valid address found in a visitor message is accepted; nothing is ever
  read from the assistant's reply, which structurally rules out "the
  assistant's own example email got captured."
- **WhatsApp** — only captured when the message both (a) contains the word
  "whatsapp" and (b) contains a phone-number-shaped token (7+ digits).
  "I want a WhatsApp AI agent" has no digits, so it never matches;
  "My WhatsApp is +92 300 1234567" has both, so it does. A bare phone
  number with no "whatsapp" mention is never captured — there's no general
  "phone" field, matching the spec's explicit "do not assume every phone
  number is WhatsApp" rule. Normalization only strips whitespace/dashes;
  no country code is ever invented or altered.
- **Website** — `https://…`, `www.…`, or a bare `domain.tld` are all
  recognized. Extraction runs on the message with any matched email
  removed first, so `test@example.com`'s domain is never mistaken for a
  separate website.
- **Name** — five explicit, ordered patterns ("my name is X", "you can
  call me X", "call me X", "I'm X", "I am X"). The last two ("I'm X") are
  the most ambiguous, so a captured word from a small stopword list
  (`looking`, `interested`, `trying`, ...) is rejected rather than
  guessed. Multi-word captures ("my name is Ahmed Khan") stop at the first
  connector word (`and`, `my`, `email`, ...) so a sentence like *"My name
  is Ahmed and my email is ..."* correctly yields `"Ahmed"`, not `"Ahmed
  And My"` — this was caught and fixed during the real-Ollama regression
  for this phase (see below).

### Why contact fields aren't on `ConversationState`

Phase 6 spec section 31 explicitly asks to avoid modifying
`ConversationState`'s schema unless absolutely necessary. Contact fields
are fundamentally different from conversation *understanding* — they're
literally new capture, not a byproduct of `ConversationState`'s intent
extraction — so they were added as new `Lead` columns and a separate,
independent extraction module instead. This is also why the existing
`data/brilyx.db` was verified untouched after this change: `leads` is a
brand-new table (added the same additive way `lead_qualifications` was in
Phase 5), and no existing table's schema changed.

## Lead Creation Trigger (section 22)

A `Lead` row is only created — never for every visitor — when at least one
of these is true on the current or any prior turn:

- the visitor's message contains an email
- the visitor's message contains a WhatsApp contact
- `ConversationState.demo_requested` is true
- `ConversationState.human_handoff_requested` is true (also covers
  "clearly asks to be contacted" — Phase 4's human-handoff patterns
  already catch phrasing like "connect me with your team"; Phase 6
  deliberately doesn't duplicate that extraction, per section 4)
- the Phase 5 qualification score is `>= 60` (HIGH) — the "meaningful
  commercial intent + business context" case

A bare "Hi" or "What services do you offer?" never creates a row
(`backend/app/leads/rules.py::should_create_lead`,
`tests/test_leads_api.py::test_no_lead_for_simple_greeting` /
`test_no_lead_for_generic_services_question`).

**Backfill on creation.** A visitor might state their name several turns
before anything meets the trigger above. To avoid losing that,
`run_lead_capture` — the first time a `Lead` is created for a
conversation — walks every prior visitor message (chronological order,
same as `Message.id` ordering) and merges each one's extracted contact
fields, rather than only looking at the triggering message. After
creation, each subsequent turn only processes that turn's message (cheap,
no full-history rescan).

## Deduplication (section 27)

`conversation_id` is the dedup key (`Lead.conversation_id` is a unique
column). `run_lead_capture` always checks `conversation.lead` first — if
it exists, it's updated in place; a new row is only ever constructed on
the `None` branch. Verified directly against the database after the real
regression run: one row per `conversation_id`, no duplicates, across
multiple manually-run scenarios.

## Corrections (section 26)

Contact fields use the same sticky-with-correction philosophy as Phase
4's `business_type`/`business_name` (`intelligence/rules.py::
is_explicit_correction`, reused directly rather than reimplemented): an
empty extraction never clears an existing value, and a *different* value
only overwrites the current one when the message contains an explicit
correction phrase ("actually", "I meant", "instead", ...). "By the way my
email is c@..." — with no correction marker — leaves the previously
captured email untouched.

## Lead Status Rules (section 28)

```
if current_status == "do_not_contact": stay "do_not_contact"   (never overridden)
elif demo_requested:                    "demo_requested"
elif human_handoff_requested:           "qualified"
elif lead_score >= 60:                  "qualified"
else:                                   "new"
```

`contacted`, `demo_sent`, `converted`, and `lost` are never set by this
phase — those describe actions (a human reaching out, a demo actually
being sent, a deal closing) that Phase 6 explicitly does not perform.

**`do_not_contact` note (section 30):** the current architecture (Phases
1-6) has no mechanism that ever sets a lead to `do_not_contact` — no
explicit-opt-out detection was built, per the instruction not to invent
that workflow now. `compute_lead_status` does, however, treat an existing
`do_not_contact` value as sticky and never recomputes past it, so that a
future phase can safely introduce opt-out detection without this
function silently overwriting it. Building the detection itself is
deferred to Phase 7+.

## Notes (section 21)

`compute_notes` recomputes a short, fixed list from `ConversationState`
every time (same "recalculate, don't accumulate" pattern as Phase 5's
qualification) — never free text, never assistant-generated:

- `"Captured from Brilyx website chatbot."` (always present)
- `"Visitor requested a demo."` (if `demo_requested`)
- `"Human handoff requested."` (if `human_handoff_requested`)

## Chat Flow Integration

```
POST /api/chat
  ↓ resolve conversation, load history, save user message      (Phase 3)
  ↓ extract intelligence, merge state, persist ConversationState (Phase 4)
  ↓ calculate + persist LeadQualification                        (Phase 5)
  ↓ run_lead_capture(state, qualification, request.message)      (Phase 6, new)
  ↓ build context, generate AI response, save assistant message  (unchanged)
```

`backend/app/routers/chat.py` calls `run_lead_capture` right after
`run_qualification`, inside the same `try/except SQLAlchemyError` block. A
scoring/extraction-logic failure inside it is swallowed internally
(mirrors `run_intelligence`/`run_qualification`) so it can never break
message delivery; only a genuine database failure surfaces as the
existing controlled `500`.

**Contact extraction is visitor-only by construction**: `run_lead_capture`
is only ever given `request.message` (the visitor's text) and, for
backfill, `conversation.messages` filtered to `role == "user"`. The
assistant's generated reply is never passed to `extract_contact_fields` —
verified by
`tests/test_leads_api.py::test_assistant_generated_example_email_is_not_captured`
and `test_assistant_generated_phone_number_is_not_captured`.

## API

### `GET /api/conversations/{conversation_id}/lead`

Internal/debug endpoint, same convention as `.../state` and
`.../qualification`. Unknown conversation → `404`. No lead captured yet →
`404` with `"No lead has been captured for this conversation yet."`.
There is no lookup by email and no "list all leads" endpoint.

```json
{
  "conversation_id": "...",
  "name": "Ahmed",
  "business_name": null,
  "business_type": "dental_clinic",
  "country": null,
  "preferred_currency": null,
  "email": "ahmed@example.com",
  "whatsapp": null,
  "website": "https://example.com",
  "requested_service": "whatsapp_ai_agent",
  "problem_summary": null,
  "requirements": [],
  "estimated_package": null,
  "lead_score": 80,
  "lead_level": "high",
  "lead_status": "demo_requested",
  "source": "website_chatbot",
  "notes": ["Captured from Brilyx website chatbot.", "Visitor requested a demo."],
  "created_at": "...",
  "updated_at": "..."
}
```

**Never exposed through `POST /api/chat`.** `lead_score`, `lead_level`,
`lead_status`, and internal notes are never included in the visitor-facing
response — verified by
`test_lead_endpoint_never_exposes_internal_fields_to_chat_response`.

## Security

- **There is no `POST /api/leads` or any other client-writable lead
  endpoint.** Lead creation/update only ever happens from inside the
  trusted `/api/chat` pipeline. `test_no_public_lead_creation_endpoint_exists`
  confirms no such route is registered.
- `ChatRequest` has no `lead_score`/`lead_level`/`lead_status` field, so
  extra JSON keys with those names sent to `/api/chat` are simply ignored
  by Pydantic — they can never become authoritative
  (`test_client_supplied_score_and_status_in_chat_request_are_ignored`).
- Every rule function reads inputs defensively (`getattr(state, "<field>",
  default)`), so a missing field or a bare stub object never crashes lead
  capture — it just contributes nothing for that signal.

## Privacy & Data Minimization

- No passwords, payment information, identity documents, or auth tokens
  are ever captured — there's no extraction logic for any of these.
- `Lead` stores a **snapshot**, not the conversation: the full transcript
  already exists separately via `GET /api/conversations/{id}`, so `Lead`
  never duplicates message content beyond the four short contact fields
  and the ConversationState copy.
- All string fields have explicit length limits, both at the Pydantic
  validation boundary (`leads/models.py::ExtractedContactFields`) and the
  database column level (`String(120)` for name/business_name,
  `String(254)` for email, `String(32)` for whatsapp, `String(300)` for
  website); `requirements` is capped at 10 items (reusing the same cap
  ConversationState already applies).
- `notes` intentionally stores only short, fixed, system-generated strings
  — never a raw echo of what the visitor typed — so it can't accumulate
  arbitrary free text over time.

## Performance

No additional LLM call is made for lead capture — `extract_contact_fields`
is plain regex over already-in-memory message text. No external API, no
paid service, no network call.

## What Phase 6 Does Not Include

- No email notifications, WhatsApp outreach, or CRM integration.
- No automated follow-ups or demo delivery.
- No frontend chatbot widget or lead capture forms.
- No deployment changes, no new paid dependency, no replacement of SQLite.
- No `contacted`/`demo_sent`/`converted`/`lost` status transitions — those
  describe real-world actions this phase never performs.
- No do-not-contact *detection* (only safe preservation of that status if
  it's ever set some other way) — deferred to a later phase, per section
  30.

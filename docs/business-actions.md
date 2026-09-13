# Phase 8 — Business Actions & Notifications

## Purpose

Phase 8 closes the loop the earlier phases built toward:

```
Visitor → Conversation → Intelligence → Qualification → Lead → Business Event → Notification
```

It answers one question: *"Something meaningful just happened — does the
Brilyx owner need to know?"* It does **not** talk to the visitor, the lead,
or any external party except the Brilyx owner's own inbox. See "What Phase
8 Does Not Do" below — the boundary here is deliberately narrow.

## Architecture

```
backend/app/events/
  models.py         EventType / EventStatus enums, PendingEvent (pure data)
  rules.py          evaluate_events() — pure, deterministic, no DB, no LLM
  notifications.py  NotificationProvider (ABC) + SMTPNotificationProvider,
                     NotificationService, plain-text email rendering
  service.py        DB-facing: dedup persistence, notification attempt,
                     get_notification_service() FastAPI dependency

backend/app/models.py
  BusinessEvent      new ORM table (alongside Conversation/Message/
                     ConversationState/LeadQualification/Lead)
```

This mirrors the exact structure already used for qualification
(`backend/app/qualification/`) and lead capture (`backend/app/leads/`):
domain models + pure rules + DB-facing service, kept in a dedicated
package rather than folded into an existing one.

**The AI never triggers a notification.** `send_email()` is only ever
called from `events/service.py`, which only ever runs after
`ConversationState`, `LeadQualification`, and `Lead` have already been
computed by their own (Phase 4/5/6, unmodified) pipelines. The model has
no code path to reach the notification layer directly — it can only
produce a conversational reply.

## Business Event Types

| `event_type` | Fires when | Scope |
|---|---|---|
| `lead_created` | A `Lead` row exists for this conversation (checked every turn; the dedupe key makes it a one-time event) | per lead |
| `high_value_lead` | `LeadQualification.level == "high"` (score ≥ 60 — Phase 5's own threshold, not re-implemented here) | per lead |
| `demo_requested` | `ConversationState.demo_requested is True` | per conversation |
| `human_handoff_requested` | `ConversationState.human_handoff_requested is True` | per conversation |

No other event types exist. `evaluate_events()`
(`backend/app/events/rules.py`) is a pure function — no DB access, no
side effects — that re-derives this list from the current
`ConversationState`/`LeadQualification`/`Lead` on every turn; it is
*expected* to keep returning the same event for an already-satisfied
condition (e.g. `demo_requested` stays `True` for the rest of the
conversation) — deduplication is not this function's job.

## Event Status Lifecycle

`pending → processing → completed` or `pending → processing → failed`, with
one exception: `lead_created` stays `pending` forever when
`NOTIFY_ON_LEAD_CREATED` is off (the default) — it's genuinely never
attempted, not attempted-and-failed, so `pending` is the honest terminal
state for it. There is no background worker in this architecture (Phase 8
deliberately doesn't add a queue — see section 18 of the spec this phase
implements), so every notification attempt happens synchronously, inline,
during the `/api/chat` request that created the event.

## Deduplication / Idempotency

`BusinessEvent.dedupe_key` is a **unique** database column. Each event
type derives its key deterministically:

- `lead_created:{lead_id}`
- `high_value_lead:{lead_id}`
- `demo_requested:{conversation_id}`
- `human_handoff_requested:{conversation_id}`

`evaluate_and_persist_events()` (`backend/app/events/service.py`) always
checks for an existing row with that `dedupe_key` before inserting; if one
exists, it's skipped — no new row, no new notification attempt. A race
between two concurrent requests producing the same key is handled by
catching the resulting `IntegrityError` and treating it as "someone else
already recorded this," not an error.

This is what guarantees: a visitor saying "I want a demo," then "my email
is...", then "I still want the demo" produces **exactly one**
`demo_requested` row and **at most one** notification — not three. The
same logic protects `human_handoff_requested`, and protects
`high_value_lead` from re-firing every time the score climbs further
after first crossing 60 (60 → 70 → 90 all map to the same
`high_value_lead:{lead_id}` key).

## Notification Architecture

```
BusinessEvent → NotificationService.notify(event_type, payload, created_at)
              → NotificationProvider.send(subject, body)
              → SMTPNotificationProvider (stdlib smtplib)
```

`NotificationProvider` is an abstract base class
(mirrors `backend.app.ai.base.AIProvider`'s exact pattern) so a different
transport could be added later without touching `NotificationService` or
the event-evaluation layer above it. Only one implementation exists today:
`SMTPNotificationProvider`, built entirely on Python's standard library
(`smtplib` + `email.message.EmailMessage`) — no third-party email SDK, no
paid service.

`get_notification_service()` is a FastAPI dependency
(`Depends(get_notification_service)` in `routers/chat.py`), exactly like
`get_orchestrator()` — tests override it with a fake provider the same way
existing tests override the AI orchestrator.

## SMTP Configuration

New settings in `backend/app/config.py` / `.env.example` (names chosen to
match the existing `OLLAMA_*`/`CHAT_*` style):

```
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=
SMTP_USE_TLS=true
OWNER_NOTIFICATION_EMAIL=
NOTIFY_ON_LEAD_CREATED=false
```

If `SMTP_HOST` or `OWNER_NOTIFICATION_EMAIL` is missing,
`Settings.notifications_configured` is `False`,
`build_notification_provider()` returns `None`, and
`NotificationService.notify()` always raises `NotificationError("Notifications
are not configured")` — caught by `events/service.py` and recorded as a
`failed` event with that exact (harmless) message. **The application stays
fully usable either way** — this is not a startup requirement, just a
silently-degraded feature.

### Gmail / app-password note

If Gmail's SMTP (`smtp.gmail.com:587`) is used as `SMTP_HOST`, a normal
Google account password will **not** work — Gmail rejects plain-password
SMTP login for security reasons. `SMTP_PASSWORD` must be a Google
**App Password** (a 16-character code generated specifically for this
purpose under the Google Account's security settings, available when
2-Step Verification is enabled) or an equivalent supported
application-specific credential. This project implements no mechanism to
bypass or work around Google's authentication requirements — if an app
password isn't set up, SMTP login simply fails and the failure is recorded
exactly like any other SMTP failure (see below).

## Notification Content

Plain text only — never HTML (Phase 8 spec section 20: visitor-provided
fields like `name`/`business_name`/`requirements` are untrusted, and plain
text has no markup to inject into). Built by
`events/notifications.py::render_event_email()` from the event's own
`payload` JSON: whichever of name, business name, business type, country,
requested service, requested package, problem summary, requirements,
email, WhatsApp, website, lead score/level/status, conversation ID, and
timestamp are actually available (`unknown`/empty fields are silently
omitted, never shown as blanks).

**Subject lines are always a fixed string per event type** (e.g. "Demo
Requested — Brilyx") — visitor-controlled text only ever appears in the
body, never in a header, which eliminates any header-injection risk from
a visitor typing something like `Bcc: attacker@example.com` into their
name.

Event-specific wording:
- `demo_requested` emails explicitly state *"No demo has been created or
  sent — this is only a notification of the request."*
- `human_handoff_requested` emails explicitly state *"No one has been
  notified of a response yet — this is only a notification of the
  request."*

Neither email, nor any other part of this phase, ever claims an action was
taken that didn't happen — matching the same truthfulness rule
`BRILYX_CORE_PROMPT` already enforces for the visitor-facing chat.

`lead_created` notifications are **off by default**
(`NOTIFY_ON_LEAD_CREATED=false`) — every lead would otherwise generate an
email, which is noisy. The event is still always persisted (for the audit
trail), just not emailed unless explicitly turned on.

## Failure Handling

- A notification failure **never** raises out of `/api/chat` — only a
  genuine `db.commit()` failure while persisting the event row itself can
  propagate (and does, via the same `SQLAlchemyError` handling every other
  step in the chat pipeline already uses).
- `SMTPNotificationProvider.send()` catches `smtplib.SMTPAuthenticationError`
  specifically (message: `"SMTP authentication failed"`, never the raw
  server response, which some SMTP servers echo the attempted username
  back in) and all other `smtplib`/`OSError`/`TimeoutError` failures
  generically (message: `"SMTP delivery failed (<ExceptionClassName>)"`).
  Neither ever includes `str(exc)` verbatim, and neither can ever contain
  `SMTP_PASSWORD` — verified directly against a real, intentionally
  misconfigured SMTP endpoint during this phase's manual testing (see
  final report).
- No retry loop, no queue: one attempt per event, inline, during the
  request that created it. A `failed` event is a permanent record, not a
  "try again later" marker — there's no worker in this architecture to
  ever pick it back up. This is a deliberate, documented limitation (see
  "Future Phase 9 Considerations").

## Privacy / Data Minimization

- `BusinessEvent.payload` is a bounded, explicit field list
  (`events/rules.py::build_event_payload()`) — never a full conversation
  dump, never raw AI output, never a secret. `requirements` is capped at
  10 items (same bound `Lead.requirements` already enforces).
- `error_message` is capped at 500 characters at the column level and, per
  above, is always self-authored text — never a raw third-party exception
  string that might embed transport internals.
- No SMTP credential, database path, or stack trace is ever exposed via
  `/api/chat` or any other endpoint — verified by
  `tests/test_business_events_api.py::test_chat_response_never_exposes_event_or_smtp_details`
  and the real misconfigured-SMTP run described in the final report.

## API Surface

**No new public endpoint was added.** There is no way for a visitor (or
the frontend widget) to list events, create one manually, trigger a
notification, or alter a lead's score/status/level — business events are
entirely backend-decided, evaluated only from inside the trusted
`/api/chat` pipeline. `tests/test_business_events_api.py::test_no_public_business_event_endpoint_exists`
confirms `/api/events`, `/api/business-events`, and `/api/notifications`
all 404/405.

## Chat Pipeline Integration

```
POST /api/chat
  ↓ resolve conversation, load history, save user message      (Phase 3)
  ↓ extract intelligence, merge state, persist ConversationState (Phase 4)
  ↓ calculate + persist LeadQualification                        (Phase 5)
  ↓ run_lead_capture(...)                                        (Phase 6)
  ↓ evaluate_and_persist_events(...)                              (Phase 8, new)
  ↓ build context, generate AI response, save assistant message  (unchanged)
```

`backend/app/routers/chat.py` calls `evaluate_and_persist_events` right
after `run_lead_capture`, inside the same `try/except SQLAlchemyError`
block every other pipeline step already uses — no new transaction
boundary, no change to the existing chat lifecycle beyond this one
addition.

## Database

`business_events` is a brand-new table, added the same additive way
`lead_qualifications` and `leads` were in earlier phases:
`Base.metadata.create_all()` only creates tables that don't already exist,
so no existing `conversations`/`messages`/`conversation_states`/
`lead_qualifications`/`leads` row was touched. Verified directly against
the project's existing `data/brilyx.db` (36 pre-existing
`conversation_states` rows and 3 pre-existing `leads` rows both intact
after this phase's schema addition, and after every manual test run
described below).

## What Phase 8 Does NOT Do

- Does not send WhatsApp messages, of any kind, to anyone.
- Does not contact the lead/visitor by email or any other channel — the
  **only** outbound notification in this phase goes to the Brilyx owner's
  own configured inbox (`OWNER_NOTIFICATION_EMAIL`). The visitor initiated
  the conversation; nothing here initiates contact with them.
- Does not integrate a CRM.
- Does not send automated or scheduled follow-ups (no "2-day follow-up,"
  no cold outreach, no marketing).
- Does not deliver a demo — `demo_requested` means exactly "the visitor
  asked for one," never "a demo was sent."
- Does not book appointments or process payments.
- Does not deploy anything, add authentication, redesign the frontend, or
  add a new/cloud AI provider.
- Does not change Phase 5 scoring rules, Phase 6 extraction/capture rules,
  or Phase 4/4.1.1 prompt/intelligence behavior.
- Does not add a message queue or background worker — every notification
  attempt is synchronous and one-shot.

## Future Phase 9 Considerations (not implemented)

- A background retry mechanism for `failed` notifications (would require a
  worker/queue, explicitly out of scope here).
- An internal/authenticated dashboard to browse `BusinessEvent` history
  (no such endpoint exists yet — see "API Surface" above).
- Actual outbound contact to the lead (email/WhatsApp), once a real
  business decision is made about consent, timing, and channel — never to
  be added silently.

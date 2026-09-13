# Phase 7 — Brilyx Website Chatbot Widget

## Purpose

Phase 7 builds the embeddable browser widget visitors will use on
www.brilyx.com. It is a **presentation/client layer only** — it calls the
existing FastAPI backend (Phases 3-6) and renders whatever comes back. It
contains zero business logic: no pricing, no qualification rules, no lead
rules, no conversation intelligence. The backend remains the sole source
of truth.

```
Host page (e.g. www.brilyx.com)
  ↓ <script src=".../brilyx-chatbot.js" data-api-base-url="...">
frontend/widget/brilyx-chatbot.js   — single file, Shadow DOM, no dependencies
  ↓ fetch()
POST /api/conversations · POST /api/chat · GET /api/conversations/{id} · POST /api/conversations/{id}/close
  (backend/app/routers/{conversations,chat}.py — unchanged Phase 3-6 contract)
```

## Architecture

- **`frontend/widget/brilyx-chatbot.js`** — the entire widget. An IIFE that
  mounts a single `<div id="brilyx-chatbot-host">` into `document.body`
  and attaches an **open Shadow DOM** to it. All widget markup and CSS
  live inside that shadow root.
- **`frontend/widget/demo.html`** — a local host-page stand-in for manual
  testing (see "Local Development" below). It deliberately styles every
  `button`/`input`/`textarea`/`div` on the page with ugly, colliding rules
  so that a clean-looking widget proves Shadow DOM isolation is working.
- No build step, no bundler, no framework, no npm dependency. Plain HTML
  (built via `document.createElement`), CSS (a template string injected
  as a `<style>` inside the shadow root), and vanilla JS.

### Why Shadow DOM, and why no build step

The spec asked for the widget to survive being dropped into an arbitrary,
un-controlled host page (section 24, "Host Page Isolation") and to be
embeddable as "one script include" (section 23). An open Shadow DOM gives
both for free: host-page CSS selectors (`button {}`, `div {}`, etc.) never
penetrate the shadow boundary in either direction, and there's nothing to
build or ship besides the one `.js` file. A framework (React, Vue, etc.)
would add a real dependency and a build step for a widget with five
buttons, a message list, and a text box — not justified here (section 46).

## Files

| File | Purpose |
|---|---|
| `frontend/widget/brilyx-chatbot.js` | The widget (markup, styles, logic, API calls) |
| `frontend/widget/demo.html` | Local test harness simulating a host page |
| `backend/app/config.py` | Added `CORS_ALLOWED_ORIGINS` setting (see CORS below) |
| `backend/app/main.py` | Added `CORSMiddleware` registration |
| `.env.example` | Documented the new `CORS_ALLOWED_ORIGINS` variable |

No other backend file was changed. No Phase 4/5/6 logic was modified.

## Embedding

```html
<script
  src="https://<your-static-host>/brilyx-chatbot.js"
  data-api-base-url="https://api.brilyx.com"
></script>
```

This project doesn't invent or assume a production URL for either the
script or the API — both are supplied by whoever deploys them (Phase 7 is
explicitly not a deployment phase). Place the tag anywhere in the page;
if `document.body` doesn't exist yet (e.g. the tag is in `<head>` without
`defer`), the widget waits for `DOMContentLoaded` before mounting.

### Configuration (all via `data-*` attributes on the `<script>` tag)

| Attribute | Default | Purpose |
|---|---|---|
| `data-api-base-url` | `http://127.0.0.1:8000` | Backend origin. **Required in production** — see below. |
| `data-title` | `Brilyx AI` | Header title / accessible names |
| `data-subtitle` | `AI & Automation Assistant` | Header subtitle |
| `data-max-message-length` | `4000` | Client-side textarea cap — keep in sync with backend `CHAT_MAX_MESSAGE_LENGTH` |
| `data-dev-mode` | `false` | `"true"` enables `console.log` diagnostics |

No secret ever belongs in these attributes or anywhere in this file —
there is no Ollama, database, or SMTP credential the frontend needs or
touches (section 35).

## Local Development

1. Start the backend as usual (see `docs/conversations.md` /
   `docs/ai-engine.md`): `uvicorn backend.app.main:app --reload`
   (defaults to `http://127.0.0.1:8000`).
2. Serve `frontend/widget/` as static files from a *different* origin than
   the backend, so the browser actually exercises CORS the way production
   will (e.g. `python -m http.server 5500` from inside `frontend/widget/`,
   or the VS Code "Live Server" extension on port 5500).
3. Open `http://127.0.0.1:5500/demo.html`. The script tag at the bottom
   already points at `http://127.0.0.1:8000` — edit its
   `data-api-base-url` if your backend runs elsewhere.
4. Make sure the backend's `CORS_ALLOWED_ORIGINS` includes whatever origin
   you're serving the demo page from (the shipped default already covers
   `http://127.0.0.1:5500` and `:8080`, both with `127.0.0.1` and
   `localhost`).

## Conversation Lifecycle

```
Visitor opens widget
  → static welcome message shown (client-side only, no backend call yet)
  → quick actions shown
Visitor sends a message (typed or a quick action)
  → if no conversation_id yet: POST /api/conversations, store the id
  → POST /api/chat { conversation_id, message }
  → assistant reply appended; quick actions hidden from here on
```

A backend conversation is created **lazily**, on the first real message —
not merely because the widget was opened. Opening and closing the widget
without ever typing anything creates no database row, consistent with
Phase 6's "don't create records for zero-intent visitors" philosophy.

### Session persistence (section 15)

`conversation_id` and, as of Phase 9, its access token (`session_id` —
see `docs/security.md` "Conversation Access Control") are kept in
**`sessionStorage`** (keys `brilyx_chatbot_conversation_id` and
`brilyx_chatbot_session_token`) — nothing else. No message content, no
qualification data, no lead data, no UI state ever touches browser
storage. `sessionStorage` was chosen over `localStorage` because a chat
session tied to one browser tab, cleared when that tab closes, is the
right default for a marketing-site assistant; nothing here calls for
surviving a full browser restart.

The token is sent as an `X-Conversation-Token` header on the two calls
that need it: rehydrating history (`GET /api/conversations/{id}`) and
closing a conversation (`POST /api/conversations/{id}/close`).
`POST /api/chat` itself does not require it (see docs/security.md for why
that's a deliberate scope boundary) but its response always includes
`session_id` too, so the token stays available even if the conversation
was created via `/api/chat`'s auto-create path rather than an explicit
`POST /api/conversations` call.

**Phase 9 CORS note:** `X-Conversation-Token` is a custom header, so the
browser sends a CORS preflight before the real request — the backend's
`allow_headers` list must include it (`backend/app/main.py`), or the
browser blocks the request entirely with an opaque `TypeError: Failed to
fetch` and zero server-side trace. This was caught via a real
cross-origin browser test during Phase 9, not code review.

### Minimize / reopen (section 16)

Minimizing hides the panel via `hidden` — the DOM (and every rendered
message) stays exactly as it was. Reopening just un-hides it. No network
call, no new conversation, and focus moves back into the message input.

### Reload / new tab in the same session (section 15/16)

If the page is reloaded (or the widget script re-runs) while
`sessionStorage` still has a `conversation_id`, the widget calls
`GET /api/conversations/{id}` and re-renders the returned message history
instead of showing the welcome message — the backend is the source of
truth for content, browser storage is just a pointer to it. If that fetch
404s (e.g. a stale ID against a reset dev database), storage is cleared
and the widget falls back to a fresh welcome state rather than erroring.

### New conversation (sections 17, 31)

A "+" icon button in the header. If the current conversation has any
visitor messages, an **in-widget banner** (never `confirm()`/`alert()`)
asks to confirm; "Start new" then:
1. best-effort `POST /api/conversations/{id}/close` on the *previous*
   conversation (its data — messages, `ConversationState`, qualification,
   `Lead` — is never deleted, only marked closed, exactly like the
   backend's own close semantics),
2. clears `sessionStorage` and the visible message list,
3. shows the welcome message + quick actions again.

### Closed-conversation handling (section 18)

If any `/api/chat` call returns `409` (backend: conversation already
closed), the widget marks itself closed locally, shows an inline banner
("This conversation has ended...") with a "New conversation" action, and
stops sending further messages to that conversation — it never retries
automatically or loops.

## Quick Actions (section 9)

Five buttons, each sending a natural-language visitor message through the
normal `/api/chat` flow — there are no hard-coded canned replies:

| Button | Message sent |
|---|---|
| AI Chatbot | "I'm interested in an AI chatbot for my website." |
| WhatsApp AI | "I'm interested in a WhatsApp AI agent." |
| Business Automation | "I'm interested in business automation." |
| Website/Software | "I'm interested in website or software development." |
| Not sure what I need | "I'm not sure what I need. Can you help me figure it out?" |

They disappear after the first real exchange (once there's an assistant
reply beyond the static welcome message) and reappear only after "New
conversation."

## Security / XSS Protection (sections 19-20)

All chat text — assistant output *and* visitor input reflected back into
the DOM — is treated as untrusted. The rendering function
(`renderFormattedText`) never uses `innerHTML` with any of it: it builds
DOM nodes directly (`document.createTextNode`, `document.createElement`),
supporting only a tiny whitelisted subset (line breaks and `**bold**`,
turned into real `<br>`/`<strong>` nodes). `<script>`, `<img
onerror=...>`, and `javascript:` strings all render as inert, visible
plain text — verified live against a running Ollama backend (see
"Real End-to-End Test" below), where a payload like
`<img src=x onerror=alert(1)>` appeared as literal text in the message
bubble with no alert firing and no `<img>` element created.

The **only** `innerHTML` assignments anywhere in the file are the
hand-authored, static SVG icon strings (chat bubble, ×, +, send arrow) —
fixed content this file itself defines, never anything from the network
or from a visitor.

## CORS (section 22)

The backend had no CORS configuration before this phase (it never needed
one — Phases 0-6 only had automated `TestClient`/`pytest` callers). Phase
7 adds the **minimum necessary**, explicit configuration:

```python
# backend/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
```

- `allow_origins` is always an **explicit list** from
  `CORS_ALLOWED_ORIGINS` (new env var, comma-separated) — never `"*"`.
- `allow_credentials=False`: the widget never sends cookies or
  `Authorization` headers; `conversation_id` travels in the JSON body, so
  there's no reason to allow credentialed cross-origin requests, and doing
  so alongside a wildcard origin is exactly the combination FastAPI/browsers
  forbid for good reason.
- The shipped default only covers common local static-file dev ports
  (`127.0.0.1`/`localhost` on `5500` and `8080`). **Production deployment
  must add the real `https://www.brilyx.com` origin (and any staging
  domain) via the `CORS_ALLOWED_ORIGINS` environment variable** — this
  project never guesses or hard-codes that domain.

This is the only backend change in Phase 7.

## Accessibility (section 26)

- Launcher: `<button aria-label="Open Brilyx AI chat">`, toggling to
  `"Close Brilyx AI chat"` with `aria-expanded` kept in sync — never
  icon-only in the accessibility tree.
- Panel: `role="dialog"`, `aria-modal="false"` (it's a non-blocking corner
  panel, not a full modal, so focus is moved into it on open but never
  *trapped* — Tab can still reach the rest of the page).
- Messages: `role="log"` + `aria-live="polite"` so new assistant replies
  are announced without interrupting whatever the visitor is doing.
- Opening the panel moves focus into the message input; `Escape` closes
  the panel and returns focus to the launcher button.
- Every icon-only control (`new conversation`, `close`, `send`) has an
  explicit `aria-label`; the input has `aria-label="Message Brilyx AI"`.
- Visible `:focus-visible` outlines on every interactive element — color
  is never the only signal of focus or state.

Verified live: Tab/Escape/focus-return all confirmed against the running
widget (see below).

## Responsive Behavior (section 25)

Desktop: a 380px-wide floating panel anchored above the launcher,
bottom-right, `max-height: min(640px, 100vh - 120px)`. Below `480px`
viewport width, the panel becomes fixed and near-full-screen
(`top/left/right/bottom: 16px`) so it stays comfortably usable without
edge-to-edge cramping. Verified at 1920×1080, a resized ~900px width, and
true 390px/360px viewports (via a same-origin iframe, since this sandbox's
window manager didn't allow direct OS-window resizing during testing —
the CSS media query itself is a standard `max-width: 480px` breakpoint and
was visually confirmed working at both 390px and 360px). No horizontal
scrolling was observed at any size, including with a single 140-character
unbroken "word" in a message (`overflow-wrap: anywhere` +
`word-break: break-word` on the bubble).

## Error Handling (sections 13, 32-33)

| Condition | User-facing behavior |
|---|---|
| Network failure / offline | "Sorry, I'm having trouble connecting right now. Please try again in a moment." + Retry |
| Request timeout (30s) | "That's taking longer than expected. Please try again." + Retry |
| `409` (closed conversation) | Closed banner + "New conversation" action |
| `404` (stale/invalid conversation_id) | Local id cleared; friendly retry prompt |
| `400` / `422` (validation) | "That message couldn't be sent. Please shorten it or rephrase and try again." |
| `500` / `503` (server/AI unavailable) | Same generic connection message as network failure |

No response body, stack trace, exception message, internal path, or
Ollama-specific error text is ever shown — the frontend maps HTTP status
codes to its own fixed copy rather than displaying anything the backend
returned in `detail`. Nothing ever claims "someone has been notified" or
that a demo was sent — Phase 7 doesn't perform those actions, so it never
implies them.

A "Retry" action on error banners resends the exact original message.
Sending is fully serialized: the textarea and send button are disabled
for the duration of each request, so a rapid double-submit cannot produce
two in-flight requests or out-of-order responses (verified live: firing
two submits back-to-back only ever produced one outgoing message).

## What Was Verified (Manual/Browser Testing)

No frontend test framework was introduced (a build step + test runner
for one static JS file would be disproportionate — section 46). Instead,
every item below was exercised against a **live backend and a real local
Ollama model**, using Chrome browser automation (not simulated/mocked):

| # | Check | Result |
|---|---|---|
| A-D | Widget loads, launcher opens, welcome message + quick actions appear | ✅ |
| E-G | Quick action and typed messages reach `/api/chat`; real assistant replies render | ✅ |
| H-I | `conversation_id` persisted and reused across turns | ✅ |
| J | Close/reopen preserves all messages, no new conversation/network call | ✅ |
| — | Full page reload rehydrates history via `GET /api/conversations/{id}` (24 real messages restored, quick actions correctly hidden) | ✅ |
| K | "New conversation" (with confirm banner) starts a clean session; closed conversation is not re-closed | ✅ |
| L | `409` from a server-closed conversation → closed banner, no retry loop, no message sent | ✅ |
| M | Typing indicator shows while waiting, clears on reply | ✅ |
| N | Backend stopped → friendly network-error banner + working Retry (verified message actually resent successfully once backend came back) | ✅ |
| O | 140-char unbroken word + long multi-paragraph reply: no horizontal overflow, scroll intact | ✅ |
| P | 390px and 360px viewports: header/input/send/launcher all usable, no overflow | ✅ |
| Q | 1920×1080 and ~900px width: layout correct | ✅ |
| R | Tab focus, Escape-to-close (+ focus return to launcher), focus-visible states | ✅ |
| S | `<script>alert(1)</script>` and `<img src=x onerror=alert(1)>` rendered as inert text, zero script execution, assistant responded sensibly | ✅ |
| T | `state`/`qualification`/`lead` JSON checked directly against the API — none of `lead_score`, `lead_level`, `lead_status`, `qualification`, `conversation_id` ever appeared in the rendered widget text | ✅ |
| U | Natural lead capture (name/email/website) continued working end-to-end through the widget with zero frontend-side lead logic | ✅ |

## Real End-to-End Test (section 38) — Results

Full scripted conversation against the live backend + Ollama, driven
through the actual widget UI:

1. Opened widget → welcome message + 5 quick actions shown.
2. Clicked "AI Chatbot" → real assistant reply about AI chatbots.
3. "I run a dental clinic." → contextual reply; `business_type` became
   `dental_clinic` server-side.
4. "We receive lots of WhatsApp inquiries..." (paraphrased in-session as
   "We receive lots of WhatsApp inquiries and want them automated.") →
   normal, on-topic reply.
5. "How much does a WhatsApp AI agent cost?" → full tier pricing table
   returned (Starter/Business/Pro/Custom, PKR + USD) — see the Phase 4.1
   note below.
6. "I'd like a demo." → assistant **asked for name/business/email-or-
   WhatsApp/website** rather than claiming a demo was created or sent.
7. "My name is Ahmed and my email is ahmed@example.com." → acknowledged
   naturally, asked for the remaining fields.
8. "My website is https://example.com." → acknowledged naturally.

Checked directly via the backend's own debug endpoints afterward:

```json
// GET /api/conversations/{id}/state
"business_type": "dental_clinic", "requested_service": "websites_software",
"demo_requested": true, "problem_summary": "High volume of customer inquiries via WhatsApp"

// GET /api/conversations/{id}/qualification
"score": 80, "level": "high"

// GET /api/conversations/{id}/lead
"name": "Ahmed", "email": "ahmed@example.com", "whatsapp": null,
"website": "https://example.com", "lead_score": 80, "lead_level": "high",
"lead_status": "demo_requested"
```

`business_type` (dental_clinic), `email`, and `website` are all correct.
`whatsapp` correctly stayed `null` despite extensive WhatsApp-AI
discussion — discussing the product never fabricated a contact number.
`lead_score`/`lead_level` came straight from Phase 5's qualification
endpoint, confirmed byte-for-byte identical between the two endpoints.
**None of this ever appeared in the widget's visible text** — confirmed
by scanning the rendered panel's text content for `lead_score`,
`lead_level`, `lead_status`, `qualification`, and `conversation_id`
(zero matches).

One **pre-existing Phase 4 behavior**, not introduced by Phase 7, worth
flagging: sending "My website is https://example.com." contains the word
"website," which trips Phase 4's `WEBSITE_SOFTWARE_KEYWORDS` deterministic
rule and flips `requested_service` from `whatsapp_ai_agent` to
`websites_software`. The widget faithfully displays whatever the backend
decided — this is a Phase 4 extraction nuance, not a frontend bug, and per
this phase's explicit scope control it was left untouched.

## Phase 4.1 Regression (section 39)

- **"How much does Business Automation cost?"** (no tier named): the
  model did *not* single out the Business tier's price as "the price of
  Business Automation" (the single worst failure mode Phase 4.1 exists to
  prevent). It did, however, list out numbers for all four tiers, which
  the system prompt's own instruction says not to do in this case
  ("...do NOT list out every package's numbers either"). This is an LLM
  prompt-adherence gap in Phase 4's system prompt, not a frontend defect —
  the widget rendered the backend's actual response faithfully. Per this
  phase's scope control ("Do NOT modify Phase 4 extraction logic unless a
  critical frontend integration issue makes it unavoidable" — this isn't
  a frontend integration issue), it was **not** patched here; flagging it
  for whoever owns Phase 4's prompt tuning next.
- **"How much is the Business package?"** (tier named explicitly): correct
  — the model returned exactly the Business tier's numbers (PKR 35,000+ /
  $200+ setup, PKR 6,000+ / $40+ monthly) with no confusion. This is the
  behavior Phase 4.1 was built to guarantee, and it held.

## Phase 5 Regression (section 40)

Confirmed via the live test above: `lead_score`, `lead_level`, and
qualification reasons are computed and stored server-side but never
appear anywhere in the widget's rendered text at any point in the
conversation.

## Phase 6 Regression (section 41)

Confirmed: "My email is..."/"My name is.../my email is..." style messages
correctly created and updated the `Lead` row entirely server-side. The
widget only ever sent the visitor's literal text through `/api/chat` —
there is no lead-creation code path anywhere in the frontend, and no
`POST /api/leads`-style endpoint exists for it to call even if it wanted
to.

## What Phase 7 Does Not Do

- Does not send email.
- Does not send WhatsApp messages.
- Does not notify any human.
- Does not integrate a CRM.
- Does not send automated follow-ups.
- Does not deliver demos.
- Does not deploy the production backend or the widget script itself —
  embedding assumes *some* static host will serve `brilyx-chatbot.js`,
  which this phase does not set up or choose.
- Does not add any paid service or new runtime dependency.
- Does not duplicate pricing, qualification, lead, or conversation-
  intelligence logic in JavaScript — every one of those decisions is made
  server-side, every time.

These remain open for Phase 8+.

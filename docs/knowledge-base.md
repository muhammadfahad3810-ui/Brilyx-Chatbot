# Phase 1 — Brilyx Business Knowledge Base

## Purpose

The `knowledge/` directory is the **source of truth** for all business
information the future Brilyx AI chatbot will use — what Brilyx is, what it
offers, how it's priced, who it's for, and how it should behave when talking
to visitors.

The chatbot engine does not exist yet (that's a later phase). This
knowledge base exists now so that:

- Business information is defined once, clearly, and in one place.
- The future chatbot has a stable, well-structured source to read from
  (directly or via retrieval) instead of information being hardcoded into
  application logic.
- Non-technical stakeholders can review and edit business content without
  touching code.

## File-by-File Overview

| File | Contents |
|------|----------|
| `company.md` | What Brilyx is, its focus, positioning, target customers, and value proposition. Also lists what must *not* be claimed (history, client names, awards, etc.) until confirmed. |
| `services.md` | The four core services (AI Website Chatbots, WhatsApp AI Agents, Business Automation, Websites & Software) — purpose, capabilities, use cases, ideal customers, and problems solved for each. |
| `pricing.md` | Pakistan (PKR) and international (USD) starting prices across four tiers, plus the rules for presenting pricing correctly (starting prices only, no auto currency conversion, custom quotes for complex work). |
| `industries.md` | Primary industries (dental clinics, medical clinics, healthcare, restaurants, cafes) with common problems, automation opportunities, and relevant services. Also covers secondary/general industries. |
| `faq.md` | Common visitor questions answered using only confirmed information, with explicit "refer to the Brilyx team" answers where information isn't yet available. |
| `sales_rules.md` | Behavioral rules for the future chatbot: the help-first sales approach, hard rules (never invent pricing/features/clients/integrations, never guarantee outcomes, never pretend to be human, etc.), and lead-intent signals (low/medium/high). |
| `demo.md` | What the chatbot may and may not claim about demos, and the data fields a future demo-request flow should collect. |

## Source-of-Truth Rules

1. **These files are the only authoritative source of Brilyx business
   information.** Any future chatbot, prompt, or retrieval system must be
   built to defer to this content rather than to information invented at
   runtime.
2. **If it isn't written here, it isn't confirmed.** The chatbot must not
   assume, infer, or fabricate business facts (history, clients, awards,
   integrations, pricing details, guarantees, etc.) that aren't explicitly
   present in these files.
3. **Starting prices are not final quotes.** Any pricing logic must preserve
   the distinction between a starting price and an actual quote, and must
   never auto-convert between PKR and USD.
4. **Unconfirmed information must be deferred to the Brilyx team**, not
   guessed. `faq.md` and `company.md` both model this pattern explicitly.
5. **No demo-related claims beyond what actually happens.** The chatbot must
   not claim a demo was created/sent, or that a team member was notified,
   until a real backend process performs that action.

## How Future Developers Should Update Business Information

- Business-fact changes (pricing, services, positioning, industries, FAQ
  answers) should be made by editing the relevant Markdown file in
  `knowledge/` directly — not by changing application code.
- Keep each file's scope intact: don't mix pricing details into
  `services.md`, don't mix sales behavior into `company.md`, etc. This keeps
  the knowledge base easy for both humans and future retrieval systems to
  navigate.
- When adding a new confirmed fact, add it to the most relevant existing
  file rather than creating ad hoc new files, unless the topic clearly
  doesn't fit any existing file.
- When information is uncertain or not yet decided, do not add it as fact.
  Leave it out, or explicitly note it as unconfirmed the way `company.md`
  does.
- Any change here should be reviewed for consistency with `sales_rules.md`
  (e.g. don't add a claim to another file that `sales_rules.md` says the
  assistant should never make).

## Why the AI Must Not Invent Information

The chatbot represents Brilyx directly to potential customers. Fabricated
claims — invented pricing, fake clients, unsupported integrations, guaranteed
outcomes, or false claims about actions taken (like sending a demo) — create
real business and trust risk: customers may be misled, expectations may be
set that Brilyx can't meet, and false claims can damage credibility once
discovered. Treating this knowledge base as the strict source of truth, and
explicitly deferring unknowns to the Brilyx team, is the mechanism that
prevents this.

## Phase 1 Scope Note

Phase 1 only creates the knowledge content itself as structured Markdown
files. It does not implement:

- The chatbot engine or any AI/LLM integration (including Ollama)
- Retrieval of this content into a running system
- A database model for knowledge content
- The frontend widget
- The lead capture system
- WhatsApp integration
- Deployment

Those are addressed in later phases.

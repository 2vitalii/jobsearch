# Product Vision & Roadmap

> Honesty-first, GDPR-compliant job-search automation. Multi-user SaaS (B2C, EU/worldwide).
> The product never fabricates facts: it reorders and rephrases real experience to match a
> vacancy, and surfaces gaps honestly instead of inventing qualifications.

---

## 1. Product logic (the core user journey)

The product is a web (later mobile) application built around one cycle:

1. **Create a master CV inside the product** — a user can build a master CV from scratch.
2. **Or import an existing résumé** — upload a PDF/DOCX, the system parses it into the
   master-CV structure, and the user reviews/edits before anything is generated.
3. **Keyword assistance** — the application helps the user pick search keywords. Initial
   source is the user's own master CV (honesty-friendly: suggest searching for what the
   user genuinely matches); a market-driven source can come later.
4. **Manual keywords** — the user can also enter their own search keywords directly.
5. **Vacancy database + email notifications** — because vacancies are stored in a shared
   pool, when fresh vacancies appear that match a search the user ran earlier, the user
   receives an email notification.
6. **Locations + work format** — the user selects locations to search, and chooses the work
   format: remote (by location), hybrid, or on-site.
7. **Global remote** — the user can also search fully-remote roles worldwide.

For each matched vacancy the system produces: a tailored CV, a cover letter, and a
vacancy assessment (fit score, ATS keyword match, honest gaps).

---

## 2. Honesty-first principle (product ideology + technical constraint)

This is the core differentiator and a hard rule in all generation logic:

- Only **Professional Summary** and **Core Skills** are adjusted per vacancy (reordering and
  rephrasing of skills the candidate genuinely has, surfacing the vacancy's keywords).
- **Experience, Projects, Education are never rewritten automatically.** No fabricated
  metrics, technologies, or experience.
- Anything the vacancy requires that the candidate lacks goes into "missing / gaps" —
  never into the summary, skills, or cover letter.

This principle should become a **visible feature**, not just a silent internal rule:
on the results screen, show explicitly what was tailored vs. what is fixed.

---

## 3. Architecture principle: shared vacancies, private processing

A fundamental split that drives data-model decisions:

- **JobStore (shared vacancy pool)** — a vacancy is an objective fact about the world
  (title, company, description, location, URL). It lives in a shared pool, not tied to any
  user, accessed via `service_role`. A vacancy found by one user's search is available to
  serve another user's search. **This shares scraping cost across users and creates a
  network effect: the more users, the fuller the pool, the less re-scraping is needed.**
- **UserState (private per-user data)** — the result of processing a vacancy for a specific
  user (fit score, tailored CV, cover letter, gaps) is private. It lives under Row Level
  Security, scoped to the user, and is **never shared**.

**The boundary:** share the vacancy (a fact), never share the processing (per-person).
The same vacancy yields different fit scores and different CVs for different users.

---

## 4. Roadmap & status

### Phase 0 — Clean core ✅
Pure functions (`scrape` / `score_fit` / `analyze` / `build_package`), injected LLM client,
state behind `JobStore` / `UserState` interfaces, tests.

### Phase 1 — Structure / infra ✅
Package layout, core tests, installable package.

### Phase 2a — Walking skeleton (B2C) — in progress
Goal: one real person can go end-to-end (register → CV → search → run → results).

**Backend (FastAPI + Supabase, EU/Frankfurt) ✅**
- Auth, CV upload/parse, SearchParams CRUD.
- Async Run via FastAPI BackgroundTasks + `runs` table (history/status). Closes audit
  finding SG-03 (a synchronous Run of up to ~250 LLM calls risks timeout / runaway cost).
  One active run per user (atomic guard), startup cleanup of orphaned runs.
- Attribution seam: `matches.run_id` + `runs.search_snapshot` — foundation for future
  notifications and a funnel dashboard, without committing to a full multi-search model yet.
- Matches list/detail with signed download URLs (private Storage bucket, short-lived URLs).

**Frontend (Next.js 16, shadcn/ui, TanStack Query + Zod)**
- 5a — Auth + design system ✅
- 5b — CV page (upload, parse, section editing, save) ✅
- 5c — Search page (params form, run trigger, live progress polling) — current
- 5d — Results page (matches, assessment, tailored documents) — next

**Deploy** — backend on an EU host, frontend on Vercel. Custom SMTP for branded auth
emails is deliberately deferred to this phase (requires owning a domain).

### Phase 2b — Automation & resilience (later)
- Saved searches as durable subscriptions (the basis for notifications).
- Server-side per-user scheduling; centralized scheduled scraper with dedup
  (`query_hash` + TTL cache) to avoid bans and redundant scraping.
- Quotas / budgets, funnel dashboard (built on `runs` history), email notifications.

### Phase 3 — Scale, quality, expansion (later)
- Proxy pool + throttling for LinkedIn at volume.
- Quality metrics (apply → response → interview) + prompt A/B testing.
- Semantic matching (embeddings) to pre-rank the pool before LLM calls.
- Legal API sources, mobile app (Expo), B2B features + payments.

---

## 5. Product improvements (accepted as vision, sequenced)

These build on data the pipeline already produces — mostly a matter of surfacing it well:

- **A — Assessment transparency as the core.** Make fit score, ATS present/missing, gaps,
  and recruiter verdict the center of the results screen. Turns the product from an
  "application generator" into a career advisor that doesn't lie. (Target: 5d.)
- **B — Honesty as a visible feature.** Show "Summary & Skills tailored to this vacancy ✓ |
  Experience & Projects unchanged 🔒" so the user can see nothing was invented.
- **C — Application tracker / funnel.** generated → sent → response → interview. Built on
  the `runs` history and application tracking. Brings users back and yields data on what
  actually works.
- **D — "Why this vacancy."** Show which search/keyword surfaced a vacancy and why it
  passed filters. Reduces the black-box feeling. (Enabled by the attribution seam.)
- **E — Cost control.** Show how many vacancies will be processed before launching a run,
  with a cap. (Half-built already via the Haiku pre-filter before the expensive Sonnet step.)

### Deliberately NOT now (to avoid scope creep)
Market-driven keyword discovery, notifications (all of 2b), mobile app, B2B features.
All are on the roadmap; they come after the end-to-end web cycle works for one user.

---

## 6. Search-model decision (recorded)

The notification feature (logic item 5) requires a search to be a **durable, saved
subscription**, not just "the last parameters the user entered." That implies a future
"multiple saved searches per user" model.

**Decision:** do **not** build full multi-search now. Instead, a minimal seam:
- `matches.run_id` (nullable FK → runs) — every match knows which run produced it.
- `runs.search_snapshot` — the run records the search parameters used at start.
- `search_params` stays one-per-user for now.

This gives attribution (`match → run → snapshot`) — the foundation for notifications and the
funnel dashboard — without over-engineering a model around unknown requirements. The full
multi-search model (named searches, scheduling, editing, digest) is designed later, in the
notifications phase, when real requirements are known.

Rationale: correct engineering at this stage means *not closing the door* to the final
shape, not *building the final shape* before the product has met real usage.

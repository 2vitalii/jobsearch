# web/ — frontend conventions (Next.js + Supabase + shadcn/ui)

> Next.js here is **16** (App Router). APIs differ from older versions — see `AGENTS.md`
> and `node_modules/next/dist/docs/` before using a framework feature. Note: the
> request gate lives in `proxy.ts` (Next 16 renamed the `middleware` convention to `proxy`).

## Design system — non-negotiable

> **Source of truth:** the final visual direction approved in Claude Design (Search & Run
> screen — graphite two-column layout; landing pending). This section is the token + pattern
> spec that Developer-agents implementing 5c / 5d / the public landing MUST follow.
>
> ⚠️ **Hex values below are read from the approved Search & Run screenshot (sampled by eye —
> treat as close estimates; fine-tune the exact hex once in `globals.css` and lock them there).**
> Values not visible in the screenshot (non-`queued` status dots, button hover) are chosen
> muted in-palette defaults, marked "chosen". Landing-page tokens will be added as a separate
> block when that screenshot arrives.

### Rules (unchanged)

- **UI only from `web/components/ui` (shadcn) + Tailwind tokens.** No inline styles, no
  arbitrary hex/`rgb()` in components. Colors come from CSS variables / semantic Tailwind
  classes (`bg-background`, `bg-primary`, `text-muted-foreground`, `border-border`, …).
  The hex values in this doc define the _tokens_ (in `globals.css` / theme) — components
  reference the semantic class, never the raw hex.
- New primitive needed? Add it via `npx shadcn@latest add <name>` (it lands in
  `components/ui` and we own it). Don't hand-roll buttons/inputs/dialogs.
- **Icons: `lucide-react` only.**
- Style: dark, graphite, data-forward minimalism — near-black base, generous whitespace,
  a single muted accent, numbers rendered in mono. Radius is `--radius` (0.5rem).
  Dark is the primary theme; keep `next-themes` working.

### 1. Color palette (dark / graphite)

**Surfaces & borders**
| Token | Role | Value (from screenshot) |
|---|---|---|
| `--background` | page base, near-black graphite (slightly cool) | `#0C0E11` (top brand bar reads a touch darker, ~`#0A0B0D`) |
| `--card` / elevated fill | chips, source monograms, stepper, inactive segments, progress track | `#1B1E24` |
| segment-active fill | active segment in a segmented control (elevated neutral gray) | `#282C34` |
| `--border` | hairline dividers (metric-panel columns, source rows, panel split, outlines) | `#1F2229` |
| `--input` | input/segment/stepper outline | `#23262D` |

> The 4 metric cards are **not** elevated surfaces — they are `--background` split by vertical
> `--border` hairlines. Only chips/monograms/steppers/active-segments use `--card`.

**Text tiers**
| Token | Role | Value |
|---|---|---|
| `--foreground` | primary text (source names, "New search", active segment, values) | `#ECEEF1` |
| hero-number color | large mono metrics — foreground at light weight (slightly dim) | `#DCE0E5` |
| `--muted-foreground` | secondary ("Queued", "Add keyword…", "Billed per result", `hrs`) | `#8A9099` |
| label tier | uppercase tracked labels (see typography) — most muted | `#787F89` |
| form-group label | "Keywords / Locations / Posted within…" — brighter, medium weight | `#D3D7DD` |

**Accent — Run button + progress fill (`--primary`)**

- Muted mid-tone **slate-blue / periwinkle** — desaturated, NOT the old bright `#2E5A8C`.
- `--primary`: `#5F7296` (read from the Run button & the progress-bar fill).
- `--primary-foreground` (Run-button label): **dark**, near-black slate `#14171F` — the button
  text is dark-on-slate, not white. (Correction from the earlier draft.)
- Replaces the previous `--primary ≈ #2E5A8C`. Do not reintroduce the bright blue anywhere.

**Chips — two variants of one component**, both with a trailing `×` to remove:

- **Same fill/border for both** — `--card` `#1B1E24`, text `#D3D7DD`, `×` in `--muted-foreground`.
  The ONLY visual difference is the leading glyph — no color/tint delta. (Correction: the
  earlier "cooler blue-tinted location chip" was wrong.)
- **Keyword chip:** leading `#` glyph (in `--muted-foreground`) — e.g. `# Data Engineer ×`.
- **Location chip:** leading `MapPin` (lucide) glyph (in `--muted-foreground`) — e.g. `⌖ Remote EU ×`.

**Status dot** (left of each source row). Muted, in-palette semantic set (same low
saturation/brightness as the rest of the UI — never vivid):
| State | Color | Source |
|---|---|---|
| `queued` | muted gray `#5A616B` | confirmed (all rows in screenshot) |
| `running` | muted amber `#C7994C` | chosen — in-progress tier, between queued (gray) and done (green) |
| `done` | muted green `#4E9A6B` | chosen |
| `error` | muted red/terracotta `#C0564E` | chosen |

### 2. Typography

Two families, strict split by content type.

- **IBM Plex Mono** — **all numbers & data**, no exceptions: hero metrics (`3,120` / `350` /
  `11%` / `6`), segmented-control numbers (`24h`, `7`, `30`, `120`), stepper value (`350`),
  progress readouts (`0 / 350`, `11%`), the `hrs` suffix.
  ⚠️ **Not currently wired** — add via `next/font` in `app/layout.tsx` as `--font-mono`
  (alongside Inter) and expose a `font-mono` Tailwind utility. Part of 5c setup.
- **Inter** — everything else: all labels, headings, body, button labels, source names,
  chip text. Wired in `app/layout.tsx` as `--font-sans` (unchanged).

**Two distinct label styles — do not conflate:**

- **Uppercase tracked label** (muted, letter-spacing ~0.1em, ~11px): brand "JOB SEARCH",
  metric labels "ESTIMATED MATCHES / TO PROCESS / COVERAGE / SOURCES", column headers
  "SOURCE / STATUS". Color = label tier `#787F89`.
- **Form-group label** (sentence case, semibold ~600, ~15px, brighter `#D3D7DD`):
  "Keywords", "Locations", "Posted within", "Work format", "Processing limit". **Not uppercase.**

**Scale (approx from screenshot — confirm/lock in code)**
| Use | Family | Size | Weight |
|---|---|---|---|
| Hero metric number | IBM Plex Mono | ~48px (`3rem`) | 300 (light) |
| Uppercase tracked label | Inter | ~11px | 500, tracked ~0.1em, uppercase |
| Form-group label | Inter | ~15px | 600 |
| Body / source name / chip / segment | Inter | ~14–15px | 400 |
| Numbers (metrics, stepper, progress, segment) | IBM Plex Mono | 400 (13–48px per use) | — |

### 3. Component patterns

Documentation only — implement in 5c/5d, not now.

- **Metric card** (top panel, ×4, full-width row): uppercase tracked muted label on top
  (Inter), large light mono number below (IBM Plex Mono ~48px). No fill — separated from
  neighbours by a vertical `--border` hairline. Optional small mono prefix (`≈` on
  ESTIMATED MATCHES), muted.
- **Chip** (two variants above): `--card` fill, `--radius`, leading glyph (`#` / `MapPin`) +
  text + `×`. Same styling; only the glyph differs. A muted "Add keyword…/Add location…"
  placeholder sits in the same bordered container after the chips.
- **Segmented control** (Posted within: 24h / 7 days / 30 days / 120 hrs · Work format:
  Remote / Hybrid / On-site): single-select. Inactive = `--input` outline + `--muted-foreground`
  text; active = `--card`→`#282C34` elevated fill + `--foreground` text (neutral gray fill,
  no accent tint).
- **Stepper** (Processing limit): `[ − | value | + ]` bordered (`--input`) row; value in mono,
  `−`/`+` in `--muted-foreground`; muted "Billed per result" subtitle under the label.
- **Source row**: status dot + 2-letter monogram in a rounded `--card` square (source initials,
  e.g. `Li` `In` `Ro` `Wf` `Ot` `HN`) + source name (Inter, `--foreground`) + right-aligned
  status text (`--muted-foreground`). Rows separated by a thin `--border` divider. Column
  headers "SOURCE / STATUS" above (uppercase tracked).
- **Progress bar**: `--card` track + `--primary` fill; left label `Processing X / Y`
  ("Processing" Inter, numbers mono), right percent in mono.
- **Primary button (Run search)** — full-width, `--radius`:
  - default: `--primary` fill `#5F7296`, `--primary-foreground` (dark) label.
  - hover: slightly darker accent `#556688`.
  - disabled: reduced opacity / muted graphite fill, non-interactive (while a run is in progress).

### Layout reference (Search & Run, 5c)

Full-width top row of 4 metric cards (bg split by vertical hairlines). Below: two columns split
by a vertical `--border`. **Left** = search-params form (Keywords chips, Locations chips,
Posted-within segmented, Work-format segmented, Processing-limit stepper). **Right** = SOURCE/STATUS
header, source rows list, progress bar, and the full-width Run-search CTA pinned at the bottom.
(Exact grid columns/spacing to lock during 5c.)

## Landing page — design system (marketing)

> Approved landing screenshot (product wordmark **"Sift"**). Same graphite palette, accent, and
> mono/label typography as Search & Run above — this block adds only the marketing-page **layout
> patterns** and the few net-new component variants. Where the landing reuses an existing token
> (`--background` #0C0E11, `--primary` #5F7296, IBM Plex Mono for numbers, the uppercase-tracked
> label tier), reference it — do NOT mint new names. Hex noted only where genuinely new; eyeballed
> values marked "verify in code". Docs only — do not implement the landing here.

### Palette — reuses Search & Run tokens

Maps entirely to existing tokens: `--background` (page + every section — section rhythm is
spacing, not color bands), `--card` + `--border` (cards/panels), `--foreground` /
`--muted-foreground` / label tier (text), `--primary` `#5F7296` (logo mark, "Start free", all
primary CTAs, step numbers `01`–`04`, and the quote-block accent line). One value needs
confirming, not a new token yet:

- **Quote-block left accent line** reads as `--primary` (slate-blue) but slightly dimmer than the
  solid Run/CTA fill — likely `--primary` at reduced opacity (~60–80%). Verify exact value in code;
  only mint a named token if it proves distinct from `--primary`.

### Typography — reuses Search & Run rules

IBM Plex Mono for ALL numbers (hero demo metrics `≈1,080 / 200 / 200 / 6`; step numbers
`01`–`04`); Inter for headings/body/labels. Uppercase-tracked label tier for every eyebrow
("AI JOB SEARCH", "THE PROBLEM", "HOW IT WORKS", "WHY TEAMS TRUST IT", "LAST RUN · …",
"THE MANUAL WAY", "WITH SIFT", the metric labels). Hero + section headings = Inter, bold, large.

### New component variants (not present in Search & Run)

- **Secondary button (outline)**: transparent fill + `--input`/`--border` outline +
  `--foreground` label; pairs with the primary filled button. Used by "See how it works" /
  "Talk to us". (Search & Run only had the primary filled Run button — this variant is new.)
- **Top marketing nav**: left = logo mark (`--primary` rounded square) + "Sift" wordmark; right =
  muted text links (Product / Pricing / Sign in) + primary "Start free" button.
- **Quote / trust block** — NEW pattern: a thin vertical **left accent line** (`--primary`, see
  palette note) + eyebrow + large heading + muted body. No card fill; the left line is the only ornament.

### Landing layout patterns (top → bottom)

1. **Hero** (centered): eyebrow ("AI JOB SEARCH") → large 2–3-line headline (Inter bold) → muted
   subhead → two CTAs (primary filled "Create your master resume" + secondary outline "See how it
   works") → demo metric panel.
   - **Demo metric panel**: an eyebrow row ("LAST RUN · FRONTEND ENGINEER, REMOTE EU") then 4
     metrics inline (VACANCIES SCANNED / PROCESSED / TAILORED / SOURCES), each = uppercase muted
     label on top + IBM Plex Mono number below. **No hairline dividers between the metrics**
     (label-separated only) — this differs from the Search & Run §3 metric card, which used vertical
     `--border` hairlines. The panel sits inside one subtle `--border` frame.
2. **Problem / Solution** — two columns: eyebrow "THE PROBLEM" + centered heading, then two
   `--card`+`--border` rounded cards side by side — "THE MANUAL WAY" vs "WITH SIFT" — each an
   uppercase label + a few short muted lines.
3. **How it works** — eyebrow "HOW IT WORKS" + heading, then **4 numbered steps in a row**: `01`–`04`
   (mono, `--primary`) + bold title (Inter) + muted description. Steps separated by spacing / thin `--border`.
4. **Trust / quote block** — the new left-accent-line pattern: "WHY TEAMS TRUST IT" → "Sift never
   invents experience you don't have." → muted body. (Mirrors the resume-honesty invariant — never
   invent experience the candidate lacks.)
5. **Differentiators list** — stacked rows, each = bold title (left) + muted description (right),
   rows separated by `--border` hairlines: Full-market coverage / Transparent scoring / Cost control /
   Application tracker. (Note: "Cost control" is the processing-limit differentiator tracked in issues.md.)
6. **Final CTA block** (centered): heading → muted subtext → two CTAs (primary "Create your account" +
   secondary outline "Talk to us") → muted microcopy under ("No card required to preview matches.").
7. **Footer**: "Sift" wordmark left, muted "© 2026 · Privacy · Terms" right.

(Exact spacing / max-width / grid to lock during landing implementation — not this task.)

## Data fetching — non-negotiable

- Every backend call goes through `web/lib/api.ts` (`apiFetch`/typed helpers): it attaches
  the Supabase access token as `Authorization: Bearer` and **validates the response with a
  Zod schema from `web/lib/schemas.ts`**. Add a schema there for every new endpoint.
- In components, call the backend via **TanStack Query** (`useQuery`/`useMutation`), never
  raw `fetch`. Always handle both states: **loading → `<Skeleton/>`**, **error → `toast`
  (sonner)**. Don't leave a blank screen.

## Auth

- Supabase clients: `utils/supabase/{client,server,middleware}.ts`. Use the browser client
  in client components/handlers, the server client in Server Components, and never trust
  `getSession()` for gating server-side — use `getUser()`. The publishable key is the only
  Supabase key that may appear in the browser (`NEXT_PUBLIC_*`). The service_role/secret key
  must NEVER be in `web/`.

## TypeScript & quality gate

- `strict: true`, **no `any`**. Prefer `unknown` + Zod at boundaries.
- Before every commit: `npm run typecheck` (tsc --noEmit) **clean**, `npm run lint` (eslint)
  **clean**, `npm run build` **passes**, `npm run format` applied.

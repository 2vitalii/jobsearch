# web/ — frontend conventions (Next.js + Supabase + shadcn/ui)

> Next.js here is **16** (App Router). APIs differ from older versions — see `AGENTS.md`
> and `node_modules/next/dist/docs/` before using a framework feature. Note: the
> request gate lives in `proxy.ts` (Next 16 renamed the `middleware` convention to `proxy`).

## Design system — non-negotiable
- **UI only from `web/components/ui` (shadcn) + Tailwind tokens.** No inline styles, no
  arbitrary hex/`rgb()`. Colors come from CSS variables / semantic Tailwind classes
  (`bg-primary`, `text-muted-foreground`, `border-border`, …). The brand accent is the
  `--primary` token (≈ #2E5A8C) — never hardcode it.
- New primitive needed? Add it via `npx shadcn@latest add <name>` (it lands in
  `components/ui` and we own it). Don't hand-roll buttons/inputs/dialogs.
- **Icons: `lucide-react` only.**
- Style: clean minimalism — neutral base, lots of whitespace, one accent, readable data.
  Radius is `--radius` (0.5rem). Dark theme must keep working (`next-themes`).
- Font: Inter via `next/font` (wired in `app/layout.tsx` as `--font-sans`).

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

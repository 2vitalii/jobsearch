# Security review guidance — Job-Search Automation (multi-tenant SaaS)

Instructions for the security reviewer. This project is a multi-tenant web/mobile
application: users register, upload a master CV, and receive matched vacancies with a
tailored CV, cover letter, and assessment for each. Stack: Supabase (Postgres + Auth +
Storage + RLS, EU/Frankfurt), a FastAPI backend (service_role key server-side only), a
Next.js frontend, the Anthropic API for scoring and document generation, and
JobSpy + job-board/ATS scraping for sourcing. The stored data is real PII — names,
contacts, CVs, application history — under GDPR.

Prioritize the project-specific risks below over generic findings. The
highest-severity class is cross-tenant data exposure; treat it as critical.

## 1. Multi-tenant isolation and authorization (highest priority)

The architecture splits data into two stores with different rules:

- JobStore — the shared, deduplicated pool of scraped vacancies. Cross-user by
  design. service_role access and broad read policies here are expected, not a finding.
- UserState — per-user data: applications, generated documents, the master CV,
  preferences, seen-jobs. Must be isolated per user.

Flag as critical:

- Any query against UserState tables that does not constrain to the authenticated user,
  or that relies only on application-layer filtering with no enforced RLS behind it.
- Use of the Supabase service_role key to read or write UserState data. service_role
  bypasses RLS entirely, so it must be reserved for genuinely cross-user operations
  (JobStore writes, dedup, admin jobs). Per-user data must go through a user-scoped
  path — a forwarded user JWT, or request.jwt.claims set on the transaction — so RLS
  actually applies.
- Handlers that accept a user_id, application_id, document path, or other resource
  identifier from the request and act on it without verifying it belongs to the
  authenticated user (insecure direct object reference / broken object-level authz).
- New tables added without RLS enabled, or RLS policies that are missing, overly
  permissive (using (true)), or that compare against a client-supplied id instead of
  auth.uid().

## 2. Secrets

- The Anthropic API key, the Supabase service_role key, and database credentials must
  exist only in backend environment variables. Flag any of them appearing in frontend
  code, in any NEXT_PUBLIC_* variable, in logs, in API responses, or committed to the
  repository.
- Flag server-only modules (anything importing the service_role client or secret env)
  being imported into client-side Next.js code, which would bundle the secret into the
  browser.
- Flag hardcoded credentials of any kind. Treat sk-ant- keys, Supabase keys, JWT
  signing secrets, and connection strings as critical.

## 3. Untrusted scraped content and prompt injection

Scraped job descriptions and any external text are untrusted input.

- Flag scraped or job-description text placed into the LLM system prompt. Untrusted
  text belongs only in the user-role message.
- Flag designs where scraped content could override the honesty constraints. The product
  must never fabricate skills, metrics, or experience, even if a job description contains
  text that reads like an instruction to do so. The no-fabrication invariant must survive
  adversarial input.
- Flag outbound requests to scraped or user-supplied URLs without SSRF protection
  (validate scheme and host; block internal and cloud-metadata addresses).

## 4. PII and GDPR

- Flag logging of PII (names, emails, phone numbers, CV contents, application history)
  and responses that return more user data than the caller needs.
- The erasure path (delete_user_data) must remove the user's data from all stores —
  Postgres rows and Storage objects. Flag deletion that covers one but not the other.
- Flag data sent to regions or third-party subprocessors outside the EU residency
  commitment.

## 5. Document storage (Supabase Storage)

Generated resumes and cover letters are user-owned files.

- Flag public buckets, public object URLs, or predictable/guessable object paths for
  user documents. Access must be per-user — Storage RLS plus short-lived signed URLs.
- Flag any path where a user can read or list another user's stored documents.

## 6. API, auth, and abuse

- JWT validation must verify signature, expiry, and audience. Flag trusting unverified or
  client-supplied claims to identify the user.
- Flag wildcard CORS (*) on authenticated endpoints in non-local configuration.
- Flag missing input validation on FastAPI request bodies and parameters.
- Flag string-concatenated SQL; require parameterized queries.
- Flag expensive LLM-calling endpoints without per-user rate limiting or quotas — an
  authenticated or compromised account can otherwise run up cost.

## 7. Reduce false positives

- The shared JobStore being readable across users is intended. Do not report it as a
  tenant-isolation violation.
- The single-user local pipeline scripts (job_finder.py, pipeline.py, run.sh,
  run_daily.sh, search.sh) are not part of the multi-tenant product. Secret hygiene still
  applies to them, but per-tenant isolation rules do not.

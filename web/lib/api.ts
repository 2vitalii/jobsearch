import { z } from "zod";
import { createClient } from "@/utils/supabase/client";
import {
  MeSchema,
  type Me,
  CvSchema,
  type Cv,
  SearchParamsSchema,
  type SearchParams,
  RunAcceptedSchema,
  type RunAccepted,
  RunStatusSchema,
  type RunStatus,
  MatchListItemSchema,
  type MatchListItem,
  MatchDetailSchema,
  type MatchDetail,
  SuggestRolesResponseSchema,
  type SuggestRolesResponse,
} from "@/lib/schemas";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/** Current Supabase access token, or undefined if not signed in. */
async function getAccessToken(): Promise<string | undefined> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session?.access_token;
}

function authHeader(token: string | undefined): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function validate<T>(path: string, schema: z.ZodType<T>, json: unknown): T {
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new Error(`Unexpected response from ${path}`);
  }
  return parsed.data;
}

/**
 * Thin fetch wrapper for JSON endpoints. Attaches the Supabase access token as a
 * Bearer token and validates the response against a Zod schema, so contract drift
 * fails loudly instead of rendering a blank screen.
 */
export async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const token = await getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(token),
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    throw new Error(`Request to ${path} failed (${res.status})`);
  }
  return validate(path, schema, (await res.json()) as unknown);
}

/** GET /me — proves the Supabase-login → token → backend chain. */
export function getMe(): Promise<Me> {
  return apiFetch("/me", MeSchema);
}

/** GET /cv — returns null when the user has no CV yet (404 is not an error). */
export async function getCv(): Promise<Cv | null> {
  const token = await getAccessToken();
  const res = await fetch(`${API_BASE}/cv`, {
    headers: { ...authHeader(token) },
  });
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`Couldn't load your CV (${res.status})`);
  }
  return validate("/cv", CvSchema, (await res.json()) as unknown);
}

/** POST /cv/upload — multipart; backend parses the file into master_cv.md form. */
export async function uploadCv(file: File): Promise<Cv> {
  const token = await getAccessToken();
  const form = new FormData();
  form.append("file", file);

  // No Content-Type header: the browser sets the multipart boundary itself.
  const res = await fetch(`${API_BASE}/cv/upload`, {
    method: "POST",
    headers: { ...authHeader(token) },
    body: form,
  });

  if (!res.ok) {
    if (res.status === 413) {
      throw new Error("That file is too large — the limit is 5 MB.");
    }
    if (res.status === 400) {
      throw new Error(
        "Couldn't read that file. Upload a PDF or DOCX with selectable text.",
      );
    }
    throw new Error(`Upload failed (${res.status}).`);
  }
  return validate("/cv/upload", CvSchema, (await res.json()) as unknown);
}

/** PUT /cv — save edited markdown; backend regenerates the short profile. */
export function putCv(markdown: string): Promise<Cv> {
  return apiFetch("/cv", CvSchema, {
    method: "PUT",
    body: JSON.stringify({ markdown }),
  });
}

/**
 * GET /search-params — returns null when no search params saved yet (404 is not an error).
 */
export async function getSearchParams(): Promise<SearchParams | null> {
  const token = await getAccessToken();
  const res = await fetch(`${API_BASE}/search-params`, {
    headers: { ...authHeader(token) },
  });
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`Couldn't load search params (${res.status})`);
  }
  return validate(
    "/search-params",
    SearchParamsSchema,
    (await res.json()) as unknown,
  );
}

/** PUT /search-params — save search params. */
export function putSearchParams(body: SearchParams): Promise<SearchParams> {
  return apiFetch("/search-params", SearchParamsSchema, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/**
 * Error thrown when a run is already active (HTTP 409).
 * Callers can detect this via `err instanceof RunConflictError`.
 */
export class RunConflictError extends Error {
  readonly code = "RUN_ACTIVE" as const;
  constructor() {
    super("A run is already in progress");
    this.name = "RunConflictError";
  }
}

/**
 * POST /run — starts a new pipeline run.
 * Throws RunConflictError on 409 (run already active).
 * Returns RunAccepted (run_id) on 202.
 */
export async function startRun(): Promise<RunAccepted> {
  const token = await getAccessToken();
  const res = await fetch(`${API_BASE}/run`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeader(token),
    },
  });
  if (res.status === 409) {
    throw new RunConflictError();
  }
  if (!res.ok) {
    throw new Error(`Failed to start run (${res.status})`);
  }
  return validate("/run", RunAcceptedSchema, (await res.json()) as unknown);
}

/** GET /run/{run_id} — poll a specific run's status. */
export function getRun(runId: string): Promise<RunStatus> {
  return apiFetch(`/run/${runId}`, RunStatusSchema);
}

/** GET /matches — all matches for the current user, created_at DESC. */
export function getMatches(): Promise<MatchListItem[]> {
  return apiFetch("/matches", z.array(MatchListItemSchema));
}

/** GET /matches/{id} — single match with signed_cv_url. */
export function getMatch(id: string): Promise<MatchDetail> {
  return apiFetch(`/matches/${id}`, MatchDetailSchema);
}

/** POST /cv/suggest-roles — extract 5-8 searchable job titles from the user's CV. */
export function suggestRolesFromCV(): Promise<SuggestRolesResponse> {
  return apiFetch("/cv/suggest-roles", SuggestRolesResponseSchema, {
    method: "POST",
  });
}

/**
 * GET /run/latest — returns null when no runs exist yet (404 is not an error).
 */
export async function getLatestRun(): Promise<RunStatus | null> {
  const token = await getAccessToken();
  const res = await fetch(`${API_BASE}/run/latest`, {
    headers: { ...authHeader(token) },
  });
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`Couldn't load latest run (${res.status})`);
  }
  return validate(
    "/run/latest",
    RunStatusSchema,
    (await res.json()) as unknown,
  );
}

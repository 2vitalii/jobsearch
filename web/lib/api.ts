import type { z } from "zod";
import { createClient } from "@/utils/supabase/client";
import { MeSchema, type Me } from "@/lib/schemas";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/**
 * Thin fetch wrapper around the FastAPI backend. Pulls the current Supabase
 * access token from the browser session and sends it as a Bearer token, then
 * validates the JSON against a Zod schema so contract drift fails loudly (clear
 * error) instead of silently producing a blank screen.
 */
export async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    throw new Error(`Request to ${path} failed (${res.status})`);
  }

  const json: unknown = await res.json();
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new Error(`Unexpected response from ${path}`);
  }
  return parsed.data;
}

/** GET /me — proves the Supabase-login → token → backend chain. */
export function getMe(): Promise<Me> {
  return apiFetch("/me", MeSchema);
}

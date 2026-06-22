import { z } from "zod";

/**
 * Response shapes from the FastAPI backend. Every backend call must validate its
 * response against a schema here, so a contract drift surfaces as a clear error
 * instead of a blank screen.
 */

// GET /me  ->  api/main.py: {"user_id": ..., "email": ...}
export const MeSchema = z.object({
  user_id: z.string(),
  email: z.string(),
});
export type Me = z.infer<typeof MeSchema>;

// /cv (GET/PUT) and /cv/upload (POST)  ->  api/cv.py CvOut
export const CvSchema = z.object({
  markdown: z.string(),
  short_profile: z.string(),
});
export type Cv = z.infer<typeof CvSchema>;

// GET /search-params and PUT /search-params
export const SearchParamsSchema = z.object({
  keywords: z.array(z.string()),
  locations: z.array(z.string()),
  period_hours: z.number().default(168),
  work_format: z.string().default("remote"),
  loose: z.boolean().default(false),
  targeted: z.boolean().default(false),
});
export type SearchParams = z.infer<typeof SearchParamsSchema>;

// POST /run -> 202
export const RunAcceptedSchema = z.object({
  run_id: z.string(),
});
export type RunAccepted = z.infer<typeof RunAcceptedSchema>;

// GET /run/{run_id} and GET /run/latest
export const RunStatusSchema = z.object({
  status: z.enum(["running", "done", "failed"]),
  scraped: z.number(),
  processed: z.number(),
  generated: z.number(),
  skipped_low_fit: z.number(),
  summary: z.unknown().optional().nullable(),
  error: z.string().optional().nullable(),
  search_snapshot: z.unknown().optional().nullable(),
});
export type RunStatus = z.infer<typeof RunStatusSchema>;

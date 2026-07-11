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
  exclude_senior: z.boolean().default(false),
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

// GET /matches — analysis sub-object inside each match
export const MatchAnalysisSchema = z
  .object({
    reason: z.string().optional().nullable(),
    jd_keywords: z.array(z.string()).optional().nullable().default([]),
    ats_present: z.array(z.string()).optional().nullable().default([]),
    ats_missing: z.array(z.string()).optional().nullable().default([]),
    tailored_summary: z.string().optional().nullable(),
    tailored_skills: z.array(z.string()).optional().nullable().default([]),
    gaps: z.string().optional().nullable(),
    recruiter_verdict: z.string().optional().nullable(),
  })
  .passthrough();
export type MatchAnalysis = z.infer<typeof MatchAnalysisSchema>;

// Job sub-object synthesised by the backend in both list and detail responses
export const MatchJobSchema = z
  .object({
    title: z.string().nullable().optional(),
    company: z.string().nullable().optional(),
    url: z.string().nullable().optional(),
    region: z.string().nullable().optional(),
  })
  .passthrough();
export type MatchJob = z.infer<typeof MatchJobSchema>;

// GET /matches — list item
export const MatchListItemSchema = z
  .object({
    id: z.string(),
    fit_score: z.number().nullable().optional(),
    b2b_eligible: z.string().nullable().optional(),
    job_posted_date: z.string().nullable().optional(),
    analysis: MatchAnalysisSchema.nullable().optional(),
    cover_letter: z.string().nullable().optional(),
    ats_report: z.string().nullable().optional(),
    status: z.string().nullable().optional(),
    run_id: z.string().nullable().optional(),
    created_at: z.string(),
    job_title: z.string().nullable().optional(),
    job_company: z.string().nullable().optional(),
    job_url: z.string().nullable().optional(),
    job_region: z.string().nullable().optional(),
    job: MatchJobSchema.nullable().optional(),
  })
  .passthrough();
export type MatchListItem = z.infer<typeof MatchListItemSchema>;

// POST /cv/suggest-roles
export const SuggestRolesResponseSchema = z.object({
  roles: z.array(z.string()),
});
export type SuggestRolesResponse = z.infer<typeof SuggestRolesResponseSchema>;

// GET /matches/{id} — detail (adds signed_cv_url)
export const MatchDetailSchema = z
  .object({
    id: z.string(),
    run_id: z.string().nullable().optional(),
    status: z.string().nullable().optional(),
    fit_score: z.number().nullable().optional(),
    b2b_eligible: z.string().nullable().optional(),
    job_posted_date: z.string().nullable().optional(),
    analysis: MatchAnalysisSchema.nullable().optional(),
    cover_letter: z.string().nullable().optional(),
    ats_report: z.string().nullable().optional(),
    job: MatchJobSchema.nullable().optional(),
    signed_cv_url: z.string().nullable().optional(),
  })
  .passthrough();
export type MatchDetail = z.infer<typeof MatchDetailSchema>;

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

"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowUpRight,
  ChevronDown,
  ChevronUp,
  Download,
  FileText,
  Loader2,
  Package,
  RefreshCw,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import {
  generateMatchPackage,
  GenerateConflictError,
  getMatches,
  getMatch,
} from "@/lib/api";
import type { MatchListItem, MatchDetail } from "@/lib/schemas";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Visual boundary for the fit score split. Cards with fit_score >= LOW_FIT_BOUNDARY
 * are shown expanded; cards below are grouped under a collapsible "low-fit" section.
 * This is a frontend display constant — not wired to billing or thresholds.
 */
const LOW_FIT_BOUNDARY = 45;

/**
 * Placeholder free-package quota (NOT wired to billing/Lemon Squeezy).
 * Displayed as a simple counter: FREE_PACKAGES minus count of 'done' matches.
 * Replace with real quota from the backend when billing is implemented.
 */
const FREE_PACKAGES = 20;

// ---------------------------------------------------------------------------
// STEP 5 — recruiter_verdict parse helper (pure, exported for tests)
// ---------------------------------------------------------------------------

export type VerdictCategory = "shortlist" | "maybe" | "reject";

/**
 * Parse the leading word of a recruiter_verdict string (case-insensitive) into
 * one of the three recognised categories, or null if the format doesn't match.
 * The full text is always surfaced in the UI — this function only determines the
 * colour category of the chip.
 */
export function parseVerdictCategory(
  verdict: string | null | undefined,
): VerdictCategory | null {
  if (!verdict) return null;
  const first =
    verdict.trim().split(/\s+/)[0]?.toLowerCase().replace(/\W+$/, "") ?? "";
  if (first === "shortlist") return "shortlist";
  if (first === "maybe") return "maybe";
  if (first === "reject") return "reject";
  return null;
}

// ---------------------------------------------------------------------------
// URL safety guard (FIX-2: prevent javascript: XSS via scraped hrefs)
// ---------------------------------------------------------------------------

/**
 * Returns the URL only if it uses http or https.  Any other scheme
 * (javascript:, data:, etc.) returns null so no href is emitted.
 */
function safeHttpUrl(u: string | null | undefined): string | null {
  return u && /^https?:\/\//i.test(u) ? u : null;
}

// ---------------------------------------------------------------------------
// Region enum values (matches backend domain)
// ---------------------------------------------------------------------------

const REGION_OPTIONS = ["WORLDWIDE", "EUROPE", "US-ONLY", "UNKNOWN"] as const;
type RegionOption = (typeof REGION_OPTIONS)[number];

type SortKey = "fit" | "newest";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(isoString: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(new Date(isoString));
  } catch {
    return isoString;
  }
}

/**
 * Compute a human-readable relative age from an ISO date string.
 * Returns strings like "3 days ago", "2 weeks ago", "1 month ago".
 * Returns null if the string cannot be parsed.
 */
export function relativeAge(isoString: string | null | undefined): string | null {
  if (!isoString) return null;
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return null;
    const diffMs = Date.now() - d.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays < 1) return "today";
    if (diffDays === 1) return "1 day ago";
    if (diffDays < 14) return `${diffDays} days ago`;
    const diffWeeks = Math.floor(diffDays / 7);
    if (diffWeeks < 5) return diffWeeks === 1 ? "1 week ago" : `${diffWeeks} weeks ago`;
    const diffMonths = Math.floor(diffDays / 30);
    return diffMonths === 1 ? "1 month ago" : `${diffMonths} months ago`;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Verdict chip
// ---------------------------------------------------------------------------

const VERDICT_STYLES: Record<VerdictCategory, string> = {
  shortlist:
    "border-verdict-shortlist text-verdict-shortlist bg-verdict-shortlist/10",
  maybe: "border-verdict-maybe text-verdict-maybe bg-verdict-maybe/10",
  reject: "border-verdict-reject text-verdict-reject bg-verdict-reject/10",
};

interface VerdictChipProps {
  verdict: string;
}

function VerdictChip({ verdict }: VerdictChipProps) {
  const category = parseVerdictCategory(verdict);
  const styles = category
    ? VERDICT_STYLES[category]
    : "border-border text-muted-foreground bg-card";

  // Chip label: the clean category (Shortlist/Maybe/Reject) when recognized,
  // else the leading word with trailing punctuation stripped. Full verdict text
  // is shown verbatim in the expanded "Recruiter verdict" section.
  const label = category
    ? category.charAt(0).toUpperCase() + category.slice(1)
    : (verdict.trim().split(/\s+/)[0]?.replace(/\W+$/, "") ?? "");

  return (
    <span
      className={`inline-flex items-center rounded-[--radius-sm] border px-2 py-0.5 text-xs font-medium ${styles}`}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Keyword chip (ATS present / missing)
// ---------------------------------------------------------------------------

interface KeywordChipProps {
  word: string;
  variant: "present" | "missing";
}

function KeywordChip({ word, variant }: KeywordChipProps) {
  return (
    <span
      className={[
        "inline-flex items-center rounded-[--radius-sm] border px-2 py-0.5 text-xs",
        variant === "present"
          ? "border-verdict-shortlist/40 bg-verdict-shortlist/10 text-verdict-shortlist"
          : "border-verdict-reject/40 bg-verdict-reject/10 text-verdict-reject",
      ].join(" ")}
    >
      {word}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Generate package button — driven by generation_status
// ---------------------------------------------------------------------------

interface GenerateButtonProps {
  matchId: string;
  initialStatus: string | null | undefined;
  /** Called when generation reaches 'done' so the card can refresh documents. */
  onDone: (detail: MatchDetail) => void;
}

function GenerateButton({ matchId, initialStatus, onDone }: GenerateButtonProps) {
  const [status, setStatus] = useState<string>(initialStatus ?? "none");
  const [isStarting, setIsStarting] = useState(false);
  const queryClient = useQueryClient();

  // Poll GET /matches/{id} while generating.
  const { data: polledDetail } = useQuery({
    queryKey: ["match", matchId, "poll"],
    queryFn: () => getMatch(matchId),
    enabled: status === "generating",
    refetchInterval: (query) => {
      const gs = query.state.data?.generation_status;
      if (gs === "done" || gs === "failed") return false;
      return 2500;
    },
    retry: false,
    staleTime: 0,
  });

  // Track previous poll status to detect transitions.
  const prevPollStatus = useRef<string | null>(null);

  useEffect(() => {
    const gs = polledDetail?.generation_status;
    if (!gs || gs === prevPollStatus.current) return;
    prevPollStatus.current = gs;
    setStatus(gs);
    if (gs === "done") {
      // Invalidate the match list so the list item updates generation_status.
      void queryClient.invalidateQueries({ queryKey: ["matches"] });
      onDone(polledDetail);
    }
    if (gs === "failed") {
      toast.error("Package generation failed. Try again.");
    }
  }, [polledDetail, onDone, queryClient]);

  async function handleGenerate() {
    setIsStarting(true);
    try {
      await generateMatchPackage(matchId);
      setStatus("generating");
    } catch (err) {
      if (err instanceof GenerateConflictError) {
        toast.error("Generation is already in progress for this match.");
        setStatus("generating");
      } else {
        toast.error(
          err instanceof Error ? err.message : "Couldn't start package generation.",
        );
      }
    } finally {
      setIsStarting(false);
    }
  }

  if (status === "none" || status === "failed") {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() => void handleGenerate()}
        disabled={isStarting}
        className="gap-1.5 text-xs"
      >
        {isStarting ? (
          <Loader2 className="size-3 animate-spin" />
        ) : status === "failed" ? (
          <RefreshCw className="size-3" />
        ) : (
          <Package className="size-3" />
        )}
        {status === "failed" ? "Retry generation" : "Generate package"}
      </Button>
    );
  }

  if (status === "generating") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3 animate-spin" />
        Generating…
      </span>
    );
  }

  // status === "done" — show nothing here; documents section will show links.
  return null;
}

// ---------------------------------------------------------------------------
// CV download — fetches signed_cv_url on demand
// ---------------------------------------------------------------------------

interface CvDownloadProps {
  matchId: string;
  /** Pre-loaded detail from polling; avoids a redundant fetch if already available. */
  preloadedDetail?: MatchDetail | null;
}

function CvDownload({ matchId, preloadedDetail }: CvDownloadProps) {
  const [triggered, setTriggered] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["match", matchId],
    queryFn: () => getMatch(matchId),
    enabled: triggered && !preloadedDetail,
    staleTime: 30_000, // signed URL is short-lived; re-fetch if stale
    retry: false,
  });

  useEffect(() => {
    if (isError) {
      toast.error(
        error instanceof Error ? error.message : "Couldn't fetch CV link",
      );
    }
  }, [isError, error]);

  const detail = preloadedDetail ?? data;
  const signedUrl = safeHttpUrl(detail?.signed_cv_url);

  if (!triggered && !preloadedDetail) {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() => setTriggered(true)}
        className="gap-1.5 text-xs"
      >
        <Download className="size-3" />
        Download CV
      </Button>
    );
  }

  if (isLoading && !preloadedDetail) {
    return <Skeleton className="h-8 w-28" />;
  }

  if (signedUrl) {
    return (
      <a
        href={signedUrl}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(
          buttonVariants({ variant: "outline", size: "sm" }),
          "gap-1.5 text-xs",
        )}
      >
        <Download className="size-3" />
        Download CV
      </a>
    );
  }

  return (
    <span className="text-xs text-muted-foreground">CV not available</span>
  );
}

// ---------------------------------------------------------------------------
// Match card
// ---------------------------------------------------------------------------

interface MatchCardProps {
  match: MatchListItem;
}

function MatchCard({ match }: MatchCardProps) {
  // Derive display values — only render each element if the field is present
  const title = match.job?.title ?? match.job_title;
  const company = match.job?.company ?? match.job_company;
  const url = safeHttpUrl(match.job?.url ?? match.job_url);
  const region = match.job?.region ?? match.job_region;
  const fitScore = match.fit_score;
  const b2bEligible = match.b2b_eligible;
  const analysis = match.analysis;
  const reason = analysis?.reason;
  const recruiterVerdict = analysis?.recruiter_verdict;
  const atsPresent = analysis?.ats_present ?? [];
  const atsMissing = analysis?.ats_missing ?? [];
  const gaps = analysis?.gaps;

  // Generation status — drives the Generate button state.
  const genStatus = match.generation_status;

  // When generation completes, we receive the full MatchDetail from polling.
  // Store it locally so documents appear immediately without re-fetching.
  const [doneDetail, setDoneDetail] = useState<MatchDetail | null>(null);

  const coverLetter = doneDetail?.cover_letter ?? match.cover_letter;
  const atsReport = doneDetail?.ats_report ?? match.ats_report;

  // Determine fit score colour (optional visual accent, no fabrication)
  function fitScoreClass(score: number): string {
    if (score >= 75) return "text-verdict-shortlist";
    if (score >= 50) return "text-verdict-maybe";
    return "text-muted-foreground";
  }

  const hasExpandable =
    (atsPresent.length > 0 && atsPresent.some(Boolean)) ||
    (atsMissing.length > 0 && atsMissing.some(Boolean)) ||
    !!gaps ||
    !!recruiterVerdict ||
    !!coverLetter ||
    !!atsReport ||
    true; // Generate button / CV download is always available

  return (
    <div className="rounded-[--radius] border border-border bg-card">
      {/* Card body — always visible */}
      <div className="p-4">
        {/* Title + company + external link */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              {title && (
                <span className="text-sm font-semibold text-foreground">
                  {title}
                </span>
              )}
              {company && (
                <span className="text-sm text-muted-foreground">
                  @ {company}
                </span>
              )}
              {url && (
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 text-xs text-primary hover:underline"
                  aria-label={`Open job posting${title ? ` for ${title}` : ""}`}
                >
                  <ArrowUpRight className="size-3" />
                  View job
                </a>
              )}
            </div>
          </div>

          {/* fit_score — large mono number */}
          {fitScore !== null && fitScore !== undefined && (
            <div className="shrink-0 text-right">
              <span
                className={`font-mono text-2xl font-light ${fitScoreClass(fitScore)}`}
              >
                {fitScore}
              </span>
              <span className="ml-0.5 text-xs text-muted-foreground">/100</span>
            </div>
          )}
        </div>

        {/* Badges row */}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {region && (
            <Badge
              variant="outline"
              className="border-border bg-card text-xs text-muted-foreground"
            >
              {region}
            </Badge>
          )}

          {/* b2b_eligible — neutral label chip (NOT verdict colours) */}
          {b2bEligible && (
            <Badge
              variant="outline"
              className="border-border bg-card text-xs text-muted-foreground"
            >
              B2B: {b2bEligible}
            </Badge>
          )}

          {/* recruiter_verdict chip — coloured by category */}
          {recruiterVerdict && <VerdictChip verdict={recruiterVerdict} />}

          {/* Vacancy posting date — real date from the source, NOT the run date.
              If job_posted_date is present: show it + relative age.
              If absent/null: show an explicit "Date unknown" marker.
              The run date (created_at) is shown separately as "Found …" so it
              is never mistaken for the vacancy's posting date. */}
          <span className="ml-auto font-mono text-xs text-muted-foreground">
            {match.job_posted_date
              ? (() => {
                  const age = relativeAge(match.job_posted_date);
                  return age
                    ? `${formatDate(match.job_posted_date)} · ${age}`
                    : formatDate(match.job_posted_date);
                })()
              : "Date unknown"}
          </span>
          {match.created_at && (
            <span className="font-mono text-xs text-muted-foreground/60">
              Found {formatDate(match.created_at)}
            </span>
          )}
        </div>

        {/* reason — short analysis text */}
        {reason && (
          <p className="mt-2.5 text-xs leading-relaxed text-muted-foreground">
            {reason}
          </p>
        )}
      </div>

      {/* Expandable details */}
      {hasExpandable && (
        <>
          <Separator className="bg-border" />
          <Accordion>
            <AccordionItem value="details" className="border-0">
              <AccordionTrigger className="px-4 py-2.5 text-xs text-muted-foreground hover:no-underline">
                Details
              </AccordionTrigger>
              <AccordionContent className="px-4 pb-4">
                <div className="space-y-4">
                  {/* ATS keywords */}
                  {(atsPresent.filter(Boolean).length > 0 ||
                    atsMissing.filter(Boolean).length > 0) && (
                    <div className="space-y-2">
                      <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                        ATS KEYWORDS
                      </p>
                      {atsPresent.filter(Boolean).length > 0 && (
                        <div>
                          <p className="mb-1 text-xs text-muted-foreground">
                            Present
                          </p>
                          <div className="flex flex-wrap gap-1">
                            {atsPresent.filter(Boolean).map((kw) => (
                              <KeywordChip
                                key={kw}
                                word={kw}
                                variant="present"
                              />
                            ))}
                          </div>
                        </div>
                      )}
                      {atsMissing.filter(Boolean).length > 0 && (
                        <div>
                          <p className="mb-1 text-xs text-muted-foreground">
                            Missing
                          </p>
                          <div className="flex flex-wrap gap-1">
                            {atsMissing.filter(Boolean).map((kw) => (
                              <KeywordChip
                                key={kw}
                                word={kw}
                                variant="missing"
                              />
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Gaps */}
                  {gaps && (
                    <div className="space-y-1">
                      <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                        GAPS
                      </p>
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        {gaps}
                      </p>
                    </div>
                  )}

                  {/* Full recruiter verdict */}
                  {recruiterVerdict && (
                    <div className="space-y-1">
                      <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                        RECRUITER VERDICT
                      </p>
                      <p className="text-xs leading-relaxed text-foreground">
                        {recruiterVerdict}
                      </p>
                    </div>
                  )}

                  {/* Documents */}
                  <div className="space-y-3">
                    <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                      DOCUMENTS
                    </p>

                    {/* Generate package button — driven by generation_status */}
                    {genStatus !== "done" && doneDetail === null && (
                      <GenerateButton
                        matchId={match.id}
                        initialStatus={genStatus}
                        onDone={setDoneDetail}
                      />
                    )}

                    {/* Cover letter — shown once generation is done */}
                    {coverLetter && (
                      <div className="space-y-1">
                        <div className="flex items-center gap-1.5">
                          <FileText className="size-3 text-muted-foreground" />
                          <p className="text-xs font-medium text-foreground">
                            Cover letter
                          </p>
                        </div>
                        <pre className="whitespace-pre-wrap rounded-[--radius-sm] border border-border bg-background p-3 font-sans text-xs leading-relaxed text-muted-foreground">
                          {coverLetter}
                        </pre>
                      </div>
                    )}

                    {/* ATS report */}
                    {atsReport && (
                      <div className="space-y-1">
                        <div className="flex items-center gap-1.5">
                          <FileText className="size-3 text-muted-foreground" />
                          <p className="text-xs font-medium text-foreground">
                            ATS report
                          </p>
                        </div>
                        <pre className="whitespace-pre-wrap rounded-[--radius-sm] border border-border bg-background p-3 font-sans text-xs leading-relaxed text-muted-foreground">
                          {atsReport}
                        </pre>
                      </div>
                    )}

                    {/* CV download — shown when generation is done */}
                    {(genStatus === "done" || doneDetail !== null) && (
                      <CvDownload
                        matchId={match.id}
                        preloadedDetail={doneDetail}
                      />
                    )}
                  </div>
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function ResultsSkeleton() {
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
      <div className="mb-6 space-y-2">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-6 w-48" />
      </div>
      <div className="mb-4 flex gap-2">
        <Skeleton className="h-9 w-36" />
        <Skeleton className="h-9 w-32" />
      </div>
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-28 w-full" />
        ))}
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Empty states
// ---------------------------------------------------------------------------

function EmptyNoMatches() {
  return (
    <div className="flex flex-col items-center gap-4 rounded-[--radius] border border-border bg-card px-6 py-12 text-center">
      <Search className="size-8 text-muted-foreground" />
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">No matches yet</p>
        <p className="text-xs text-muted-foreground">
          Run a search to generate your first matches.
        </p>
      </div>
      <Link
        href="/search"
        className={buttonVariants({ variant: "outline", size: "sm" })}
      >
        <Search className="size-3.5" />
        Run a search
      </Link>
    </div>
  );
}

interface EmptyFilteredProps {
  onClear: () => void;
}

function EmptyFiltered({ onClear }: EmptyFilteredProps) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-[--radius] border border-border bg-card px-6 py-10 text-center">
      <SlidersHorizontal className="size-7 text-muted-foreground" />
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">
          No matches in this region
        </p>
        <p className="text-xs text-muted-foreground">
          Try a different region or clear the filter.
        </p>
      </div>
      <Button variant="outline" size="sm" onClick={onClear}>
        Clear filter
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Low-fit collapsible section
// ---------------------------------------------------------------------------

interface LowFitSectionProps {
  matches: MatchListItem[];
}

function LowFitSection({ matches }: LowFitSectionProps) {
  const [open, setOpen] = useState(false);

  if (matches.length === 0) return null;

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-[--radius] border border-border bg-card px-4 py-2.5 text-left text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? (
          <ChevronUp className="size-3.5 shrink-0" />
        ) : (
          <ChevronDown className="size-3.5 shrink-0" />
        )}
        <span>
          {open
            ? `Hide ${matches.length} low-fit match${matches.length !== 1 ? "es" : ""}`
            : `${matches.length} more low-fit match${matches.length !== 1 ? "es" : ""} (score < ${LOW_FIT_BOUNDARY})`}
        </span>
      </button>
      {open && (
        <div className="mt-2 space-y-3">
          {matches.map((match) => (
            <MatchCard key={match.id} match={match} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ResultsPage() {
  const [regionFilter, setRegionFilter] = useState<RegionOption | "ALL">("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("fit");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["matches"],
    queryFn: getMatches,
    staleTime: 0,
    retry: false,
  });

  useEffect(() => {
    if (isError) {
      toast.error(
        error instanceof Error ? error.message : "Couldn't load matches",
      );
    }
  }, [isError, error]);

  if (isLoading) {
    return <ResultsSkeleton />;
  }

  const matches = data ?? [];

  // Client-side region filter
  const filtered =
    regionFilter === "ALL"
      ? matches
      : matches.filter((m) => {
          const r = m.job?.region ?? m.job_region;
          return r === regionFilter;
        });

  // b2b rank helper: yes=0, maybe=1, no=2, missing/other=3 (lower = better)
  function b2bRank(b2b: string | null | undefined): number {
    if (b2b === "yes") return 0;
    if (b2b === "maybe") return 1;
    if (b2b === "no") return 2;
    return 3;
  }

  // Client-side sort
  // Primary key: fit_score desc (for "fit") or created_at desc (for "newest").
  // Secondary tiebreak (always): b2b_eligible asc (yes > maybe > no > unknown).
  const sorted = [...filtered].sort((a, b) => {
    if (sortKey === "fit") {
      const aScore = a.fit_score ?? -1;
      const bScore = b.fit_score ?? -1;
      if (bScore !== aScore) return bScore - aScore;
      // Equal fit_score — tiebreak by b2b rank
      return b2bRank(a.b2b_eligible) - b2bRank(b.b2b_eligible);
    }
    // "newest" — created_at desc, tiebreak by b2b rank
    const timeDiff =
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    if (timeDiff !== 0) return timeDiff;
    return b2bRank(a.b2b_eligible) - b2bRank(b.b2b_eligible);
  });

  // Split into high-fit (>= LOW_FIT_BOUNDARY) and low-fit (< LOW_FIT_BOUNDARY).
  const highFit = sorted.filter((m) => (m.fit_score ?? 0) >= LOW_FIT_BOUNDARY);
  const lowFit = sorted.filter((m) => (m.fit_score ?? 0) < LOW_FIT_BOUNDARY);

  // Remaining packages counter: FREE_PACKAGES minus matches with generation_status==='done'.
  // NOT wired to billing — this is a simple placeholder for the UI.
  // Replace with a real quota endpoint when billing (Lemon Squeezy) is implemented.
  const doneCount = matches.filter((m) => m.generation_status === "done").length;
  const remainingPackages = Math.max(0, FREE_PACKAGES - doneCount);

  // Regions present in current data (for filter options)
  const regionsInData = Array.from(
    new Set(matches.map((m) => m.job?.region ?? m.job_region).filter(Boolean)),
  ) as string[];

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
      {/* Page header */}
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
            JOB SEARCH
          </p>
          <h1 className="mt-1 text-xl font-semibold text-foreground">
            Your matches
          </h1>
          {matches.length > 0 && (
            <p className="mt-0.5 font-mono text-sm text-muted-foreground">
              {matches.length} match{matches.length !== 1 ? "es" : ""}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {/* Remaining packages counter — placeholder, NOT wired to billing */}
          {matches.length > 0 && (
            <span className="font-mono text-xs text-muted-foreground" title="Free package quota (placeholder — not wired to billing)">
              <Package className="mb-0.5 mr-0.5 inline size-3" />
              {remainingPackages} left
            </span>
          )}
          <Link
            href="/search"
            className={cn(
              buttonVariants({ variant: "outline", size: "sm" }),
            )}
          >
            <Search className="size-3.5" />
            New search
          </Link>
        </div>
      </div>

      {/* Controls row — only when there are matches */}
      {matches.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {/* Region filter */}
          <Select
            value={regionFilter}
            onValueChange={(v) => setRegionFilter(v as RegionOption | "ALL")}
          >
            <SelectTrigger className="h-8 w-auto min-w-32 text-xs">
              <SelectValue placeholder="Region" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL" className="text-xs">
                All regions
              </SelectItem>
              {REGION_OPTIONS.filter((r) => regionsInData.includes(r)).map(
                (r) => (
                  <SelectItem key={r} value={r} className="text-xs">
                    {r}
                  </SelectItem>
                ),
              )}
            </SelectContent>
          </Select>

          {/* Sort */}
          <Select
            value={sortKey}
            onValueChange={(v) => setSortKey(v as SortKey)}
          >
            <SelectTrigger className="h-8 w-auto min-w-32 text-xs">
              <SelectValue placeholder="Sort by" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="fit" className="text-xs">
                Best fit
              </SelectItem>
              <SelectItem value="newest" className="text-xs">
                Newest
              </SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Match list */}
      {matches.length === 0 ? (
        <EmptyNoMatches />
      ) : sorted.length === 0 ? (
        <EmptyFiltered onClear={() => setRegionFilter("ALL")} />
      ) : (
        <div className="space-y-3">
          {/* High-fit cards (>= LOW_FIT_BOUNDARY) — shown expanded */}
          {highFit.map((match) => (
            <MatchCard key={match.id} match={match} />
          ))}

          {/* Low-fit section (< LOW_FIT_BOUNDARY) — collapsible */}
          <LowFitSection matches={lowFit} />
        </div>
      )}
    </main>
  );
}

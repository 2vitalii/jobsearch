"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowUpRight,
  Download,
  FileText,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { getMatches, getMatch } from "@/lib/api";
import type { MatchListItem } from "@/lib/schemas";
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
// CV download — fetches signed_cv_url on demand
// ---------------------------------------------------------------------------

interface CvDownloadProps {
  matchId: string;
}

function CvDownload({ matchId }: CvDownloadProps) {
  const [triggered, setTriggered] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["match", matchId],
    queryFn: () => getMatch(matchId),
    enabled: triggered,
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

  const signedUrl = safeHttpUrl(data?.signed_cv_url);

  if (!triggered) {
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

  if (isLoading) {
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
  const coverLetter = match.cover_letter;
  const atsReport = match.ats_report;

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
    true; // CV download is always available (it attempts fetch)

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

                    {/* Cover letter */}
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

                    {/* CV download — fetches signed_cv_url on demand */}
                    <CvDownload matchId={match.id} />
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

  // Client-side sort
  const sorted = [...filtered].sort((a, b) => {
    if (sortKey === "fit") {
      const aScore = a.fit_score ?? -1;
      const bScore = b.fit_score ?? -1;
      return bScore - aScore;
    }
    // "newest" — created_at desc
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

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
        <Link
          href="/search"
          className={cn(
            buttonVariants({ variant: "outline", size: "sm" }),
            "shrink-0",
          )}
        >
          <Search className="size-3.5" />
          New search
        </Link>
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
          {sorted.map((match) => (
            <MatchCard key={match.id} match={match} />
          ))}
        </div>
      )}
    </main>
  );
}

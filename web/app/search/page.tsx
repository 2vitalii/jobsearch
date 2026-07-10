"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { MapPin, Sparkles, X } from "lucide-react";
import {
  getCv,
  getSearchParams,
  putSearchParams,
  startRun,
  getRun,
  getLatestRun,
  suggestRolesFromCV,
  RunConflictError,
} from "@/lib/api";
import type { SearchParams, RunStatus } from "@/lib/schemas";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

// ---------- constants ----------

// Period presets with string values so ToggleGroup (Value extends string) works cleanly.
const PERIOD_PRESETS: { label: string; strVal: string; hours: number }[] = [
  { label: "24h", strVal: "24", hours: 24 },
  { label: "7 days", strVal: "168", hours: 168 },
  { label: "30 days", strVal: "720", hours: 720 },
];

const WORK_FORMAT_OPTIONS: { label: string; value: string }[] = [
  { label: "Remote", value: "remote" },
  { label: "Hybrid", value: "hybrid" },
  { label: "On-site", value: "onsite" },
];

const DEFAULT_SEARCH_PARAMS: SearchParams = {
  keywords: [],
  locations: [],
  period_hours: 168,
  work_format: "remote",
  loose: false,
  targeted: false,
  exclude_senior: false,
};

const POLL_INTERVAL_MS = 2500;

/**
 * Real search sources — mirrors jobsearch/sources.py (9 sources).
 * Keep in sync with: linkedin, indeed, google, RemoteOK, WeWorkRemotely,
 * Remotive, Greenhouse, Lever, Ashby.
 */
const SEARCH_SOURCES = [
  "LinkedIn",
  "Indeed",
  "Google",
  "RemoteOK",
  "WeWorkRemotely",
  "Remotive",
  "Greenhouse",
  "Lever",
  "Ashby",
] as const;

// ---------- form state ----------

interface FormState {
  keywords: string[];
  locations: string[];
  periodHours: number;
  /** String value from the custom hours input (may be empty while user types). */
  customPeriod: string;
  /** When true the custom-hours input is active; period preset segmented control is unselected. */
  useCustomPeriod: boolean;
  workFormat: string;
  excludeSenior: boolean;
}

function makeFormState(p: SearchParams): FormState {
  const preset = PERIOD_PRESETS.find((pr) => pr.hours === p.period_hours);
  return {
    keywords: p.keywords,
    locations: p.locations,
    periodHours: p.period_hours,
    customPeriod: preset ? "" : String(p.period_hours),
    useCustomPeriod: !preset,
    workFormat: p.work_format,
    excludeSenior: p.exclude_senior,
  };
}

// ---------- chip input ----------

interface ChipsInputProps {
  label: string;
  placeholder: string;
  chips: string[];
  onChange: (chips: string[]) => void;
  leadingGlyph?: "hash" | "pin";
}

function ChipsInput({
  label,
  placeholder,
  chips,
  onChange,
  leadingGlyph = "hash",
}: ChipsInputProps) {
  const [inputValue, setInputValue] = useState("");
  const inputId = `chips-${label.toLowerCase().replace(/\s+/g, "-")}`;

  function addChip(raw: string) {
    const value = raw.trim();
    if (!value || chips.includes(value)) return;
    onChange([...chips, value]);
    setInputValue("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addChip(inputValue);
    } else if (e.key === "Backspace" && inputValue === "" && chips.length > 0) {
      onChange(chips.slice(0, -1));
    }
  }

  function handleBlur() {
    if (inputValue.trim()) addChip(inputValue);
  }

  function removeChip(chip: string) {
    onChange(chips.filter((c) => c !== chip));
  }

  return (
    <div className="space-y-1.5">
      <label
        htmlFor={inputId}
        className="block text-[15px] font-semibold text-form-label"
      >
        {label}
      </label>
      <div className="flex min-h-10 w-full flex-wrap items-center gap-1.5 rounded-[--radius] border border-input bg-input px-2.5 py-2 text-sm focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50">
        {chips.map((chip) => (
          <span
            key={chip}
            className="inline-flex items-center gap-1 rounded-[--radius-sm] bg-card px-2 py-0.5 text-sm text-form-label"
          >
            {leadingGlyph === "hash" ? (
              <span
                aria-hidden="true"
                className="font-mono text-muted-foreground"
              >
                #
              </span>
            ) : (
              <MapPin
                aria-hidden="true"
                className="size-3 text-muted-foreground"
              />
            )}
            {chip}
            <button
              type="button"
              onClick={() => removeChip(chip)}
              className="ml-0.5 text-muted-foreground hover:text-foreground"
              aria-label={`Remove ${chip}`}
            >
              <X className="size-3" />
            </button>
          </span>
        ))}
        <input
          id={inputId}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          placeholder={chips.length === 0 ? placeholder : ""}
          className="min-w-28 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      <p className="text-xs text-muted-foreground">Enter or comma to add</p>
    </div>
  );
}

// ---------- segmented control (string values) ----------

interface SegmentedControlProps {
  options: { label: string; value: string }[];
  value: string;
  onChange: (value: string) => void;
}

function SegmentedControl({ options, value, onChange }: SegmentedControlProps) {
  return (
    <ToggleGroup
      multiple={false}
      value={value ? [value] : []}
      onValueChange={(vals: string[]) => {
        if (vals.length > 0) onChange(vals[0]);
      }}
      spacing={0}
      className="w-fit rounded-[--radius] border border-input"
    >
      {options.map((opt) => (
        <ToggleGroupItem
          key={opt.value}
          value={opt.value}
          variant="default"
          size="sm"
          className="h-8 rounded-none px-3 text-sm text-muted-foreground first:rounded-l-[--radius-sm] last:rounded-r-[--radius-sm] aria-pressed:bg-segment-active aria-pressed:text-foreground"
        >
          {opt.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

// ---------- aggregate progress bar ----------

interface RunProgressBarProps {
  status: RunStatus | null | undefined;
}

function RunProgressBar({ status }: RunProgressBarProps) {
  // to_process = processed (size of scoring queue)
  // done_so_far = generated + skipped_low_fit (jobs that completed scoring)
  const toProcess = status?.processed ?? 0;
  const doneSoFar = (status?.generated ?? 0) + (status?.skipped_low_fit ?? 0);
  const percent = toProcess > 0 ? Math.round((doneSoFar / toProcess) * 100) : 0;

  // Early scraping phase: run is "running" but no jobs queued for scoring yet.
  const isEarlyScraping = status?.status === "running" && toProcess === 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        {isEarlyScraping ? (
          <span className="text-sm text-muted-foreground">Scraping…</span>
        ) : (
          <span className="text-sm text-muted-foreground">
            Processing{" "}
            <span className="font-mono text-foreground">{doneSoFar}</span>
            {" / "}
            <span className="font-mono text-foreground">{toProcess}</span>
          </span>
        )}
        {!isEarlyScraping && (
          <span className="font-mono text-sm text-muted-foreground">
            {percent}%
          </span>
        )}
      </div>
      {/* bg-card track + bg-primary fill; semantic classes only, no raw hex */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-card">
        <div
          className="h-full bg-primary transition-all"
          style={{ width: `${isEarlyScraping ? 0 : percent}%` }}
          role="progressbar"
          aria-valuenow={isEarlyScraping ? 0 : percent}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
    </div>
  );
}

// ---------- left-column search form ----------

interface SearchFormProps {
  form: FormState;
  setForm: React.Dispatch<React.SetStateAction<FormState>>;
  hasCv: boolean;
  onSuggestRoles: () => void;
  isSuggesting: boolean;
  /** Roles returned by the LLM but not yet added to keywords (add-buttons). */
  pendingRoles: string[];
  onAddPendingRole: (role: string) => void;
}

function SearchForm({
  form,
  setForm,
  hasCv,
  onSuggestRoles,
  isSuggesting,
  pendingRoles,
  onAddPendingRole,
}: SearchFormProps) {
  const patch = useCallback(
    (partial: Partial<FormState>) =>
      setForm((prev) => ({ ...prev, ...partial })),
    [setForm],
  );

  const handleCustomPeriodChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value;
      const parsed = parseInt(raw, 10);
      patch({
        customPeriod: raw,
        periodHours: !isNaN(parsed) && parsed > 0 ? parsed : form.periodHours,
      });
    },
    [patch, form.periodHours],
  );

  // Active preset string value, or "" when custom is selected.
  const activePeriodStrVal = form.useCustomPeriod
    ? ""
    : (PERIOD_PRESETS.find((p) => p.hours === form.periodHours)?.strVal ?? "");

  return (
    <div className="space-y-6">
      {/* Keywords */}
      <div className="space-y-2">
        <ChipsInput
          label="Keywords"
          placeholder="Add keyword…"
          chips={form.keywords}
          onChange={(v) => patch({ keywords: v })}
          leadingGlyph="hash"
        />
        {/* "Suggest from CV" button — shown only when user has a CV */}
        {hasCv && (
          <button
            type="button"
            onClick={onSuggestRoles}
            disabled={isSuggesting}
            className="inline-flex items-center gap-1.5 rounded-[--radius] border border-input bg-transparent px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Sparkles className="size-3" />
            {isSuggesting ? "Suggesting…" : "Suggest from CV"}
          </button>
        )}
        {/* Clickable add-buttons for remaining suggested roles (not yet added) */}
        {pendingRoles.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {pendingRoles.map((role) => (
              <button
                key={role}
                type="button"
                onClick={() => onAddPendingRole(role)}
                className="inline-flex items-center gap-1 rounded-[--radius-sm] border border-input bg-transparent px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:border-ring hover:text-foreground"
              >
                <span aria-hidden="true">+</span>
                {role}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Locations */}
      <ChipsInput
        label="Locations"
        placeholder="Add location…"
        chips={form.locations}
        onChange={(v) => patch({ locations: v })}
        leadingGlyph="pin"
      />

      {/* Posted within */}
      <div className="space-y-2">
        <p className="text-[15px] font-semibold text-form-label">
          Posted within
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl
            options={PERIOD_PRESETS.map((p) => ({
              label: p.label,
              value: p.strVal,
            }))}
            value={activePeriodStrVal}
            onChange={(strVal) => {
              const preset = PERIOD_PRESETS.find((p) => p.strVal === strVal);
              if (preset)
                patch({
                  periodHours: preset.hours,
                  useCustomPeriod: false,
                  customPeriod: "",
                });
            }}
          />
          <button
            type="button"
            onClick={() => patch({ useCustomPeriod: true })}
            className={[
              "flex h-8 items-center rounded-[--radius] border px-3 text-sm transition-colors",
              form.useCustomPeriod
                ? "border-ring bg-segment-active text-foreground"
                : "border-input bg-transparent text-muted-foreground hover:text-foreground",
            ].join(" ")}
          >
            Custom{" "}
            <span className="ml-1 font-mono text-muted-foreground">hrs</span>
          </button>
          {form.useCustomPeriod && (
            <input
              type="number"
              min={1}
              value={form.customPeriod}
              onChange={handleCustomPeriodChange}
              placeholder="120"
              className="h-8 w-20 rounded-[--radius] border border-input bg-input px-2 font-mono text-sm text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
            />
          )}
        </div>
      </div>

      {/* Work format */}
      <div className="space-y-2">
        <p className="text-[15px] font-semibold text-form-label">Work format</p>
        <SegmentedControl
          options={WORK_FORMAT_OPTIONS}
          value={form.workFormat}
          onChange={(v) => patch({ workFormat: v })}
        />
      </div>

      {/* Exclude senior */}
      <div className="flex items-center gap-3">
        <Switch
          id="exclude-senior"
          checked={form.excludeSenior}
          onCheckedChange={(checked) => patch({ excludeSenior: checked })}
        />
        <label
          htmlFor="exclude-senior"
          className="cursor-pointer text-[15px] font-semibold text-form-label"
        >
          Exclude senior
        </label>
      </div>
    </div>
  );
}

// ---------- main page ----------

export default function SearchPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  /**
   * Run id set by user actions (startMutation success / 409-conflict attach).
   * "__latest__" = attach to the most recent in-progress run.
   * null = no user-initiated run in this session.
   */
  const [userRunId, setUserRunId] = useState<string | null>(null);

  // Guard against double navigation / double toast on terminal status.
  const finalizedRef = useRef(false);

  // All form state in a single object to avoid cascading setState in effects.
  const [form, setForm] = useState<FormState>(
    makeFormState(DEFAULT_SEARCH_PARAMS),
  );

  // Suggested roles returned by LLM but not yet added to keywords.
  const [pendingRoles, setPendingRoles] = useState<string[]>([]);

  // ---------- 1. Check latest run on mount (restore-after-reload) ----------
  const {
    data: latestRun,
    isLoading: latestRunLoading,
    isFetched: latestRunFetched,
  } = useQuery({
    queryKey: ["run", "latest"],
    queryFn: getLatestRun,
    staleTime: 0,
    retry: false,
  });

  // Derive active run id.
  // User-initiated run takes priority; fall back to "running" latest run on
  // page reload (restore-after-reload path).
  const restoredRunId =
    latestRunFetched && latestRun?.status === "running" && userRunId === null
      ? "__latest__"
      : null;

  const activeRunId = userRunId ?? restoredRunId;
  const inProgressMode = activeRunId !== null;

  // ---------- 1b. Check whether the user has a CV (gates "Suggest from CV" button) ----------
  const { data: cvData } = useQuery({
    queryKey: ["cv"],
    queryFn: getCv,
    staleTime: 60_000, // re-check at most once per minute
    retry: false,
  });
  const hasCv = cvData !== null && cvData !== undefined;

  // ---------- 2. Load saved search params ----------
  const {
    data: savedParams,
    isLoading: paramsLoading,
    isFetched: paramsFetched,
    isError: paramsError,
    error: paramsErrorObj,
  } = useQuery({
    queryKey: ["search-params"],
    queryFn: getSearchParams,
  });

  // Seed form state once saved params arrive (single setState call via makeFormState).
  const seededRef = useRef(false);
  useEffect(() => {
    if (paramsFetched && !seededRef.current) {
      seededRef.current = true;
      setForm(makeFormState(savedParams ?? DEFAULT_SEARCH_PARAMS));
    }
  }, [paramsFetched, savedParams]);

  useEffect(() => {
    if (paramsError) {
      toast.error(
        paramsErrorObj instanceof Error
          ? paramsErrorObj.message
          : "Couldn't load search params",
      );
    }
  }, [paramsError, paramsErrorObj]);

  // ---------- 3. Poll the active run ----------
  const { data: runStatus, error: runError } = useQuery({
    queryKey: ["run", activeRunId],
    queryFn: () => {
      if (activeRunId === "__latest__") return getLatestRun();
      if (activeRunId) return getRun(activeRunId);
      return null;
    },
    enabled: activeRunId !== null,
    refetchInterval: (query) => {
      const data = query.state.data as RunStatus | null | undefined;
      if (!data) return POLL_INTERVAL_MS;
      if (data.status === "done" || data.status === "failed") return false;
      return POLL_INTERVAL_MS;
    },
    staleTime: 0,
  });

  // Handle terminal run status with a run-once guard (finalizedRef).
  useEffect(() => {
    if (!runStatus) return;
    if (runStatus.status === "done" && !finalizedRef.current) {
      finalizedRef.current = true;
      setUserRunId(null);
      void queryClient.invalidateQueries({ queryKey: ["run"] });
      router.push("/results");
    } else if (runStatus.status === "failed" && !finalizedRef.current) {
      finalizedRef.current = true;
      setUserRunId(null);
      toast.error(runStatus.error ?? "Search run failed");
    }
  }, [runStatus, router, queryClient]);

  useEffect(() => {
    if (runError) {
      toast.error(
        runError instanceof Error
          ? runError.message
          : "Couldn't get run status",
      );
    }
  }, [runError]);

  // ---------- mutations ----------

  const saveMutation = useMutation({
    mutationFn: putSearchParams,
    onSuccess: (data) => {
      queryClient.setQueryData(["search-params"], data);
    },
    onError: (err) => {
      toast.error(
        err instanceof Error ? err.message : "Couldn't save search params",
      );
    },
  });

  const startMutation = useMutation({
    mutationFn: startRun,
    onSuccess: (data) => {
      finalizedRef.current = false;
      setUserRunId(data.run_id);
    },
    onError: (err) => {
      if (err instanceof RunConflictError) {
        // 409: a run is already in progress — attach to it.
        toast.info("A run is already in progress — attaching…");
        finalizedRef.current = false;
        setUserRunId("__latest__");
      } else {
        toast.error(err instanceof Error ? err.message : "Failed to start run");
      }
    },
  });

  const suggestMutation = useMutation({
    mutationFn: suggestRolesFromCV,
    onSuccess: (data) => {
      const roles = data.roles;
      if (roles.length === 0) {
        toast.info("No role suggestions returned — try updating your CV.");
        return;
      }
      // Auto-add the first 3-4 roles to form.keywords (skip duplicates).
      const AUTO_ADD_COUNT = 4;
      const toAdd = roles.slice(0, AUTO_ADD_COUNT);
      const rest = roles.slice(AUTO_ADD_COUNT);
      setForm((prev) => {
        const existing = new Set(prev.keywords);
        const newKeywords = [...prev.keywords];
        for (const role of toAdd) {
          if (!existing.has(role)) {
            existing.add(role);
            newKeywords.push(role);
          }
        }
        return { ...prev, keywords: newKeywords };
      });
      // Remaining roles shown as clickable add-buttons (filter out those already in keywords).
      setForm((prev) => {
        const existing = new Set(prev.keywords);
        setPendingRoles(rest.filter((r) => !existing.has(r)));
        return prev;
      });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to suggest roles");
    },
  });

  // ---------- derived ----------
  const isRunning = runStatus?.status === "running";
  const isBusy = startMutation.isPending || isRunning || inProgressMode;
  const hasKeywords = form.keywords.some((k) => k.trim().length > 0);

  function handleAddPendingRole(role: string) {
    setForm((prev) => {
      if (prev.keywords.includes(role)) return prev;
      return { ...prev, keywords: [...prev.keywords, role] };
    });
    setPendingRoles((prev) => prev.filter((r) => r !== role));
  }

  function buildPayload(): SearchParams {
    return {
      keywords: form.keywords,
      locations: form.locations,
      period_hours: form.periodHours,
      work_format: form.workFormat,
      loose: savedParams?.loose ?? false,
      targeted: savedParams?.targeted ?? false,
      exclude_senior: form.excludeSenior,
    };
  }

  // PUT /search-params then POST /run — single "Run search" action.
  async function handleRunSearch() {
    if (!hasKeywords) return;
    if (form.useCustomPeriod) {
      const parsed = parseInt(form.customPeriod, 10);
      if (isNaN(parsed) || parsed <= 0) {
        toast.error("Enter a valid number of hours");
        return;
      }
    }
    try {
      await saveMutation.mutateAsync(buildPayload());
    } catch {
      // Error already toasted by mutation onError handler.
      return;
    }
    startMutation.mutate();
  }

  // ---------- loading skeleton ----------
  if (latestRunLoading) {
    return (
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        <div className="mb-6 space-y-1">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-6 w-32" />
        </div>
        <div className="grid gap-8 md:grid-cols-[1fr_300px]">
          <div className="space-y-6">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
          <div className="space-y-4">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        </div>
      </main>
    );
  }

  // Live status for the right-panel progress display.
  const displayStatus = inProgressMode ? (runStatus ?? latestRun) : null;

  // ---------- page ----------
  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
      {/* Page header */}
      <div className="mb-6">
        <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
          JOB SEARCH
        </p>
        <h1 className="mt-1 text-xl font-semibold text-foreground">
          New search
        </h1>
      </div>

      {/* Desktop 2-column: left = form, right = run control */}
      <div className="grid gap-0 md:grid-cols-[1fr_300px]">
        {/* LEFT — search params form */}
        <div className="border-border pr-0 md:border-r md:pr-8">
          {paramsLoading ? (
            <div className="space-y-6">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-8 w-40" />
            </div>
          ) : (
            <SearchForm
              form={form}
              setForm={setForm}
              hasCv={hasCv}
              onSuggestRoles={() => suggestMutation.mutate()}
              isSuggesting={suggestMutation.isPending}
              pendingRoles={pendingRoles}
              onAddPendingRole={handleAddPendingRole}
            />
          )}
        </div>

        {/* RIGHT — run control */}
        <div className="mt-8 flex flex-col gap-6 pl-0 md:mt-0 md:pl-8">
          {/* Aggregate progress bar — visible only while a run is active */}
          {inProgressMode && (
            <div className="space-y-3 rounded-[--radius] border border-border bg-card p-4">
              <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                PROGRESS
              </p>
              <RunProgressBar status={displayStatus} />
            </div>
          )}

          {/* Run search — PUT /search-params then POST /run in one click */}
          <div className="space-y-1.5">
            <Button
              className="w-full bg-primary text-primary-foreground hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => void handleRunSearch()}
              disabled={isBusy || !hasKeywords}
            >
              {inProgressMode ? "Running…" : "Run search"}
            </Button>
            {/* STEP 4 — empty-keywords guard: helper text below the button */}
            {!hasKeywords && !isBusy && (
              <p className="text-center text-xs text-muted-foreground">
                Add at least one keyword to run a search
              </p>
            )}
          </div>

          {/* Searched sources — static info list (no live per-source status; deferred) */}
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
              SEARCHED SOURCES
            </p>
            <ul className="divide-y divide-border rounded-[--radius] border border-border">
              {SEARCH_SOURCES.map((source) => (
                <li key={source} className="px-3 py-2 text-sm text-foreground">
                  {source}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </main>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Loader2, Play, X } from "lucide-react";
import {
  getSearchParams,
  putSearchParams,
  startRun,
  getRun,
  getLatestRun,
  RunConflictError,
} from "@/lib/api";
import type { SearchParams, RunStatus } from "@/lib/schemas";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// ---------- constants ----------

const PERIOD_PRESETS: { label: string; value: number }[] = [
  { label: "24 часа", value: 24 },
  { label: "3 дня", value: 72 },
  { label: "Неделя", value: 168 },
];

const WORK_FORMAT_OPTIONS: { label: string; value: string }[] = [
  { label: "Remote", value: "remote" },
  { label: "Hybrid", value: "hybrid" },
  { label: "Onsite", value: "onsite" },
];

const DEFAULT_SEARCH_PARAMS: SearchParams = {
  keywords: [],
  locations: [],
  period_hours: 168,
  work_format: "remote",
  loose: false,
  targeted: false,
};

const POLL_INTERVAL_MS = 2500;

// ---------- helper: chips input ----------

interface ChipsInputProps {
  label: string;
  placeholder: string;
  chips: string[];
  onChange: (chips: string[]) => void;
}

function ChipsInput({ label, placeholder, chips, onChange }: ChipsInputProps) {
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
    if (inputValue.trim()) {
      addChip(inputValue);
    }
  }

  function removeChip(chip: string) {
    onChange(chips.filter((c) => c !== chip));
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{label}</Label>
      <div className="flex min-h-8 w-full flex-wrap items-center gap-1.5 rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
        {chips.map((chip) => (
          <Badge key={chip} variant="secondary" className="gap-1 pr-1">
            {chip}
            <button
              type="button"
              onClick={() => removeChip(chip)}
              className="ml-0.5 rounded-full p-0.5 hover:bg-foreground/10"
              aria-label={`Remove ${chip}`}
            >
              <X className="size-2.5" />
            </button>
          </Badge>
        ))}
        <input
          id={inputId}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          placeholder={chips.length === 0 ? placeholder : ""}
          className="min-w-24 flex-1 bg-transparent outline-none placeholder:text-muted-foreground"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Press Enter or comma to add
      </p>
    </div>
  );
}

// ---------- helper: progress phase text ----------

function getPhaseText(run: RunStatus): string {
  if (run.status === "failed") return "Ошибка выполнения";
  if (run.status === "done") return "Готово";
  if (run.processed > 0 || run.generated > 0) {
    return "Анализируем соответствие…";
  }
  return "Собираем вакансии…";
}

// ---------- SearchForm (child) ----------
// Receives resolved initial params at mount-time; no effect-seeding needed.

interface SearchFormProps {
  initial: SearchParams;
  savedParams: SearchParams | null;
  onSave: (payload: SearchParams) => void;
  onStart: () => void;
  savePending: boolean;
  isBusy: boolean;
}

function SearchForm({
  initial,
  savedParams,
  onSave,
  onStart,
  savePending,
  isBusy,
}: SearchFormProps) {
  const [keywords, setKeywords] = useState<string[]>(initial.keywords);
  const [locations, setLocations] = useState<string[]>(initial.locations);

  const initialPreset = PERIOD_PRESETS.find(
    (p) => p.value === initial.period_hours,
  );
  const [periodHours, setPeriodHours] = useState<number>(initial.period_hours);
  const [customPeriod, setCustomPeriod] = useState<string>(
    initialPreset ? "" : String(initial.period_hours),
  );
  const [useCustomPeriod, setUseCustomPeriod] = useState(!initialPreset);
  const [workFormat, setWorkFormat] = useState<string>(initial.work_format);

  const handlePeriodPreset = useCallback((value: number) => {
    setPeriodHours(value);
    setUseCustomPeriod(false);
  }, []);

  const handleCustomPeriodChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value;
      setCustomPeriod(raw);
      const parsed = parseInt(raw, 10);
      if (!isNaN(parsed) && parsed > 0) {
        setPeriodHours(parsed);
      }
    },
    [],
  );

  function buildPayload(): SearchParams {
    return {
      keywords,
      locations,
      period_hours: periodHours,
      work_format: workFormat,
      loose: savedParams?.loose ?? false,
      targeted: savedParams?.targeted ?? false,
    };
  }

  function handleSave() {
    // FIX 3: guard invalid custom period before saving
    if (useCustomPeriod) {
      const parsed = parseInt(customPeriod, 10);
      if (isNaN(parsed) || parsed <= 0) {
        toast.error("Укажите корректное число часов");
        return;
      }
    }
    onSave(buildPayload());
  }

  function handleStartRun() {
    // FIX 4: guard empty keywords
    if (keywords.length === 0) {
      toast.error("Добавьте хотя бы одно ключевое слово");
      return;
    }
    // FIX 3: guard invalid custom period before starting
    if (useCustomPeriod) {
      const parsed = parseInt(customPeriod, 10);
      if (isNaN(parsed) || parsed <= 0) {
        toast.error("Укажите корректное число часов");
        return;
      }
    }
    onStart();
  }

  const activePeriodPreset = useCustomPeriod
    ? null
    : (PERIOD_PRESETS.find((p) => p.value === periodHours) ?? null);

  return (
    <div className="space-y-6">
      {/* Keywords */}
      <ChipsInput
        label="Ключевые слова"
        placeholder="Python, Backend, ML…"
        chips={keywords}
        onChange={setKeywords}
      />

      {/* Locations */}
      <ChipsInput
        label="Локации"
        placeholder="Remote, Berlin, USA…"
        chips={locations}
        onChange={setLocations}
      />

      {/* Period */}
      <div className="space-y-1.5">
        <Label>Период</Label>
        <div className="flex flex-wrap gap-2">
          {PERIOD_PRESETS.map((preset) => (
            <Button
              key={preset.value}
              variant={
                activePeriodPreset?.value === preset.value
                  ? "default"
                  : "outline"
              }
              size="sm"
              onClick={() => handlePeriodPreset(preset.value)}
              type="button"
            >
              {preset.label}
            </Button>
          ))}
          <Button
            variant={useCustomPeriod ? "default" : "outline"}
            size="sm"
            onClick={() => setUseCustomPeriod(true)}
            type="button"
          >
            Своё
          </Button>
        </div>
        {useCustomPeriod && (
          <div className="mt-2 flex items-center gap-2">
            <Input
              type="number"
              min={1}
              value={customPeriod}
              onChange={handleCustomPeriodChange}
              className="w-28"
              placeholder="часов"
            />
            <span className="text-sm text-muted-foreground">часов</span>
          </div>
        )}
      </div>

      {/* Work format */}
      <div className="space-y-1.5">
        <Label>Формат работы</Label>
        <div className="flex gap-2">
          {WORK_FORMAT_OPTIONS.map((opt) => (
            <Button
              key={opt.value}
              variant={workFormat === opt.value ? "default" : "outline"}
              size="sm"
              onClick={() => setWorkFormat(opt.value)}
              type="button"
            >
              {opt.label}
            </Button>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-3 pt-2">
        <Button variant="outline" onClick={handleSave} disabled={savePending}>
          {savePending ? <Loader2 className="animate-spin" /> : null}
          Сохранить параметры
        </Button>

        <Button onClick={handleStartRun} disabled={isBusy}>
          {isBusy ? <Loader2 className="animate-spin" /> : <Play />}
          Запустить поиск
        </Button>
      </div>
    </div>
  );
}

// ---------- main page ----------

export default function SearchPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  /**
   * Run id set by user actions (startMutation success / conflict).
   * "__latest__" = attach to the most recent run.
   * null = no user-initiated run in this session.
   */
  const [userRunId, setUserRunId] = useState<string | null>(null);

  // FIX 1: guard against double navigation / double toast on terminal status
  const finalizedRef = useRef(false);

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

  // Derive whether we are in progress mode.
  // FIX 2 (restore path): instead of an effect that calls setState, derive activeRunId
  // during render: if a user-initiated run is in flight, use it; otherwise fall back to the
  // latest-run query result when it came back "running" (reload-restore path).
  const restoredRunId =
    latestRunFetched && latestRun?.status === "running" && userRunId === null
      ? "__latest__"
      : null;

  const activeRunId = userRunId ?? restoredRunId;
  const inProgressMode = activeRunId !== null;

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

  // FIX 1 + FIX 2: handle terminal run status with a run-once guard (no setTimeout).
  // finalizedRef prevents double navigation / double toast when TanStack Query
  // re-delivers a cached 'done'/'failed' before invalidation propagates.
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
      toast.error(runStatus.error ?? "Поиск завершился с ошибкой");
    }
  }, [runStatus, router, queryClient]);

  useEffect(() => {
    if (runError) {
      toast.error(
        runError instanceof Error
          ? runError.message
          : "Не удалось получить статус поиска",
      );
    }
  }, [runError]);

  // ---------- mutations ----------

  const saveMutation = useMutation({
    mutationFn: putSearchParams,
    onSuccess: (data) => {
      queryClient.setQueryData(["search-params"], data);
      toast.success("Параметры сохранены.");
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
      // FIX 1: reset finalized guard for a brand-new run
      finalizedRef.current = false;
      setUserRunId(data.run_id);
    },
    onError: (err) => {
      if (err instanceof RunConflictError) {
        toast.info("Поиск уже выполняется");
        // FIX 1: reset finalized guard when attaching to an existing run via 409
        finalizedRef.current = false;
        setUserRunId("__latest__");
      } else {
        toast.error(
          err instanceof Error ? err.message : "Не удалось запустить поиск",
        );
      }
    },
  });

  // ---------- derived ----------
  const isRunning = runStatus?.status === "running";
  const isBusy = startMutation.isPending || isRunning;

  // ---------- loading: waiting to know if there's an active run ----------
  if (latestRunLoading) {
    return (
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8">
        <div className="mb-6 flex items-center gap-2">
          <Skeleton className="h-7 w-20" />
          <Skeleton className="h-5 w-40" />
        </div>
        <div className="space-y-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      </main>
    );
  }

  // ---------- progress mode ----------
  if (inProgressMode) {
    const status = runStatus ?? latestRun;
    const scraped = status?.scraped ?? 0;
    const processed = status?.processed ?? 0;
    const generated = status?.generated ?? 0;
    const phaseText = status ? getPhaseText(status) : "Запускаем поиск…";

    return (
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8">
        <div className="mb-6 flex items-center gap-2">
          <Link
            href="/"
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            <ArrowLeft />
            Back
          </Link>
          <h1 className="text-lg font-semibold">Поиск вакансий</h1>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Loader2 className="size-4 animate-spin text-primary" />
              {phaseText}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4 py-2 text-center">
              <div>
                <p className="text-2xl font-semibold tabular-nums">{scraped}</p>
                <p className="text-xs text-muted-foreground">Собрано</p>
              </div>
              <div>
                <p className="text-2xl font-semibold tabular-nums">
                  {processed}
                </p>
                <p className="text-xs text-muted-foreground">Обработано</p>
              </div>
              <div>
                <p className="text-2xl font-semibold tabular-nums">
                  {generated}
                </p>
                <p className="text-xs text-muted-foreground">Подходящих</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </main>
    );
  }

  // ---------- form mode ----------
  return (
    <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8">
      <div className="mb-6 flex items-center gap-2">
        <Link
          href="/"
          className={buttonVariants({ variant: "ghost", size: "sm" })}
        >
          <ArrowLeft />
          Back
        </Link>
        <h1 className="text-lg font-semibold">Параметры поиска</h1>
      </div>

      {paramsLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-10 w-40" />
        </div>
      ) : paramsFetched ? (
        <SearchForm
          initial={savedParams ?? DEFAULT_SEARCH_PARAMS}
          savedParams={savedParams ?? null}
          onSave={(payload) => saveMutation.mutate(payload)}
          onStart={() => startMutation.mutate()}
          savePending={saveMutation.isPending}
          isBusy={isBusy}
        />
      ) : null}
    </main>
  );
}

"use client";

import { useState } from "react";
import {
  AlignLeft,
  Wrench,
  Briefcase,
  Rocket,
  GraduationCap,
  Info,
  Contact,
  FileText,
  RefreshCw,
  Save,
  Loader2,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  parseCvMarkdown,
  buildCvMarkdown,
  type CvSection,
} from "@/lib/cv-markdown";
import type { Cv } from "@/lib/schemas";

function iconFor(name: string): LucideIcon {
  const n = name.toLowerCase();
  if (n.startsWith("professional summary")) return AlignLeft;
  if (n.startsWith("core skills")) return Wrench;
  if (n.startsWith("professional experience")) return Briefcase;
  if (n.startsWith("project experience")) return Rocket;
  if (n.startsWith("education")) return GraduationCap;
  if (n.startsWith("additional information")) return Info;
  return FileText;
}

function isTailored(name: string): boolean {
  const n = name.toLowerCase();
  return n.startsWith("professional summary") || n.startsWith("core skills");
}

export function CvEditor({
  cv,
  onReplace,
  onSave,
  saving,
}: {
  cv: Cv;
  onReplace: () => void;
  onSave: (markdown: string) => void;
  saving: boolean;
}) {
  const initial = parseCvMarkdown(cv.markdown);
  const [header, setHeader] = useState(initial.header);
  const [sections, setSections] = useState<CvSection[]>(initial.sections);

  function reset() {
    setHeader(initial.header);
    setSections(initial.sections);
  }

  function updateBody(index: number, body: string) {
    setSections((prev) =>
      prev.map((s, i) => (i === index ? { ...s, body } : s)),
    );
  }

  function save() {
    onSave(buildCvMarkdown(header, sections));
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-lg border bg-muted/40 px-4 py-3">
        <p className="text-sm">Parsed from your file — review each section.</p>
        <Button variant="outline" size="sm" onClick={onReplace}>
          <RefreshCw />
          Replace file
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Contact className="size-4 text-muted-foreground" />
            Header
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Label htmlFor="cv-header" className="sr-only">
            Header
          </Label>
          <Textarea
            id="cv-header"
            value={header}
            onChange={(e) => setHeader(e.target.value)}
            className="min-h-16 font-mono text-sm"
          />
        </CardContent>
      </Card>

      {sections.map((section, index) => {
        const tailored = isTailored(section.name);
        // Show the group label when this section's group differs from the
        // previous section's (computed from data, not a render-time mutable).
        const prevTailored =
          index > 0 ? isTailored(sections[index - 1].name) : null;
        const showLabel = tailored !== prevTailored;
        const Icon = iconFor(section.name);

        return (
          <div key={index} className="space-y-2">
            {showLabel ? (
              <p className="px-1 text-xs text-muted-foreground">
                {tailored
                  ? "Tailored per vacancy"
                  : "Fixed core — never rewritten automatically"}
              </p>
            ) : null}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Icon className="size-4 text-muted-foreground" />
                  {section.name}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <Label htmlFor={`cv-section-${index}`} className="sr-only">
                  {section.name}
                </Label>
                <Textarea
                  id={`cv-section-${index}`}
                  value={section.body}
                  onChange={(e) => updateBody(index, e.target.value)}
                  className="min-h-24 font-mono text-sm"
                />
              </CardContent>
            </Card>
          </div>
        );
      })}

      <div className="flex justify-end gap-2 pb-10">
        <Button variant="ghost" onClick={reset} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={save} disabled={saving}>
          {saving ? (
            <>
              <Loader2 className="animate-spin" />
              Saving…
            </>
          ) : (
            <>
              <Save />
              Save
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

"use client";

import { useRef, useState } from "react";
import { Upload, Loader2, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const ACCEPT =
  ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export function CvUpload({
  onSelect,
  pending,
  onCancel,
}: {
  onSelect: (file: File) => void;
  pending: boolean;
  onCancel?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function pick(files: FileList | null) {
    const file = files?.[0];
    if (file) {
      onSelect(file);
    }
  }

  return (
    <Card className="w-full">
      <CardContent>
        {pending ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <Loader2 className="size-8 animate-spin text-primary" />
            <p className="text-sm font-medium">Parsing your résumé…</p>
            <p className="text-sm text-muted-foreground">
              This can take a few seconds while the model reads your file.
            </p>
          </div>
        ) : (
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              pick(e.dataTransfer.files);
            }}
            className={cn(
              "flex flex-col items-center gap-3 rounded-lg border border-dashed p-12 text-center transition-colors",
              dragging ? "border-primary bg-muted/50" : "border-border",
            )}
          >
            <Upload className="size-8 text-muted-foreground" />
            <div className="space-y-1">
              <p className="text-sm font-medium">
                Drag &amp; drop your résumé here
              </p>
              <p className="text-sm text-muted-foreground">
                PDF or DOCX, up to 5 MB
              </p>
            </div>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => pick(e.target.files)}
            />
            <div className="flex gap-2">
              <Button type="button" onClick={() => inputRef.current?.click()}>
                <FileText />
                Choose file
              </Button>
              {onCancel ? (
                <Button type="button" variant="ghost" onClick={onCancel}>
                  Cancel
                </Button>
              ) : null}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

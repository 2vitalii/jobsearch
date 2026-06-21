"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft } from "lucide-react";
import { getCv, uploadCv, putCv } from "@/lib/api";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CvUpload } from "@/components/cv/cv-upload";
import { CvEditor } from "@/components/cv/cv-editor";

export default function CvPage() {
  const queryClient = useQueryClient();
  const [replacing, setReplacing] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["cv"],
    queryFn: getCv,
  });

  useEffect(() => {
    if (isError) {
      toast.error(
        error instanceof Error ? error.message : "Couldn't load your CV",
      );
    }
  }, [isError, error]);

  const uploadMutation = useMutation({
    mutationFn: uploadCv,
    onSuccess: (cv) => {
      queryClient.setQueryData(["cv"], cv);
      setReplacing(false);
      toast.success("Parsed — review each section below.");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Upload failed");
    },
  });

  const saveMutation = useMutation({
    mutationFn: putCv,
    onSuccess: (cv) => {
      queryClient.setQueryData(["cv"], cv);
      toast.success("CV saved.");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Couldn't save your CV");
    },
  });

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
        <h1 className="text-lg font-semibold">Your CV</h1>
      </div>

      {isLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : isError ? (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 p-6">
          <p className="text-sm text-destructive">
            We couldn&apos;t load your CV.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => queryClient.invalidateQueries({ queryKey: ["cv"] })}
          >
            Try again
          </Button>
        </div>
      ) : !data || replacing ? (
        <CvUpload
          onSelect={(file) => uploadMutation.mutate(file)}
          pending={uploadMutation.isPending}
          onCancel={replacing && data ? () => setReplacing(false) : undefined}
        />
      ) : (
        <CvEditor
          key={data.markdown}
          cv={data}
          onReplace={() => setReplacing(true)}
          onSave={(markdown) => saveMutation.mutate(markdown)}
          saving={saveMutation.isPending}
        />
      )}
    </main>
  );
}

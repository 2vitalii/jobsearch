"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, Search } from "lucide-react";
import { getLatestRun } from "@/lib/api";
import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function ResultsPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["run", "latest"],
    queryFn: getLatestRun,
    staleTime: 0,
    retry: false,
  });

  useEffect(() => {
    if (isError) {
      toast.error(
        error instanceof Error ? error.message : "Couldn't load results",
      );
    }
  }, [isError, error]);

  return (
    <main className="flex min-h-full items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CheckCircle2 className="size-5 text-primary" />
            Готово
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-5 w-40" />
          ) : data ? (
            <p className="text-sm text-muted-foreground">
              Найдено подходящих вакансий:{" "}
              <span className="font-medium text-foreground">
                {data.generated}
              </span>
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Результаты поиска готовы.
            </p>
          )}
        </CardContent>
        <CardFooter>
          <Link
            href="/search"
            className={buttonVariants({ variant: "outline" })}
          >
            <Search />
            Новый поиск
          </Link>
        </CardFooter>
      </Card>
    </main>
  );
}

"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { LogOut } from "lucide-react";
import { createClient } from "@/utils/supabase/client";
import { getMe } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function MeCard() {
  const router = useRouter();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["me"],
    queryFn: getMe,
  });

  useEffect(() => {
    if (isError) {
      toast.error(
        error instanceof Error ? error.message : "Could not load your profile",
      );
    }
  }, [isError, error]);

  async function logout() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>You&apos;re signed in</CardTitle>
        <CardDescription>
          Verified end-to-end: Supabase login → token → backend{" "}
          <code className="font-mono">/me</code>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-5 w-56" />
        ) : isError ? (
          <p className="text-sm text-destructive">
            Couldn&apos;t reach the backend.
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            The backend identifies you as{" "}
            <span className="font-medium text-foreground">{data?.email}</span>.
          </p>
        )}
      </CardContent>
      <CardFooter>
        <Button variant="outline" onClick={logout}>
          <LogOut />
          Log out
        </Button>
      </CardFooter>
    </Card>
  );
}

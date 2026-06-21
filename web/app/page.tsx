import { redirect } from "next/navigation";
import { createClient } from "@/utils/supabase/server";
import { MeCard } from "@/components/me-card";

export default async function Home() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    redirect("/login");
  }

  return (
    <main className="flex min-h-full items-center justify-center p-6">
      <MeCard />
    </main>
  );
}

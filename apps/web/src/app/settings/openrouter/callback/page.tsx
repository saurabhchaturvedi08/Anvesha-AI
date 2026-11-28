"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Callout, Spinner } from "@/components/ui/feedback";
import { api, describeError, unwrap } from "@/lib/api";
import { takeOpenRouterState } from "@/lib/openrouter";
import { keys } from "@/lib/queries";

/** Where OpenRouter sends the user back with a one-time code. */
export default function OpenRouterCallbackPage() {
  return (
    <Suspense>
      <Callback />
    </Suspense>
  );
}

async function complete(code: string | null) {
  const state = takeOpenRouterState();
  if (!code) throw new Error("OpenRouter didn't send a code back. Connect again from Settings.");
  if (!state) {
    throw new Error(
      "This sign-in was started in another tab or browser. Connect OpenRouter again from Settings.",
    );
  }
  return unwrap(api.POST("/api/v1/providers/openrouter/oauth/complete", { body: { code, state } }));
}

function Callback() {
  const code = useSearchParams().get("code");
  const router = useRouter();
  const queryClient = useQueryClient();
  // A query keyed by the code: it runs once (the code only works once), even when React
  // mounts the page twice.
  const exchange = useQuery({
    queryKey: ["openrouter-callback", code],
    queryFn: () => complete(code),
    retry: false,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (!exchange.isSuccess) return;
    toast.success("OpenRouter connected", { description: "Usage is billed to your OpenRouter account." });
    void queryClient.invalidateQueries({ queryKey: keys.providers });
    router.replace("/settings");
  }, [exchange.isSuccess, queryClient, router]);

  return (
    <div className="py-10">
      <Card className="mx-auto w-full max-w-md">
        <CardContent className="space-y-4 py-6">
          {exchange.isError ? (
            <>
              <Callout tone="danger" title="OpenRouter isn't connected">
                {describeError(exchange.error)}
              </Callout>
              <Button asChild variant="outline">
                <Link href="/settings">Back to settings</Link>
              </Button>
            </>
          ) : (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner /> Connecting OpenRouter…
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

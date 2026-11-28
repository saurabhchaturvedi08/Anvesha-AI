"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { PlugIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Skeleton, Spinner } from "@/components/ui/feedback";
import { api, describeError, unwrap } from "@/lib/api";
import { useMe } from "@/lib/queries";

/** Approve an AI assistant (claude.ai, ChatGPT...) connecting to Anvesha (multi-user mode). */
export default function ConnectPage() {
  return (
    <Suspense>
      <Consent />
    </Suspense>
  );
}

function Consent() {
  const sealed = useSearchParams().get("request") ?? "";
  const me = useMe();
  const details = useQuery({
    queryKey: ["consent", sealed],
    queryFn: () => unwrap(api.GET("/api/v1/oauth/consent", { params: { query: { request: sealed } } })),
    enabled: !!sealed,
    retry: false,
  });
  const decide = useMutation({
    mutationFn: (decision: "allow" | "deny") =>
      unwrap(api.POST("/api/v1/oauth/consent", { body: { request: sealed, decision } })),
    // Back to the assistant, which finishes connecting.
    onSuccess: ({ redirect_url }) => window.location.assign(redirect_url),
  });

  const workspace = me.data?.workspace;
  return (
    <div className="py-10">
      <Card className="mx-auto w-full max-w-md">
        {!sealed ? (
          <CardContent className="py-6">
            <ErrorState error={new Error("This page is opened by an app that wants to connect.")} />
          </CardContent>
        ) : details.isPending ? (
          <CardContent className="py-6">
            <Skeleton className="h-32" />
          </CardContent>
        ) : details.isError ? (
          <CardContent className="py-6">
            <ErrorState error={details.error} />
          </CardContent>
        ) : (
          <>
            <CardHeader>
              <span className="mb-2 grid size-10 place-items-center rounded-full bg-primary/10 text-primary">
                <PlugIcon className="size-5" />
              </span>
              <CardTitle>{details.data.client_name || "An app"} wants to use Anvesha for you</CardTitle>
              <CardDescription>
                It will act as you in{" "}
                <strong>{workspace ? (workspace.personal ? "your personal workspace" : workspace.name) : "…"}</strong>
                . Switch workspaces from the menu at the top first if you meant another one.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <ul className="list-disc space-y-1 pl-5">
                <li>start and steer research runs, which spend the workspace&apos;s model credits within each run&apos;s limit</li>
                <li>read the workspace&apos;s projects, reports, sources and evidence</li>
                <li>search the web and literature, and save pages to projects</li>
              </ul>
              <p className="text-muted-foreground">
                Afterwards you&apos;ll return to <span className="font-mono">{details.data.redirect_host}</span>. Only
                approve if you started connecting just now. You can disconnect it any time in Settings.
              </p>
              {decide.isError && <p className="text-destructive">{describeError(decide.error)}</p>}
              <div className="flex gap-2">
                <Button variant="outline" className="flex-1" disabled={decide.isPending} onClick={() => decide.mutate("deny")}>
                  Deny
                </Button>
                <Button className="flex-1" disabled={decide.isPending || !workspace} onClick={() => decide.mutate("allow")}>
                  {decide.isPending && <Spinner />}
                  Approve
                </Button>
              </div>
            </CardContent>
          </>
        )}
      </Card>
    </div>
  );
}

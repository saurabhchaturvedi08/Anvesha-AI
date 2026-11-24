"use client";

import type { RunDetail } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, ArrowRightIcon, RotateCwIcon, SquareIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";

import { ActivityFeed } from "@/components/run/activity-feed";
import { PlanApproval } from "@/components/run/plan-approval";
import { SubQuestionList } from "@/components/run/sub-questions";
import { DepthBadge, RunStatusBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Callout, ErrorState, Skeleton, Spinner } from "@/components/ui/feedback";
import { api, ApiError, describeError, unwrap } from "@/lib/api";
import { formatCompact, formatDateTime, formatDuration, formatUsd, toNumber } from "@/lib/format";
import { isActive, keys, useRun } from "@/lib/queries";
import { useRunEvents } from "@/lib/use-run-events";

export default function RunPage() {
  const { projectId, runId } = useParams<{ projectId: string; runId: string }>();
  return <RunView key={runId} projectId={projectId} runId={runId} />;
}

function RunView({ projectId, runId }: { projectId: string; runId: string }) {
  const queryClient = useQueryClient();
  const run = useRun(runId);
  const { events, state } = useRunEvents(runId, projectId);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: keys.run(runId) });
    void queryClient.invalidateQueries({ queryKey: keys.project(projectId) });
    void queryClient.invalidateQueries({ queryKey: keys.runs(projectId) });
  };
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/runs/{run_id}/cancel", { params: { path: { run_id: runId } } })),
    onSuccess: (data) => {
      toast(data.status === "cancelled" ? "Run cancelled" : "Cancelling… the agents stop within seconds");
      invalidate();
    },
    onError: (error) => toast.error(describeError(error)),
  });
  const retry = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/runs/{run_id}/retry", { params: { path: { run_id: runId } } })),
    onSuccess: () => {
      toast.success("Retrying from the last completed step");
      invalidate();
    },
    onError: (error) => toast.error(describeError(error)),
  });

  if (run.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (run.isError) {
    if (run.error instanceof ApiError && run.error.status === 404) {
      return <Callout tone="danger" title="Run not found">It may have been deleted with its project.</Callout>;
    }
    return <ErrorState error={run.error} retry={() => void run.refetch()} />;
  }

  const data = run.data;
  const active = isActive(data.status);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-2">
          <Link
            href={`/projects/${projectId}`}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeftIcon className="size-3" /> All runs
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            <RunStatusBadge status={data.status} />
            <DepthBadge depth={data.depth} />
            <span className="text-xs text-muted-foreground" title={formatDateTime(data.created_at)}>
              {data.started_at ? `Running time ${formatDuration(data.started_at, data.finished_at)}` : "Not started yet"}
            </span>
          </div>
          <p className="max-w-3xl whitespace-pre-line text-sm">{data.question}</p>
        </div>
        <div className="flex gap-2">
          {data.report_id && (
            <Button asChild>
              <Link href={`/projects/${projectId}/report?id=${data.report_id}`}>
                Read the report <ArrowRightIcon />
              </Link>
            </Button>
          )}
          {data.status === "failed" && (
            <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
              {retry.isPending ? <Spinner /> : <RotateCwIcon />} Retry
            </Button>
          )}
          {active && data.status !== "awaiting_approval" && (
            <Button
              variant="outline"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending || data.cancel_requested}
            >
              {cancel.isPending ? <Spinner /> : <SquareIcon />}
              {data.cancel_requested ? "Cancelling…" : "Cancel"}
            </Button>
          )}
        </div>
      </div>

      <RunOutcome run={data} />

      {data.status === "awaiting_approval" && (
        <PlanApproval key={JSON.stringify(data.plan)} run={data} onCancel={() => cancel.mutate()} />
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <ActivityFeed events={events} state={state} />
        <aside className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Research plan</CardTitle>
            </CardHeader>
            <CardContent>
              <SubQuestionList items={data.sub_questions} />
            </CardContent>
          </Card>
          <UsageCard run={data} />
        </aside>
      </div>
    </div>
  );
}

function RunOutcome({ run }: { run: RunDetail }) {
  if (run.status === "failed") {
    const modelProblem =
      run.error_code === "model_not_configured" || run.error_code === "provider_auth_failed";
    return (
      <Callout
        tone="danger"
        title="The run stopped because of an error"
        action={
          modelProblem && (
            <Button asChild size="sm" variant="outline">
              <Link href="/settings">Check settings</Link>
            </Button>
          )
        }
      >
        {run.error_message ?? "Unknown error."} Fix the cause, then retry: the run continues from
        its last completed step.
      </Callout>
    );
  }
  if (run.status === "cancelled") {
    return <Callout tone="info" title="This run was cancelled" />;
  }
  return null;
}

function UsageCard({ run }: { run: RunDetail }) {
  const spent = toNumber(run.usage.cost_usd) ?? 0;
  const limit = toNumber(run.max_cost_usd);
  const share = limit ? Math.min(100, (spent / limit) * 100) : null;
  const findings = run.findings as Record<string, number>;
  const verified = (findings.verified ?? 0) + (findings.quote_verified ?? 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Spending and evidence</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-1.5">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Cost</span>
            <span className="tabular-nums">
              {formatUsd(spent)}
              {limit !== null && <span className="text-muted-foreground"> of {formatUsd(limit)}</span>}
            </span>
          </div>
          {share !== null && (
            <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="presentation">
              <div
                className={share > 85 ? "h-full bg-warning" : "h-full bg-primary"}
                style={{ width: `${share}%` }}
              />
            </div>
          )}
          {run.usage.unpriced_calls > 0 && (
            <p className="text-xs text-muted-foreground">
              {run.usage.unpriced_calls} call(s) to models without known prices aren&apos;t counted.
            </p>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
          <dt className="text-muted-foreground">Model calls</dt>
          <dd className="text-right tabular-nums">{run.usage.calls}</dd>
          <dt className="text-muted-foreground">Tokens</dt>
          <dd className="text-right tabular-nums">
            {formatCompact(run.usage.input_tokens + run.usage.output_tokens)}
          </dd>
          <dt className="text-muted-foreground">Verified findings</dt>
          <dd className="text-right tabular-nums">{verified}</dd>
          <dt className="text-muted-foreground">Rejected</dt>
          <dd className="text-right tabular-nums">{findings.rejected ?? 0}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}

"use client";

import { ArrowRightIcon, FileTextIcon, HistoryIcon, LibraryIcon, MessageSquareIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { RunStatusBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Callout, EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { formatDateTime, formatDuration, timeAgo } from "@/lib/format";
import { isActive, useProject, useReports, useRuns, useSources } from "@/lib/queries";

export default function ProjectOverviewPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const project = useProject(projectId);
  const runs = useRuns(projectId);
  const reports = useReports(projectId);
  const sources = useSources(projectId);
  const base = `/projects/${projectId}`;
  const latest = project.data?.latest_run;
  const latestReport = reports.data?.[0];

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        {latest && isActive(latest.status) && (
          <Callout
            tone={latest.status === "awaiting_approval" ? "warning" : "info"}
            title={
              latest.status === "awaiting_approval"
                ? "The research plan is ready for your review"
                : "Research in progress"
            }
            action={
              <Button asChild size="sm">
                <Link href={`${base}/runs/${latest.id}`}>
                  {latest.status === "awaiting_approval" ? "Review plan" : "Watch live"} <ArrowRightIcon />
                </Link>
              </Button>
            }
          >
            Started {timeAgo(latest.created_at)}.
          </Callout>
        )}

        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle className="flex items-center gap-2">
                <FileTextIcon className="size-4 text-muted-foreground" /> Latest report
              </CardTitle>
              <CardDescription>
                {latestReport
                  ? `Version ${latestReport.version}, written ${timeAgo(latestReport.created_at)}`
                  : "Reports appear here when a run finishes."}
              </CardDescription>
            </div>
            {latestReport && (
              <Badge tone={latestReport.status === "complete" ? "success" : "warning"}>
                {latestReport.status === "complete" ? "Complete" : "Partial"}
              </Badge>
            )}
          </CardHeader>
          <CardContent>
            {reports.isPending ? (
              <Skeleton className="h-10" />
            ) : reports.isError ? (
              <ErrorState error={reports.error} retry={() => void reports.refetch()} />
            ) : latestReport ? (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="font-medium">{latestReport.title}</p>
                <Button asChild variant="outline" size="sm">
                  <Link href={`${base}/report`}>
                    Read report <ArrowRightIcon />
                  </Link>
                </Button>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No report yet.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HistoryIcon className="size-4 text-muted-foreground" /> Runs
            </CardTitle>
          </CardHeader>
          <CardContent>
            {runs.isPending ? (
              <Skeleton className="h-24" />
            ) : runs.isError ? (
              <ErrorState error={runs.error} retry={() => void runs.refetch()} />
            ) : runs.data.length === 0 ? (
              <EmptyState icon={HistoryIcon} title="No runs yet" className="py-8">
                Start a run with the New run button.
              </EmptyState>
            ) : (
              <ul className="divide-y">
                {runs.data.map((run) => (
                  <li key={run.id}>
                    <Link
                      href={`${base}/runs/${run.id}`}
                      className="-mx-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-2 py-3 hover:bg-muted/60"
                    >
                      <RunStatusBadge status={run.status} />
                      <span className="min-w-0 flex-1 truncate text-sm">{run.question}</span>
                      <span className="text-xs text-muted-foreground" title={formatDateTime(run.created_at)}>
                        {timeAgo(run.created_at)}
                        {run.started_at && ` · ${formatDuration(run.started_at, run.finished_at)}`}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="space-y-4">
        <Link href={`${base}/sources`} className="block">
          <Card className="flex items-center gap-3 p-4 transition-colors hover:border-primary/40">
            <LibraryIcon className="size-5 text-muted-foreground" />
            <div className="flex-1">
              <p className="text-sm font-medium">Sources</p>
              <p className="text-xs text-muted-foreground">
                {sources.data ? `${sources.data.length} saved` : "Pages, papers and files"}
              </p>
            </div>
            <ArrowRightIcon className="size-4 text-muted-foreground" />
          </Card>
        </Link>
        <Link href={`${base}/chat`} className="block">
          <Card className="flex items-center gap-3 p-4 transition-colors hover:border-primary/40">
            <MessageSquareIcon className="size-5 text-muted-foreground" />
            <div className="flex-1">
              <p className="text-sm font-medium">Ask about this research</p>
              <p className="text-xs text-muted-foreground">Answers cite the project&apos;s evidence</p>
            </div>
            <ArrowRightIcon className="size-4 text-muted-foreground" />
          </Card>
        </Link>
      </div>
    </div>
  );
}

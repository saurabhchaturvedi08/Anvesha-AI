"use client";

import type { Finding } from "@anvesha/api-client";
import { FileSearchIcon, QuoteIcon } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { formatDateTime, hostname } from "@/lib/format";
import { useFindings, useRun, useRuns, useSources } from "@/lib/queries";
import { cn } from "@/lib/utils";

type Filter = "supported" | "rejected" | "all";

const STATUS = {
  verified: { label: "Verified", tone: "success", hint: "The quote is in the source, and the verifier agreed it supports the claim." },
  quote_verified: { label: "Quote checked", tone: "primary", hint: "The quote is in the source word for word; the verifier didn't review it." },
  rejected: { label: "Rejected", tone: "danger", hint: "The quote is in the source, but the verifier found it doesn't support the claim." },
} as const;

export default function EvidencePage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <EvidenceView />
    </Suspense>
  );
}

function EvidenceView() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const runs = useRuns(projectId);
  const sources = useSources(projectId);
  const runId = searchParams.get("run") ?? runs.data?.find((r) => r.status === "completed")?.id ?? runs.data?.[0]?.id ?? null;
  const run = useRun(runId);
  const findings = useFindings(runId);
  const [filter, setFilter] = useState<Filter>("supported");
  const [query, setQuery] = useState("");

  const sourceById = useMemo(() => new Map((sources.data ?? []).map((s) => [s.id, s])), [sources.data]);
  const questionById = useMemo(
    () => new Map((run.data?.sub_questions ?? []).map((q) => [q.id, q.question])),
    [run.data],
  );
  const counts = useMemo(() => {
    const all = findings.data ?? [];
    return {
      supported: all.filter((f) => f.status !== "rejected").length,
      rejected: all.filter((f) => f.status === "rejected").length,
      all: all.length,
    };
  }, [findings.data]);
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (findings.data ?? []).filter((finding) => {
      if (filter === "supported" && finding.status === "rejected") return false;
      if (filter === "rejected" && finding.status !== "rejected") return false;
      if (!needle) return true;
      const source = sourceById.get(finding.source_id);
      return [finding.statement, finding.quote, source?.title, source?.url].some((v) =>
        v?.toLowerCase().includes(needle),
      );
    });
  }, [findings.data, filter, query, sourceById]);

  if (runs.isPending) return <Skeleton className="h-96" />;
  if (runs.isError) return <ErrorState error={runs.error} retry={() => void runs.refetch()} />;
  if (runs.data.length === 0) {
    return (
      <EmptyState icon={FileSearchIcon} title="No evidence yet">
        Findings appear here as researchers record quotes from their sources.
      </EmptyState>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select
          aria-label="Run"
          className="w-auto max-w-full"
          value={runId ?? ""}
          onChange={(event) => router.replace(`/projects/${projectId}/evidence?run=${event.target.value}`)}
        >
          {runs.data.map((item) => (
            <option key={item.id} value={item.id}>
              {formatDateTime(item.created_at)} · {item.status}
            </option>
          ))}
        </Select>
        <div className="flex rounded-md border p-0.5 text-sm" role="group" aria-label="Show">
          {(["supported", "rejected", "all"] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
              className={cn(
                "rounded px-3 py-1 text-muted-foreground",
                filter === value && "bg-secondary font-medium text-secondary-foreground",
              )}
            >
              {value === "supported" ? "Supporting" : value === "rejected" ? "Rejected" : "All"} ({counts[value]})
            </button>
          ))}
        </div>
        <Input
          className="ml-auto max-w-xs"
          aria-label="Search findings"
          placeholder="Search statements and quotes"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {findings.isPending ? (
        <Skeleton className="h-64" />
      ) : findings.isError ? (
        <ErrorState error={findings.error} retry={() => void findings.refetch()} />
      ) : shown.length === 0 ? (
        <EmptyState icon={QuoteIcon} title={counts.all ? "No findings match" : "No findings in this run"} />
      ) : (
        <ul className="space-y-3">
          {shown.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              projectId={projectId}
              sourceTitle={sourceById.get(finding.source_id)?.title ?? null}
              sourceUrl={sourceById.get(finding.source_id)?.url ?? null}
              subQuestion={finding.sub_question_id ? (questionById.get(finding.sub_question_id) ?? null) : null}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function FindingCard({
  finding,
  projectId,
  sourceTitle,
  sourceUrl,
  subQuestion,
}: {
  finding: Finding;
  projectId: string;
  sourceTitle: string | null;
  sourceUrl: string | null;
  subQuestion: string | null;
}) {
  const status = STATUS[finding.status as keyof typeof STATUS] ?? STATUS.quote_verified;
  const first = finding.spans[0];
  const last = finding.spans[finding.spans.length - 1];
  const href =
    first && last
      ? `/projects/${projectId}/sources/${finding.source_id}?start=${first[0]}&end=${last[1]}`
      : `/projects/${projectId}/sources/${finding.source_id}`;
  return (
    <li>
      <Card className="space-y-3 p-4">
        <div className="flex flex-wrap items-start gap-2">
          <Badge tone={status.tone} title={status.hint}>
            {status.label}
          </Badge>
          <p className="min-w-0 flex-1 text-sm font-medium">{finding.statement}</p>
        </div>
        <blockquote className="flex gap-2 rounded-md bg-muted/60 p-3 font-serif text-sm leading-relaxed">
          <QuoteIcon className="mt-1 size-3 shrink-0 text-muted-foreground" aria-hidden />
          <span>{finding.quote}</span>
        </blockquote>
        {finding.status === "rejected" && finding.judge_note && (
          <p className="text-xs text-destructive">Verifier: {finding.judge_note}</p>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <Link href={href} className="font-medium text-foreground hover:underline">
            {sourceTitle || hostname(sourceUrl) || "Source"}
          </Link>
          {finding.page && <span>Page {finding.page}</span>}
          {subQuestion && <span className="line-clamp-1">For: {subQuestion}</span>}
        </div>
      </Card>
    </li>
  );
}

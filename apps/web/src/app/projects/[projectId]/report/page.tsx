"use client";

import type { Finding, Report } from "@anvesha/api-client";
import { CheckIcon, CopyIcon, DownloadIcon, FileTextIcon, PrinterIcon } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import type { CitationInfo } from "@/components/citation-chip";
import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout, EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { formatDateTime } from "@/lib/format";
import { useFindings, useReport, useReports } from "@/lib/queries";

export default function ReportPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <ReportView />
    </Suspense>
  );
}

function ReportView() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const reports = useReports(projectId);
  const selectedId = searchParams.get("id") ?? reports.data?.[0]?.id ?? null;
  const report = useReport(selectedId);

  if (reports.isPending) return <Skeleton className="h-96" />;
  if (reports.isError) return <ErrorState error={reports.error} retry={() => void reports.refetch()} />;
  if (reports.data.length === 0) {
    return (
      <EmptyState icon={FileTextIcon} title="No report yet">
        A report is written at the end of each research run.
      </EmptyState>
    );
  }

  return (
    <div className="space-y-4">
      <div className="no-print flex flex-wrap items-center gap-3">
        {reports.data.length > 1 && (
          <Select
            aria-label="Report version"
            className="w-auto"
            value={selectedId ?? ""}
            onChange={(event) => router.replace(`/projects/${projectId}/report?id=${event.target.value}`)}
          >
            {reports.data.map((item) => (
              <option key={item.id} value={item.id}>
                Version {item.version} · {formatDateTime(item.created_at)}
              </option>
            ))}
          </Select>
        )}
        {report.data && <ReportActions report={report.data} />}
      </div>
      {report.isPending ? (
        <Skeleton className="h-96" />
      ) : report.isError ? (
        <ErrorState error={report.error} retry={() => void report.refetch()} />
      ) : (
        <ReportBody report={report.data} projectId={projectId} />
      )}
    </div>
  );
}

function incompleteReason(report: Report): string {
  const details = report.details as { stop_reason?: string; mode?: string; truncated?: boolean };
  if (details.mode === "no_evidence") {
    return "The researchers found no verified evidence, so this report only lists what they tried.";
  }
  if (details.mode === "digest") {
    return "The report couldn't be written in full, so it lists the verified findings instead.";
  }
  if (details.stop_reason === "budget") {
    return "The spending limit was reached before research finished. The report covers what was found.";
  }
  if (details.truncated) return "The writer model ran out of room, so the report may end early.";
  return "Research stopped before it finished. The report covers what was found.";
}

function ReportBody({ report, projectId }: { report: Report; projectId: string }) {
  const findings = useFindings(report.run_id);
  const citations = useMemo<CitationInfo[]>(() => {
    const byId = new Map<string, Finding>((findings.data ?? []).map((f) => [f.id, f]));
    return report.citations.map((raw) => {
      const citation = raw as {
        number: number;
        source_id: string;
        title: string | null;
        url: string | null;
        finding_ids: string[];
      };
      return {
        number: citation.number,
        title: citation.title,
        url: citation.url,
        sourceHref: `/projects/${projectId}/sources/${citation.source_id}`,
        quotes: citation.finding_ids
          .map((id) => byId.get(id))
          .filter((f): f is Finding => !!f)
          .map((f) => ({ quote: f.quote, statement: f.statement, page: f.page })),
      };
    });
  }, [report.citations, findings.data, projectId]);

  return (
    <div className="space-y-4">
      {report.status === "incomplete" && (
        <Callout tone="warning" title="Partial report" className="no-print">
          {incompleteReason(report)}
        </Callout>
      )}
      <Card className="px-5 py-8 sm:px-10 sm:py-10">
        <div className="no-print mb-6 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge tone={report.status === "complete" ? "success" : "warning"}>
            {report.status === "complete" ? "Complete" : "Partial"}
          </Badge>
          <span>Version {report.version}</span>
          <span>·</span>
          <span>{formatDateTime(report.created_at)}</span>
          <span>·</span>
          <span>Hover or tap a citation to see the quoted evidence</span>
        </div>
        <article className="mx-auto max-w-3xl">
          <Markdown markdown={report.markdown} citations={citations} />
        </article>
      </Card>
    </div>
  );
}

function slug(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "report"
  );
}

function ReportActions({ report }: { report: Report }) {
  const [copied, setCopied] = useState(false);
  const download = () => {
    const url = URL.createObjectURL(new Blob([report.markdown], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${slug(report.title)}-v${report.version}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="ml-auto flex gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          void navigator.clipboard.writeText(report.markdown).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? <CheckIcon /> : <CopyIcon />} {copied ? "Copied" : "Copy Markdown"}
      </Button>
      <Button variant="outline" size="sm" onClick={download}>
        <DownloadIcon /> Download
      </Button>
      <Button variant="outline" size="sm" onClick={() => window.print()}>
        <PrinterIcon /> Print or PDF
      </Button>
    </div>
  );
}
